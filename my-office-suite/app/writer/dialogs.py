"""Dialogs used by the Writer."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextBlockFormat, QTextCursor, QTextDocument, QTextFormat
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPushButton, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from app.services.i18n import _t
from app.writer.model import PAGE_SIZES, PX_PER_PT, PageSetup


# ============================================================================ find & replace
class FindReplaceDialog(QDialog):
    def __init__(self, writer_doc, replace: bool = False):
        super().__init__(writer_doc)
        self.wd = writer_doc
        self.setWindowTitle(_t("Find and Replace"))
        self.setModal(False)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.find_edit = QLineEdit()
        self.replace_edit = QLineEdit()
        form.addRow(_t("Find what:"), self.find_edit)
        self.replace_label = QLabel(_t("Replace with:"))
        form.addRow(self.replace_label, self.replace_edit)
        lay.addLayout(form)
        opts = QHBoxLayout()
        self.case = QCheckBox(_t("Match case"))
        self.whole = QCheckBox(_t("Whole words"))
        self.regex = QCheckBox(_t("Regular expression"))
        self.back = QCheckBox(_t("Search backward"))
        for w in (self.case, self.whole, self.regex, self.back):
            opts.addWidget(w)
        lay.addLayout(opts)
        btns = QHBoxLayout()
        self.b_next = QPushButton(_t("Find Next"))
        self.b_repl = QPushButton(_t("Replace"))
        self.b_all = QPushButton(_t("Replace All"))
        self.b_close = QPushButton(_t("Close"))
        for b in (self.b_next, self.b_repl, self.b_all, self.b_close):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        lay.addWidget(self.status)
        self.b_next.setDefault(True)
        self.b_next.clicked.connect(self.find_next)
        self.b_repl.clicked.connect(self.replace_one)
        self.b_all.clicked.connect(self.replace_all)
        self.b_close.clicked.connect(self.close)
        self.set_replace_mode(replace)

    def set_replace_mode(self, on: bool):
        for w in (self.replace_label, self.replace_edit, self.b_repl, self.b_all):
            w.setVisible(on)
        self.setWindowTitle(_t("Find and Replace") if on else _t("Find"))

    def _find(self, from_cursor=None):
        text = self.find_edit.text()
        if not text:
            return None
        return self.wd.editor.find(text, self.back.isChecked(), self.case.isChecked(), self.whole.isChecked(),
                                   self.regex.isChecked(), from_cursor)

    def find_next(self):
        res = self._find()
        if res is None:
            self.status.setText(_t("Text not found."))
            return False
        self.wd.editor.setTextCursor(res)
        self.status.setText("")
        return True

    def _matches_selection(self) -> bool:
        cur = self.wd.editor.textCursor()
        if not cur.hasSelection():
            return False
        sel = cur.selectedText()
        text = self.find_edit.text()
        if self.regex.isChecked():
            import re

            flags = 0 if self.case.isChecked() else re.IGNORECASE
            try:
                return re.fullmatch(text, sel, flags) is not None
            except re.error:
                return False
        return sel == text if self.case.isChecked() else sel.lower() == text.lower()

    def replace_one(self):
        if self.wd.read_only:
            return
        if self._matches_selection():
            self.wd.replace_selection(self.replace_edit.text())
        self.find_next()

    def replace_all(self):
        if self.wd.read_only:
            return
        text = self.find_edit.text()
        if not text:
            return
        doc = self.wd.editor.document()
        count = 0
        cur = QTextCursor(doc)
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        flags = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if self.whole.isChecked():
            flags |= QTextDocument.FindWholeWords
        from PySide6.QtCore import QRegularExpression

        pattern = QRegularExpression(text) if self.regex.isChecked() else None
        if pattern is not None and not self.case.isChecked():
            pattern.setPatternOptions(QRegularExpression.CaseInsensitiveOption)
        while True:
            res = doc.find(pattern if pattern is not None else text, cur, flags)
            if res.isNull():
                break
            self.wd.editor.setTextCursor(res)
            end_pos = self.wd.replace_selection(self.replace_edit.text(), in_edit_block=False)
            cur = QTextCursor(doc)
            cur.setPosition(end_pos)
            count += 1
            if count > 100000:
                break
        edit.endEditBlock()
        self.status.setText(_t("{0} replacement(s) made.").format(count))


# ============================================================================ paragraph
class ParagraphDialog(QDialog):
    LINE_CHOICES = [("single", 100), ("1.15", 115), ("1.5", 150), ("double", 200)]

    def __init__(self, parent, bf: QTextBlockFormat):
        super().__init__(parent)
        self.setWindowTitle(_t("Paragraph"))
        lay = QVBoxLayout(self)
        g1 = QGroupBox(_t("General"))
        f1 = QFormLayout(g1)
        self.align = QComboBox()
        for label, val in ((_t("Left / Start"), Qt.AlignLeft), (_t("Centered"), Qt.AlignHCenter),
                           (_t("Right / End"), Qt.AlignRight), (_t("Justified"), Qt.AlignJustify)):
            self.align.addItem(label, int(val.value if hasattr(val, "value") else val))
        cur_al = bf.alignment() & Qt.AlignHorizontal_Mask
        for i in range(self.align.count()):
            if Qt.AlignmentFlag(self.align.itemData(i)) & cur_al:
                self.align.setCurrentIndex(i)
                break
        self.direction = QComboBox()
        self.direction.addItem(_t("Automatic"), int(Qt.LayoutDirectionAuto.value))
        self.direction.addItem(_t("Left-to-right"), int(Qt.LeftToRight.value))
        self.direction.addItem(_t("Right-to-left (Urdu)"), int(Qt.RightToLeft.value))
        self.direction.setCurrentIndex({Qt.LeftToRight: 1, Qt.RightToLeft: 2}.get(bf.layoutDirection(), 0))
        f1.addRow(_t("Alignment:"), self.align)
        f1.addRow(_t("Direction:"), self.direction)
        lay.addWidget(g1)
        g2 = QGroupBox(_t("Indentation (cm)"))
        f2 = QFormLayout(g2)
        cm = 96.0 / 2.54
        self.left = self._spin(bf.leftMargin() / cm, -10, 30)
        self.right = self._spin(bf.rightMargin() / cm, -10, 30)
        self.first = self._spin(bf.textIndent() / cm, -10, 30)
        f2.addRow(_t("Before text:"), self.left)
        f2.addRow(_t("After text:"), self.right)
        f2.addRow(_t("First line (negative = hanging):"), self.first)
        lay.addWidget(g2)
        g3 = QGroupBox(_t("Spacing"))
        f3 = QFormLayout(g3)
        self.before = self._spin(bf.topMargin() / PX_PER_PT, 0, 400, 1, 0)
        self.after = self._spin(bf.bottomMargin() / PX_PER_PT, 0, 400, 1, 0)
        f3.addRow(_t("Before (pt):"), self.before)
        f3.addRow(_t("After (pt):"), self.after)
        self.line = QComboBox()
        self.line.addItems([_t("Single"), "1.15", "1.5", _t("Double"), _t("Multiple"), _t("Exactly (pt)"),
                            _t("At least (pt)")])
        self.line_val = self._spin(1.0, 0.5, 400, 0.1)
        lht = bf.lineHeightType()
        if lht == QTextBlockFormat.FixedHeight.value:
            self.line.setCurrentIndex(5)
            self.line_val.setValue(bf.lineHeight() / PX_PER_PT)
        elif lht == QTextBlockFormat.MinimumHeight.value:
            self.line.setCurrentIndex(6)
            self.line_val.setValue(bf.lineHeight() / PX_PER_PT)
        elif lht == QTextBlockFormat.ProportionalHeight.value:
            pct = bf.lineHeight()
            idx = {100: 0, 115: 1, 150: 2, 200: 3}.get(int(round(pct)), 4)
            self.line.setCurrentIndex(idx)
            self.line_val.setValue(pct / 100.0)
        row = QHBoxLayout()
        row.addWidget(self.line)
        row.addWidget(self.line_val)
        f3.addRow(_t("Line spacing:"), row)
        lay.addWidget(g3)
        self.page_break = QCheckBox(_t("Page break before"))
        self.page_break.setChecked(bool(bf.pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore))
        lay.addWidget(self.page_break)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _spin(value, lo, hi, step=0.1, decimals=2):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setValue(round(value, decimals))
        return s

    def block_format(self) -> QTextBlockFormat:
        bf = QTextBlockFormat()
        bf.setAlignment(Qt.AlignmentFlag(self.align.currentData()))
        bf.setLayoutDirection(Qt.LayoutDirection(self.direction.currentData()))
        cm = 96.0 / 2.54
        bf.setLeftMargin(self.left.value() * cm)
        bf.setRightMargin(self.right.value() * cm)
        bf.setTextIndent(self.first.value() * cm)
        bf.setTopMargin(self.before.value() * PX_PER_PT)
        bf.setBottomMargin(self.after.value() * PX_PER_PT)
        idx = self.line.currentIndex()
        if idx <= 3:
            bf.setLineHeight([100, 115, 150, 200][idx], QTextBlockFormat.ProportionalHeight.value)
        elif idx == 4:
            bf.setLineHeight(self.line_val.value() * 100, QTextBlockFormat.ProportionalHeight.value)
        elif idx == 5:
            bf.setLineHeight(self.line_val.value() * PX_PER_PT, QTextBlockFormat.FixedHeight.value)
        else:
            bf.setLineHeight(self.line_val.value() * PX_PER_PT, QTextBlockFormat.MinimumHeight.value)
        bf.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore if self.page_break.isChecked()
                              else QTextFormat.PageBreak_Auto)
        return bf


# ============================================================================ page setup
class PageSetupDialog(QDialog):
    def __init__(self, parent, page: PageSetup, hf, watermark: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_t("Page Setup"))
        self.page = PageSetup.from_dict(page.to_dict())
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        # --- paper
        w1 = QWidget()
        f1 = QFormLayout(w1)
        self.size = QComboBox()
        for name in PAGE_SIZES:
            self.size.addItem(name, name)
        self.size.addItem(_t("Custom"), "Custom")
        idx = self.size.findData(page.size)
        self.size.setCurrentIndex(idx if idx >= 0 else self.size.count() - 1)
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for s in (self.width, self.height):
            s.setRange(50, 1500)
            s.setSuffix(" mm")
            s.setDecimals(1)
        self.width.setValue(page.width_mm)
        self.height.setValue(page.height_mm)
        self.orient = QComboBox()
        self.orient.addItem(_t("Portrait"), "portrait")
        self.orient.addItem(_t("Landscape"), "landscape")
        self.orient.setCurrentIndex(1 if page.orientation == "landscape" else 0)
        f1.addRow(_t("Paper size:"), self.size)
        f1.addRow(_t("Width:"), self.width)
        f1.addRow(_t("Height:"), self.height)
        f1.addRow(_t("Orientation:"), self.orient)
        self.size.currentIndexChanged.connect(self._size_changed)
        self._size_changed()
        tabs.addTab(w1, _t("Paper"))
        # --- margins
        w2 = QWidget()
        f2 = QFormLayout(w2)
        self.margins = []
        for label, val in zip((_t("Top:"), _t("Right:"), _t("Bottom:"), _t("Left:")), page.margins_mm):
            s = QDoubleSpinBox()
            s.setRange(0, 200)
            s.setSuffix(" mm")
            s.setDecimals(1)
            s.setValue(val)
            f2.addRow(label, s)
            self.margins.append(s)
        self.header_dist = QDoubleSpinBox()
        self.footer_dist = QDoubleSpinBox()
        for s, v in ((self.header_dist, page.header_mm), (self.footer_dist, page.footer_mm)):
            s.setRange(0, 100)
            s.setSuffix(" mm")
            s.setValue(v)
        f2.addRow(_t("Header from edge:"), self.header_dist)
        f2.addRow(_t("Footer from edge:"), self.footer_dist)
        tabs.addTab(w2, _t("Margins"))
        # --- header/footer
        w3 = QWidget()
        f3 = QFormLayout(w3)
        self.header = QLineEdit(hf.header)
        self.footer = QLineEdit(hf.footer)
        self.header_align = QComboBox()
        self.footer_align = QComboBox()
        for cb, cur in ((self.header_align, hf.header_align), (self.footer_align, hf.footer_align)):
            for label, val in ((_t("Left"), "left"), (_t("Center"), "center"), (_t("Right"), "right")):
                cb.addItem(label, val)
            cb.setCurrentIndex(max(0, cb.findData(cur)))
        f3.addRow(_t("Header text:"), self.header)
        f3.addRow(_t("Header alignment:"), self.header_align)
        f3.addRow(_t("Footer text:"), self.footer)
        f3.addRow(_t("Footer alignment:"), self.footer_align)
        hint = QLabel(_t("Fields: {PAGE} page number, {NUMPAGES} total pages, {DATE} today, {TITLE} document title"))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        f3.addRow(hint)
        tabs.addTab(w3, _t("Header && Footer"))
        # --- watermark
        w4 = QWidget()
        f4 = QFormLayout(w4)
        self.watermark = QComboBox()
        self.watermark.setEditable(True)
        self.watermark.addItems(["", "CONFIDENTIAL", "DRAFT", "DO NOT COPY", "SAMPLE", "خفیہ"])
        self.watermark.setCurrentText(watermark)
        f4.addRow(_t("Watermark text:"), self.watermark)
        tabs.addTab(w4, _t("Watermark"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def select_tab(self, index: int):
        self.findChild(QTabWidget).setCurrentIndex(index)

    def _size_changed(self):
        name = self.size.currentData()
        custom = name == "Custom"
        self.width.setEnabled(custom)
        self.height.setEnabled(custom)
        if not custom:
            w, h = PAGE_SIZES[name]
            self.width.setValue(w)
            self.height.setValue(h)

    def result_values(self):
        p = self.page
        p.size = self.size.currentData()
        p.width_mm = min(self.width.value(), self.height.value())
        p.height_mm = max(self.width.value(), self.height.value())
        p.orientation = self.orient.currentData()
        p.margins_mm = [s.value() for s in self.margins]
        p.header_mm = self.header_dist.value()
        p.footer_mm = self.footer_dist.value()
        hf = {"header": self.header.text(), "footer": self.footer.text(),
              "header_align": self.header_align.currentData(), "footer_align": self.footer_align.currentData()}
        return p, hf, self.watermark.currentText().strip()


# ============================================================================ table / link / image
class InsertTableDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Insert Table"))
        f = QFormLayout(self)
        self.rows = QSpinBox()
        self.rows.setRange(1, 500)
        self.rows.setValue(3)
        self.cols = QSpinBox()
        self.cols.setRange(1, 63)
        self.cols.setValue(3)
        self.header = QCheckBox(_t("Header row (bold, shaded)"))
        self.header.setChecked(True)
        self.border = QDoubleSpinBox()
        self.border.setRange(0, 6)
        self.border.setValue(1)
        f.addRow(_t("Rows:"), self.rows)
        f.addRow(_t("Columns:"), self.cols)
        f.addRow(_t("Border width:"), self.border)
        f.addRow(self.header)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class HyperlinkDialog(QDialog):
    def __init__(self, parent, text: str = "", url: str = "", bookmarks: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Hyperlink"))
        f = QFormLayout(self)
        self.text = QLineEdit(text)
        self.url = QComboBox()
        self.url.setEditable(True)
        self.url.addItem(url)
        for b in bookmarks or []:
            self.url.addItem("#" + b)
        self.url.setCurrentText(url)
        f.addRow(_t("Text to display:"), self.text)
        f.addRow(_t("Address (URL, mailto: or #bookmark):"), self.url)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def values(self):
        url = self.url.currentText().strip()
        if url and "://" not in url and not url.startswith(("#", "mailto:", "file:")):
            url = ("mailto:" + url) if "@" in url and " " not in url else ("https://" + url)
        return self.text.text().strip() or url, url


class ImageSizeDialog(QDialog):
    def __init__(self, parent, width: float, height: float, natural: tuple[int, int]):
        super().__init__(parent)
        self.setWindowTitle(_t("Picture Size"))
        self._ratio = (height / width) if width else 1.0
        f = QFormLayout(self)
        self.w = QDoubleSpinBox()
        self.h = QDoubleSpinBox()
        for s, v in ((self.w, width), (self.h, height)):
            s.setRange(4, 5000)
            s.setSuffix(" px")
            s.setValue(v)
        self.keep = QCheckBox(_t("Lock aspect ratio"))
        self.keep.setChecked(True)
        self.w.valueChanged.connect(lambda v: self.keep.isChecked() and self._sync(self.h, v * self._ratio))
        self.h.valueChanged.connect(lambda v: self.keep.isChecked() and self._sync(self.w, v / self._ratio))
        reset = QPushButton(_t("Original size ({0} \u00d7 {1})").format(*natural))
        reset.clicked.connect(lambda: (self.w.setValue(natural[0]), self.h.setValue(natural[1])))
        f.addRow(_t("Width:"), self.w)
        f.addRow(_t("Height:"), self.h)
        f.addRow(self.keep)
        f.addRow(reset)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _sync(self, spin, value):
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)


# ============================================================================ word count / symbols / date
class WordCountDialog(QDialog):
    def __init__(self, parent, stats: dict, selection_stats: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Word Count"))
        g = QGridLayout(self)
        rows = [(_t("Pages"), "pages"), (_t("Words"), "words"), (_t("Characters (no spaces)"), "characters_no_spaces"),
                (_t("Characters (with spaces)"), "characters"), (_t("Paragraphs"), "paragraphs")]
        g.addWidget(QLabel("<b>%s</b>" % _t("Document")), 0, 1)
        if selection_stats:
            g.addWidget(QLabel("<b>%s</b>" % _t("Selection")), 0, 2)
        for i, (label, key) in enumerate(rows, start=1):
            g.addWidget(QLabel(label), i, 0)
            g.addWidget(QLabel(f"{stats.get(key, 0):,}"), i, 1)
            if selection_stats:
                g.addWidget(QLabel(f"{selection_stats.get(key, 0):,}" if key != "pages" else "-"), i, 2)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        g.addWidget(bb, len(rows) + 1, 0, 1, 3)


SYMBOLS = ("© ® ™ § ¶ † ‡ • … ‰ ′ ″ € £ ¥ ¢ ₹ ₨ ° ± × ÷ ≠ ≈ ≤ ≥ ∞ √ ∑ ∏ ∫ ∂ ∆ π Ω µ α β γ δ θ λ σ φ ψ ω "
           "← → ↑ ↓ ↔ ⇒ ⇔ ✓ ✗ ★ ☆ ♠ ♣ ♥ ♦ ☎ ✉ ☐ ☑ ½ ¼ ¾ ¹ ² ³ "
           "ﷺ ؏ ؁ ؂ ؃ ﷽ ۔ ، ؛ ؟ ٪ ٫ ٬ ۰ ۱ ۲ ۳ ۴ ۵ ۶ ۷ ۸ ۹ ॐ । ॥ ₹").split()


class SymbolDialog(QDialog):
    def __init__(self, parent, urdu_font: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Insert Symbol"))
        self.chosen = ""
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        cols = 14
        for i, s in enumerate(SYMBOLS):
            b = QToolButton()
            b.setText(s)
            b.setFixedSize(34, 34)
            f = b.font()
            f.setPointSize(13)
            if "\u0600" <= s <= "\uFDFF":
                f.setFamily(urdu_font)
            b.setFont(f)
            b.clicked.connect(lambda _=False, ch=s: self._pick(ch))
            grid.addWidget(b, i // cols, i % cols)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(QLabel(_t("Unicode (hex):")))
        self.code = QLineEdit()
        self.code.setPlaceholderText("e.g. 2665")
        row.addWidget(self.code)
        ins = QPushButton(_t("Insert"))
        ins.clicked.connect(self._insert_code)
        row.addWidget(ins)
        lay.addLayout(row)

    def _pick(self, ch):
        self.chosen = ch
        self.accept()

    def _insert_code(self):
        try:
            self.chosen = chr(int(self.code.text().strip().lower().replace("u+", ""), 16))
            self.accept()
        except (ValueError, OverflowError):
            self.code.selectAll()


class DateTimeDialog(QDialog):
    FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%A, %d %B %Y", "%B %d, %Y", "%d-%b-%Y",
               "%H:%M", "%I:%M %p", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S"]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Date and Time"))
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        now = datetime.now()
        for f in self.FORMATS:
            self.list.addItem(now.strftime(f))
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def value(self) -> str:
        it = self.list.currentItem()
        return it.text() if it else ""

