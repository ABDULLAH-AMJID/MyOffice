"""Pin MyOffice to the Windows taskbar - the way Microsoft allows it.

Windows does not let installers pin programs silently.  Supported approach, in order:

1. **Windows 11 with the January 2026 update or later** (KB5074105, builds 26100.7705 / 26200.7705):
   ``TaskbarManager.RequestPinCurrentAppAsync`` shows Windows' own "Pin MyOffice to the taskbar?" prompt
   and the user decides.  Microsoft's rules are followed: the call is made only after the user clicked
   *Pin to taskbar* inside MyOffice, while MyOffice is in the foreground, never from the installer, and
   the app has a Start-menu entry whose AppUserModelID equals the running process (``APP_ID``).
2. **Windows 10** (no TaskbarManager desktop support): Explorer's *Pin to taskbar* command is invoked on
   MyOffice's Start-menu shortcut.  Best effort - success is verified by looking for the pinned shortcut.
3. Otherwise MyOffice explains the one manual step (right-click the taskbar button -> *Pin to taskbar*).

``PinRequest`` runs these steps asynchronously and reports ``finished(pinned, method)``; *method* is one
of ``"api"``, ``"legacy"``, ``"declined"`` (user said no in the Windows prompt) or ``"manual"``.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path, PurePath

from PySide6.QtCore import QObject, QTimer, Signal

from app.version import APP_NAME

log = logging.getLogger(__name__)

LAF_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModel\LimitedAccessFeatures"
           r"\com.microsoft.windows.taskbar.pin")
LAF_SEED = "4096B239A7295B635C090E647E867B5707DA6AB6CB78340B01FE4E0C8F4953D4"
SETUP_KEY = r"Software\MyOffice"          # HKLM, written by MyOffice Setup
WIN11_FIRST_BUILD = 22000


def is_windows() -> bool:
    return sys.platform == "win32"


def windows_build() -> int:
    if not is_windows():
        return 0
    try:
        return int(sys.getwindowsversion().build)
    except Exception:  # pragma: no cover
        return 0


def pinned_dir() -> Path:
    """Folder where Explorer keeps the shortcuts of pinned taskbar items (per user)."""
    return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Internet Explorer" / "Quick Launch"
            / "User Pinned" / "TaskBar")


def pinned_shortcut(folder: Path | None = None) -> Path | None:
    folder = folder or pinned_dir()
    for name in (f"{APP_NAME}.lnk",):
        p = folder / name
        if p.exists():
            return p
    return None


def start_menu_shortcut() -> Path | None:
    """MyOffice's Start-menu shortcut (created by MyOffice Setup, or by Tools > Create Desktop Shortcuts)."""
    roots = [os.environ.get("ProgramData", r"C:\ProgramData"), os.environ.get("APPDATA", "")]
    for root in roots:
        if not root:
            continue
        programs = Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for p in (programs / f"{APP_NAME}.lnk", programs / APP_NAME / f"{APP_NAME}.lnk"):
            if p.exists():
                return p
    return None


def _read_hklm(name: str):  # pragma: no cover - Windows only
    import winreg

    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SETUP_KEY, 0, winreg.KEY_READ | view) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            continue
    return None


def setup_requested_pin() -> bool:
    """True when the user ticked "Pin MyOffice to the taskbar" in MyOffice Setup."""
    if not is_windows():
        return False
    try:
        return str(_read_hklm("PinToTaskbar") or "") == "1"
    except Exception:
        return False


def laf_token_required() -> bool:  # pragma: no cover - Windows only
    """Builds before KB5074105 lock TaskbarManager behind a Limited Access Feature token (which only
    Microsoft-approved apps have) - the check documented by Microsoft."""
    if not is_windows():
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, LAF_KEY, 0, winreg.KEY_READ) as k:
            value, _kind = winreg.QueryValueEx(k, LAF_SEED)
        return bool(value)
    except OSError:
        return False


def _taskbar_manager():  # pragma: no cover - Windows only
    from winrt.windows.ui.shell import TaskbarManager

    return TaskbarManager.get_default()


