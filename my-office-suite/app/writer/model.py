"""Writer document model helpers: page setup, header/footer, styles and custom text properties."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QTextCharFormat, QTextCursor, QTextFormat

# --------------------------------------------------------------------------- custom properties
P_STYLE = QTextFormat.UserProperty + 2      # block: paragraph style name
P_TOC = QTextFormat.UserProperty + 3        # block: generated table of contents entry
P_BIBLIO = QTextFormat.UserProperty + 4     # block: generated bibliography entry
P_INS = QTextFormat.UserProperty + 10       # char: tracked insertion "author|iso-date"
P_DEL = QTextFormat.UserProperty + 11       # char: tracked deletion  "author|iso-date"
P_COMMENTS = QTextFormat.UserProperty + 20  # char: comment ids "c1,c2"
P_CITE = QTextFormat.UserProperty + 30      # char: citation source keys "key1;key2"
P_FOOTNOTE = QTextFormat.UserProperty + 40   # char: footnote reference - the note text
P_FOOTNOTE_ID = QTextFormat.UserProperty + 41  # char: footnote reference - unique id
P_EQUATION = QTextFormat.UserProperty + 42   # image: equation picture - its LaTeX source

PX_PER_MM = 96.0 / 25.4
PX_PER_PT = 96.0 / 72.0

PAGE_SIZES = {
    "A3": (297.0, 420.0), "A4": (210.0, 297.0), "A5": (148.0, 210.0), "B5": (176.0, 250.0),
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Executive": (184.2, 266.7),
}


def mm_to_px(mm: float) -> float:
    return mm * PX_PER_MM


def px_to_mm(px: float) -> float:
    return px / PX_PER_MM


@dataclass
class PageSetup:
    size: str = "A4"
    width_mm: float = 210.0
    height_mm: float = 297.0
    orientation: str = "portrait"
    margins_mm: list = field(default_factory=lambda: [25.4, 25.4, 25.4, 25.4])  # top, right, bottom, left
    header_mm: float = 12.7
    footer_mm: float = 12.7

    def page_mm(self) -> tuple[float, float]:
        w, h = self.width_mm, self.height_mm
        if self.orientation == "landscape" and w < h:
            w, h = h, w
        if self.orientation == "portrait" and w > h:
            w, h = h, w
        return w, h

    def page_px(self) -> tuple[float, float]:
        w, h = self.page_mm()
        return mm_to_px(w), mm_to_px(h)

    def margins_px(self) -> tuple[float, float, float, float]:
        t, r, b, l = self.margins_mm
        return mm_to_px(t), mm_to_px(r), mm_to_px(b), mm_to_px(l)

    def set_size(self, name: str) -> None:
        if name in PAGE_SIZES:
            self.size = name
            self.width_mm, self.height_mm = PAGE_SIZES[name]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PageSetup":
        p = cls()
        for k, v in (d or {}).items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p

    @staticmethod
    def detect_size(w_mm: float, h_mm: float) -> str:
        a, b = sorted((w_mm, h_mm))
        for name, (x, y) in PAGE_SIZES.items():
            if abs(a - x) < 2 and abs(b - y) < 2:
                return name
        return "Custom"


@dataclass
class HeaderFooter:
    header: str = ""
    footer: str = ""
    header_align: str = "center"
    footer_align: str = "center"

    def render(self, text: str, page: int, pages: int, title: str = "") -> str:
        return (text.replace("{PAGE}", str(page)).replace("{NUMPAGES}", str(pages))
                .replace("{DATE}", datetime.now().strftime("%Y-%m-%d")).replace("{TITLE}", title))


@dataclass
class Comment:
    id: str
    author: str
    text: str
    date: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    initials: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- styles
STYLE_NAMES = ["Normal", "Title", "Subtitle", "Heading 1", "Heading 2", "Heading 3", "Heading 4",
               "Quote", "Code", "List Paragraph"]

_STYLE_DEFS = {
    "Normal": dict(size=None, bold=False, italic=False, color=None, family=None, before=0, after=8, heading=0),
    "Title": dict(size=28, bold=False, italic=False, color="#1f3864", family="Calibri Light", before=0, after=10, heading=0),
    "Subtitle": dict(size=15, bold=False, italic=False, color="#5a5a5a", family=None, before=0, after=10, heading=0),
    "Heading 1": dict(size=16, bold=True, italic=False, color="#2f5496", family="Calibri Light", before=12, after=4, heading=1),
    "Heading 2": dict(size=13, bold=True, italic=False, color="#2f5496", family="Calibri Light", before=8, after=4, heading=2),
    "Heading 3": dict(size=12, bold=True, italic=False, color="#1f3763", family="Calibri Light", before=6, after=3, heading=3),
    "Heading 4": dict(size=11, bold=True, italic=True, color="#2f5496", family=None, before=6, after=3, heading=4),
    "Quote": dict(size=None, bold=False, italic=True, color="#404040", family=None, before=8, after=8, heading=0, indent=20),
    "Code": dict(size=10, bold=False, italic=False, color="#24292f", family="Consolas", before=0, after=0,
                 heading=0, background="#f3f4f6"),
    "List Paragraph": dict(size=None, bold=False, italic=False, color=None, family=None, before=0, after=4, heading=0, indent=18),
}


def style_def(name: str) -> dict:
    return _STYLE_DEFS.get(name, _STYLE_DEFS["Normal"])


def style_for_heading(level: int) -> str:
    return f"Heading {level}" if 1 <= level <= 4 else ("Heading 4" if level > 4 else "Normal")


def _style_families() -> set[str]:
    return {v["family"] for v in _STYLE_DEFS.values() if v.get("family")}


def apply_style(cursor: QTextCursor, name: str, base_size: float = 11.0) -> None:
    """Apply a paragraph style to all blocks touched by ``cursor``.

    Fonts introduced by another style (e.g. *Calibri Light* of headings) are removed when the
    new style has no font of its own; fonts chosen by the user (e.g. an Urdu font) are kept.
    With no selection, the cursor's typing format is updated too, so new text uses the style.
    """
    d = style_def(name)
    style_fams = _style_families()
    doc = cursor.document()
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    fam_prop = QTextFormat.FontFamilies.value
    c = QTextCursor(doc)
    c.beginEditBlock()
    cf = QTextCharFormat()
    cf.setFontPointSize(float(d["size"] or base_size))
    cf.setFontWeight(QFont.Bold if d["bold"] else QFont.Normal)
    cf.setFontItalic(bool(d["italic"]))
    cf.setForeground(QBrush(QColor(d["color"] or "#000000")))
    if d.get("family"):
        cf.setFontFamilies([d["family"]])
    block = doc.findBlock(start)
    while block.isValid() and block.position() <= end:
        bf = block.blockFormat()
        bf.setProperty(P_STYLE, name)
        bf.setHeadingLevel(int(d.get("heading", 0)))
        bf.setTopMargin(d["before"] * PX_PER_PT)
        bf.setBottomMargin(d["after"] * PX_PER_PT)
        bf.setLeftMargin(d.get("indent", 0) * PX_PER_PT)
        if d.get("background"):
            bf.setBackground(QColor(d["background"]))
        else:
            bf.clearBackground()
        c.setPosition(block.position())
        c.setBlockFormat(bf)
        if not d.get("family"):
            stale = []
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                it += 1
                fams = frag.charFormat().fontFamilies()
                if frag.isValid() and fams and fams[0] in style_fams:
                    stale.append((frag.position(), frag.length(), QTextCharFormat(frag.charFormat())))
            for pos, length, f in stale:
                f.clearProperty(fam_prop)
                c.setPosition(pos)
                c.setPosition(pos + length, QTextCursor.KeepAnchor)
                c.setCharFormat(f)
            bcf = block.charFormat()
            if (bcf.fontFamilies() or [None])[0] in style_fams:
                bcf.clearProperty(fam_prop)
                QTextCursor(block).setBlockCharFormat(bcf)
        if block.length() > 1:
            c.setPosition(block.position())
            c.setPosition(block.position() + block.length() - 1, QTextCursor.KeepAnchor)
            c.mergeCharFormat(cf)
        QTextCursor(block).mergeBlockCharFormat(cf)
        block = block.next()
    c.endEditBlock()
    if not cursor.hasSelection():
        tf = QTextCharFormat(cursor.charFormat())
        if not d.get("family") and (tf.fontFamilies() or [None])[0] in style_fams:
            tf.clearProperty(fam_prop)
        tf.merge(cf)
        cursor.setCharFormat(tf)


# --------------------------------------------------------------------------- text helpers
_RTL_RE = re.compile("[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")
_ARABIC_RE = re.compile("[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_DEVANAGARI_RE = re.compile("[\u0900-\u097F\uA8E0-\uA8FF]")


def has_rtl(text: str) -> bool:
    return bool(_RTL_RE.search(text))


def has_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text))


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI_RE.search(text))


def first_strong_rtl(text: str) -> bool | None:
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL"):
            return True
        if bidi == "L":
            return False
    return None


def count_words(text: str) -> int:
    return len(text.split())


def revision_stamp(author: str) -> str:
    return f"{author}|{datetime.now().isoformat(timespec='seconds')}"


def parse_stamp(value) -> tuple[str, str]:
    s = str(value or "")
    if "|" in s:
        a, d = s.split("|", 1)
        return a, d
    return s, ""


def color_hex(brush_or_color) -> str | None:
    if isinstance(brush_or_color, QBrush):
        if brush_or_color.style() == Qt.NoBrush:
            return None
        c = brush_or_color.color()
    else:
        c = brush_or_color
    if not c.isValid():
        return None
    return "%02X%02X%02X" % (c.red(), c.green(), c.blue())
