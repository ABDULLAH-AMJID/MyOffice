"""Writer features: spelling, footnotes, equations, restrict editing, merge/split documents, table toolbox and the
MyOffice AI commands.  Every function takes the WriterDocument widget."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QImage, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor,
                           QTextDocument, QTextDocumentFragment, QTextFormat, QTextImageFormat, QTextLength,
                           QTextTableFormat)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMenu,
                               QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from app.services import spell
from app.services.config import cfg
from app.services.i18n import _t
from app.writer import equations, notes
from app.writer.model import P_COMMENTS, P_EQUATION, P_STYLE

log = logging.getLogger(__name__)

SPELL_VARIANT = {"en_US": "American English", "en_GB": "British English", "es_ES": "Spanish"}


def _sel_text(c: QTextCursor) -> str:
    return c.selectedText().replace("\u2029", "\n").replace("\u2028", "\n")


# ============================================================================ spelling
def install_spelling(w) -> None:
    """Attach the spell checker to the document highlighter (word list loads in the background)."""
    lang = cfg().get("writer.spell_lang", "en_US")
    sp = spell.speller(lang)
    hl = w.editor.highlighter
    hl.speller = sp if cfg().get("writer.spell_as_you_type", True) else None
    if hl.speller is not None and not sp.ready():
        sp.load_async()
        _rehighlight_when_ready(w, sp)


def _rehighlight_when_ready(w, sp, tries: int = 40):
    if sp.ready():
        if w.editor.highlighter.speller is sp:
            w.editor.highlighter.rehighlight()
        return
    if tries > 0:
        QTimer.singleShot(250, lambda: _rehighlight_when_ready(w, sp, tries - 1))


def set_spelling(w, on: bool) -> None:
    cfg().set("writer.spell_as_you_type", bool(on), save=True)
    install_spelling(w)
    w.editor.highlighter.rehighlight()


def set_spell_language(w, lang: str) -> None:
    cfg().set("writer.spell_lang", lang, save=True)
    install_spelling(w)
    w.editor.highlighter.rehighlight()


def word_at(c: QTextCursor) -> QTextCursor:
    wc = QTextCursor(c)
    wc.clearSelection()
    wc.select(QTextCursor.WordUnderCursor)
    return wc


def spelling_menu_items(w, menu: QMenu) -> None:
    """Suggestions for the misspelled word under the cursor, at the top of the context menu."""
    sp = w.editor.highlighter.speller
    if sp is None or not sp.ready():
        return
    wc = word_at(w.editor.textCursor())
    word = wc.selectedText()
    if not word or sp.check(word):
        return
    first = menu.actions()[0] if menu.actions() else None
    sugg = sp.suggest(word)
    acts = []
    for s in sugg:
        a = menu.addAction(s)
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        a.triggered.connect(lambda _=False, s=s, wc=QTextCursor(wc): _replace_word(w, wc, s))
        acts.append(a)
    if not sugg:
        a = menu.addAction(_t("(no suggestions)"))
        a.setEnabled(False)
        acts.append(a)
    add = menu.addAction(_t("Add to Dictionary"))
    add.triggered.connect(lambda: (sp.add(word), w.editor.highlighter.rehighlight()))
    ign = menu.addAction(_t("Ignore All"))
    ign.triggered.connect(lambda: (sp.ignore(word), w.editor.highlighter.rehighlight()))
    acts += [add, ign, menu.addSeparator()]
    if first is not None:
        for a in acts:
            menu.removeAction(a)
        menu.insertActions(first, acts)


def _replace_word(w, wc: QTextCursor, new: str):
    fmt = wc.charFormat()
    wc.insertText(new, fmt)


class SpellDialog(QDialog):
    """F7: walks through the misspelled words of the document."""

    def __init__(self, w):
        super().__init__(w)
        self.w = w
        self.setWindowTitle(_t("Spelling"))
        self.resize(460, 360)
        lang = cfg().get("writer.spell_lang", "en_US")
        self.sp = spell.speller(lang)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(_t("Dictionary language:")))
        self.lang = QComboBox()
        for code, (label, _) in spell.LANGS.items():
            self.lang.addItem(label, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(lang)))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        row.addWidget(self.lang, 1)
        v.addLayout(row)
        self.word = QLabel("")
        self.word.setStyleSheet("font-size:15px; font-weight:600; color:#c5221f")
        v.addWidget(self.word)
        self.context = QLabel("")
        self.context.setWordWrap(True)
        self.context.setStyleSheet("color:#5f6368")
        v.addWidget(self.context)
        v.addWidget(QLabel(_t("Suggestions:")))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.change())
        v.addWidget(self.list, 1)
        grid = QGridLayout()
        for i, (label, slot) in enumerate(((_t("Change"), self.change), (_t("Change All"), self.change_all),
                                           (_t("Ignore"), self.next), (_t("Ignore All"), self.ignore_all),
                                           (_t("Add to Dictionary"), self.add), (_t("Close"), self.reject))):
            b = QPushButton(label)
            b.clicked.connect(slot)
            grid.addWidget(b, i // 3, i % 3)
        v.addLayout(grid)
        self.cursor = QTextCursor(w.editor.document())
        self.cursor.setPosition(w.editor.textCursor().selectionStart())
        self.current: QTextCursor | None = None
        self._skip: set[int] = set()
        QTimer.singleShot(0, self.next)

    def _lang_changed(self):
        set_spell_language(self.w, self.lang.currentData())
        self.sp = spell.speller(self.lang.currentData())
        self.next()

    def _find_next(self) -> QTextCursor | None:
        doc = self.w.editor.document()
        start = self.cursor.position()
        block = doc.findBlock(start)
        wrapped = False
        while True:
            if not block.isValid():
                if wrapped:
                    return None
                wrapped = True
                block = doc.begin()
                start = 0
            text = block.text()
            for s, e, _word in self.sp.errors(text):
                pos = block.position() + s
                if pos < start or pos in self._skip:
                    continue
                c = QTextCursor(doc)
                c.setPosition(pos)
                c.setPosition(block.position() + e, QTextCursor.KeepAnchor)
                return c
            if wrapped and block.position() > self.cursor.position():
                return None
            block = block.next()
            start = block.position() if block.isValid() else 0

    def next(self):
        c = self._find_next()
        self.current = c
        self.list.clear()
        if c is None:
            self.word.setText(_t("Spell check complete."))
            self.context.setText("")
            return
        word = c.selectedText()
        self.word.setText(word)
        btext = c.block().text()
        s = c.selectionStart() - c.block().position()
        self.context.setText("\u2026" + btext[max(0, s - 40):s + len(word) + 40] + "\u2026")
        self.list.addItems(self.sp.suggest(word))
        if self.list.count():
            self.list.setCurrentRow(0)
        self.w.editor.setTextCursor(c)
        self.w.editor.ensure_cursor_visible()
        self.cursor.setPosition(c.selectionEnd())

    def _choice(self) -> str | None:
        it = self.list.currentItem()
        return it.text() if it else None

    def change(self):
        new = self._choice()
        if self.current is not None and new:
            _replace_word(self.w, self.current, new)
            self.cursor.setPosition(self.current.position())
        self.next()

    def change_all(self):
        new = self._choice()
        if self.current is None or not new:
            return
        old = self.current.selectedText()
        doc = self.w.editor.document()
        c = QTextCursor(doc)
        c.beginEditBlock()
        found = doc.find(old, 0, QTextDocument.FindWholeWords | QTextDocument.FindCaseSensitively)
        while not found.isNull():
            _replace_word(self.w, found, new)
            found = doc.find(old, found.position(), QTextDocument.FindWholeWords | QTextDocument.FindCaseSensitively)
        c.endEditBlock()
        self.next()

    def ignore_all(self):
        if self.current is not None:
            self.sp.ignore(self.current.selectedText())
            self.w.editor.highlighter.rehighlight()
        self.next()

    def add(self):
        if self.current is not None:
            self.sp.add(self.current.selectedText())
            self.w.editor.highlighter.rehighlight()
        self.next()


# ============================================================================ footnotes
def _ask_text(parent, title: str, label: str, text: str = "") -> str | None:
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.resize(460, 220)
    v = QVBoxLayout(d)
    v.addWidget(QLabel(label))
    ed = QPlainTextEdit(text)
    v.addWidget(ed, 1)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    v.addWidget(bb)
    ed.setFocus()
    if d.exec() != QDialog.Accepted:
        return None
    return ed.toPlainText().strip()


def insert_footnote(w) -> None:
    if w.editor.read_only if hasattr(w.editor, "read_only") else False:
        return
    text = _ask_text(w, _t("Insert Footnote"), _t("Footnote text (shown at the bottom of the page):"))
    if not text:
        return
    c = w.editor.textCursor()
    notes.insert_footnote(c, text)
    w.editor.setTextCursor(c)
    w.editor.update_notes_reserve()
    w.statusChanged.emit(_t("Footnote {0} inserted").format(len(list(notes.iter_footnotes(w.editor.document())))))


def edit_footnote(w) -> None:
    item = notes.footnote_at(w.editor.textCursor())
    if item is None:
        return
    pos, length, text, _id = item
    new = _ask_text(w, _t("Edit Footnote"), _t("Footnote text:"), text)
    if new is not None and new != text:
        if new:
            notes.set_footnote_text(w.editor.document(), pos, length, new)
        else:
            notes.delete_footnote(w.editor.document(), pos, length)
        w.editor.update_notes_reserve()


def delete_footnote(w) -> None:
    item = notes.footnote_at(w.editor.textCursor())
    if item is not None:
        notes.delete_footnote(w.editor.document(), item[0], item[1])
        w.editor.update_notes_reserve()


def goto_next_footnote(w) -> None:
    pos = w.editor.textCursor().position()
    items = list(notes.iter_footnotes(w.editor.document()))
    nxt = next((i for i in items if i[0] > pos), items[0] if items else None)
    if nxt is not None:
        c = w.editor.textCursor()
        c.setPosition(nxt[0])
        c.setPosition(nxt[0] + nxt[1], QTextCursor.KeepAnchor)
        w.editor.setTextCursor(c)
        w.editor.ensure_cursor_visible()


# ============================================================================ equations
class EquationDialog(QDialog):
    def __init__(self, parent, latex: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_t("Equation"))
        self.resize(700, 520)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(_t("Examples:")))
        self.examples = QComboBox()
        self.examples.addItem(_t("Choose\u2026"), "")
        for name, ltx in equations.EXAMPLES:
            self.examples.addItem(_t(name), ltx)
        self.examples.activated.connect(lambda i: self.edit.setPlainText(self.examples.itemData(i)) if
                                        self.examples.itemData(i) else None)
        top.addWidget(self.examples, 1)
        v.addLayout(top)
        pal = QWidget()
        grid = QGridLayout(pal)
        grid.setSpacing(2)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, (label, ltx) in enumerate(equations.PALETTE):
            b = QToolButton()
            b.setText(label)
            b.setToolTip(ltx)
            b.setMinimumWidth(38)
            b.clicked.connect(lambda _=False, t=ltx: self._insert(t))
            grid.addWidget(b, i // 15, i % 15)
        v.addWidget(pal)
        v.addWidget(QLabel(_t("LaTeX:")))
        self.edit = QPlainTextEdit(latex)
        self.edit.setMaximumHeight(110)
        self.edit.textChanged.connect(lambda: self._timer.start())
        v.addWidget(self.edit)
        v.addWidget(QLabel(_t("Preview:")))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(120)
        self.preview.setStyleSheet("background:#ffffff; border:1px solid #dadce0")
        sa = QScrollArea()
        sa.setWidget(self.preview)
        sa.setWidgetResizable(True)
        v.addWidget(sa, 1)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#c5221f")
        self.err.setWordWrap(True)
        v.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._render)
        self.png = b""
        self._render()

    def _insert(self, ltx):
        c = self.edit.textCursor()
        c.insertText(ltx)
        self.edit.setFocus()

    def latex(self) -> str:
        return self.edit.toPlainText().strip()

    def _render(self):
        ltx = self.latex()
        if not ltx:
            self.preview.clear()
            self.err.setText("")
            self.png = b""
            return
        try:
            self.png = equations.render_png(ltx, fontsize=20)
        except equations.EquationError as exc:
            self.err.setText(str(exc))
            self.png = b""
            return
        self.err.setText("")
        pm = QPixmap()
        pm.loadFromData(self.png)
        self.preview.setPixmap(pm)

    def _ok(self):
        self._render()
        if self.png:
            self.accept()


def _equation_format(w, latex: str) -> QTextImageFormat:
    png = equations.render_png(latex)
    img = QImage.fromData(png)
    name = w.editor.register_image(png, "png", img)
    imf = QTextImageFormat()
    imf.setName(name)
    imf.setWidth(img.width() / 2.0)
    imf.setHeight(img.height() / 2.0)
    imf.setVerticalAlignment(QTextCharFormat.AlignMiddle)
    imf.setProperty(P_EQUATION, latex)
    return imf


def equation_at_cursor(w):
    c = w.editor.textCursor()
    for pos in (c.position() - 1, c.position()):
        if pos < 0:
            continue
        probe = QTextCursor(w.editor.document())
        probe.setPosition(pos)
        probe.setPosition(pos + 1, QTextCursor.KeepAnchor)
        fmt = probe.charFormat()
        if fmt.isImageFormat() and fmt.hasProperty(P_EQUATION):
            return probe, str(fmt.property(P_EQUATION))
    return None, None


def insert_equation(w) -> None:
    probe, latex = equation_at_cursor(w)
    d = EquationDialog(w, latex or "")
    if d.exec() != QDialog.Accepted:
        return
    imf = _equation_format(w, d.latex())
    c = probe if probe is not None else w.editor.textCursor()
    c.insertImage(imf)
    w.editor.setTextCursor(c)


# ============================================================================ restrict editing
class RestrictDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Restrict Editing"))
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        info = QLabel(_t("The document opens read-only in MyOffice, Word and LibreOffice until protection is "
                         "stopped. With a password, only people who know it can stop protection."))
        info.setWordWrap(True)
        v.addWidget(info)
        form = QFormLayout()
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        form.addRow(_t("Password (optional):"), self.pw1)
        form.addRow(_t("Repeat password:"), self.pw2)
        v.addLayout(form)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#c5221f")
        v.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _ok(self):
        if self.pw1.text() != self.pw2.text():
            self.err.setText(_t("The passwords do not match."))
            return
        self.accept()


def start_protection(w) -> bool:
    d = RestrictDialog(w)
    if d.exec() != QDialog.Accepted:
        return False
    w.protection = notes.protection_attributes(d.pw1.text(), "readOnly")
    w.editor.set_read_only(True)
    w.set_modified(True)
    return True


def stop_protection(w) -> bool:
    prot = getattr(w, "protection", None) or {}
    if prot.get("hash") or prot.get("hashValue"):
        pw, ok = QInputDialog.getText(w, _t("Stop Protection"), _t("Password:"), QLineEdit.Password)
        if not ok:
            return False
        if not notes.verify_protection(pw, prot):
            from app.shell.dialogs import show_error

            show_error(w, _t("Stop Protection"), _t("The password is not correct."))
            return False
    w.protection = {}
    w.editor.set_read_only(False)
    w.set_modified(True)
    return True


# ============================================================================ merge / split documents
def _load_into(path: str, w) -> QTextDocument:
    """A document's content as a QTextDocument whose images live in ``w``'s image store."""
    from app.writer.docx_io import load_docx

    ext = Path(path).suffix.lower()
    src = path
    if ext in (".doc", ".rtf", ".odt", ".wps", ".dot"):
        from app.services.office_convert import convert

        src = convert(path, "docx")
        ext = ".docx"
    doc = QTextDocument()
    doc.setDefaultFont(w.editor.document().defaultFont())
    if ext in (".docx", ".docm", ".dotx", ".wpt"):
        load_docx(src, doc, w.editor.register_image)
    else:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
        if ext == ".md":
            doc.setMarkdown(text)
        elif ext in (".html", ".htm"):
            doc.setHtml(text)
        else:
            doc.setPlainText(text)
    return doc


def merge_documents(w) -> None:
    from app.shell.dialogs import get_open_files, handle_exception

    files = get_open_files(w, _t("Merge Documents"),
                           "Documents (*.docx *.docm *.dotx *.doc *.rtf *.odt *.txt *.md *.html *.htm)")
    if not files:
        return
    page_break = cfg().get("writer.merge_page_break", True)
    c = QTextCursor(w.editor.document())
    c.movePosition(QTextCursor.End)
    c.beginEditBlock()
    try:
        for path in files:
            doc = _load_into(path, w)
            first = QTextBlockFormat(doc.begin().blockFormat())  # e.g. the chapter heading's style
            if page_break:
                first.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
            if c.block().length() > 1 or c.position() > 0:
                c.insertBlock(first, doc.begin().charFormat())
            else:
                c.setBlockFormat(first)
            start = c.position()
            c.insertFragment(QTextDocumentFragment(doc))
            # Qt merges the fragment's first block into the current one and keeps the current block's format
            QTextCursor(w.editor.document().findBlock(start)).setBlockFormat(first)
    except Exception as exc:
        c.endEditBlock()
        handle_exception(w, _t("Merge Documents"), exc)
        return
    c.endEditBlock()
    notes.renumber(w.editor.document())
    w.editor.update_notes_reserve()
    w.statusChanged.emit(_t("{0} documents merged").format(len(files)))


class SplitDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Split Document"))
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        self.by_heading = QRadioButton(_t("At every Heading 1"))
        self.by_break = QRadioButton(_t("At every page break"))
        self.by_pages = QRadioButton(_t("Every N pages:"))
        self.by_heading.setChecked(True)
        self.pages = QSpinBox()
        self.pages.setRange(1, 999)
        self.pages.setValue(5)
        row = QHBoxLayout()
        row.addWidget(self.by_pages)
        row.addWidget(self.pages)
        row.addStretch(1)
        v.addWidget(self.by_heading)
        v.addWidget(self.by_break)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def mode(self):
        if self.by_break.isChecked():
            return "break", 0
        if self.by_pages.isChecked():
            return "pages", self.pages.value()
        return "heading", 0


def split_points(doc: QTextDocument, mode: str, n: int, page_h: float) -> list[int]:
    """Block positions where new parts start (the first part starts at 0)."""
    points = [0]
    block = doc.begin().next()
    lay = doc.documentLayout()
    while block.isValid():
        start = False
        if mode == "heading":
            start = str(block.blockFormat().property(P_STYLE) or "").lower() == "heading 1"
        elif mode == "break":
            start = bool(block.blockFormat().pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore)
        elif mode == "pages" and page_h > 0:
            page = int(lay.blockBoundingRect(block).top() // page_h)
            prev = int(lay.blockBoundingRect(block.previous()).top() // page_h)
            start = page != prev and page % n == 0
        if start and block.position() > points[-1]:
            points.append(block.position())
        block = block.next()
    return points


def split_document(w) -> None:
    from app.shell.dialogs import get_save_file, handle_exception, show_info
    from app.writer.docx_io import save_docx

    d = SplitDialog(w)
    if d.exec() != QDialog.Accepted:
        return
    mode, n = d.mode()
    doc = w.editor.document()
    points = split_points(doc, mode, n, w.editor.page.page_px()[1])
    if len(points) < 2:
        show_info(w, _t("Split Document"), _t("No split points were found - nothing to split."))
        return
    base = w.path.stem if w.path else w.display_name()
    first = get_save_file(w, _t("Split Document - name of the first part"), "Word (*.docx)", base + "_part1.docx",
                          "docx")
    if not first:
        return
    folder = os.path.dirname(first)
    stem = Path(first).stem.removesuffix("_part1").removesuffix("1") or base
    end = doc.characterCount() - 1
    written = []
    try:
        for i, start in enumerate(points):
            stop = points[i + 1] - 1 if i + 1 < len(points) else end
            c = QTextCursor(doc)
            c.setPosition(start)
            c.setPosition(stop, QTextCursor.KeepAnchor)
            part = QTextDocument()
            part.setDefaultFont(doc.defaultFont())
            pc = QTextCursor(part)
            pc.insertFragment(c.selection())
            first_block = part.begin()
            bf = first_block.blockFormat()
            if bf.pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore:
                bf.setPageBreakPolicy(QTextFormat.PageBreak_Auto)
                QTextCursor(first_block).setBlockFormat(bf)
            notes.renumber(part)
            used = set()
            for _p, _l, fmt in _fragments(part):
                if fmt.hasProperty(P_COMMENTS):
                    used.update(str(fmt.property(P_COMMENTS)).split(","))
            comments = {k: v for k, v in w.editor.comments.items() if k in used}
            out = os.path.join(folder, f"{stem}_part{i + 1}.docx")
            save_docx(out, part, w.editor.page, w.editor.hf, comments, w.editor.image_bytes,
                      title=f"{base} ({i + 1})", watermark=w.editor.watermark)
            written.append(out)
    except Exception as exc:
        handle_exception(w, _t("Split Document"), exc)
        return
    show_info(w, _t("Split Document"), _t("{0} documents were created in:\n{1}").format(len(written), folder))


def _fragments(doc: QTextDocument):
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            f = it.fragment()
            it += 1
            if f.isValid():
                yield f.position(), f.length(), f.charFormat()
        block = block.next()


# ============================================================================ table toolbox
TABLE_STYLES = {
    "Grid": ("#ffffff", "#000000", None, "#000000"),
    "Blue Header": ("#1a73e8", "#ffffff", None, "#9fc3f7"),
    "Blue Banded": ("#1a73e8", "#ffffff", "#e8f0fe", "#9fc3f7"),
    "Green Banded": ("#188038", "#ffffff", "#e6f4ea", "#a8dab5"),
    "Orange Banded": ("#e8710a", "#ffffff", "#fef0e1", "#f9c58f"),
    "Gray Professional": ("#3c4043", "#ffffff", "#f1f3f4", "#bdc1c6"),
    "Purple Elegant": ("#7b1fa2", "#ffffff", "#f3e5f5", "#ce93d8"),
    "Plain (no borders)": ("#ffffff", "#000000", None, None),
}


def apply_table_style(w, name: str) -> bool:
    table = w.editor.textCursor().currentTable()
    if table is None:
        return False
    head_bg, head_fg, band, border = TABLE_STYLES[name]
    c = QTextCursor(w.editor.document())
    c.beginEditBlock()
    tf = table.format()
    tf.setBorder(1 if border else 0)
    tf.setBorderStyle(QTextTableFormat.BorderStyle_Solid if border else QTextTableFormat.BorderStyle_None)
    if border:
        tf.setBorderBrush(QBrush(QColor(border)))
    tf.setCellSpacing(0)
    tf.setCellPadding(4)
    table.setFormat(tf)
    for r in range(table.rows()):
        for col in range(table.columns()):
            cell = table.cellAt(r, col)
            cf = cell.format().toTableCellFormat()
            if r == 0 and name not in ("Grid", "Plain (no borders)"):
                cf.setBackground(QColor(head_bg))
            elif band and r % 2 == 0:
                cf.setBackground(QColor(band))
            else:
                cf.setBackground(QColor("#ffffff"))
            cell.setFormat(cf)
            if r == 0:
                cc = cell.firstCursorPosition()
                cc.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
                f = QTextCharFormat()
                f.setForeground(QColor(head_fg))
                f.setFontWeight(700 if name not in ("Grid", "Plain (no borders)") else 400)
                cc.mergeCharFormat(f)
    c.endEditBlock()
    return True


def _cell_text(table, r, col) -> str:
    cell = table.cellAt(r, col)
    c = cell.firstCursorPosition()
    c.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
    return _sel_text(c)


def sort_table(w, column: int, descending: bool, header: bool) -> bool:
    table = w.editor.textCursor().currentTable()
    if table is None:
        return False
    first = 1 if header else 0
    rows = []
    for r in range(first, table.rows()):
        frags = []
        for col in range(table.columns()):
            cell = table.cellAt(r, col)
            c = cell.firstCursorPosition()
            c.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
            frags.append(c.selection())
        rows.append((_cell_text(table, r, column), frags))

    def key(item):
        t = item[0].strip().replace(",", "")
        try:
            return (0, float(t), "")
        except ValueError:
            return (1, 0.0, item[0].lower())
    rows.sort(key=key, reverse=descending)
    c = QTextCursor(w.editor.document())
    c.beginEditBlock()
    for i, (_key, frags) in enumerate(rows):
        r = first + i
        for col, frag in enumerate(frags):
            cell = table.cellAt(r, col)
            cc = cell.firstCursorPosition()
            cc.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
            cc.removeSelectedText()
            cc.insertFragment(frag)
    c.endEditBlock()
    return True


def text_to_table(w, sep: str = "\t") -> bool:
    c = w.editor.textCursor()
    if not c.hasSelection():
        return False
    lines = [ln for ln in _sel_text(c).split("\n")]
    rows = [ln.split(sep) if sep else [ln] for ln in lines]
    cols = max(len(r) for r in rows)
    c.beginEditBlock()
    c.removeSelectedText()
    tf = QTextTableFormat()
    tf.setCellPadding(4)
    tf.setCellSpacing(0)
    tf.setBorder(1)
    tf.setWidth(QTextLength(QTextLength.PercentageLength, 100))
    table = c.insertTable(len(rows), cols, tf)
    for r, row in enumerate(rows):
        for col in range(cols):
            table.cellAt(r, col).firstCursorPosition().insertText(row[col] if col < len(row) else "")
    c.endEditBlock()
    w.editor.setTextCursor(table.cellAt(0, 0).firstCursorPosition())
    return True


def table_to_text(w, sep: str = "\t") -> bool:
    c = w.editor.textCursor()
    table = c.currentTable()
    if table is None:
        return False
    lines = [sep.join(_cell_text(table, r, col).replace("\n", " ") for col in range(table.columns()))
             for r in range(table.rows())]
    first = table.firstCursorPosition()
    last = table.lastCursorPosition()
    cc = QTextCursor(w.editor.document())
    cc.beginEditBlock()
    start = first.position() - 1
    cc.setPosition(start)
    cc.setPosition(last.position() + 1, QTextCursor.KeepAnchor)
    cc.removeSelectedText()
    cc.insertText("\n".join(lines))
    cc.endEditBlock()
    return True


def distribute_columns(w) -> bool:
    table = w.editor.textCursor().currentTable()
    if table is None:
        return False
    tf = table.format()
    tf.setColumnWidthConstraints([QTextLength(QTextLength.PercentageLength, 100.0 / table.columns())] *
                                 table.columns())
    tf.setWidth(QTextLength(QTextLength.PercentageLength, 100))
    table.setFormat(tf)
    return True


class SortTableDialog(QDialog):
    def __init__(self, parent, columns: int):
        super().__init__(parent)
        self.setWindowTitle(_t("Sort Table"))
        form = QFormLayout(self)
        self.col = QComboBox()
        for i in range(columns):
            self.col.addItem(_t("Column {0}").format(i + 1), i)
        self.desc = QCheckBox(_t("Descending (Z\u2192A, 9\u21921)"))
        self.header = QCheckBox(_t("The first row is a header"))
        self.header.setChecked(True)
        form.addRow(_t("Sort by:"), self.col)
        form.addRow("", self.desc)
        form.addRow("", self.header)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


def sort_table_dialog(w) -> None:
    table = w.editor.textCursor().currentTable()
    if table is None:
        from app.shell.dialogs import show_info

        show_info(w, _t("Sort Table"), _t("Place the cursor in a table first."))
        return
    d = SortTableDialog(w, table.columns())
    if d.exec() == QDialog.Accepted:
        sort_table(w, d.col.currentData(), d.desc.isChecked(), d.header.isChecked())


# ============================================================================ MyOffice AI
def _target(w) -> tuple[QTextCursor, str]:
    """The selection, or (if nothing is selected) the current paragraph."""
    c = w.editor.textCursor()
    if not c.hasSelection():
        c = QTextCursor(c.block())
        c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
    return c, _sel_text(c)


def ai_writer(w) -> None:
    from app.shell.ai_dialogs import AIWriterDialog, ensure_ai

    if not ensure_ai(w):
        return
    c = w.editor.textCursor()
    text = _sel_text(c) if c.hasSelection() else ""
    start, end = c.selectionStart(), c.selectionEnd()

    def use(result: str, mode: str):
        cur = QTextCursor(w.editor.document())
        cur.beginEditBlock()
        if mode == "replace":
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.KeepAnchor)
            cur.insertText(result)
        else:
            cur.setPosition(end)
            cur.movePosition(QTextCursor.EndOfBlock)
            for i, para in enumerate(result.split("\n")):
                cur.insertBlock()
                cur.insertText(para)
        cur.endEditBlock()
    AIWriterDialog(w, text, use).exec()


def ai_grammar(w) -> None:
    from app.shell.ai_dialogs import GrammarDialog

    c = w.editor.textCursor()
    if c.hasSelection():
        start, text = c.selectionStart(), _sel_text(c)
    else:
        start, text = 0, w.editor.document().toPlainText()
    lang = cfg().get("writer.spell_lang", "en_US")

    def apply(chosen):
        doc = w.editor.document()
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        for corr in sorted(chosen, key=lambda x: x.start, reverse=True):
            cur.setPosition(start + corr.start)
            cur.setPosition(start + corr.end, QTextCursor.KeepAnchor)
            probe = QTextCursor(doc)
            probe.setPosition(start + corr.start + (1 if corr.end > corr.start else 0))
            cur.insertText(corr.suggestion, probe.charFormat())
        cur.endEditBlock()
        w.statusChanged.emit(_t("{0} corrections applied").format(len(chosen)))
    GrammarDialog(w, text, apply, SPELL_VARIANT.get(lang, "American English"), lang).exec()


def ai_translate(w) -> None:
    from app.shell.ai_dialogs import TranslateDialog

    c = w.editor.textCursor()
    doc = w.editor.document()
    if c.hasSelection():
        b0, b1 = doc.findBlock(c.selectionStart()), doc.findBlock(c.selectionEnd())
    else:
        b0, b1 = doc.begin(), doc.lastBlock()
    blocks = []
    b = b0
    while b.isValid():
        blocks.append(b)
        if b == b1:
            break
        b = b.next()
    paras = [b.text().replace("\ufffc", "") for b in blocks]

    def use(translated: list[str], mode: str):
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        if mode == "replace":
            for blk, t in reversed(list(zip(blocks, translated))):
                if not blk.text().strip():
                    continue
                bc = QTextCursor(blk)
                fmt = bc.charFormat() if blk.length() <= 1 else QTextCursor(doc.findBlock(blk.position())).charFormat()
                probe = QTextCursor(doc)
                probe.setPosition(blk.position() + 1)
                fmt = probe.charFormat()
                bc.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                bc.insertText(t, fmt)
        else:
            cur.setPosition(blocks[-1].position() + blocks[-1].length() - 1)
            for t in translated:
                cur.insertBlock()
                cur.insertText(t)
        cur.endEditBlock()
    TranslateDialog(w, paras, use).exec()
