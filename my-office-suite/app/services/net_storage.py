"""Network & Removable Storage Backends for Wi-Fi Router USB Drives, NAS Shares, and Encrypted Cloud Sync.

Supports:
* Automatic discovery of removable USB drives, mounted router network shares, and local router gateway FTP/SMB hints
* Windows SMB (`\\\\192.168.1.1\\USB_Storage`) authenticated mount helper (`net use`)
* RFC-959 / RFC-4217 `FtpStorageBackend` (`ftp://` and `ftps://`) for USB sticks plugged into Wi-Fi routers (TP-Link,
  ASUS, Huawei, PTCL/ZTE, Netgear, Xiaomi, MikroTik, OpenWrt)
* `VaultSyncBackend`: transparent zero-knowledge `MOVAULT1` hardware-bound encryption wrapper over ANY `SyncBackend`!
"""
from __future__ import annotations

import ftplib
import logging
import os
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services import vault
from app.services.cloud_sync import RemoteEntry, SyncBackend

log = logging.getLogger(__name__)
_NO_WIN = 0x08000000 if os.name == "nt" else 0


@dataclass
class StorageTarget:
    label: str
    path_or_url: str
    kind: str  # "usb" | "smb" | "ftp"


def _default_gateway_ip() -> str:
    """Detect the active Wi-Fi router gateway IP (e.g. `192.168.1.1` or `192.168.0.1`) so we can suggest router USB URLs."""
    try:
        if sys.platform.startswith("linux") and os.path.exists("/proc/net/route"):
            for line in Path("/proc/net/route").read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
                cols = line.split()
                if len(cols) >= 3 and cols[1] == "00000000":
                    g = int(cols[2], 16)
                    ip = f"{g & 0xFF}.{(g >> 8) & 0xFF}.{(g >> 16) & 0xFF}.{(g >> 24) & 0xFF}"
                    if not ip.startswith(("0.", "127.", "169.254.")):
                        return ip
        elif sys.platform == "win32":  # pragma: no cover - Windows runtime
            out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5, creationflags=_NO_WIN)
            for line in out.stdout.splitlines():
                if "Default Gateway" in line and ":" in line:
                    cand = line.split(":", 1)[1].strip()
                    if cand and "." in cand:
                        return cand
    except Exception:
        pass
    return "192.168.1.1"


def discover_storage_targets() -> list[StorageTarget]:
    """Discover plugged-in USB drives, network drives, and Wi-Fi Router USB share templates."""
    targets: list[StorageTarget] = []
    if sys.platform == "win32":  # pragma: no cover - Windows runtime
        import ctypes

        DRIVE_REMOVABLE, DRIVE_REMOTE = 2, 4
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for idx in range(26):
            if not (mask & (1 << idx)):
                continue
            root = f"{chr(65 + idx)}:\\"
            dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
            if dtype == DRIVE_REMOVABLE:
                targets.append(StorageTarget(f"USB Drive ({root}) — Direct Laptop USB Port", os.path.join(root, "MyOffice_Vault"), "usb"))
            elif dtype == DRIVE_REMOTE:
                targets.append(StorageTarget(f"Network / Router Drive ({root})", os.path.join(root, "MyOffice_Vault"), "smb"))
    else:
        for base in ("/media", "/mnt", "/run/media"):
            p = Path(base)
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.is_dir():
                        targets.append(StorageTarget(f"Removable / Mounted Drive ({child})", str(child / "MyOffice_Vault"), "usb"))

    gw = _default_gateway_ip()
    targets.append(StorageTarget(
        f"Wi-Fi Router USB via SMB (\\\\{gw}\\USB_Storage\\MyOffice_Vault)",
        f"\\\\{gw}\\USB_Storage\\MyOffice_Vault",
        "smb",
    ))
    targets.append(StorageTarget(
        f"Wi-Fi Router USB via FTP (ftp://{gw}:21/MyOffice_Vault)",
        f"ftp://{gw}:21/MyOffice_Vault",
        "ftp",
    ))
    return targets


