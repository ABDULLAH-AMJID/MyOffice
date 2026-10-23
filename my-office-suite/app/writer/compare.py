"""Legal-blackline style document comparison.

Two documents are compared with :mod:`difflib` - first paragraph by paragraph, then word by
word inside changed paragraphs.  The result is a new document whose differences are real
tracked changes (insertions/deletions attributed to the "Compare" author), so they can be
reviewed with Accept/Reject exactly like edits made with Track Changes.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass

from PySide6.QtGui import QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit, QPushButton

from app.services.i18n import _t
from app.writer.model import P_DEL, P_INS, revision_stamp
from app.writer.revisions import fragments

TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


@dataclass
class CompareStats:
    inserted_words: int = 0
    deleted_words: int = 0
    changed_paragraphs: int = 0
    inserted_paragraphs: int = 0
    deleted_paragraphs: int = 0

    def summary(self) -> str:
        return _t("{0} word(s) inserted, {1} word(s) deleted, {2} paragraph(s) changed, {3} inserted, {4} deleted.").format(
            self.inserted_words, self.deleted_words, self.changed_paragraphs, self.inserted_paragraphs,
            self.deleted_paragraphs)


def _words(tokens: list[str]) -> int:
    return sum(1 for t in tokens if t.strip())


def document_paragraphs(doc: QTextDocument) -> list[str]:
    """Paragraph texts of a document in its 'final' state (tracked deletions removed)."""
    paras = []
    block = doc.begin()
    while block.isValid():
        parts = []
        it = block.begin()
        while not it.atEnd():
            f = it.fragment()
            it += 1
            if f.isValid() and not f.charFormat().hasProperty(P_DEL) and not f.charFormat().isImageFormat():
                parts.append(f.text())
        paras.append("".join(parts).replace("\u2028", " "))
        block = block.next()
    return paras


def file_paragraphs(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".csv"):
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.read().splitlines()
    doc = QTextDocument()
    if ext in (".html", ".htm"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            doc.setHtml(fh.read())
        return document_paragraphs(doc)
    if ext not in (".docx", ".docm", ".dotx", ".wpt"):
        from app.services.office_convert import convert

        path = convert(path, "docx")
    from app.writer.docx_io import load_docx

    load_docx(path, doc, lambda data, ext, img=None: "img")
    return document_paragraphs(doc)


def compare_into(old: list[str], new: list[str], out: QTextDocument, author: str = "Compare") -> CompareStats:
    stats = CompareStats()
    stamp = revision_stamp(author)
    plain = QTextCharFormat()
    ins = QTextCharFormat()
    ins.setProperty(P_INS, stamp)
    dele = QTextCharFormat()
    dele.setProperty(P_DEL, stamp)
    cur = QTextCursor(out)
    cur.beginEditBlock()
    first = [True]

    def new_block():
        if first[0]:
            first[0] = False
        else:
            cur.insertBlock(QTextBlockFormat(), plain)

    def whole(text: str, fmt):
        new_block()
        if text:
            cur.insertText(text, fmt)

    def word_diff(a: str, b: str):
        ta, tb = TOKEN_RE.findall(a), TOKEN_RE.findall(b)
        new_block()
        sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                cur.insertText("".join(ta[i1:i2]), plain)
            if tag in ("delete", "replace"):
                cur.insertText("".join(ta[i1:i2]), dele)
                stats.deleted_words += _words(ta[i1:i2])
            if tag in ("insert", "replace"):
                cur.insertText("".join(tb[j1:j2]), ins)
                stats.inserted_words += _words(tb[j1:j2])

    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for p in new[j1:j2]:
                whole(p, plain)
        elif tag == "delete":
            for p in old[i1:i2]:
                whole(p, dele)
                stats.deleted_paragraphs += 1
                stats.deleted_words += _words(TOKEN_RE.findall(p))
        elif tag == "insert":
            for p in new[j1:j2]:
                whole(p, ins)
                stats.inserted_paragraphs += 1
                stats.inserted_words += _words(TOKEN_RE.findall(p))
        else:
            a, b = old[i1:i2], new[j1:j2]
            for k in range(max(len(a), len(b))):
                if k < len(a) and k < len(b):
                    ratio = difflib.SequenceMatcher(None, a[k], b[k], autojunk=False).quick_ratio()
                    if ratio > 0.35:
                        word_diff(a[k], b[k])
                        stats.changed_paragraphs += 1
                    else:
                        whole(a[k], dele)
                        whole(b[k], ins)
                        stats.deleted_paragraphs += 1
                        stats.inserted_paragraphs += 1
                        stats.deleted_words += _words(TOKEN_RE.findall(a[k]))
                        stats.inserted_words += _words(TOKEN_RE.findall(b[k]))
                elif k < len(a):
                    whole(a[k], dele)
                    stats.deleted_paragraphs += 1
                    stats.deleted_words += _words(TOKEN_RE.findall(a[k]))
                else:
                    whole(b[k], ins)
                    stats.inserted_paragraphs += 1
                    stats.inserted_words += _words(TOKEN_RE.findall(b[k]))
    cur.endEditBlock()
    return stats


def count_revisions(doc: QTextDocument) -> tuple[int, int]:
    ins = dele = 0
    for _p, _l, fmt in fragments(doc):
        if fmt.hasProperty(P_INS):
            ins += 1
        elif fmt.hasProperty(P_DEL):
            dele += 1
    return ins, dele


class CompareDialog(QDialog):
    def __init__(self, parent, original: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_t("Compare Documents"))
        self.resize(560, 150)
        f = QFormLayout(self)
        self.orig = QLineEdit(original)
        self.rev = QLineEdit()
        for label, edit in ((_t("Original document:"), self.orig), (_t("Revised document:"), self.rev)):
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            b = QPushButton(_t("Browse\u2026"))
            b.clicked.connect(lambda _=False, e=edit: self._browse(e))
            row.addWidget(b)
            f.addRow(label, row)
        self.author = QLineEdit(_t("Compare"))
        f.addRow(_t("Label changes with author:"), self.author)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self, edit: QLineEdit):
        from app.shell.dialogs import get_open_file

        p = get_open_file(self, _t("Select document"),
                          "Documents (*.docx *.docm *.dotx *.wpt *.doc *.odt *.rtf *.txt *.md *.html);;All files (*)")
        if p:
            edit.setText(p)
