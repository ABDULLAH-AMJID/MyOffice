"""Uninstall MyOffice from inside the application (ksomisc ``-uninstall`` equivalent).

Two situations are handled:

* **Installed with MyOffice Setup** (Windows).  The Inno Setup uninstaller that belongs to *this* copy
  (``unins000.exe`` next to ``MyOffice.exe``, registered under ``...\\Uninstall\\{AppId}_is1``) is started
  with ``/FROMAPP``.  It waits until MyOffice has exited and then removes the program folder, the
  settings and data folders of every user profile, file associations, shortcuts, taskbar pins, temporary
  files and the firewall rule - see the ``[Code]`` section of ``installer/setup.iss``.
* **Portable or source copy** (Windows and Linux).  Everything MyOffice created *outside* its own folder
  is removed here: the settings/data folder, file-type registrations, shortcuts, the firewall rule and
  temporary files.  The user is told which program folder to delete afterwards.

Documents the user saved elsewhere are never touched.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.paths import app_root, user_dir
from app.version import INNO_APP_ID

log = logging.getLogger(__name__)

UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INNO_APP_ID}_is1"
# Prefixes of the temporary files/folders MyOffice creates (office_convert, templates, pdf ops).
TEMP_PREFIXES = ("myoffice_conv_", "myoffice_tpl_", "myoffice_pdf_", "myoffice_selftest_", "myoffice_dec_",
                 "myoffice_plain_")
# Everything MyOffice itself writes into its data folder (used when MYOFFICE_HOME points to a folder
# that may also contain other files - then only these entries are deleted).
DATA_ENTRIES = ("config.json", "config.json.tmp", "config.json.corrupt", "addins", "backup", "captures",
                "collab", "downloads", "fonts", "forms", "lo_profile", "logs", "macros", "recovery", "sync", "templates",
                "updates", "instance.lock", "versions", "recordings", "dict", "resume")


def program_dir() -> Path:
    """Folder that holds the program: the folder of MyOffice.exe when frozen, else the source tree."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return app_root()


def parse_command(cmd: str) -> Path | None:
    """Executable part of an ``UninstallString`` such as ``"C:\\Program Files\\MyOffice\\unins000.exe" /x``."""
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        exe = cmd[1:end] if end > 0 else cmd[1:]
    else:
        i = cmd.lower().find(".exe")
        exe = cmd[: i + 4] if i >= 0 else cmd.split(" ")[0]
    exe = exe.strip()
    return Path(exe) if exe else None


def _registry_uninstallers() -> list[Path]:  # pragma: no cover - Windows only
    import winreg

    found: list[Path] = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as k:
                    value, _kind = winreg.QueryValueEx(k, "UninstallString")
            except OSError:
                continue
            p = parse_command(str(value))
            if p is not None and p not in found:
                found.append(p)
    return found


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return False


def find_uninstaller(prog: Path | None = None, candidates: list[Path] | None = None) -> Path | None:
    """The MyOffice Setup uninstaller that belongs to *this* copy of MyOffice, or ``None``.

    A registered uninstaller of another copy (e.g. while running a portable copy or the source tree on a
    machine where MyOffice is also installed) is deliberately ignored."""
    prog = prog or program_dir()
    if candidates is None:
        candidates = _registry_uninstallers() if sys.platform == "win32" else []
        candidates += sorted(prog.glob("unins[0-9][0-9][0-9].exe"))
    for c in candidates:
        try:
            if c.is_file() and _same_dir(c.parent, prog):
                return c
        except OSError:
            continue
    return None


def _custom_data_dir() -> bool:
    return bool(os.environ.get("MYOFFICE_HOME"))


@dataclass
class UninstallPlan:
    mode: str                       # "installer" (MyOffice Setup) or "portable"
    program_dir: Path
    data_dir: Path
    uninstaller: Path | None = None
    registered: bool = False        # file types registered from inside MyOffice (portable mode)
    shortcuts: list[str] = field(default_factory=list)
    temp_files: list[str] = field(default_factory=list)

    @property
    def installed(self) -> bool:
        return self.mode == "installer"


def make_plan() -> UninstallPlan:
    prog = program_dir()
    data = user_dir()
    unins = find_uninstaller(prog)
    plan = UninstallPlan("installer" if unins else "portable", prog, data, unins)
    plan.temp_files = [str(p) for p in temp_entries()]
    if not unins:
        try:
            from app.services import register

            plan.registered = any(register.is_registered(e) for e in register.ASSOCIATIONS)
        except Exception:
            plan.registered = False
        try:
            from app.services import shortcuts

            plan.shortcuts = [str(p) for p in shortcuts.existing_shortcuts()]
        except Exception:
            plan.shortcuts = []
    return plan