def connect_smb_share(unc_path: str, username: str = "", password: str = "") -> tuple[bool, str]:
    """Authenticate a Windows UNC Router/NAS share (`\\\\192.168.1.1\\USB`) via `net use` when credentials are supplied."""
    if sys.platform != "win32" or not unc_path.startswith("\\\\") or not username:
        return True, "OK"
    parts = [p for p in unc_path.strip("\\").split("\\") if p]
    if len(parts) < 2:
        return False, f"Invalid UNC network path: {unc_path}"
    share_root = f"\\\\{parts[0]}\\{parts[1]}"
    cmd = ["net", "use", share_root, password or "", f"/user:{username}", "/persistent:no"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=_NO_WIN, errors="replace")
    if res.returncode == 0 or "1219" in res.stderr or "already" in (res.stdout + res.stderr).lower():
        return True, "Connected"
    return False, (res.stderr or res.stdout).strip() or f"net use exited {res.returncode}"


class FtpStorageBackend(SyncBackend):
    """FTP / FTPS backend specifically tuned for Wi-Fi Router USB ports and home/office NAS devices."""
    name = "ftp"

    def __init__(self, url: str, username: str = "anonymous", password: str = "", timeout: int = 20):
        u = urllib.parse.urlparse((url or "").strip())
        if u.scheme not in ("ftp", "ftps") or not u.hostname:
            raise ValueError("Enter a valid FTP/FTPS router URL, e.g. ftp://192.168.1.1:21/MyOffice_Vault")
        self.tls = u.scheme == "ftps"
        self.host = u.hostname
        self.port = int(u.port or 21)
        self.base_dir = "/" + u.path.strip("/") if u.path.strip("/") else "/"
        self.username = username or u.username or "anonymous"
        self.password = password if password != "" else (u.password or "")
        self.timeout = timeout

    def _connect(self) -> ftplib.FTP:
        ftp = ftplib.FTP_TLS(timeout=self.timeout) if self.tls else ftplib.FTP(timeout=self.timeout)
        ftp.connect(self.host, self.port, timeout=self.timeout)
        ftp.login(self.username, self.password)
        if self.tls and isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        self._ensure_dir(ftp, self.base_dir)
        ftp.cwd(self.base_dir)
        return ftp

    @staticmethod
    def _ensure_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
        if not remote_dir or remote_dir == "/":
            return
        cur = ""
        for part in [p for p in remote_dir.split("/") if p]:
            cur += "/" + part
            try:
                ftp.cwd(cur)
            except ftplib.error_perm:
                try:
                    ftp.mkd(cur)
                except ftplib.error_perm:
                    pass
                ftp.cwd(cur)

    def _walk(self, ftp: ftplib.FTP, prefix: str = "") -> dict[str, RemoteEntry]:
        out: dict[str, RemoteEntry] = {}
        target = f"{self.base_dir.rstrip('/')}/{prefix}".rstrip("/") or "/"
        ftp.cwd(target)
        try:
            for name, facts in ftp.mlsd():
                if name in (".", "..") or name.endswith(".partial"):
                    continue
                rel = f"{prefix}/{name}".lstrip("/")
                ftype = facts.get("type", "file")
                if ftype == "dir":
                    out.update(self._walk(ftp, rel))
                    ftp.cwd(target)
                elif ftype == "file":
                    sz = int(facts.get("size", 0))
                    raw_m = facts.get("modify", "")
                    mt = datetime.strptime(raw_m[:14], "%Y%m%d%H%M%S").timestamp() if len(raw_m) >= 14 else 0.0
                    out[rel] = RemoteEntry(sz, mt, f"{sz}-{int(mt)}")
            return out
        except Exception:
            names = ftp.nlst()
            for raw_n in names:
                name = os.path.basename(raw_n.rstrip("/"))
                if not name or name in (".", "..") or name.endswith(".partial"):
                    continue
                rel = f"{prefix}/{name}".lstrip("/")
                sz = 0
                try:
                    sz = int(ftp.size(name) or 0)
                except Exception:
                    pass
                out[rel] = RemoteEntry(sz, 0.0, f"{sz}-0")
            return out

    def list(self) -> dict[str, RemoteEntry]:
        ftp = self._connect()
        try:
            return self._walk(ftp)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def upload(self, local: str, rel: str) -> RemoteEntry:
        ftp = self._connect()
        try:
            rel_norm = rel.strip("/")
            parent = os.path.dirname(rel_norm)
            if parent:
                self._ensure_dir(ftp, f"{self.base_dir.rstrip('/')}/{parent}")
            else:
                ftp.cwd(self.base_dir)
            fname = os.path.basename(rel_norm)
            tmp_name = fname + ".partial"
            with open(local, "rb") as fh:
                ftp.storbinary(f"STOR {tmp_name}", fh)
            try:
                ftp.delete(fname)
            except Exception:
                pass
            ftp.rename(tmp_name, fname)
            st = os.stat(local)
            return RemoteEntry(st.st_size, st.st_mtime, f"{st.st_size}-{int(st.st_mtime)}")
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def download(self, rel: str, local: str) -> None:
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        tmp = local + ".partial"
        ftp = self._connect()
        try:
            rel_norm = rel.strip("/")
            parent = os.path.dirname(rel_norm)
            if parent:
                ftp.cwd(f"{self.base_dir.rstrip('/')}/{parent}")
            else:
                ftp.cwd(self.base_dir)
            with open(tmp, "wb") as fh:
                ftp.retrbinary(f"RETR {os.path.basename(rel_norm)}", fh.write)
            os.replace(tmp, local)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def delete(self, rel: str) -> None:
        ftp = self._connect()
        try:
            ftp.cwd(self.base_dir)
            ftp.delete(rel.strip("/"))
        except Exception:
            pass
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()


