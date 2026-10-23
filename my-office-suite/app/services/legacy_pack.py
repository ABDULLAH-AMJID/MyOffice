"""Legacy Formats Pack: LibreOffice, downloaded and installed only when it is really needed.

MyOffice opens and converts Word, Excel and PowerPoint files with its own engines.  The old binary
and OpenDocument formats (.doc .xls .ppt .odt .ods .odp .rtf .wps ...) need LibreOffice, a 375 MB
program - far too big to put into MyOffice Setup.  So it is offered at the moment a user opens such
a file (or from MyOffice menu > Tools > Legacy Formats Pack):

1. ``download``  - official LibreOffice MSI, resumable (HTTP Range), mirror fallback, SHA-256 check;
                   the partial file is kept, so a cancelled download continues where it stopped.
2. ``install``   - ``msiexec /i ... /qn`` started with the "runas" verb: Windows shows its own UAC
                   prompt for "Windows Installer" (Microsoft-signed) and the installation runs silently.
3. ``record_installed`` - a per-user marker, so MyOffice's uninstaller offers to remove LibreOffice too.

The pinned version, addresses and SHA-256 are the same as in installer/setup.iss (a test keeps them in
sync), where the pack can also be ticked during setup.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.version import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)

LO_VERSION = "26.8.1"
LO_BUILD = "26.8.1.1"
LO_URLS = (
    f"https://download.documentfoundation.org/libreoffice/stable/{LO_VERSION}/win/x86_64/"
    f"LibreOffice_{LO_VERSION}_Win_x86-64.msi",
    f"https://downloadarchive.documentfoundation.org/libreoffice/old/{LO_BUILD}/win/x86_64/"
    f"LibreOffice_{LO_BUILD}_Win_x86-64.msi",
)
LO_SHA256 = "c6298b10cfa1748bcbedad94c62ce771e240e5021bfd6d7a967cd571b04b5312"
LO_SIZE = 374_870_016
MSI_NAME = f"LibreOffice_{LO_VERSION}_Win_x86-64.msi"
# the same properties MyOffice Setup uses: no desktop icon, no update checks, no MS Office takeover
MSI_PROPERTIES = ("ALLUSERS=1 CREATEDESKTOPLINK=0 REGISTER_NO_MSO_TYPES=1 ISCHECKFORPRODUCTUPDATES=0 "
                  "QUICKSTART=0")
OK_EXIT_CODES = {0: False, 3010: True, 1641: True}  # exit code -> restart required
ERROR_CANCELLED = 1223  # the user said "No" in the UAC prompt
USER_MARKER_KEY = r"Software\MyOffice\InstalledByMyOffice"        # HKCU, written by the app
SETUP_MARKER_KEY = r"Software\MyOffice\InstalledByMyOfficeSetup"  # HKLM, written by MyOffice Setup
CHUNK = 256 * 1024
# LibreOffice 26.8 takes ~1.4 GB in Program Files; Windows Installer needs some working space on top
LO_DISK_NEEDED = 1_700_000_000
USER_AGENT = f"{APP_NAME}/{APP_VERSION} (Legacy Formats Pack)"


class PackError(RuntimeError):
    pass


class DownloadCancelled(PackError):
    pass


class InstallDeclined(PackError):
    """The UAC prompt was answered with No."""


def download_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "MyOffice"
    else:
        from app.services.paths import user_dir

        base = user_dir()
    d = base / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path, progress=None, size: int = 0) -> str:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
            done += len(block)
            if progress and size:
                progress(min(99, int(100 * done / size)), "")
    return h.hexdigest()


def _fetch(url: str, part: Path, size: int, progress, is_cancelled, timeout: float) -> None:
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have == size:
        return
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if have and resp.status != 206:  # the server ignored the range: start again
            have = 0
        done = have
        start, last = time.monotonic(), 0.0
        with open(part, "ab" if have else "wb") as fh:
            while True:
                if is_cancelled and is_cancelled():
                    raise DownloadCancelled("download cancelled")
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if progress and (now - last > 0.25 or done >= size):
                    last = now
                    speed = (done - have) / max(0.001, now - start)
                    progress(min(99, int(100 * done / size)),
                             f"LibreOffice {LO_VERSION}:  {done / 1e6:,.0f} / {size / 1e6:,.0f} MB  \u2022  "
                             f"{speed / 1e6:.1f} MB/s")
    if done != size:
        raise PackError(f"incomplete download ({done:,} of {size:,} bytes)")


def pending_download_bytes() -> int:
    """What the download still has to fetch (0 when the MSI is complete, less after an interrupted download)."""
    dest = download_dir() / MSI_NAME
    if dest.is_file() and dest.stat().st_size == LO_SIZE:
        return 0
    part = dest.with_name(dest.name + ".part")
    return max(0, LO_SIZE - (part.stat().st_size if part.is_file() else 0))


def disk_space_shortfall(extra: int = 0):
    """``(needed, free, drive)`` when the Program Files drive has no room for LibreOffice (plus ``extra`` bytes, for
    example the rest of the download), else None.  Checked before anything starts, so that a full disk gives a clear
    message instead of Windows Installer error 1603."""
    target = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or \
        (os.environ.get("SystemDrive", "C:") + "\\")
    try:
        free = shutil.disk_usage(target).free
    except OSError:  # cannot be measured: let Windows Installer decide
        return None
    needed = LO_DISK_NEEDED + max(0, extra)
    if free >= needed:
        return None
    return needed, free, os.path.splitdrive(target)[0] or target


def download(progress=None, is_cancelled=None, *, urls=LO_URLS, sha256: str = LO_SHA256, size: int = LO_SIZE,
             dest_dir: Path | None = None, name: str = MSI_NAME, timeout: float = 30.0) -> Path:
    """Download the LibreOffice MSI (resuming a previous partial download) and verify it.
    Returns the path of the verified file."""
    dest = (dest_dir or download_dir()) / name
    part = dest.with_name(dest.name + ".part")
    if dest.is_file() and dest.stat().st_size == size and _sha256(dest) == sha256:
        return dest
    errors = []
    for url in urls:
        try:
            _fetch(url, part, size, progress, is_cancelled, timeout)
            if progress:
                from app.services.i18n import _t

                progress(99, _t("Verifying the download\u2026"))
            digest = _sha256(part)
            if digest != sha256:
                part.unlink(missing_ok=True)
                raise PackError(f"checksum mismatch (got {digest[:16]}\u2026)")
            os.replace(part, dest)
            if progress:
                progress(100, "")
            log.info("Legacy Formats Pack downloaded from %s", url)
            return dest
        except DownloadCancelled:
            raise
        except (OSError, PackError, urllib.error.URLError) as exc:
            log.warning("Legacy Formats Pack download from %s failed: %s", url, exc)
            errors.append(f"{url.split('/')[2]}: {exc}")
    raise PackError("The download failed:\n" + "\n".join(errors))


def verify_installer(path, progress=None) -> Path:
    """A LibreOffice MSI the user already has (USB stick, network share): accepted only if it is exactly the
    pinned release - same size and SHA-256 as the official download.  Nothing is downloaded."""
    path = Path(path)
    if not path.is_file():
        raise PackError(f"File not found: {path}")
    if path.stat().st_size != LO_SIZE or _sha256(path, progress, LO_SIZE) != LO_SHA256:
        raise PackError(f"{path.name} is not the LibreOffice {LO_VERSION} installer (Windows x64) that MyOffice "
                        f"expects, or the file is damaged.")
    if progress:
        progress(100, "")
    return path


def install_log_path(msi: Path) -> Path:
    """Windows Installer log - in MyOffice's own folder (the MSI may lie on read-only media)."""
    return download_dir() / (Path(msi).stem + ".install.log")


