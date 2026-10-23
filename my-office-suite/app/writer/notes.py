"""Footnotes and Word document protection.

Footnotes
    A footnote reference is a superscript number in the text whose character format carries the note text
    (``P_FOOTNOTE``) and a unique id (``P_FOOTNOTE_ID``, so neighbouring references never merge).  Notes are drawn at
    the bottom of the page that holds their reference - in the editor, the PDF export and when printing - in an area
    reserved above the bottom margin.  In DOCX they are real Word footnotes (``word/footnotes.xml``).

Document protection
    "Restrict editing" is saved as ``w:documentProtection`` with the password hashed exactly as Word does it
    (ECMA-376 Part 4 legacy password key, then SHA-512 with salt and 100,000 iterations), so Word, LibreOffice and
    MyOffice all ask for the same password to stop protection.
"""
from __future__ import annotations

import base64
import hashlib
import os
import uuid

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QTextCharFormat, QTextCursor, QTextDocument

from app.writer.model import P_FOOTNOTE, P_FOOTNOTE_ID

NOTE_FONT_PX = 12          # 9 pt at 96 dpi
NOTE_GAP = 10              # space between the body text and the separator line


# ============================================================================ footnotes in the document
def iter_footnotes(doc: QTextDocument):
    """(position, length, note text, id) of every footnote reference, in document order."""
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if frag.isValid():
                fmt = frag.charFormat()
                if fmt.hasProperty(P_FOOTNOTE):
                    yield frag.position(), frag.length(), str(fmt.property(P_FOOTNOTE)), \
                        str(fmt.property(P_FOOTNOTE_ID) or "")
        block = block.next()


def has_footnotes(doc: QTextDocument) -> bool:
    return next(iter_footnotes(doc), None) is not None


def reference_format(base: QTextCharFormat, text: str, note_id: str | None = None) -> QTextCharFormat:
    f = QTextCharFormat(base)
    f.setVerticalAlignment(QTextCharFormat.AlignSuperScript)
    f.setProperty(P_FOOTNOTE, text)
    f.setProperty(P_FOOTNOTE_ID, note_id or uuid.uuid4().hex[:12])
    f.setAnchor(False)
    return f


def plain_format(fmt: QTextCharFormat) -> QTextCharFormat:
    f = QTextCharFormat(fmt)
    f.clearProperty(P_FOOTNOTE)
    f.clearProperty(P_FOOTNOTE_ID)
    f.setVerticalAlignment(QTextCharFormat.AlignNormal)
    return f


def insert_footnote(cursor: QTextCursor, text: str) -> None:
    """Insert a reference at the cursor; afterwards the cursor stands behind it, ready for ordinary text."""
    if cursor.hasSelection():
        cursor.setPosition(cursor.selectionEnd())
    base = plain_format(cursor.charFormat())
    note_id = uuid.uuid4().hex[:12]
    cursor.insertText("0", reference_format(base, text, note_id))
    doc = cursor.document()
    renumber(doc)
    for pos, length, _t, nid in iter_footnotes(doc):
        if nid == note_id:
            cursor.setPosition(pos + length)
            break
    cursor.setCharFormat(base)


def renumber(doc: QTextDocument) -> int:
    """Number the references 1, 2, 3... in document order; returns the count.

    A reference is only its digits: other text that ended up with the footnote format (pasted, or written by
    another program) becomes ordinary text again instead of being overwritten."""
    notes = list(iter_footnotes(doc))
    c = QTextCursor(doc)
    started = False
    for i in reversed(range(len(notes))):
        pos, length, _note, _nid = notes[i]
        num = str(i + 1)
        text = "".join(doc.characterAt(pos + k) for k in range(length))
        if text == num:
            continue
        if not started:
            c.beginEditBlock()
            started = True
        probe = QTextCursor(doc)
        probe.setPosition(pos + 1)
        ref_fmt = probe.charFormat()          # the reference's own format, read before changing anything
        digits = len(text) - len(text.lstrip("0123456789"))
        if digits < length:
            c.setPosition(pos + digits)
            c.setPosition(pos + length, QTextCursor.KeepAnchor)
            c.setCharFormat(plain_format(ref_fmt))
        c.setPosition(pos)
        c.setPosition(pos + digits, QTextCursor.KeepAnchor)
        c.insertText(num, ref_fmt)
    if started:
        c.endEditBlock()
    return len(notes)


def footnote_at(cursor: QTextCursor):
    """(position, length, text, id) of the footnote reference at/next to the cursor, or None."""
    pos = cursor.position()
    for item in iter_footnotes(cursor.document()):
        if item[0] <= pos <= item[0] + item[1]:
            return item
    return None