class VaultSyncBackend(SyncBackend):
    """Wraps ANY `SyncBackend` with laptop-bound `MOVAULT1` AES-256-GCM encryption!"""
    name = "vault_sync"

    def __init__(self, inner: SyncBackend, store: vault.DeviceKeyStore | None = None, passphrase: str = ""):
        self.inner = inner
        self.store = store
        self.passphrase = passphrase
        self._remote_map: dict[str, str] = {}  # logical `rel` -> opaque remote `.movault` filename

    def list(self) -> dict[str, RemoteEntry]:
        raw_list = self.inner.list()
        self._remote_map.clear()
        out: dict[str, RemoteEntry] = {}
        for vname, rem in raw_list.items():
            if not vname.lower().endswith(".movault"):
                continue
            fd, tmp = tempfile.mkstemp(suffix=".movault")
            os.close(fd)
            try:
                self.inner.download(vname, tmp)
                entry = vault.inspect_vault_file(tmp, store=self.store, passphrase=self.passphrase)
                if entry.locked_to_other_device:
                    continue
                rel = entry.original_name
                self._remote_map[rel] = vname
                out[rel] = RemoteEntry(entry.original_size, rem.mtime, entry.sha256[:16])
            except Exception:
                continue
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return out

    def upload(self, local: str, rel: str) -> RemoteEntry:
        vname = self._remote_map.get(rel) or vault.random_vault_filename()
        blob = vault.encrypt_bytes(
            Path(local).read_bytes(),
            rel,
            local,
            store=self.store,
            passphrase=self.passphrase,
        )
        fd, tmp = tempfile.mkstemp(suffix=".movault")
        os.close(fd)
        try:
            Path(tmp).write_bytes(blob)
            rem = self.inner.upload(tmp, vname)
            self._remote_map[rel] = vname
            st = os.stat(local)
            return RemoteEntry(st.st_size, rem.mtime, rem.etag)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def download(self, rel: str, local: str) -> None:
        vname = self._remote_map.get(rel)
        if not vname:
            self.list()
            vname = self._remote_map.get(rel)
        if not vname:
            raise FileNotFoundError(rel)
        fd, tmp = tempfile.mkstemp(suffix=".movault")
        os.close(fd)
        try:
            self.inner.download(vname, tmp)
            vault.decrypt_vault_file(tmp, local, store=self.store, passphrase=self.passphrase)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def delete(self, rel: str) -> None:
        vname = self._remote_map.pop(rel, None)
        if vname:
            self.inner.delete(vname)