def api_manager():
    """The TaskbarManager when the official pin API can be used by MyOffice, else ``None``."""
    if not is_windows() or windows_build() < WIN11_FIRST_BUILD:
        return None
    try:  # pragma: no cover - Windows only
        if laf_token_required():
            return None
        mgr = _taskbar_manager()
        if mgr is not None and mgr.is_supported and mgr.is_pinning_allowed:
            return mgr
    except Exception as exc:  # module missing, class not registered, policy ...
        log.info("Taskbar pin API unavailable: %s", exc)
    return None


def should_offer_pin(cfg_state: str) -> bool:
    """Show the 'Pin to taskbar' banner?  Only on Windows, once, when Setup asked for it."""
    return is_windows() and cfg_state == "auto" and setup_requested_pin() and pinned_shortcut() is None


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def legacy_pin_script(shortcut: PurePath) -> str:
    """PowerShell for Windows 10: run Explorer's own 'Pin to taskbar' command on the shortcut."""
    return (
        "$ErrorActionPreference='Stop';"
        "$h=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\CommandStore"
        "\\shell\\Windows.taskbarpin').ExplorerCommandHandler;"
        "$k='HKCU:\\Software\\Classes\\*\\shell\\{:}';"
        "New-Item -Path $k -Force | Out-Null;"
        "Set-ItemProperty -LiteralPath $k -Name ExplorerCommandHandler -Value $h;"
        "try{"
        f"$f=(New-Object -ComObject Shell.Application).Namespace({_ps_quote(str(shortcut.parent))});"
        f"$f.ParseName({_ps_quote(shortcut.name)}).InvokeVerb('{{:}}')"
        "}finally{Remove-Item -LiteralPath $k -Recurse -Force -ErrorAction SilentlyContinue}"
    )


class PinRequest(QObject):
    """Asynchronous 'pin MyOffice to the taskbar' request (see module docstring)."""

    finished = Signal(bool, str)  # pinned, method

    POLL_MS = 150
    TIMEOUT_MS = 120_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._op = None
        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if not is_windows():
            self.finished.emit(False, "manual")
            return
        if pinned_shortcut() is not None:
            self.finished.emit(True, "already")
            return
        mgr = api_manager()
        if mgr is not None:  # pragma: no cover - Windows 11 only
            try:
                self._op = mgr.request_pin_current_app_async()  # must run on the foreground UI thread
                self._timer.start()
                return
            except Exception as exc:
                log.warning("RequestPinCurrentAppAsync failed: %s", exc)
        if windows_build() < WIN11_FIRST_BUILD:  # pragma: no cover - Windows 10 only
            QTimer.singleShot(0, self._legacy)
            return
        self.finished.emit(False, "manual")

    def _poll(self) -> None:  # pragma: no cover - Windows 11 only
        from winrt.windows.foundation import AsyncStatus

        self._elapsed += self.POLL_MS
        try:
            status = self._op.wait(0)
        except Exception as exc:
            log.warning("Pin request failed: %s", exc)
            status = AsyncStatus.ERROR
        if status == AsyncStatus.STARTED and self._elapsed < self.TIMEOUT_MS:
            return
        self._timer.stop()
        pinned = False
        if status == AsyncStatus.COMPLETED:
            try:
                pinned = bool(self._op.get_results())
            except Exception as exc:
                log.warning("Pin request result unavailable: %s", exc)
        self._op = None
        self.finished.emit(pinned, "api" if pinned else "declined")

    def _legacy(self) -> None:  # pragma: no cover - Windows 10 only
        shortcut = start_menu_shortcut()
        ok = False
        if shortcut is not None:
            try:
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-Command", legacy_pin_script(shortcut)],
                               capture_output=True, timeout=30, creationflags=0x08000000)
            except (OSError, subprocess.SubprocessError) as exc:
                log.info("Legacy taskbar pin failed: %s", exc)
            ok = pinned_shortcut() is not None
        self.finished.emit(ok, "legacy" if ok else "manual")
