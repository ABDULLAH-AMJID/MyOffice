"""Backup & Security Vault Service (Tool 27).

1. Local rolling version backups: ``make_backup`` / ``list_backups`` / ``prune`` / ``restore_backup`` + ``RecoveryManager``.
2. Router USB / Network Hardware Vault (``MOVAULT1``, AES-256-GCM + Windows DPAPI / Machine Key): encrypts filenames &
   file contents so backups on a Wi-Fi router USB stick (SMB ``\\\\192.168.1.1\\...`` or Router FTP ``ftp://...``) or
   direct USB port can ONLY be unlocked on the owner's laptop.
3. Gmail Cloud Backup: stores versioned backups inside Gmail label ``MyOffice/Backups`` (encrypted by default).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from app.services import vault
from app.services.config import cfg
from app.services.paths import user_subdir

log = logging.getLogger(__name__)
_BK_RE = re.compile(r"^(?P<stem>.+)_backup_(?P<ts>\d{8}_\d{6})(?:_\d+)?(?P<ext>\.[^.]+)?$")


def backup_dir() -> Path:
    custom = cfg().get("backup.dir", "")
    if custom:
        p = Path(custom)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return user_subdir("backup")


@dataclass
class BackupEntry:
    path: Path
    original_name: str
    timestamp: datetime
    size: int

    @property
    def original(self) -> str:
        return self.original_name

    @property
    def when(self) -> datetime:
        return self.timestamp


BackupItem = BackupEntry


def make_backup(path: str | os.PathLike, force: bool = False, mirror_background: bool = True) -> Path | None:
    """Copy ``path`` into the backup folder (if backups are enabled) and trigger any enabled Vault / Gmail mirrors."""
    src = Path(path)
    if not src.exists() or not src.is_file():
        return None
    if not force and not cfg().get("backup.enabled", True):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = backup_dir()
    dest = d / f"{src.stem}_backup_{ts}{src.suffix}"
    n = 1
    while dest.exists():
        dest = d / f"{src.stem}_backup_{ts}_{n}{src.suffix}"
        n += 1
    shutil.copy2(src, dest)
    prune(src.stem, src.suffix, int(cfg().get("backup.keep", 10)))
    log.info("Backup created: %s", dest)
    if mirror_background:
        _trigger_auto_mirrors(str(src.resolve()))
    return dest


def snapshot(path: str | os.PathLike, max_versions: int = 10, mirror_background: bool = True) -> Path | None:
    old_keep = cfg().get("backup.keep", 10)
    cfg().set("backup.keep", max_versions, save=False)
    try:
        return make_backup(path, force=True, mirror_background=mirror_background)
    finally:
        cfg().set("backup.keep", old_keep, save=False)


def _trigger_auto_mirrors(src_path: str) -> None:
    try:
        vault_cfg = cfg().get("backup_vault") or {}
        gmail_cfg = cfg().get("backup_gmail") or {}
        want_vault = bool(vault_cfg.get("auto_mirror") and vault_cfg.get("location"))
        want_gmail = bool(gmail_cfg.get("auto_mirror") and gmail_cfg.get("email") and gmail_cfg.get("app_password"))
        if not (want_vault or want_gmail):
            return

        def _worker():
            if want_vault:
                try:
                    backup_to_vault_target(src_path, vault_cfg)
                except Exception as exc:
                    log.warning("Auto-mirror to Router/USB Vault skipped: %s", exc)
            if want_gmail:
                try:
                    backup_to_gmail(src_path, gmail_cfg)
                except Exception as exc:
                    log.warning("Auto-mirror to Gmail skipped: %s", exc)

        threading.Thread(target=_worker, daemon=True, name="myoffice-backup-mirror").start()
    except Exception:
        pass


def list_backups(original_name: str | None = None) -> list[BackupEntry]:
    target_base = os.path.basename(original_name) if original_name else None
    out = []
    for p in backup_dir().iterdir():
        m = _BK_RE.match(p.name)
        if not m or not p.is_file():
            continue
        orig = m.group("stem") + (m.group("ext") or "")
        if target_base and orig != target_base:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        out.append(BackupEntry(p, orig, ts, p.stat().st_size))
    out.sort(key=lambda e: (e.timestamp, e.path.name), reverse=True)
    return out


def prune(stem: str, suffix: str, keep: int) -> int:
    entries = list_backups(stem + suffix)
    removed = 0
    for e in entries[max(1, keep):]:
        try:
            e.path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def restore_backup(backup_path: str | os.PathLike, target: str | os.PathLike) -> Path:
    target_p = Path(target)
    target_p.parent.mkdir(parents=True, exist_ok=True)
    if target_p.exists():
        make_backup(target_p, force=True, mirror_background=False)
    shutil.copy2(backup_path, target_p)
    return target_p


def restore(item: BackupEntry, dest: str) -> None:
    restore_backup(item.path, dest)


# ============================================================================= Router USB / Network Hardware Vault
def _is_ftp_location(location: str) -> bool:
    return location.strip().lower().startswith(("ftp://", "ftps://"))


def _make_ftp(vault_cfg: dict):
    from app.services.net_storage import FtpStorageBackend

    return FtpStorageBackend(
        vault_cfg.get("location", ""),
        vault_cfg.get("username", "") or "anonymous",
        vault.unseal_secret(vault_cfg.get("password", "")),
    )


def _prepare_vault_folder(vault_cfg: dict) -> Path:
    from app.services.net_storage import connect_smb_share

    loc = (vault_cfg.get("location") or "").strip()
    if not loc:
        raise ValueError("Choose a USB drive, Router SMB path, or Router FTP URL first.")
    if loc.startswith("\\\\") and vault_cfg.get("username"):
        ok, msg = connect_smb_share(loc, vault_cfg.get("username", ""),
                                    vault.unseal_secret(vault_cfg.get("password", "")))
        if not ok:
            raise RuntimeError(f"Router SMB login failed: {msg}")
    p = Path(loc)
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_to_vault_target(src_path: str, vault_cfg: dict,
                           store: vault.DeviceKeyStore | None = None) -> str:
    """Encrypt *src_path* with ``MOVAULT1`` hardware-bound AES-256-GCM onto USB, Router SMB share, or Router FTP."""
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(src_path)
    passphrase = vault.unseal_secret(vault_cfg.get("passphrase", ""))
    loc = (vault_cfg.get("location") or "").strip()
    if _is_ftp_location(loc):
        ftp = _make_ftp(vault_cfg)
        blob = vault.encrypt_bytes(src.read_bytes(), src.name, str(src.resolve()), store=store, passphrase=passphrase)
        vname = vault.random_vault_filename()
        fd, tmp = tempfile.mkstemp(suffix=".movault")
        os.close(fd)
        try:
            Path(tmp).write_bytes(blob)
            ftp.upload(tmp, vname)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return f"{loc.rstrip('/')}/{vname}"
    folder = _prepare_vault_folder(vault_cfg)
    out = vault.encrypt_file_to_dir(src, folder, store=store, passphrase=passphrase)
    return str(out)


def list_vault_target(vault_cfg: dict, store: vault.DeviceKeyStore | None = None,
                      passphrase: str = "") -> list[vault.VaultEntry]:
    pw = passphrase or vault.unseal_secret(vault_cfg.get("passphrase", ""))
    loc = (vault_cfg.get("location") or "").strip()
    if not loc:
        return []
    if _is_ftp_location(loc):
        ftp = _make_ftp(vault_cfg)
        remote = ftp.list()
        entries: list[vault.VaultEntry] = []
        for rel, rem in remote.items():
            if not rel.lower().endswith(".movault"):
                continue
            fd, tmp = tempfile.mkstemp(suffix=".movault")
            os.close(fd)
            try:
                ftp.download(rel, tmp)
                entry = vault.inspect_vault_file(tmp, store=store, passphrase=pw)
                entry.vault_filename = rel
                entry.encrypted_size = rem.size or entry.encrypted_size
                entries.append(entry)
            except Exception as exc:
                log.warning("Skipping remote vault item %s: %s", rel, exc)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries
    folder = _prepare_vault_folder(vault_cfg)
    return vault.list_vault_dir(folder, store=store, passphrase=pw, include_locked=True)


def restore_from_vault_target(entry: vault.VaultEntry, dest_path: str, vault_cfg: dict,
                              store: vault.DeviceKeyStore | None = None, passphrase: str = "") -> Path:
    pw = passphrase or vault.unseal_secret(vault_cfg.get("passphrase", ""))
    loc = (vault_cfg.get("location") or "").strip()
    if _is_ftp_location(loc):
        ftp = _make_ftp(vault_cfg)
        fd, tmp = tempfile.mkstemp(suffix=".movault")
        os.close(fd)
        try:
            ftp.download(entry.vault_filename, tmp)
            out, _ = vault.decrypt_vault_file(tmp, dest_path, store=store, passphrase=pw)
            return out
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    out, _ = vault.decrypt_vault_file(entry.path, dest_path, store=store, passphrase=pw)
    return out


# ============================================================================= Gmail Cloud Backup
def _make_gmail_backend(gmail_cfg: dict):
    from app.services.gmail_storage import GmailBackend

    return GmailBackend(
        gmail_cfg.get("email", ""),
        vault.unseal_secret(gmail_cfg.get("app_password", "")),
        label=gmail_cfg.get("label", "") or "MyOffice/Backups",
        imap_host=gmail_cfg.get("imap_host", "") or "imap.gmail.com",
        imap_port=int(gmail_cfg.get("imap_port") or 993),
        use_ssl=bool(gmail_cfg.get("use_ssl", True)),
    )


def backup_to_gmail(src_path: str, gmail_cfg: dict, store: vault.DeviceKeyStore | None = None) -> str:
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(src_path)
    backend = _make_gmail_backend(gmail_cfg)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    encrypt = bool(gmail_cfg.get("encrypt", True))
    if encrypt:
        pw = vault.unseal_secret(gmail_cfg.get("passphrase", ""))
        blob = vault.encrypt_bytes(src.read_bytes(), src.name, str(src.resolve()), store=store, passphrase=pw)
        rel = f"{stamp}__{vault.random_vault_filename()}"
        fd, tmp = tempfile.mkstemp(suffix=".movault")
        os.close(fd)
        try:
            Path(tmp).write_bytes(blob)
            backend.upload(tmp, rel)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return rel
    rel = f"{stamp}__{src.name}"
    backend.upload(str(src), rel)
    return rel


def list_gmail_backups(gmail_cfg: dict) -> list[tuple[str, dt.datetime, int]]:
    backend = _make_gmail_backend(gmail_cfg)
    items: list[tuple[str, dt.datetime, int]] = []
    for rel, rem in backend.list().items():
        items.append((rel, dt.datetime.fromtimestamp(rem.mtime), rem.size))
    items.sort(key=lambda t: t[1], reverse=True)
    return items


def restore_gmail_backup(rel: str, dest_dir_or_file: str, gmail_cfg: dict,
                         store: vault.DeviceKeyStore | None = None, passphrase: str = "") -> Path:
    backend = _make_gmail_backend(gmail_cfg)
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    try:
        backend.download(rel, tmp)
        dest_p = Path(dest_dir_or_file)
        is_dir_target = dest_p.is_dir() or not dest_p.suffix
        if rel.lower().endswith(".movault"):
            pw = passphrase or vault.unseal_secret(gmail_cfg.get("passphrase", ""))
            plain, meta = vault.decrypt_bytes(Path(tmp).read_bytes(), store=store, passphrase=pw)
            out = (dest_p / meta["original_name"]) if is_dir_target else dest_p
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(plain)
            return out
        raw_name = rel.split("__", 1)[-1] if "__" in rel else os.path.basename(rel)
        out = (dest_p / raw_name) if is_dir_target else dest_p
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp, out)
        return out
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ----------------------------------------------------------------------------- Crash RecoveryManager
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RecoveryManager:
    """Per-session auto-recovery snapshots."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else user_subdir("recovery")
        self.session_id = time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.session_dir = self.root / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "session.lock").write_text(str(os.getpid()))
        self.manifest_path = self.session_dir / "manifest.json"
        self.manifest: dict = {}
        self._write_manifest()

    def _write_manifest(self):
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifest, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.manifest_path)

    def snapshot(self, doc_id: str, display_name: str, kind: str, original: str | None, ext: str, writer) -> Path:
        dest = self.session_dir / f"{doc_id}{ext}"
        tmp = self.session_dir / f"{doc_id}.tmp{ext}"
        writer(str(tmp))
        os.replace(tmp, dest)
        self.manifest[doc_id] = {"name": display_name, "kind": kind, "original": original or "",
                                 "file": dest.name, "time": datetime.now().isoformat(timespec="seconds")}
        self._write_manifest()
        return dest

    def discard(self, doc_id: str) -> None:
        info = self.manifest.pop(doc_id, None)
        if info:
            try:
                (self.session_dir / info["file"]).unlink()
            except OSError:
                pass
            self._write_manifest()

    def orphaned_sessions(self) -> list[dict]:
        found = []
        for d in self.root.iterdir():
            if not d.is_dir() or d == self.session_dir:
                continue
            lock = d / "session.lock"
            try:
                pid = int(lock.read_text().strip()) if lock.exists() else -1
            except ValueError:
                pid = -1
            if _pid_alive(pid) and pid != os.getpid():
                continue
            man = d / "manifest.json"
            try:
                entries = json.loads(man.read_text(encoding="utf-8")) if man.exists() else {}
            except Exception:
                entries = {}
            for doc_id, info in entries.items():
                f = d / info.get("file", "")
                if f.exists():
                    found.append(dict(info, path=str(f), session=str(d), doc_id=doc_id))
            if not entries:
                shutil.rmtree(d, ignore_errors=True)
        return found

    def purge_session(self, session_dir: str) -> None:
        shutil.rmtree(session_dir, ignore_errors=True)

    def clean_shutdown(self) -> None:
        shutil.rmtree(self.session_dir, ignore_errors=True)


_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def repair_ooxml(src: str, dest: str) -> dict:
    report = {"kept": [], "repaired": [], "dropped": [], "recovered_text": ""}
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(src, "r") as zf:
        for name in zf.namelist():
            try:
                entries[name] = zf.read(name)
            except Exception as e:
                report["dropped"].append(f"{name} ({e})")
    out_entries: dict[str, bytes] = {}
    for name, data in entries.items():
        if name.endswith((".xml", ".rels")):
            try:
                ET.fromstring(data)
                out_entries[name] = data
                report["kept"].append(name)
                continue
            except ET.ParseError:
                txt = _XML_ILLEGAL.sub("", data.decode("utf-8", "replace"))
                ET.fromstring(txt.encode("utf-8"))
                out_entries[name] = txt.encode("utf-8")
                report["repaired"].append(name)
        else:
            out_entries[name] = data
            report["kept"].append(name)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in out_entries.items():
            zf.writestr(name, data)
    return report