def set_footnote_text(doc: QTextDocument, position: int, length: int, text: str) -> None:
    c = QTextCursor(doc)
    c.setPosition(position)
    c.setPosition(position + length, QTextCursor.KeepAnchor)
    f = QTextCharFormat()
    f.setProperty(P_FOOTNOTE, text)
    c.mergeCharFormat(f)


def delete_footnote(doc: QTextDocument, position: int, length: int) -> None:
    c = QTextCursor(doc)
    c.setPosition(position)
    c.setPosition(position + length, QTextCursor.KeepAnchor)
    c.removeSelectedText()
    renumber(doc)


# ============================================================================ layout
def notes_by_page(doc: QTextDocument, page_h: float) -> dict[int, list[tuple[str, str]]]:
    """page index -> [(number, text)] using the document's current layout."""
    out: dict[int, list[tuple[str, str]]] = {}
    lay = doc.documentLayout()
    for n, (pos, _length, text, _id) in enumerate(iter_footnotes(doc), 1):
        block = doc.findBlock(pos)
        rect = lay.blockBoundingRect(block)
        line = block.layout().lineForTextPosition(pos - block.position())
        y = rect.top() + (line.y() if line.isValid() else 0)
        out.setdefault(int(y // page_h) if page_h > 0 else 0, []).append((str(n), text))
    return out


def _font(family: str) -> QFont:
    f = QFont(family or "Calibri")
    f.setPixelSize(NOTE_FONT_PX)
    return f


def notes_height(notes: list[tuple[str, str]], width: float, family: str) -> float:
    fm = QFontMetricsF(_font(family))
    total = NOTE_GAP + 4
    for num, text in notes:
        r = fm.boundingRect(QRectF(0, 0, max(40.0, width - 18), 10000), Qt.TextWordWrap, text or " ")
        total += r.height() + 2
    return total


def reserve_px(doc: QTextDocument, page_h: float, text_width: float, family: str, cap: float) -> float:
    """Space to keep free above the bottom margin for the notes of the fullest page."""
    pages = notes_by_page(doc, page_h)
    if not pages:
        return 0.0
    need = max(notes_height(v, text_width, family) for v in pages.values())
    return float(min(cap, int(need / 4 + 1) * 4))


def draw_notes(p: QPainter, notes: list[tuple[str, str]], left: float, top: float, width: float, family: str,
               color: QColor | None = None) -> None:
    """Separator line and the notes, starting at ``top`` (the bottom of the text area)."""
    if not notes:
        return
    p.save()
    font = _font(family)
    p.setFont(font)
    fm = QFontMetricsF(font)
    y = top + NOTE_GAP
    p.setPen(QColor("#555555"))
    p.drawLine(left, y - 4, left + width / 3, y - 4)
    p.setPen(color or QColor("#000000"))
    small = QFont(font)
    small.setPixelSize(int(NOTE_FONT_PX * 0.75))
    for num, text in notes:
        p.setFont(small)
        p.drawText(QRectF(left, y - 2, 16, 14), Qt.AlignLeft | Qt.AlignTop, num)
        p.setFont(font)
        r = QRectF(left + 16, y, max(40.0, width - 18), 10000)
        br = fm.boundingRect(r, Qt.TextWordWrap, text or " ")
        p.drawText(r, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text)
        y += br.height() + 2
    p.restore()


# ============================================================================ Word document protection hash
_INITIAL_CODE = [0xE1F0, 0x1D0F, 0xCC9C, 0x84C0, 0x110C, 0x0E10, 0xF1CE, 0x313E, 0x1872, 0xE139, 0xD40F, 0x84F9,
                 0x280C, 0xA96A, 0x4EC3]
_MATRIX = [
    [0xAEFC, 0x4DD9, 0x9BB2, 0x2745, 0x4E8A, 0x9D14, 0x2A09], [0x7B61, 0xF6C2, 0xFDA5, 0xEB6B, 0xC6F7, 0x9DCF, 0x2BBF],
    [0x4563, 0x8AC6, 0x05AD, 0x0B5A, 0x16B4, 0x2D68, 0x5AD0], [0x0375, 0x06EA, 0x0DD4, 0x1BA8, 0x3750, 0x6EA0, 0xDD40],
    [0xD849, 0xA0B3, 0x5147, 0xA28E, 0x553D, 0xAA7A, 0x44D5], [0x6F45, 0xDE8A, 0xAD35, 0x4A4B, 0x9496, 0x390D, 0x721A],
    [0xEB23, 0xC667, 0x9CEF, 0x29FF, 0x53FE, 0xA7FC, 0x5FD9], [0x47D3, 0x8FA6, 0x0F6D, 0x1EDA, 0x3DB4, 0x7B68, 0xF6D0],
    [0xB861, 0x60E3, 0xC1C6, 0x93AD, 0x377B, 0x6EF6, 0xDDEC], [0x45A0, 0x8B40, 0x06A1, 0x0D42, 0x1A84, 0x3508, 0x6A10],
    [0xAA51, 0x4483, 0x8906, 0x022D, 0x045A, 0x08B4, 0x1168], [0x76B4, 0xED68, 0xCAF1, 0x85C3, 0x1BA7, 0x374E, 0x6E9C],
    [0x3730, 0x6E60, 0xDCC0, 0xA9A1, 0x4363, 0x86C6, 0x1DAD], [0x3331, 0x6662, 0xCCC4, 0x89A9, 0x0373, 0x06E6, 0x0DCC],
    [0x1021, 0x2042, 0x4084, 0x8108, 0x1231, 0x2462, 0x48C4]]
SPIN_COUNT = 100000


def _ansi_bytes(password: str) -> list[int]:
    out = []
    for ch in password[:15]:
        code = ord(ch)
        low = code & 0xFF
        out.append(low if low else (code >> 8) & 0xFF)
    return out


def legacy_key(password: str) -> int:
    """The 32-bit Word 2007 password key (ECMA-376 Part 4, 2.15.1.28): "Example" -> 0x64CEED7E."""
    chars = _ansi_bytes(password)
    if not chars:
        return 0
    high = _INITIAL_CODE[len(chars) - 1]
    for i, b in enumerate(chars):
        row = _MATRIX[15 - len(chars) + i]
        for bit in range(7):
            if b & (1 << bit):
                high ^= row[bit]
    low = 0
    for b in reversed(chars):
        low = (((low >> 14) & 1) | ((low << 1) & 0x7FFF)) ^ b
    low = (((low >> 14) & 1) | ((low << 1) & 0x7FFF)) ^ len(chars) ^ 0xCE4B
    return ((high & 0xFFFF) << 16) | (low & 0xFFFF)


def protection_hash(password: str, salt: bytes, spin: int = SPIN_COUNT) -> bytes:
    key = legacy_key(password)
    # the key with its byte order reversed, as an upper-case hex string, hashed as UTF-16LE text
    reversed_hex = "".join(f"{(key >> s) & 0xFF:02X}" for s in (0, 8, 16, 24))
    h = hashlib.sha512(salt + reversed_hex.encode("utf-16-le")).digest()
    for i in range(spin):
        h = hashlib.sha512(h + i.to_bytes(4, "little")).digest()
    return h


def protection_attributes(password: str, edit: str = "readOnly") -> dict[str, str]:
    """Attributes for ``w:documentProtection`` (Word 2010+ form, SHA-512)."""
    attrs = {"edit": edit, "enforcement": "1"}
    if password:
        salt = os.urandom(16)
        attrs.update({"cryptProviderType": "rsaAES", "cryptAlgorithmClass": "hash", "cryptAlgorithmType": "typeAny",
                      "cryptAlgorithmSid": "14", "cryptSpinCount": str(SPIN_COUNT),
                      "hash": base64.b64encode(protection_hash(password, salt)).decode(),
                      "salt": base64.b64encode(salt).decode()})
    return attrs


def verify_protection(password: str, attrs: dict[str, str]) -> bool:
    """True if ``password`` matches the stored hash (or there is no password)."""
    stored = attrs.get("hash") or attrs.get("hashValue")
    salt = attrs.get("salt") or attrs.get("saltValue")
    if not stored:
        return True
    if not salt:
        return False
    spin = int(attrs.get("cryptSpinCount") or attrs.get("spinCount") or SPIN_COUNT)
    sid = attrs.get("cryptAlgorithmSid", "14")
    algo = (attrs.get("algorithmName") or {"4": "SHA-1", "12": "SHA-256", "13": "SHA-384", "14": "SHA-512"}.get(
        sid, "SHA-512")).replace("-", "").lower()
    try:
        salt_b = base64.b64decode(salt)
        key = legacy_key(password)
        reversed_hex = "".join(f"{(key >> s) & 0xFF:02X}" for s in (0, 8, 16, 24))
        hf = getattr(hashlib, algo)
        h = hf(salt_b + reversed_hex.encode("utf-16-le")).digest()
        for i in range(spin):
            h = hf(h + i.to_bytes(4, "little")).digest()
        return base64.b64encode(h).decode() == stored
    except Exception:
        return False
