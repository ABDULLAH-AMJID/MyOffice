"""Desktop / Start-menu shortcuts (ksomisc ``-createlink`` equivalent)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.services.paths import executable_command

APPS = {
    "main": ("MyOffice", ""),
    "writer": ("MyOffice Writer", "--new writer"),
    "sheet": ("MyOffice Spreadsheets", "--new sheet"),
    "slide": ("MyOffice Presentation", "--new slide"),
    "pdf": ("MyOffice PDF", "--new pdf"),
}
ICON_INDEX = {"main": 0, "writer": 1, "sheet": 2, "slide": 3, "pdf": 4}


def desktop_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def start_menu_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "MyOffice"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "applications"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _create_lnk(path: Path, target: str, args: str, icon: str, icon_index: int, workdir: str) -> None:  # pragma: no cover
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(path))});"
              f"$s.TargetPath={_ps_quote(target)};$s.Arguments={_ps_quote(args)};"
              f"$s.WorkingDirectory={_ps_quote(workdir)};$s.IconLocation={_ps_quote(f'{icon},{icon_index}')};"
              f"$s.Description='MyOffice';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                   check=True, capture_output=True, timeout=60, creationflags=0x08000000)


def create_shortcuts(desktop: bool = True, start_menu: bool = True, apps=("main", "writer", "sheet", "slide", "pdf")) -> list[str]:
    created: list[str] = []
    cmd = executable_command()
    targets = []
    if desktop:
        targets.append(desktop_dir())
    if start_menu:
        targets.append(start_menu_dir())
    for folder in targets:
        folder.mkdir(parents=True, exist_ok=True)
        for app in apps:
            name, args = APPS[app]
            if sys.platform == "win32":  # pragma: no cover
                target = cmd[0]
                full_args = " ".join([f'"{c}"' for c in cmd[1:]] + ([args] if args else []))
                lnk = folder / f"{name}.lnk"
                _create_lnk(lnk, target, full_args, cmd[0], ICON_INDEX[app], str(Path(cmd[0]).parent))
                created.append(str(lnk))
            else:
                from app.services.register import desktop_entry

                entry = desktop_entry(app if app != "main" else "main", [])
                if args:
                    entry = entry.replace(" %F", f" {args}")
                f = folder / f"myoffice-{app}.desktop"
                f.write_text(entry, encoding="utf-8")
                f.chmod(0o755)
                created.append(str(f))
    return created


def existing_shortcuts() -> list[Path]:
    """Shortcuts created by :func:`create_shortcuts` that currently exist."""
    found: list[Path] = []
    for folder in (desktop_dir(), start_menu_dir()):
        for app, (name, _a) in APPS.items():
            for f in (folder / f"{name}.lnk", folder / f"myoffice-{app}.desktop"):
                if f.exists() and f not in found:
                    found.append(f)
    return found


def remove_shortcuts() -> list[str]:
    removed = []
    for f in existing_shortcuts():
        try:
            f.unlink()
            removed.append(str(f))
        except OSError:
            pass
    if sys.platform == "win32":  # the per-user "Start Menu\Programs\MyOffice" folder, once empty
        try:
            start_menu_dir().rmdir()
        except OSError:
            pass
    return removed
