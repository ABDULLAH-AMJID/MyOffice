"""Internationalisation (ksomisc ``-setlng`` equivalent).

All user-visible strings go through :func:`_t`, which maps to
``QCoreApplication.translate("MyOffice", text)``.  Compiled Qt translation files
(``resources/langs/<lang>.qm``) are produced by ``tools/build_translations.py``.
Shaping of Urdu (Nastaleeq) and Hindi (Devanagari) text is done by Qt's built-in
HarfBuzz engine; the matching fonts are bundled in ``resources/fonts``.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, Qt, QTranslator

from app.services.paths import resource_path

log = logging.getLogger(__name__)

CONTEXT = "MyOffice"

LANGUAGES: dict[str, dict] = {
    "en_US": {"name": "English", "native": "English", "rtl": False, "qt": "en"},
    "ur_PK": {"name": "Urdu", "native": "اردو", "rtl": True, "qt": "ur"},
    "hi_IN": {"name": "Hindi", "native": "हिन्दी", "rtl": False, "qt": "hi"},
}

_installed: list[QTranslator] = []
_current = "en_US"


def _t(text: str) -> str:
    """Translate ``text`` using the installed translator (falls back to English)."""
    return QCoreApplication.translate(CONTEXT, text)


tr = _t


def current_language() -> str:
    return _current


def is_rtl(lang: str | None = None) -> bool:
    return LANGUAGES.get(lang or _current, {}).get("rtl", False)


def qm_path(lang: str):
    return resource_path("langs", f"{lang}.qm")


def install_translator(app, lang: str) -> bool:
    """Install the translator for ``lang`` on ``app``. Returns True when a catalog was loaded."""
    global _current
    for tr in _installed:
        app.removeTranslator(tr)
    _installed.clear()
    if lang not in LANGUAGES:
        log.warning("Unknown language %s, falling back to en_US", lang)
        lang = "en_US"
    _current = lang
    loaded = False
    if lang != "en_US":
        tr = QTranslator(app)
        if tr.load(str(qm_path(lang))):
            app.installTranslator(tr)
            _installed.append(tr)
            loaded = True
        else:
            log.warning("Translation catalog missing: %s", qm_path(lang))
        # Qt's own strings (standard dialogs) when Qt ships them
        qt_tr = QTranslator(app)
        if qt_tr.load(QLocale(lang), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(qt_tr)
            _installed.append(qt_tr)
    app.setLayoutDirection(Qt.RightToLeft if is_rtl(lang) else Qt.LeftToRight)
    QLocale.setDefault(QLocale(lang))
    return loaded or lang == "en_US"


def set_language(lang: str) -> None:
    """Persist the UI language in config.json (like ``ksomisc -setlng``)."""
    from app.services.config import cfg

    if lang not in LANGUAGES:
        raise ValueError(f"Unsupported language '{lang}'. Choose one of: {', '.join(LANGUAGES)}")
    cfg().set("lang", lang, save=True)
