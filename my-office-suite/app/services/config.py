"""JSON configuration store (``config.json``) - the equivalent of WPS ksomisc settings.

Values are addressed with dotted keys, e.g. ``cfg().get("backup.keep")``.
Writes are atomic (temp file + ``os.replace``) so a crash never corrupts the file.
"""
from __future__ import annotations

import copy
import getpass
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from app.services.paths import user_dir

log = logging.getLogger(__name__)


def _default_author() -> str:
    try:
        return getpass.getuser() or "User"
    except Exception:  # pragma: no cover - platform specific
        return "User"


DEFAULTS: dict[str, Any] = {
    "lang": "en_US",
    "theme": "light",
    "author": _default_author(),
    "recent_files": [],
    "max_recent": 15,
    "autosave_minutes": 3,
    "single_instance": True,
    "backup": {"enabled": True, "keep": 10, "dir": ""},
    "update": {"url": "https://github.com/ABDULLAH-AMJID/MyOffice", "check_on_startup": True},
    "templates": {"catalog_url": ""},
    "servers": {"sync": "", "update": "", "templates": "", "collab": ""},
    "sync": {
        "enabled": False,
        "backend": "folder",
        "local_dir": "",
        "folder_target": "",
        "interval_minutes": 10,
        "delete_remote": False,
        "s3": {"endpoint": "", "bucket": "", "prefix": "MyOffice/", "region": "",
               "access_key": "", "secret_key": ""},
    },
    "plugins": {"disabled": []},
    "collab": {"server_url": "ws://127.0.0.1:1234", "port": 1234, "username": ""},
    "firewall": {"consent": None},
    "writer": {
        "font_family": "Calibri",
        "font_size": 11,
        "urdu_font": "Noto Nastaliq Urdu",
        "hindi_font": "Noto Sans Devanagari",
        "page_size": "A4",
        "orientation": "portrait",
        "margins_mm": [25.4, 25.4, 25.4, 25.4],
        "track_changes_default": False,
    },
    "sheet": {"default_col_width": 90, "default_row_height": 24},
    "ui": {"font_family": "", "font_size": 0, "window_geometry": "", "show_home_on_start": True},
    "ocr": {"tesseract_cmd": "", "languages": "urd+eng"},
    "macros": {"trusted_dirs": []},
    # "auto" = offer the 'Pin to taskbar' tip once (if MyOffice Setup asked for it); "done" / "dismissed"
    "taskbar": {"pin_prompt": "auto"},
}

SUPPORTED_LANGS = ("en_US", "ur_PK", "hi_IN")


def installer_language() -> str | None:
    """UI language chosen in MyOffice Setup (``HKLM\\Software\\MyOffice\\Language``), Windows only."""
    import sys

    if sys.platform != "win32":
        return None
    try:  # pragma: no cover - Windows only
        import winreg

        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\MyOffice", 0,
                                    winreg.KEY_READ | view) as k:
                    value = str(winreg.QueryValueEx(k, "Language")[0])
                return value if value in SUPPORTED_LANGS else None
            except OSError:
                continue
    except Exception:
        pass
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else user_dir() / "config.json"
        self._lock = threading.RLock()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    # -- persistence ---------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
                    if not isinstance(raw, dict):
                        raise ValueError("config root must be an object")
                    self.data = _deep_merge(DEFAULTS, raw)
                except Exception as exc:  # corrupt config: keep a copy, fall back to defaults
                    log.error("Invalid config.json (%s); using defaults", exc)
                    try:
                        self.path.replace(self.path.with_suffix(".json.corrupt"))
                    except OSError:
                        pass
                    self.data = copy.deepcopy(DEFAULTS)
            else:  # first run for this user: start in the language picked during setup (if any)
                self.data = copy.deepcopy(DEFAULTS)
                lang = installer_language()
                if lang:
                    self.data["lang"] = lang

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # -- access --------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return copy.deepcopy(node)

    def set(self, key: str, value: Any, save: bool = False) -> None:
        with self._lock:
            parts = key.split(".")
            node = self.data
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = value
            if save:
                self.save()

    # -- recent files --------------------------------------------------------------
    def add_recent(self, path: str) -> None:
        path = str(Path(path).resolve())
        with self._lock:
            rec = [p for p in self.data.get("recent_files", []) if p != path]
            rec.insert(0, path)
            self.data["recent_files"] = rec[: int(self.data.get("max_recent", 15))]
            self.save()

    def recent_files(self, existing_only: bool = True) -> list[str]:
        rec = list(self.data.get("recent_files", []))
        return [p for p in rec if not existing_only or Path(p).exists()]


_instance: Config | None = None


class _CfgAccessor:
    """Callable singleton accessor: supports both ``cfg().get(...)`` and ``cfg.get(...)`` seamlessly."""

    def __call__(self) -> Config:
        global _instance
        if _instance is None:
            _instance = Config()
        return _instance

    def get(self, key: str, default: Any = None) -> Any:
        return self().get(key, default)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self().set(key, value, save=save)

    def save(self) -> None:
        self().save()

    def add_recent(self, path: str) -> None:
        self().add_recent(path)

    def recent_files(self, existing_only: bool = True) -> list[str]:
        return self().recent_files(existing_only=existing_only)

    def __getattr__(self, name: str) -> Any:
        return getattr(self(), name)


cfg = _CfgAccessor()


def reset_config_for_tests(path: Path | None = None) -> Config:
    global _instance
    _instance = Config(path)
    return _instance
