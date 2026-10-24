"""Filesystem locations used by the suite.

* ``app_root()``      - installation / source directory (read-only resources)
* ``user_dir()``      - per-user writable directory (config.json, backups, logs...)

The environment variable ``MYOFFICE_HOME`` overrides the user directory, which is
used for portable installs and by the test-suite.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def _ota_resource_root() -> Path | None:
    """Root of the active signed OTA hot-patch, if any (``<ota>/current``)."""
    env = os.environ.get("MYOFFICE_OTA_DIR", "").strip()
    candidates = []
    if env:
        candidates.append(Path(env))
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        candidates.append(root / "MyOffice" / "ota")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "MyOffice" / "ota")
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        candidates.append(Path(xdg) / "MyOffice" / "ota")
    for base in candidates:
        cur = base / "current"
        if (cur / "app").is_dir():
            return cur
    return None


def resource_path(*parts: str) -> Path:
    """Bundled resource (``resources/...``), with a signed OTA hot-patch taking precedence.

    A hot-patch may ship new file-type icons, QSS themes, templates or language data; those land in
    ``<ota>/current/resources`` and must win over the copy frozen inside the installed EXE.
    """
    patch_root = _ota_resource_root()
    if patch_root is not None:
        patched = patch_root.joinpath("resources", *parts)
        if patched.exists():
            return patched
    return app_root().joinpath("resources", *parts)


def install_dir() -> Path | None:
    """Folder of MyOffice.exe in an installed (frozen) copy, else None.

    MyOffice Setup puts the helper programs it carries next to the executable:
    ``tesseract\\`` (OCR engine), ``tessdata\\`` (OCR languages) and ``gs\\`` (Ghostscript)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def bundled_program(*parts: str) -> str | None:
    """Path of a helper program shipped inside the MyOffice folder, e.g. ``("gs", "gswin64c.exe")``."""
    base = install_dir()
    if base is None:
        return None
    p = base.joinpath(*parts)
    return str(p) if p.is_file() else None


def bundled_plugins_dir() -> Path:
    return app_root() / "plugins"


def user_dir() -> Path:
    env = os.environ.get("MYOFFICE_HOME")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "MyOffice"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "MyOffice"
    else:
        p = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "MyOffice"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_subdir(*names: str) -> Path:
    d = user_dir().joinpath(*names)
    d.mkdir(parents=True, exist_ok=True)
    return d


def executable_command() -> list[str]:
    """Command line that launches this application (frozen exe or python + main.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(app_root() / "app" / "main.py")]
