"""Font Manager service (fontTools + Qt font database).

* registers the bundled fonts (Noto Nastaliq Urdu, Noto Sans Devanagari) and user-installed fonts
* installs substitutions so documents written with common Windows/Urdu fonts render even
  when those fonts are missing (e.g. *Jameel Noori Nastaleeq* \u2192 *Noto Nastaliq Urdu*)
* inspects font files with fontTools (names, version, licence, scripts, variable axes)
* installs fonts per-user (no admin rights), on Windows also into the per-user Fonts folder
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from app.services.paths import resource_path, user_subdir

log = logging.getLogger(__name__)

FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")

FONT_SUBSTITUTIONS: dict[str, list[str]] = {
    # Urdu / Arabic script
    "Jameel Noori Nastaleeq": ["Noto Nastaliq Urdu"],
    "Jameel Noori Nastaleeq Kasheeda": ["Noto Nastaliq Urdu"],
    "Alvi Nastaleeq": ["Noto Nastaliq Urdu"],
    "Nafees Nastaleeq": ["Noto Nastaliq Urdu"],
    "Nafees Web Naskh": ["Noto Naskh Arabic", "Noto Nastaliq Urdu"],
    "Mehr Nastaliq Web": ["Noto Nastaliq Urdu"],
    "Urdu Typesetting": ["Noto Nastaliq Urdu"],
    "Pak Nastaleeq": ["Noto Nastaliq Urdu"],
    # Devanagari
    "Mangal": ["Noto Sans Devanagari"],
    "Kokila": ["Noto Sans Devanagari"],
    "Aparajita": ["Noto Sans Devanagari"],
    "Utsaah": ["Noto Sans Devanagari"],
    "Nirmala UI": ["Noto Sans Devanagari", "Segoe UI"],
    # Latin office fonts (metric-compatible open fonts first)
    "Calibri": ["Carlito", "Liberation Sans", "DejaVu Sans", "Arial"],
    "Calibri Light": ["Carlito", "Calibri", "Liberation Sans", "DejaVu Sans"],
    "Cambria": ["Caladea", "Liberation Serif", "DejaVu Serif", "Times New Roman"],
    "Times New Roman": ["Liberation Serif", "Tinos", "DejaVu Serif"],
    "Arial": ["Liberation Sans", "Arimo", "DejaVu Sans"],
    "Courier New": ["Liberation Mono", "Cousine", "DejaVu Sans Mono"],
    "Consolas": ["Cascadia Mono", "Liberation Mono", "DejaVu Sans Mono", "Courier New"],
    "Segoe UI": ["Noto Sans", "DejaVu Sans", "Arial"],
}

_loaded: dict[str, list[str]] = {}


def user_fonts_dir() -> Path:
    return user_subdir("fonts")


def _font_files(folder: Path):
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() in FONT_EXTS:
                yield p


def load_bundled_fonts() -> list[str]:
    """Register bundled + user fonts with Qt.  Returns the list of families made available."""
    from PySide6.QtGui import QFontDatabase

    families: list[str] = []
    for folder in (resource_path("fonts"), user_fonts_dir()):
        for f in _font_files(folder):
            if str(f) in _loaded:
                families.extend(_loaded[str(f)])
                continue
            fid = QFontDatabase.addApplicationFont(str(f))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            _loaded[str(f)] = list(fams)
            families.extend(fams)
            if fid < 0:
                log.warning("Could not load font %s", f)
    return families


def install_substitutions() -> None:
    from PySide6.QtGui import QFont

    for family, subs in FONT_SUBSTITUTIONS.items():
        QFont.insertSubstitutions(family, subs)


def font_info(path: str) -> dict:
    """Metadata of a font file via fontTools."""
    from fontTools.ttLib import TTCollection, TTFont

    info: dict = {"path": path, "faces": []}
    if path.lower().endswith((".ttc", ".otc")):
        fonts = list(TTCollection(path).fonts)
    else:
        fonts = [TTFont(path, lazy=True)]
    for f in fonts:
        name = f["name"]

        def n(i):
            rec = name.getName(i, 3, 1, 0x409) or name.getName(i, 1, 0, 0) or name.getDebugName(i)
            return str(rec) if rec is not None else ""

        cmap = f.getBestCmap() or {}
        scripts = []
        for label, lo, hi in (("Latin", 0x41, 0x7A), ("Arabic/Urdu", 0x0600, 0x06FF), ("Devanagari", 0x0900, 0x097F),
                              ("Cyrillic", 0x0400, 0x04FF), ("Greek", 0x0370, 0x03FF), ("CJK", 0x4E00, 0x9FFF)):
            if any(lo <= cp <= hi for cp in cmap):
                scripts.append(label)
        face = {
            "family": n(16) or n(1), "style": n(17) or n(2), "full_name": n(4), "version": n(5),
            "designer": n(9), "license": n(13), "license_url": n(14), "glyphs": f["maxp"].numGlyphs,
            "scripts": scripts, "variable": "fvar" in f,
            "axes": [a.axisTag for a in f["fvar"].axes] if "fvar" in f else [],
            "format": "OpenType (CFF)" if "CFF " in f or "CFF2" in f else "TrueType",
        }
        info["faces"].append(face)
        f.close()
    return info


def validate_font(path: str) -> dict:
    try:
        info = font_info(path)
    except Exception as exc:
        raise ValueError(f"Not a valid font file: {exc}") from exc
    if not info["faces"]:
        raise ValueError("The font file contains no faces")
    return info


def install_user_font(path: str) -> list[str]:
    """Install a font for the current user and register it immediately."""
    from PySide6.QtGui import QFontDatabase

    info = validate_font(path)
    dest = user_fonts_dir() / Path(path).name
    if Path(path).resolve() != dest.resolve():
        shutil.copyfile(path, dest)
    fid = QFontDatabase.addApplicationFont(str(dest))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    _loaded[str(dest)] = list(fams)
    if sys.platform == "win32":
        try:
            _install_windows_user_font(dest, info["faces"][0]["full_name"] or dest.stem)
        except OSError as exc:
            log.warning("Per-user Windows font registration failed: %s", exc)
    return fams or [info["faces"][0]["family"]]


def _install_windows_user_font(src: Path, display: str) -> None:  # pragma: no cover - Windows only
    import winreg

    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
    local.mkdir(parents=True, exist_ok=True)
    target = local / src.name
    if not target.exists():
        shutil.copyfile(src, target)
    kind = "TrueType" if src.suffix.lower() in (".ttf", ".ttc") else "OpenType"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
        winreg.SetValueEx(k, f"{display} ({kind})", 0, winreg.REG_SZ, str(target))


def remove_user_font(path: str) -> None:
    from PySide6.QtGui import QFontDatabase

    p = Path(path)
    if p.parent.resolve() != user_fonts_dir().resolve():
        raise PermissionError("Only fonts installed through MyOffice can be removed here.")
    QFontDatabase.removeAllApplicationFonts()
    _loaded.clear()
    p.unlink(missing_ok=True)
    load_bundled_fonts()


def installed_user_fonts() -> list[Path]:
    return list(_font_files(user_fonts_dir()))


def bundled_fonts() -> list[Path]:
    return list(_font_files(resource_path("fonts")))


def families_for_script(script: str) -> list[str]:
    from PySide6.QtGui import QFontDatabase

    ws = {"arabic": QFontDatabase.Arabic, "devanagari": QFontDatabase.Devanagari, "latin": QFontDatabase.Latin,
          "any": QFontDatabase.Any}[script]
    return sorted(QFontDatabase.families(ws))