UNINSTALLER_ARGS = "/SILENT /FROMAPP"  # no second confirmation (MyOffice already asked), progress shown


def launch_uninstaller(path: Path) -> None:  # pragma: no cover - Windows only
    """Start the Inno Setup uninstaller.  ``ShellExecute`` lets Windows show the UAC prompt it needs.

    ``/FROMAPP`` tells the uninstaller to wait for MyOffice to exit and to confirm the removal at the end."""
    import ctypes

    rc = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), UNINSTALLER_ARGS, str(path.parent), 1)
    if rc <= 32:
        raise OSError(f"Windows could not start the uninstaller (error {rc}).")


# --------------------------------------------------------------------------------------- portable mode
def temp_entries(tmp: Path | None = None) -> list[Path]:
    base = Path(tmp or tempfile.gettempdir())
    out: list[Path] = []
    for prefix in TEMP_PREFIXES:
        out.extend(sorted(base.glob(prefix + "*")))
    return out


def remove_temp_files(tmp: Path | None = None) -> list[str]:
    removed = []
    for p in temp_entries(tmp):
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(str(p))
        except OSError as exc:
            log.warning("Could not remove %s: %s", p, exc)
    return removed


def _close_log_files(folder: Path) -> None:
    """Close log handlers writing into *folder* (Windows cannot delete open files)."""
    try:
        target = folder.resolve()
    except OSError:
        return
    for logger in [logging.getLogger()] + [logging.getLogger(n) for n in list(logging.root.manager.loggerDict)]:
        for h in list(getattr(logger, "handlers", [])):
            fn = getattr(h, "baseFilename", None)
            if not fn:
                continue
            try:
                inside = Path(fn).resolve().is_relative_to(target)
            except (OSError, ValueError):
                inside = False
            if inside:
                try:
                    h.close()
                finally:
                    logger.removeHandler(h)


def remove_data_dir(data: Path) -> list[str]:
    """Delete MyOffice's settings/data folder.

    The default folder (``%APPDATA%\\MyOffice`` or ``~/.config/MyOffice``) belongs to MyOffice and is
    removed completely.  A custom ``MYOFFICE_HOME`` folder may contain other files, so only the entries
    MyOffice creates are deleted there (and the folder itself only if it ends up empty)."""
    _close_log_files(data)
    removed: list[str] = []
    if not data.exists():
        return removed
    if not _custom_data_dir():
        shutil.rmtree(data, ignore_errors=True)
        if not data.exists():
            removed.append(str(data))
        return removed
    for name in DATA_ENTRIES:
        p = data / name
        if not (p.exists() or p.is_symlink()):
            continue
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(str(p))
        except OSError as exc:
            log.warning("Could not remove %s: %s", p, exc)
    try:
        data.rmdir()  # only succeeds when empty
        removed.append(str(data))
    except OSError:
        pass
    return removed


def remove_portable_traces(plan: UninstallPlan) -> list[str]:
    """Remove everything this portable/source copy created outside its own folder.

    Call this *after* the main window has shut down (so nothing writes into the data folder again).
    Returns the list of removed items."""
    removed: list[str] = []
    try:
        from app.services import register

        if plan.registered or sys.platform == "win32":
            register.unregister_associations()
            removed.append("file associations")
    except Exception as exc:
        log.warning("Could not remove file associations: %s", exc)
    try:
        from app.services import shortcuts

        removed += shortcuts.remove_shortcuts()
    except Exception as exc:
        log.warning("Could not remove shortcuts: %s", exc)
    if sys.platform == "win32":  # pragma: no cover - Windows only
        try:
            from app.services import firewall

            if firewall.firewall_rule_present():
                firewall.remove_firewall_rule()
                removed.append("firewall rule")
        except Exception as exc:
            log.warning("Could not remove the firewall rule: %s", exc)
    removed += remove_temp_files()
    removed += remove_data_dir(plan.data_dir)
    if sys.platform == "win32":  # pragma: no cover - Windows only: Legacy Formats Pack downloads
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "MyOffice"
        if os.environ.get("LOCALAPPDATA") and local.is_dir():
            shutil.rmtree(local, ignore_errors=True)
            if not local.exists():
                removed.append(str(local))
    return removed
