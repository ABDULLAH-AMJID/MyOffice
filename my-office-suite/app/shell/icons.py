"""Icon factory: recolourable SVG line icons (Lucide, ISC licence) + crisp Windows 11 app & file-type icons."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer

from app.services.paths import resource_path

DEFAULT_COLOR = "#3c4043"
DARK_COLOR = "#e8eaed"
_SIZES = (16, 20, 24, 32, 40, 48, 64)
_cache: dict = {}
_svg_text: dict = {}
_theme_color = DEFAULT_COLOR

BRAND = {
    "writer": "#2563eb",
    "sheet": "#16a34a",
    "slide": "#ea580c",
    "pdf": "#dc2626",
    "toolbox": "#7c3aed",
    "home": "#0f766e",
    "app": "#3b5bdb",
    "photos": "#7c3aed",
    "code": "#0891b2",
    "vault": "#d97706",
}

KIND_ICON = {
    "writer": "writer",
    "sheet": "sheet",
    "slide": "slide",
    "pdf": "pdf",
    "toolbox": "repair",
    "home": "home",
    "photos": "photos",
    "code": "code",
    "vault": "vault",
}

# Built-in fallback SVGs for v1.3 glyphs not yet present as external `.svg` files in `resources/icons/`
INLINE_SVGS: dict[str, str] = {
    "qr": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="3" height="3"/><line x1="21" y1="14" x2="21" y2="14.01"/><line x1="18" y1="18" x2="21" y2="21"/></svg>',
    "smartphone": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/></svg>',
    "keyboard": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="6" y1="8" x2="6.01" y2="8"/><line x1="10" y1="8" x2="10.01" y2="8"/><line x1="14" y1="8" x2="14.01" y2="8"/><line x1="18" y1="8" x2="18.01" y2="8"/><line x1="6" y1="12" x2="6.01" y2="12"/><line x1="10" y1="12" x2="10.01" y2="12"/><line x1="14" y1="12" x2="14.01" y2="12"/><line x1="18" y1="12" x2="18.01" y2="12"/><line x1="7" y1="16" x2="17" y2="16"/></svg>',
    "vault": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>',
    "code": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    "stamp": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 22h14"/><path d="M19.27 13.73A2.5 2.5 0 0 0 17.5 13h-11A2.5 2.5 0 0 0 4 15.5V17a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5c0-.66-.26-1.3-.73-1.77Z"/><path d="M14 13V8.5C14 7 15 7 15 5a3 3 0 0 0-6 0c0 2 1 2 1 3.5V13"/></svg>',
    "hash": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/></svg>',
    "flatten": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
    "refresh": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
}


def set_theme_color(color: str) -> None:
    global _theme_color
    _theme_color = color
    _cache.clear()


def _svg(name: str) -> str | None:
    if name not in _svg_text:
        p = resource_path("icons", f"{name}.svg")
        if p.exists():
            _svg_text[name] = p.read_text(encoding="utf-8")
        else:
            _svg_text[name] = INLINE_SVGS.get(name)
    return _svg_text[name]


def render_svg(name: str, size: int, color: str) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    text = _svg(name)
    if text:
        r = QSvgRenderer(QByteArray(text.replace("currentColor", color).encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, size, size))
        p.end()
    return pm


def icon(name: str, color: str | None = None, size: int | None = None) -> QIcon:
    color = color or _theme_color
    key = ("i", name, color)
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    if _svg(name):
        for s in _SIZES:
            ic.addPixmap(render_svg(name, s, color), QIcon.Normal)
            ic.addPixmap(render_svg(name, s, "#a0a4a8"), QIcon.Disabled)
    _cache[key] = ic
    return ic


def badge_pixmap(kind: str, size: int = 64, glyph: str | None = None) -> QPixmap:
    """Rounded-square app/document badge with vivid gradient + crisp white vector glyph."""
    base = QColor(BRAND.get(kind, BRAND["app"]))
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    rect = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.22, size * 0.22)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, base.lighter(114))
    grad.setColorAt(1.0, base.darker(118))
    p.fillPath(path, grad)
    p.setPen(QPen(QColor(255, 255, 255, 52), max(1.0, size * 0.02)))
    p.drawPath(path)

    if glyph:
        f = QFont("DejaVu Sans")
        f.setBold(True)
        f.setPixelSize(max(8, int(size * (0.52 if len(glyph) == 1 else 0.28))))
        p.setFont(f)
        p.setPen(Qt.white)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph)
    else:
        inner = render_svg(KIND_ICON.get(kind, kind), max(10, int(size * 0.58)), "#ffffff")
        off = int(size * 0.21)
        p.drawPixmap(off, off, inner)
    p.end()
    return QPixmap.fromImage(img)


def badge(kind: str, glyph: str | None = None) -> QIcon:
    key = ("b", kind, glyph)
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for s in _SIZES:
        ic.addPixmap(badge_pixmap(kind, s, glyph))
    _cache[key] = ic
    return ic


def app_icon() -> QIcon:
    p = resource_path("icons", "app", "myoffice.png")
    if p.exists():
        return QIcon(str(p))
    return badge("app", "M")