def msiexec_arguments(msi: Path, log_file: Path) -> str:
    return f'/i "{msi}" /qn /norestart {MSI_PROPERTIES} /L*v "{log_file}"'


def install(msi: Path, hwnd: int = 0, poll=None) -> bool:  # pragma: no cover - Windows only
    """Install the MSI with administrator rights (Windows asks via its UAC prompt) and wait.
    Returns True if Windows must be restarted to finish.  ``poll()`` is called every half second."""
    if sys.platform != "win32":
        raise PackError("The Legacy Formats Pack installer is for Windows; install LibreOffice with your "
                        "system's package manager instead.")
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC = 0x40, 0x100
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    log_file = install_log_path(msi)
    sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.hwnd = hwnd or None
    info.lpVerb = "runas"
    info.lpFile = os.path.join(sysdir, "msiexec.exe")
    info.lpParameters = msiexec_arguments(msi, log_file)
    info.lpDirectory = str(msi.parent)
    info.nShow = 0  # SW_HIDE
    # ShellExecuteEx may hand the request to COM-based shell extensions, so COM must be initialised on this
    # (worker) thread first - Microsoft's documented requirement.
    ole32 = ctypes.WinDLL("ole32")
    hr = ole32.CoInitializeEx(None, 0x2 | 0x4)  # COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE
    try:
        launched = shell32.ShellExecuteExW(ctypes.byref(info))
        err = ctypes.get_last_error()
    finally:
        if hr in (0, 1):  # S_OK / S_FALSE: balance the successful CoInitializeEx
            ole32.CoUninitialize()
    if not launched:
        if err == ERROR_CANCELLED:
            raise InstallDeclined("Windows did not get permission to install LibreOffice.")
        raise PackError(f"Could not start Windows Installer (error {err}).")
    if not info.hProcess:
        raise PackError("Windows Installer did not start.")
    try:
        while kernel32.WaitForSingleObject(info.hProcess, 500) == 0x102:  # WAIT_TIMEOUT
            if poll:
                poll()
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(info.hProcess)
    log.info("LibreOffice installation finished with exit code %s (log: %s)", code.value, log_file)
    if code.value in OK_EXIT_CODES:
        return OK_EXIT_CODES[code.value]
    if code.value == 1618:
        raise PackError("Another installation is in progress. Try again when it has finished.")
    if code.value == 1602:
        raise DownloadCancelled("installation cancelled")
    raise PackError(f"Windows Installer reported error {code.value}. Details: {log_file}")


