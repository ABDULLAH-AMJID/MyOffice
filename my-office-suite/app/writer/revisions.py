"""Track Changes and Comments (tool 18).

Revisions are stored as custom character properties on the text itself
(``P_INS`` / ``P_DEL`` = "author|date"), so they survive copy/paste and undo, and
are exported to DOCX as real ``<w:ins>`` / ``<w:del>`` revisions that Word, WPS and
LibreOffice understand.  The visual mark-up is painted by a QSyntaxHighlighter
(it never modifies the stored formatting).  Revisions can also be exported as a
Quill/Yjs-style *Delta* (``{"ops": [...]}``).
"""
from __future__ import annotations

import hashlib
import uuid

from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument

from app.writer.model import P_COMMENTS, P_DEL, P_INS, parse_stamp, revision_stamp

_AUTHOR_COLORS = ["#1a73e8", "#9334e6", "#188038", "#e37400", "#c5221f", "#007b83", "#b80672"]


def author_color(author: str) -> QColor:
    h = int(hashlib.md5((author or "?").encode("utf-8")).hexdigest(), 16)
    return QColor(_AUTHOR_COLORS[h % len(_AUTHOR_COLORS)])


class RevisionHighlighter(QSyntaxHighlighter):
    """Paints insertions (coloured underline), deletions (red strike-through) and comment anchors."""

    def __init__(self, doc: QTextDocument):
        super().__init__(doc)
        self.show_markup = True
        self.active_comment: str | None = None
        self.speller = None   # app.services.spell.Speller while "check spelling as you type" is on

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        if self.show_markup:
            self._markup(text)
        sp = self.speller
        if sp is not None and sp.ready() and text:
            for start, end, _word in sp.errors(text):
                for i in range(start, end):
                    f = self.format(i)
                    f.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
                    f.setUnderlineColor(QColor("#d93025"))
                    self.setFormat(i, 1, f)

    def _markup(self, text: str) -> None:
        block = self.currentBlock()
        bpos = block.position()
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid():
                continue
            fmt = frag.charFormat()
            start = frag.position() - bpos
            n = frag.length()
            vis = None
            if fmt.hasProperty(P_INS):
                author, _ = parse_stamp(fmt.property(P_INS))
                col = author_color(author)
                vis = QTextCharFormat()
                vis.setForeground(col)
                vis.setUnderlineStyle(QTextCharFormat.SingleUnderline)
                vis.setUnderlineColor(col)
            elif fmt.hasProperty(P_DEL):
                vis = QTextCharFormat()
                vis.setForeground(QColor("#c5221f"))
                vis.setFontStrikeOut(True)
            if fmt.hasProperty(P_COMMENTS) and str(fmt.property(P_COMMENTS)):
                vis = vis or QTextCharFormat()
                ids = str(fmt.property(P_COMMENTS)).split(",")
                active = self.active_comment in ids
                vis.setBackground(QColor("#ffd666" if active else "#fff3bf"))
            if vis is not None:
                self.setFormat(start, n, vis)


def _fragments(doc: QTextDocument, start: int = 0, end: int | None = None):
    """Yield (position, length, QTextCharFormat) for fragments intersecting [start, end)."""
    end = doc.characterCount() if end is None else end
    block = doc.findBlock(start)
    while block.isValid() and block.position() < end:
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid():
                continue
            fs, fe = frag.position(), frag.position() + frag.length()
            if fe <= start or fs >= end:
                continue
            s, e = max(fs, start), min(fe, end)
            yield s, e - s, frag.charFormat()
        block = block.next()


