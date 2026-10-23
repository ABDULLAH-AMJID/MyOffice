"""Signed OTA Hot-Patch Runtime Loader & Self-Healing Boot Watchdog (`ota_runtime.py`).

Executes at process startup BEFORE importing application modules (`app.shell.*`, `app.writer.*`, etc.):
  1. Verifies crash canary (`boot_in_progress` + `boot_attempts`) and automatically quarantines any
     broken update back to `ota/previous` or clean factory built-in modules.
  2. Registers `OtaHotpatchFinder` at `sys.meta_path[0]` — ahead of PyInstaller's `PyiFrozenImporter` —
     so pure-Python feature/UI upgrades (`~280 KB` signed `.zip`) run inside frozen `MyOffice.exe`
     immediately without recompiling or downloading a `112 MB` installer.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

RELEASE_PUBLIC_KEY_B64 = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEv7m2geEry3tzazfcAhVieGNKMVkg"
    "Q6QQHLfFqgRCaRGTHQxuDm9/MCrTuVZ17qJZeXfuRbIlEorzlqYcjGXB/Q=="
)

_FINDER_INSTALLED: OtaHotpatchFinder | None = None


def ota_base_dir() -> Path:
    override = os.environ.get("MYOFFICE_OTA_DIR", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return root / "MyOffice" / "ota"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MyOffice" / "ota"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "MyOffice" / "ota"


def _state_file(base: Path | None = None) -> Path:
    return (base or ota_base_dir()) / "state.json"


def read_ota_state(base: Path | None = None) -> dict:
    sf = _state_file(base)
    if not sf.exists():
        return {}
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_ota_state(state: dict, base: Path | None = None) -> None:
    target_base = base or ota_base_dir()
    target_base.mkdir(parents=True, exist_ok=True)
    tmp = target_base / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _state_file(target_base))


class OtaHotpatchFinder(importlib.abc.MetaPathFinder):
    """Loads hot-patched `app.*` packages/modules from `patch_root` ahead of `PyiFrozenImporter`."""

    def __init__(self, patch_root: Path, base_app_dir: Path) -> None:
        self.patch_root = patch_root.resolve()
        self.base_app_dir = base_app_dir.resolve()

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname != "app" and not fullname.startswith("app."):
            return None
        parts = fullname.split(".")
        pkg_init = self.patch_root.joinpath(*parts, "__init__.py")
        if pkg_init.is_file():
            search_dirs = [str(self.patch_root.joinpath(*parts))]
            fallback_dir = self.base_app_dir.parent.joinpath(*parts)
            if fallback_dir.is_dir():
                search_dirs.append(str(fallback_dir))
            loader = importlib.machinery.SourceFileLoader(fullname, str(pkg_init))
            return importlib.util.spec_from_file_location(
                fullname, str(pkg_init), loader=loader, submodule_search_locations=search_dirs
            )

        mod_file = self.patch_root.joinpath(*parts[:-1], f"{parts[-1]}.py")
        if mod_file.is_file():
            loader = importlib.machinery.SourceFileLoader(fullname, str(mod_file))
            return importlib.util.spec_from_file_location(fullname, str(mod_file), loader=loader)
        return None


def uninstall_hotpatch_finder() -> None:
    global _FINDER_INSTALLED
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, OtaHotpatchFinder)]
    _FINDER_INSTALLED = None


def rollback_corrupt_patch(base: Path | None = None, reason: str = "boot_watchdog") -> bool:
    """Quarantine failing `ota/current` and restore `ota/previous` (or clean factory build)."""
    target_base = base or ota_base_dir()
    cur = target_base / "current"
    prev = target_base / "previous"
    quarantine = target_base / f"quarantine_{int(time.time())}"
    uninstall_hotpatch_finder()

    moved = False
    if cur.exists():
        shutil.move(str(cur), str(quarantine))
        moved = True
    restored_ver = ""
    if prev.exists():
        shutil.move(str(prev), str(cur))
        prev_meta = cur / "ota_meta.json"
        if prev_meta.exists():
            try:
                restored_ver = json.loads(prev_meta.read_text(encoding="utf-8")).get("version", "")
            except Exception:
                restored_ver = ""
    write_ota_state(
        {
            "active_version": restored_ver,
            "boot_in_progress": False,
            "boot_attempts": 0,
            "healthy": True,
            "last_rollback_reason": reason,
            "last_rollback_at": int(time.time()),
        },
        base=target_base,
    )
    return moved


def bootstrap_ota_runtime(base_app_dir: Path, base: Path | None = None) -> str | None:
    """Activate verified `ota/current` if present; auto-rollback if previous boot crashed repeatedly."""
    global _FINDER_INSTALLED
    if os.environ.get("MYOFFICE_DISABLE_OTA") == "1":
        return None
    target_base = base or ota_base_dir()
    cur = target_base / "current"
    if not (cur / "app").is_dir():
        return None

    st = read_ota_state(target_base)
    attempts = int(st.get("boot_attempts", 0) or 0)
    if st.get("boot_in_progress") and attempts >= 2:
        rollback_corrupt_patch(target_base, reason="repeated_unclean_startup")
        if not (cur / "app").is_dir():
            return None
        st = read_ota_state(target_base)
        attempts = 0

    st["boot_in_progress"] = True
    st["boot_attempts"] = attempts + 1
    write_ota_state(st, target_base)

    uninstall_hotpatch_finder()
    finder = OtaHotpatchFinder(cur, base_app_dir)
    sys.meta_path.insert(0, finder)
    _FINDER_INSTALLED = finder
    return st.get("active_version") or None


def mark_ota_boot_healthy(base: Path | None = None) -> None:
    target_base = base or ota_base_dir()
    st = read_ota_state(target_base)
    if not st:
        return
    st["boot_in_progress"] = False
    st["boot_attempts"] = 0
    st["healthy"] = True
    st["verified_at"] = int(time.time())
    write_ota_state(st, target_base)


def reset_to_factory(base: Path | None = None) -> None:
    target_base = base or ota_base_dir()
    uninstall_hotpatch_finder()
    for sub in ("current", "previous", "staging"):
        p = target_base / sub
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    write_ota_state({"active_version": "", "healthy": True, "boot_in_progress": False, "boot_attempts": 0}, target_base)


def active_patch_version(base: Path | None = None) -> str:
    st = read_ota_state(base)
    cur = (base or ota_base_dir()) / "current"
    if st.get("active_version") and cur.exists():
        return str(st["active_version"])
    return ""