def remove_download(msi: Path) -> None:
    """After a successful installation the MSI (375 MB) and its installation log are not needed any more.
    Only MyOffice's own download is deleted - never an installer file the user chose."""
    msi = Path(msi)
    own = msi.parent.resolve() == download_dir().resolve()
    for p in ((msi, msi.with_name(msi.name + ".part")) if own else ()) + (install_log_path(msi),):
        try:
            p.unlink()
        except OSError:
            pass


def record_installed() -> None:
    """Remember that MyOffice installed LibreOffice, so its uninstaller can offer to remove it."""
    if sys.platform != "win32":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, USER_MARKER_KEY) as key:
            winreg.SetValueEx(key, "libreoffice", 0, winreg.REG_SZ, LO_VERSION)
    except OSError as exc:  # pragma: no cover - registry failure
        log.warning("Could not record the Legacy Formats Pack installation: %s", exc)


def installed_by_myoffice() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    for root, key, flags in ((winreg.HKEY_CURRENT_USER, USER_MARKER_KEY, 0),
                             (winreg.HKEY_LOCAL_MACHINE, SETUP_MARKER_KEY, winreg.KEY_WOW64_64KEY)):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | flags) as k:
                winreg.QueryValueEx(k, "libreoffice")
                return True
        except OSError:
            continue
    return False


def status() -> dict:
    from app.services.office_convert import find_soffice

    path = find_soffice()
    return {"installed": path is not None, "path": path or "", "by_myoffice": installed_by_myoffice(),
            "version": LO_VERSION, "size_mb": round(LO_SIZE / 1e6)}


__all__ = ["download", "install", "verify_installer", "disk_space_shortfall", "record_installed", "installed_by_myoffice", "status", "remove_download",
           "PackError", "DownloadCancelled", "InstallDeclined", "LO_VERSION", "LO_URLS", "LO_SHA256", "LO_SIZE"]