class RevisionEngine:
    """Implements tracked editing operations on a QTextDocument."""

    def __init__(self, doc: QTextDocument, author_getter):
        self.doc = doc
        self._author = author_getter

    @property
    def author(self) -> str:
        return self._author()

    # ------------------------------------------------------------- formats
    def insertion_format(self, base: QTextCharFormat) -> QTextCharFormat:
        f = QTextCharFormat(base)
        f.clearProperty(P_DEL)
        f.setProperty(P_INS, revision_stamp(self.author))
        return f

    # ------------------------------------------------------------- tracked delete
    def tracked_delete(self, start: int, end: int, cursor: QTextCursor) -> None:
        """Mark [start, end) as deleted; text inserted by tracking is removed for real."""
        if end <= start:
            return
        stamp = revision_stamp(self.author)
        ranges = list(_fragments(self.doc, start, end))
        cursor.beginEditBlock()
        for pos, length, fmt in reversed(ranges):
            c = QTextCursor(self.doc)
            c.setPosition(pos)
            c.setPosition(pos + length, QTextCursor.KeepAnchor)
            if fmt.hasProperty(P_INS):
                c.removeSelectedText()
            elif fmt.hasProperty(P_DEL):
                continue
            else:
                text = c.selectedText()
                # paragraph separators are kept (deleting them would merge paragraphs)
                if text == "\u2029":
                    continue
                f = QTextCharFormat(fmt)
                f.setProperty(P_DEL, stamp)
                c.setCharFormat(f)
        cursor.endEditBlock()

    # ------------------------------------------------------------- navigation
    def revision_ranges(self) -> list[tuple[int, int, str, str, str]]:
        """List of (start, end, kind, author, date) for contiguous revisions."""
        out: list[list] = []
        for pos, length, fmt in _fragments(self.doc):
            kind = "ins" if fmt.hasProperty(P_INS) else ("del" if fmt.hasProperty(P_DEL) else None)
            if not kind:
                continue
            author, date = parse_stamp(fmt.property(P_INS if kind == "ins" else P_DEL))
            if out and out[-1][1] == pos and out[-1][2] == kind and out[-1][3] == author:
                out[-1][1] = pos + length
            else:
                out.append([pos, pos + length, kind, author, date])
        return [tuple(x) for x in out]

    def has_revisions(self) -> bool:
        return bool(self.revision_ranges())

    # ------------------------------------------------------------- accept / reject
    def _resolve(self, start: int, end: int, accept: bool) -> int:
        ranges = list(_fragments(self.doc, start, end))
        c = QTextCursor(self.doc)
        c.beginEditBlock()
        count = 0
        for pos, length, fmt in reversed(ranges):
            is_ins, is_del = fmt.hasProperty(P_INS), fmt.hasProperty(P_DEL)
            if not (is_ins or is_del):
                continue
            count += 1
            c.setPosition(pos)
            c.setPosition(pos + length, QTextCursor.KeepAnchor)
            remove = (is_del and accept) or (is_ins and not accept)
            if remove:
                c.removeSelectedText()
            else:
                f = QTextCharFormat(fmt)
                f.clearProperty(P_INS)
                f.clearProperty(P_DEL)
                c.setCharFormat(f)
        c.endEditBlock()
        return count

    def accept_all(self) -> int:
        return self._resolve(0, self.doc.characterCount(), True)

    def reject_all(self) -> int:
        return self._resolve(0, self.doc.characterCount(), False)

    def range_at(self, cursor: QTextCursor) -> tuple[int, int] | None:
        if cursor.hasSelection():
            return cursor.selectionStart(), cursor.selectionEnd()
        p = cursor.position()
        for s, e, *_ in self.revision_ranges():
            if s <= p <= e:
                return s, e
        return None

    def accept_at(self, cursor: QTextCursor) -> int:
        r = self.range_at(cursor)
        return self._resolve(r[0], r[1], True) if r else 0

    def reject_at(self, cursor: QTextCursor) -> int:
        r = self.range_at(cursor)
        return self._resolve(r[0], r[1], False) if r else 0

    # ------------------------------------------------------------- delta export
    def to_delta(self) -> dict:
        """Quill/Yjs Delta of the document with revision attributes."""
        ops = []
        for pos, length, fmt in _fragments(self.doc):
            c = QTextCursor(self.doc)
            c.setPosition(pos)
            c.setPosition(pos + length, QTextCursor.KeepAnchor)
            text = c.selectedText().replace("\u2029", "\n").replace("\u2028", "\n")
            if not text:
                continue
            attrs = {}
            if fmt.fontWeight() >= 600:
                attrs["bold"] = True
            if fmt.fontItalic():
                attrs["italic"] = True
            if fmt.fontUnderline():
                attrs["underline"] = True
            if fmt.hasProperty(P_INS):
                a, d = parse_stamp(fmt.property(P_INS))
                attrs["insert"] = {"author": a, "date": d}
            if fmt.hasProperty(P_DEL):
                a, d = parse_stamp(fmt.property(P_DEL))
                attrs["delete"] = {"author": a, "date": d}
                attrs["strike"] = True
            op = {"insert": text}
            if attrs:
                op["attributes"] = attrs
            if ops and ops[-1].get("attributes") == op.get("attributes"):
                ops[-1]["insert"] += text
            else:
                ops.append(op)
        if not ops or not ops[-1]["insert"].endswith("\n"):
            ops.append({"insert": "\n"})
        return {"ops": ops}


# ----------------------------------------------------------------------------- comments
def new_comment_id() -> str:
    return "c" + uuid.uuid4().hex[:8]


def add_comment_anchor(doc: QTextDocument, start: int, end: int, cid: str) -> None:
    ranges = list(_fragments(doc, start, end))
    c = QTextCursor(doc)
    c.beginEditBlock()
    for pos, length, fmt in ranges:
        ids = [x for x in str(fmt.property(P_COMMENTS) or "").split(",") if x]
        if cid not in ids:
            ids.append(cid)
        f = QTextCharFormat(fmt)
        f.setProperty(P_COMMENTS, ",".join(ids))
        c.setPosition(pos)
        c.setPosition(pos + length, QTextCursor.KeepAnchor)
        c.setCharFormat(f)
    c.endEditBlock()


def remove_comment_anchor(doc: QTextDocument, cid: str) -> None:
    ranges = [r for r in _fragments(doc) if cid in str(r[2].property(P_COMMENTS) or "").split(",")]
    c = QTextCursor(doc)
    c.beginEditBlock()
    for pos, length, fmt in ranges:
        ids = [x for x in str(fmt.property(P_COMMENTS) or "").split(",") if x and x != cid]
        f = QTextCharFormat(fmt)
        if ids:
            f.setProperty(P_COMMENTS, ",".join(ids))
        else:
            f.clearProperty(P_COMMENTS)
        c.setPosition(pos)
        c.setPosition(pos + length, QTextCursor.KeepAnchor)
        c.setCharFormat(f)
    c.endEditBlock()


def comment_range(doc: QTextDocument, cid: str) -> tuple[int, int] | None:
    s = e = None
    for pos, length, fmt in _fragments(doc):
        if cid in str(fmt.property(P_COMMENTS) or "").split(","):
            s = pos if s is None else s
            e = pos + length
    return (s, e) if s is not None else None


def anchored_comment_ids(doc: QTextDocument) -> list[str]:
    seen: list[str] = []
    for _pos, _len, fmt in _fragments(doc):
        for cid in str(fmt.property(P_COMMENTS) or "").split(","):
            if cid and cid not in seen:
                seen.append(cid)
    return seen


def fragments(doc: QTextDocument, start: int = 0, end: int | None = None):
    return _fragments(doc, start, end)

