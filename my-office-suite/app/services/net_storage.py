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
    """One place encrypted backups can live: a USB drive, a router share, or an FTP/SMB endpoint.

    The dialogs (`backup_dialog.py`, `cloud_sync_dialog.py`) render these as
    ``f"{label} -> {location} ({detail})"`` and read ``backend`` to pick the SMB tab or the FTP tab, so the
    attribute names below are part of the UI contract. `path_or_url`/`kind` are kept as aliases because they
    were the original public names.
    """

    label: str
    location: str
    backend: str = "usb"        # "usb" | "smb" | "ftp"
    detail: str = ""

    # ---- back-compat aliases (older call sites / third-party scripts) ----
    @property
    def path_or_url(self) -> str:
        return self.location

    @property
    def kind(self) -> str:
        return self.backend

    def __str__(self) -> str:  # used directly in the "no target found" dialogs
        return f"{self.label} -> {self.location} ({self.detail})" if self.detail else f"{self.label} -> {self.location}"


def _default_gateway_ip() -> str:
    """Detect the active Wi-Fi router gateway IP (e.g. `192.168.1.1` or `192.168.0.1`).

    Windows ships a *localised* `ipconfig`, so matching the English words "Default Gateway" finds nothing on a
    Chinese/Urdu/German system. PowerShell's `Get-NetRoute` is language-neutral, `route print 0.0.0.0` is the
    fallback, and only then do we fall back to a plain-IPv4 scan of `ipconfig`.
    """
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
            ip = _windows_gateway_powershell() or _windows_gateway_route_print() or _windows_gateway_ipconfig()
            if ip:
                return ip
    except Exception:
        pass
    return "192.168.1.1"


_IPV4_RE = None


def _ipv4_candidates(text: str) -> list[str]:
    global _IPV4_RE
    if _IPV4_RE is None:
        import re

        _IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    out: list[str] = []
    for raw in _IPV4_RE.findall(text or ""):
        octets = [int(x) for x in raw.split(".")]
        if any(o > 255 for o in octets):
            continue
        if raw.startswith(("0.", "127.", "169.254.", "255.")):
            continue
        if raw not in out:
            out.append(raw)
    return out


def _windows_gateway_powershell() -> str:  # pragma: no cover - Windows runtime
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | "
             "Select-Object -First 1 -ExpandProperty NextHop)"],
            capture_output=True, text=True, timeout=8, creationflags=_NO_WIN, errors="replace",
        )
        cands = _ipv4_candidates(out.stdout)
        return cands[0] if cands else ""
    except Exception:
        return ""


def _windows_gateway_route_print() -> str:  # pragma: no cover - Windows runtime
    try:
        out = subprocess.run(["route", "print", "0.0.0.0"], capture_output=True, text=True,
                             timeout=8, creationflags=_NO_WIN, errors="replace")
        for line in out.stdout.splitlines():
            cols = line.split()
            if len(cols) >= 3 and cols[0] == "0.0.0.0":
                cands = _ipv4_candidates(cols[2])
                if cands:
                    return cands[0]
    except Exception:
        pass
    return ""


def _windows_gateway_ipconfig() -> str:  # pragma: no cover - Windows runtime
    """Last resort: the gateway is the first private IPv4 that is not one of this machine's own addresses."""
    try:
        out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=8,
                             creationflags=_NO_WIN, errors="replace").stdout
    except Exception:
        return ""
    lines = out.splitlines()
    hints = ("gateway", "standardgateway", "gateway standard", "tor", "gateway:", "brama", "ağ geçidi",
             "ゲートウェイ", "网关", "العبّارة", "العبارة")
    for line in lines:
        low = line.lower()
        if any(h in low for h in hints):
            cands = _ipv4_candidates(line)
            if cands:
                return cands[0]
    # localised to the point that no keyword matches: prefer .1 / .254 style private addresses
    cands = [ip for ip in _ipv4_candidates(out) if ip.startswith(("192.168.", "10.", "172."))]
    for ip in cands:
        if ip.endswith((".1", ".254")):
            return ip
    return cands[0] if cands else ""


def _removable_drives_windows() -> list[StorageTarget]:
    """Pluggable USB sticks / SD cards, and drives that are already mapped over the network."""
    out: list[StorageTarget] = []
    import ctypes

    DRIVE_REMOVABLE, DRIVE_REMOTE, DRIVE_FIXED = 2, 4, 3
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:  # pragma: no cover - non-Windows
        return out
    for idx in range(26):
        if not (mask & (1 << idx)):
            continue
        root = f"{chr(65 + idx)}:\\"
        try:
            dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
        except Exception:
            continue
        if dtype == DRIVE_REMOVABLE:
            free, total = _free_space(root)
            out.append(StorageTarget(
                f"USB Drive ({root[:-1]})",
                os.path.join(root, "MyOffice_Vault"),
                "usb",
                f"removable, {free} free of {total}",
            ))
        elif dtype == DRIVE_REMOTE:
            out.append(StorageTarget(
                f"Network / Router Drive ({root[:-1]})",
                os.path.join(root, "MyOffice_Vault"),
                "smb",
                "mapped network drive",
            ))
        elif dtype == DRIVE_FIXED and os.path.isdir(os.path.join(root, "MyOffice_Vault")):
            out.append(StorageTarget(
                f"Local Drive ({root[:-1]})",
                os.path.join(root, "MyOffice_Vault"),
                "usb",
                "existing MyOffice_Vault folder",
            ))
    return out


def _free_space(path: str) -> tuple[str, str]:
    try:
        usage = __import__("shutil").disk_usage(path)
        gb = 1024 ** 3
        return f"{usage.free / gb:.1f} GB", f"{usage.total / gb:.1f} GB"
    except Exception:
        return "?", "?"


def _mounted_drives_posix() -> list[StorageTarget]:
    out: list[StorageTarget] = []
    for base in ("/media", "/mnt", "/run/media"):
        p = Path(base)
        if not p.is_dir():
            continue
        try:
            children = sorted(p.iterdir())
        except Exception:
            continue
        for child in children:
            if not child.is_dir():
                continue
            free, total = _free_space(str(child))
            out.append(StorageTarget(
                f"Removable / Mounted Drive ({child})",
                str(child / "MyOffice_Vault"),
                "usb",
                f"{free} free of {total}",
            ))
    return out


def _port_open(host: str, port: int, timeout: float) -> bool:
    """Non-blocking-ish TCP probe; a closed/absent router port answers instantly, a silent one times out."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _smb_open(host: str) -> bool:
    return _port_open(host, 445, 0.35) or _port_open(host, 139, 0.35)


def _ftp_open(host: str, port: int = 21, timeout: float = 0.6) -> bool:
    return _port_open(host, port, timeout)


def _gateway_candidates() -> list[str]:
    """The default gateway first, then the few addresses home routers actually use."""
    primary = _default_gateway_ip()
    guesses = ["192.168.1.1", "192.168.0.1", "192.168.100.1", "10.0.0.1", "192.168.2.1"]
    ordered = [primary] + [g for g in guesses if g != primary]
    out: list[str] = []
    for ip in ordered:
        if ip and ip not in out and not ip.startswith(("0.", "127.", "169.254.")):
            out.append(ip)
    return out


def discover_storage_targets(
    scan_network: bool = True,
    include_local: bool = True,
    timeout: float = 0.6,
) -> list[StorageTarget]:
    """Find every storage target that is actually reachable right now.

    `scan_network=False` returns local drives plus *unverified* router suggestions instantly (used for the
    "what could I use?" list); `scan_network=True` additionally opens a TCP socket to the router's SMB/FTP
    ports and only reports the router shares that really answered, each labelled with what answered.

    The whole probe is bounded by `timeout` per address (6 addresses max), so the UI spinner always ends in
    a couple of seconds even when the router answers nothing — it can never hang the caller.
    """
    targets: list[StorageTarget] = []
    if include_local:
        if sys.platform == "win32":  # pragma: no cover - Windows runtime
            targets.extend(_removable_drives_windows())
        else:
            targets.extend(_mounted_drives_posix())

    gateways = _gateway_candidates()
    if not scan_network:
        gw = gateways[0]
        targets.append(StorageTarget(f"Wi-Fi Router USB via SMB", f"\\\\{gw}\\USB_Storage\\MyOffice_Vault", "smb", "suggested"))
        targets.append(StorageTarget(f"Wi-Fi Router USB via FTP", f"ftp://{gw}:21/MyOffice_Vault", "ftp", "suggested"))
        return targets

    gateways = gateways[:5]
    radius = min(max(timeout, 0.25), 3.0)

    # Probe every candidate address in parallel: the worst case is one timeout (~0.6 s) instead of one
    # timeout per address, so the dialog spinner never looks frozen on a network where nothing answers.
    import concurrent.futures as futures

    with futures.ThreadPoolExecutor(max_workers=min(8, len(gateways) * 2)) as pool:
        smb_jobs = {gw: pool.submit(_smb_open, gw) for gw in gateways}
        ftp_jobs = {gw: pool.submit(_probe_ftp, gw, radius) for gw in gateways}
        smb_results = {gw: job.result() for gw, job in smb_jobs.items()}
        ftp_results = {gw: job.result() for gw, job in ftp_jobs.items()}

    for gw in gateways:
        if smb_results.get(gw):
            targets.append(StorageTarget(
                "Wi-Fi Router USB via SMB",
                f"\\\\{gw}\\USB_Storage\\MyOffice_Vault",
                "smb",
                f"SMB port open on {gw} — sign in with the router username/password",
            ))
        ok, detail = ftp_results.get(gw, (False, ""))
        if ok:
            targets.append(StorageTarget(
                "Wi-Fi Router USB via FTP",
                f"ftp://{gw}:21/MyOffice_Vault",
                "ftp",
                detail,
            ))
    return targets


def _probe_ftp(host: str, timeout: float) -> tuple[bool, str]:
    """True when an FTP server answered on `host:21`, plus a human-readable detail line."""
    if not _ftp_open(host, 21, timeout):
        return False, ""
    try:
        ftp = ftplib.FTP(timeout=timeout)
        ftp.connect(host, 21, timeout=timeout)
        ftp.login()
        hint = _guess_share_folder(_normalise_ftp_names(ftp))
        try:
            ftp.quit()
        except Exception:
            ftp.close()
        if hint:
            return True, f"FTP server on {host}:21 — USB folder '{hint}' found"
        return True, f"FTP server answered on {host}:21"
    except Exception as exc:  # login required / listing blocked: still a real candidate
        return True, f"FTP server answered on {host}:21 ({type(exc).__name__}) — enter router credentials"


def _normalise_ftp_names(ftp: ftplib.FTP) -> list[str]:
    try:
        return [os.path.basename(n.rstrip("/")) for n in ftp.nlst()]
    except Exception:
        return []


def _guess_share_folder(names: list[str]) -> str:
    """Routers expose their USB stick under vendor-specific names (USB_Storage, usb1_1, sda1, ...)."""
    preferred = ("MyOffice_Vault", "MyOfficeVault")
    for p in preferred:
        if p in names:
            return p
    for n in names:
        low = n.lower()
        if low.startswith(("usb", "sda", "sdb", "share", "disk", "volume", "udisk")):
            return n
    return ""


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
