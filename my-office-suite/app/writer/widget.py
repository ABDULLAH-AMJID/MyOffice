"""The Writer document tab (word processor)."""
from __future__ import annotations

import base64
import logging
import os
import re
import zipfile
from pathlib import Path

from PySide6.QtCore import QSizeF, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QKeySequence, QTextBlockFormat, QTextCharFormat,
                           QTextCursor, QTextDocumentWriter, QTextFormat, QTextLength, QTextListFormat, QTextOption,
                           QTextTableFormat, QTextFrameFormat)
from PySide6.QtWidgets import (QApplication, QComboBox, QFontComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from app.services.config import cfg
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_file, handle_exception, show_error, show_info
from app.shell.document import DocumentError, DocumentWidget
from app.shell.icons import icon
from app.shell.ribbon import Ribbon
from app.shell.widgets import ColorMenu, TableGridPicker, ZoomSlider
from app.writer import citations, render
from app.writer.dialogs import (DateTimeDialog, FindReplaceDialog, HyperlinkDialog, ImageSizeDialog,
                                InsertTableDialog, PageSetupDialog, ParagraphDialog, SymbolDialog, WordCountDialog)
from app.writer.docx_io import load_docx, save_docx
from app.writer.editor import WriterEditor
from app.writer import features
from app.writer.model import (P_BIBLIO, P_CITE, P_COMMENTS, P_DEL, P_EQUATION, P_INS, P_STYLE, P_TOC, PX_PER_PT,
                              STYLE_NAMES,
                              Comment, HeaderFooter, PageSetup, apply_style, count_words, style_for_heading)
from app.writer.revisions import (add_comment_anchor, anchored_comment_ids, comment_range, new_comment_id,
                                  remove_comment_anchor)

log = logging.getLogger(__name__)

DOCX_EXTS = (".docx", ".docm", ".dotx", ".dotm")
CONVERT_EXTS = (".doc", ".rtf", ".odt", ".wps", ".dot", ".fodt", ".sxw", ".xml")
OPEN_FILTER = ("Word documents (*.docx *.docm *.dotx *.dotm *.wpt *.doc *.dot *.rtf *.odt *.wps);;"
               "Text & web (*.txt *.md *.markdown *.html *.htm);;All files (*)")
PRESERVE_PROPS = (P_INS, P_DEL, P_COMMENTS, P_CITE)


def _selected_blocks(cursor: QTextCursor):
    doc = cursor.document()
    block = doc.findBlock(cursor.selectionStart())
    end = cursor.selectionEnd()
    while block.isValid():
        yield block
        if block.position() + block.length() > end:
            break
        block = block.next()


class CommentsPanel(QWidget):
    def __init__(self, wd: "WriterDocument"):
        super().__init__(wd)
        self.wd = wd
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        title = QLabel(_t("Comments"))
        title.setObjectName("H2")
        v.addWidget(title)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemClicked.connect(self._goto)
        self.list.itemDoubleClicked.connect(self._edit)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in ((_t("New"), wd.add_comment), (_t("Edit"), lambda: self._edit(self.list.currentItem())),
                         (_t("Delete"), wd.delete_comment)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)

    def refresh(self):
        self.list.clear()
        doc = self.wd.editor.document()
        anchored = anchored_comment_ids(doc)
        ordered = [cid for cid in anchored if cid in self.wd.editor.comments]
        ordered += [cid for cid in self.wd.editor.comments if cid not in ordered]
        for cid in ordered:
            c = self.wd.editor.comments[cid]
            date = (c.date or "")[:16].replace("T", " ")
            it = QListWidgetItem(f"{c.author} \u2022 {date}\n{c.text}")
            it.setData(Qt.UserRole, cid)
            if cid not in anchored:
                it.setForeground(QColor("#9aa0a6"))
            self.list.addItem(it)

    def current_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _goto(self, it):
        if not it:
            return
        rng = comment_range(self.wd.editor.document(), it.data(Qt.UserRole))
        if rng:
            c = self.wd.editor.textCursor()
            c.setPosition(rng[0])
            c.setPosition(rng[1], QTextCursor.KeepAnchor)
            self.wd.editor.setTextCursor(c)

    def _edit(self, it):
        if not it:
            return
        cid = it.data(Qt.UserRole)
        c = self.wd.editor.comments.get(cid)
        if c is None:
            return
        text, ok = QInputDialog.getMultiLineText(self, _t("Edit Comment"), _t("Comment:"), c.text)
        if ok:
            c.text = text
            self.wd.set_modified(True)
            self.refresh()


class WriterDocument(DocumentWidget):
    kind = "writer"
    default_ext = ".docx"
    writable_exts = (".docx", ".wpt", ".odt", ".html", ".htm", ".txt", ".md")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sources: list[dict] = []
        self.citation_style = cfg().get("writer.citation_style", "APA")
        self.title_text = ""
        self.find_dialog: FindReplaceDialog | None = None
        self._painter_fmt: QTextCharFormat | None = None
        self.collab_session = None

        self.editor = WriterEditor(self)
        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.itemClicked.connect(self._nav_goto)
        self.nav.setMinimumWidth(180)
        self.comments_panel = CommentsPanel(self)
        self.comments_panel.setMinimumWidth(220)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.nav)
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.comments_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 900, 260])
        self.nav.hide()
        self.comments_panel.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.splitter, 1)
        strip = QWidget()
        strip.setObjectName("StatusStrip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(8, 1, 8, 1)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        sl.addWidget(self.info, 1)
        self.zoom_slider = ZoomSlider()
        self.zoom_slider.zoomRequested.connect(self.editor.set_zoom)
        self.editor.zoomChanged.connect(self.zoom_slider.set_zoom)
        sl.addWidget(self.zoom_slider)
        lay.addWidget(strip)

        doc = self.editor.document()
        doc.modificationChanged.connect(self._doc_modified)
        doc.contentsChanged.connect(self._schedule_refresh)
        self.editor.cursorChanged.connect(self._cursor_changed)
        self.editor.pageCountChanged.connect(lambda *_: self._update_info())
        self.editor.contextMenuRequested.connect(self._context_menu)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._deferred_refresh)

        self.protection: dict = {}
        self._build_actions()
        self.ribbon = self._build_ribbon()
        features.install_spelling(self)
        self._update_info()
        self._cursor_changed()
        QTimer.singleShot(0, self.editor.fit_width if cfg().get("writer.fit_width", False) else (lambda: None))

    # ================================================================== DocumentWidget API
    def untitled_label(self) -> str:
        return _t("Document")

    def ribbon_widget(self):
        return self.ribbon

    def save_filters(self) -> str:
        return ("Word Document (*.docx);;Writer Template (*.wpt);;OpenDocument Text (*.odt);;"
                "Web Page (*.html *.htm);;Markdown (*.md);;Plain Text (*.txt)")

    @staticmethod
    def open_filter() -> str:
        return OPEN_FILTER

    def set_modified(self, value: bool = True) -> None:
        if not value:
            self.editor.document().setModified(False)
        super().set_modified(value)

    def _doc_modified(self, m: bool):
        if m:
            super().set_modified(True)

    def load(self, path: str) -> None:
        ext = Path(path).suffix.lower()
        src = path
        if ext == ".wpt" and not zipfile.is_zipfile(path):
            ext = ".doc"
        if ext in CONVERT_EXTS:
            from app.services.office_convert import ConversionError, convert
            from app.shell.tasks import TaskError, run_task

            try:
                src = run_task(self, _t("Converting document\u2026"), convert, path, "docx")
            except TaskError as exc:
                raise DocumentError(str(exc.original)) from exc
            except ConversionError as exc:
                raise DocumentError(str(exc)) from exc
            ext = ".docx"
        ed = self.editor
        doc = ed.document()
        ed.highlighter.setDocument(None)
        doc.setUndoRedoEnabled(False)
        try:
            if ext in DOCX_EXTS or ext == ".wpt":
                ed.image_store.clear()
                imp = load_docx(src, doc, ed.register_image)
                ed.comments = dict(imp.comments)
                ed.hf = imp.hf
                ed.watermark = imp.watermark
                self.sources = imp.sources
                self.citation_style = imp.citation_style or self.citation_style
                self.title_text = imp.title
                self.protection = dict(imp.protection)
                ed.apply_page_setup(imp.page)
            elif ext in (".txt", ".csv", ".log", ".md", ".markdown", ".html", ".htm"):
                with open(src, "rb") as fh:
                    raw = fh.read()
                from app.writer.mailmerge import _decode

                text = _decode(raw)
                doc.clear()
                if ext in (".html", ".htm"):
                    doc.setHtml(text)
                elif ext in (".md", ".markdown"):
                    doc.setMarkdown(text)
                else:
                    doc.setPlainText(text)
                ed.apply_page_setup(PageSetup())
            else:
                raise DocumentError(_t("Unsupported file type: {0}").format(ext or path))
        finally:
            doc.setUndoRedoEnabled(True)
            ed.highlighter.setDocument(doc)
        doc.clearUndoRedoStacks()
        ed.title = self.title_text or Path(path).stem
        self.path = Path(path)
        self.read_only = ext not in self.writable_exts
        doc.setModified(False)
        super().set_modified(False)
        c = QTextCursor(doc)
        ed.setTextCursor(c)
        self.cite_style_combo.setCurrentText(self.citation_style)
        if ed.comments:
            self.comments_panel.show()
        if self.protection:
            ed.set_read_only(True)
            self.a_protect.setChecked(True)
            self.statusChanged.emit(_t("This document is protected: editing is restricted."))
        ed.update_notes_reserve()
        self._deferred_refresh()
        self.titleChanged.emit()

    def save_to(self, path: str) -> None:
        ext = Path(path).suffix.lower()
        ed = self.editor
        doc = ed.document()
        author = cfg().get("author", "")
        title = self.title_text or Path(path).stem
        if ext in (".docx", ".wpt", ".dotx", ".docm"):
            save_docx(path, doc, ed.page, ed.hf, ed.comments, ed.image_bytes, title=title, author=author,
                      sources=self.sources, citation_style=self.citation_style, watermark=ed.watermark,
                      protection=self.protection)
        elif ext == ".odt":
            clone = render.prepare_clone(doc, ed.page)
            w = QTextDocumentWriter(path, b"odf")
            if not w.write(clone):
                raise DocumentError(_t("Could not write ODT file: {0}").format(path))
        elif ext in (".html", ".htm"):
            clone = render.prepare_clone(doc, ed.page)
            html = clone.toHtml()
            for name, (data, iext) in ed.image_store.items():
                mime = "image/jpeg" if iext in ("jpg", "jpeg") else f"image/{iext}"
                uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
                html = html.replace(f'src="{name}"', f'src="{uri}"')
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
        elif ext == ".md":
            clone = render.prepare_clone(doc, ed.page)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(clone.toMarkdown())
        elif ext == ".txt":
            clone = render.prepare_clone(doc, ed.page)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(clone.toPlainText().replace("\u2028", "\n"))
        elif ext == ".pdf":
            self.export_pdf_to(path)
        else:
            raise DocumentError(_t("Cannot save as {0}").format(ext))

    def export_pdf_to(self, path: str) -> None:
        ed = self.editor
        render.export_pdf(ed.document(), ed.page, ed.hf, path, title=self.title_text or self.display_name(),
                          author=cfg().get("author", ""), watermark=ed.watermark)

    def print_document(self) -> None:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(self.display_name())
        dlg = QPrintDialog(printer, self)
        dlg.setOption(QPrintDialog.PrintPageRange, True)
        dlg.setMinMax(1, self.editor.page_count())
        if dlg.exec():
            try:
                render.print_document(self.editor.document(), self.editor.page, self.editor.hf, printer,
                                      self.display_name(), self.editor.watermark)
            except Exception as exc:
                handle_exception(self, _t("Print"), exc)

    def print_preview(self) -> None:
        from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog

        printer = QPrinter(QPrinter.HighResolution)
        dlg = QPrintPreviewDialog(printer, self)
        dlg.setWindowTitle(_t("Print Preview"))
        dlg.paintRequested.connect(lambda p: render.print_document(
            self.editor.document(), self.editor.page, self.editor.hf, p, self.display_name(), self.editor.watermark))
        dlg.resize(900, 760)
        dlg.exec()

    def status_text(self) -> str:
        ed = self.editor
        words = count_words(ed.document().toPlainText())
        parts = [_t("Page {0} of {1}").format(ed.current_page(), ed.page_count()), _t("Words: {0}").format(words)]
        cur = ed.textCursor()
        if cur.hasSelection():
            parts.append(_t("Selected: {0}").format(count_words(cur.selectedText())))
        rtl = cur.block().textDirection() == Qt.RightToLeft
        parts.append(_t("Urdu (RTL)") if rtl else _t("English (LTR)"))
        if ed.track_changes:
            parts.append(_t("Track Changes: On"))
        if self.read_only:
            parts.append(_t("Read-only"))
        return "   |   ".join(parts)

    # ================================================================== actions
    def _act(self, text, icon_name=None, slot=None, shortcut=None, checkable=False, tip=None) -> QAction:
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        a.setCheckable(checkable)
        a.setToolTip((tip or text.replace("&", "")) + (f" ({QKeySequence(shortcut).toString(QKeySequence.NativeText)})" if shortcut else ""))
        if slot is not None:
            a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        ed = self.editor
        # clipboard / edit
        self.a_undo = A(_t("Undo"), "undo", ed.undo, "Ctrl+Z")
        self.a_redo = A(_t("Redo"), "redo", ed.redo, "Ctrl+Y")
        self.a_cut = A(_t("Cut"), "cut", ed.cut)
        self.a_copy = A(_t("Copy"), "copy", ed.copy)
        self.a_paste = A(_t("Paste"), "paste", ed.paste)
        self.a_paste_plain = A(_t("Paste as Plain Text"), "text_insert", self.paste_plain, "Ctrl+Shift+V")
        m = QMenu(self)
        m.addAction(self.a_paste)
        m.addAction(self.a_paste_plain)
        self.a_paste.setMenu(m)
        self.a_painter = A(_t("Format Painter"), "edit", self.format_painter, checkable=True)
        # font
        self.a_bold = A(_t("Bold"), "bold", self.toggle_bold, "Ctrl+B", True)
        self.a_italic = A(_t("Italic"), "italic", self.toggle_italic, "Ctrl+I", True)
        self.a_underline = A(_t("Underline"), "underline", self.toggle_underline, "Ctrl+U", True)
        self.a_strike = A(_t("Strikethrough"), "strike", self.toggle_strike, checkable=True)
        self.a_super = A(_t("Superscript"), "superscript", lambda c: self.set_valign(c, True), "Ctrl+Shift+=", True)
        self.a_sub = A(_t("Subscript"), "subscript", lambda c: self.set_valign(c, False), "Ctrl+=", True)
        self.a_grow = A(_t("Grow Font"), "plus", lambda: self.step_font(1), "Ctrl+]")
        self.a_shrink = A(_t("Shrink Font"), "minus", lambda: self.step_font(-1), "Ctrl+[")
        self.a_clear = A(_t("Clear Formatting"), "close", self.clear_formatting)
        self.a_color = A(_t("Font Color"), "font_color", lambda: self.set_color(self._last_color))
        self._last_color = QColor("#c00000")
        cm = ColorMenu(self, _t("Automatic"))
        cm.colorPicked.connect(self.set_color)
        self.a_color.setMenu(cm)
        self.a_highlight = A(_t("Highlight"), "highlight", lambda: self.set_highlight(self._last_highlight))
        self._last_highlight = QColor("#ffff00")
        hm = ColorMenu(self, _t("No Color"))
        hm.colorPicked.connect(self.set_highlight)
        self.a_highlight.setMenu(hm)
        # paragraph
        self.a_bullets = A(_t("Bullets"), "bullets", lambda: self.toggle_list(QTextListFormat.ListDisc))
        bm = QMenu(self)
        for label, st in ((_t("\u2022 Disc"), QTextListFormat.ListDisc), (_t("\u25e6 Circle"), QTextListFormat.ListCircle),
                          (_t("\u25aa Square"), QTextListFormat.ListSquare)):
            bm.addAction(label, lambda s=st: self.toggle_list(s))
        self.a_bullets.setMenu(bm)
        self.a_numbering = A(_t("Numbering"), "numbering", lambda: self.toggle_list(QTextListFormat.ListDecimal))
        nm = QMenu(self)
        for label, st in (("1. 2. 3.", QTextListFormat.ListDecimal), ("a. b. c.", QTextListFormat.ListLowerAlpha),
                          ("A. B. C.", QTextListFormat.ListUpperAlpha), ("i. ii. iii.", QTextListFormat.ListLowerRoman),
                          ("I. II. III.", QTextListFormat.ListUpperRoman)):
            nm.addAction(label, lambda s=st: self.toggle_list(s))
        self.a_numbering.setMenu(nm)
        self.a_indent_less = A(_t("Decrease Indent"), "indent_less", lambda: self.change_indent(-1))
        self.a_indent_more = A(_t("Increase Indent"), "indent_more", lambda: self.change_indent(1))
        self.align_group = QActionGroup(self)
        self.a_left = A(_t("Align Left"), "align_left", lambda: self.set_alignment("left"), "Ctrl+L", True)
        self.a_center = A(_t("Center"), "align_center", lambda: self.set_alignment("center"), "Ctrl+E", True)
        self.a_right = A(_t("Align Right"), "align_right", lambda: self.set_alignment("right"), "Ctrl+R", True)
        self.a_justify = A(_t("Justify"), "align_justify", lambda: self.set_alignment("justify"), "Ctrl+J", True)
        for a in (self.a_left, self.a_center, self.a_right, self.a_justify):
            self.align_group.addAction(a)
        self.a_ltr = A(_t("Left-to-Right"), "ltr", lambda: self.set_direction(Qt.LeftToRight))
        self.a_rtl = A(_t("Right-to-Left"), "rtl", lambda: self.set_direction(Qt.RightToLeft))
        self.a_spacing = A(_t("Line Spacing"), "rows", self.paragraph_dialog)
        sm = QMenu(self)
        for v in (100, 115, 150, 200, 250, 300):
            sm.addAction(f"{v / 100:.2f}".rstrip("0").rstrip("."), lambda val=v: self.set_line_spacing(val))
        sm.addSeparator()
        sm.addAction(_t("Paragraph Options\u2026"), self.paragraph_dialog)
        self.a_spacing.setMenu(sm)
        self.a_paragraph = A(_t("Paragraph\u2026"), "settings", self.paragraph_dialog)
        self.a_marks = A(_t("Show/Hide \u00b6"), "text_insert", self.toggle_marks, "Ctrl+Shift+8", True)
        # editing
        self.a_find = A(_t("Find"), "find", lambda: self.show_find(False), "Ctrl+F")
        self.a_replace = A(_t("Replace"), "replace", lambda: self.show_find(True), "Ctrl+H")
        self.a_select_all = A(_t("Select All"), "select", ed.select_all)
        # insert
        self.a_page_break = A(_t("Page Break"), "page_break", self.insert_page_break, "Ctrl+Return")
        self.a_table = A(_t("Table"), "table", self.insert_table_dialog)
        tp = TableGridPicker(self)
        tp.picked.connect(lambda r, c: self.insert_table(r, c))
        tp.addSeparator()
        tp.addAction(_t("Insert Table\u2026"), self.insert_table_dialog)
        self.a_table.setMenu(tp)
        self.a_picture = A(_t("Picture"), "image", self.insert_picture)
        self.a_screenshot = A(_t("Screenshot"), "capture", self.insert_screenshot)
        self.a_link = A(_t("Hyperlink"), "link", self.insert_link, "Ctrl+K")
        self.a_unlink = A(_t("Remove Hyperlink"), "link_off", self.remove_link)
        self.a_bookmark = A(_t("Bookmark"), "book", self.insert_bookmark)
        self.a_header = A(_t("Header"), "header", lambda: self.page_setup_dialog(2))
        self.a_footer = A(_t("Footer"), "footer", lambda: self.page_setup_dialog(2))
        self.a_page_numbers = A(_t("Page Numbers"), "page_numbers", lambda: self.set_page_numbers("footer", "center"))
        pm = QMenu(self)
        pm.addAction(_t("Bottom center: Page X of Y"), lambda: self.set_page_numbers("footer", "center"))
        pm.addAction(_t("Bottom right: X"), lambda: self.set_page_numbers("footer", "right", simple=True))
        pm.addAction(_t("Top right: X"), lambda: self.set_page_numbers("header", "right", simple=True))
        pm.addSeparator()
        pm.addAction(_t("Remove Page Numbers"), self.remove_page_numbers)
        self.a_page_numbers.setMenu(pm)
        self.a_date = A(_t("Date && Time"), "date", self.insert_date)
        self.a_symbol = A(_t("Symbol"), "symbol", self.insert_symbol)
        self.a_hr = A(_t("Horizontal Line"), "hr", self.insert_hr)
        self.a_text_file = A(_t("Text from File"), "open", self.insert_text_from_file)
        self.a_caption = A(_t("Insert Caption"), "textbox", self.insert_caption)
        # layout
        self.a_margins = A(_t("Margins"), "layout", lambda: self.page_setup_dialog(1))
        mm = QMenu(self)
        for label, vals in ((_t("Normal (2.54 cm)"), [25.4] * 4), (_t("Narrow (1.27 cm)"), [12.7] * 4),
                            (_t("Moderate"), [25.4, 19.05, 25.4, 19.05]), (_t("Wide"), [25.4, 50.8, 25.4, 50.8])):
            mm.addAction(label, lambda v=vals: self.set_margins(v))
        mm.addSeparator()
        mm.addAction(_t("Custom Margins\u2026"), lambda: self.page_setup_dialog(1))
        self.a_margins.setMenu(mm)
        self.a_orientation = A(_t("Orientation"), "rotate", self.toggle_orientation)
        om = QMenu(self)
        om.addAction(_t("Portrait"), lambda: self.set_orientation("portrait"))
        om.addAction(_t("Landscape"), lambda: self.set_orientation("landscape"))
        self.a_orientation.setMenu(om)
        self.a_size = A(_t("Size"), "page_setup", lambda: self.page_setup_dialog(0))
        szm = QMenu(self)
        from app.writer.model import PAGE_SIZES

        for name in PAGE_SIZES:
            szm.addAction(name, lambda n=name: self.set_page_size(n))
        self.a_size.setMenu(szm)
        self.a_page_setup = A(_t("Page Setup\u2026"), "page_setup", lambda: self.page_setup_dialog(0))
        self.a_watermark = A(_t("Watermark"), "watermark", lambda: self.page_setup_dialog(3))
        # references
        self.a_toc = A(_t("Table of Contents"), "toc", self.insert_toc)
        self.a_toc_update = A(_t("Update Table"), "refresh", self.update_toc)
        self.a_cite = A(_t("Insert Citation"), "quote", self.insert_citation)
        self.a_sources = A(_t("Manage Sources"), "book", self.manage_sources)
        self.a_biblio = A(_t("Bibliography"), "notes", self.insert_bibliography)
        self.a_cite_update = A(_t("Update Citations"), "refresh", self.update_citations)
        # review
        self.a_word_count = A(_t("Word Count"), "word_count", self.word_count)
        self.a_track = A(_t("Track Changes"), "track", self.toggle_track, "Ctrl+Shift+E", True)
        self.a_accept = A(_t("Accept"), "accept", self.accept_change)
        am = QMenu(self)
        am.addAction(_t("Accept This Change"), self.accept_change)
        am.addAction(_t("Accept All Changes"), self.accept_all)
        self.a_accept.setMenu(am)
        self.a_reject = A(_t("Reject"), "reject", self.reject_change)
        rm = QMenu(self)
        rm.addAction(_t("Reject This Change"), self.reject_change)
        rm.addAction(_t("Reject All Changes"), self.reject_all)
        self.a_reject.setMenu(rm)
        self.a_prev_change = A(_t("Previous"), "prev", lambda: self.goto_change(-1))
        self.a_next_change = A(_t("Next"), "next", lambda: self.goto_change(1))
        self.a_comment = A(_t("New Comment"), "comment", self.add_comment, "Ctrl+Alt+M")
        self.a_del_comment = A(_t("Delete Comment"), "delete", self.delete_comment)
        self.a_comments_pane = A(_t("Comments Pane"), "comment_list", self.toggle_comments_pane, checkable=True)
        self.a_compare = A(_t("Compare"), "compare", self.compare_documents)
        self.a_protect = A(_t("Restrict Editing"), "lock", self.toggle_protect, checkable=True)
        # spelling, footnotes, equations, documents, tables, AI
        self.a_spell = A(_t("Spelling"), "spell", lambda: features.SpellDialog(self).exec(), "F7")
        self.a_spell_auto = A(_t("Check as You Type"), "check", lambda on: features.set_spelling(self, on),
                              checkable=True)
        self.a_spell_auto.setChecked(cfg().get("writer.spell_as_you_type", True))
        self.spell_lang_menu = QMenu(_t("Dictionary Language"), self)
        for code, (label, _x) in features.spell.LANGS.items():
            act = self.spell_lang_menu.addAction(label, lambda c=code: features.set_spell_language(self, c))
            act.setCheckable(True)
            act.setChecked(cfg().get("writer.spell_lang", "en_US") == code)
        self.spell_lang_menu.aboutToShow.connect(self._sync_spell_menu)
        self.a_spell_lang = A(_t("Language"), "language", None)
        self.a_spell_lang.setMenu(self.spell_lang_menu)
        self.a_footnote = A(_t("Insert Footnote"), "footnote", lambda: features.insert_footnote(self), "Ctrl+Alt+F")
        self.a_next_footnote = A(_t("Next Footnote"), "next", lambda: features.goto_next_footnote(self))
        self.a_equation = A(_t("Equation"), "equation", lambda: features.insert_equation(self), "Alt+=")
        self.a_merge_docs = A(_t("Merge Documents"), "merge_pdf", lambda: features.merge_documents(self))
        self.a_split_doc = A(_t("Split Document"), "split_pdf", lambda: features.split_document(self))
        self.table_style_menu = QMenu(_t("Table Style"), self)
        for name in features.TABLE_STYLES:
            self.table_style_menu.addAction(_t(name), lambda n=name: self._table_style(n))
        self.a_table_style = A(_t("Table Style"), "table", None)
        self.a_table_style.setMenu(self.table_style_menu)
        self.a_sort_table = A(_t("Sort Table"), "sort", lambda: features.sort_table_dialog(self))
        self.a_text_to_table = A(_t("Text to Table"), "table", lambda: features.text_to_table(self) or
                                 self.statusChanged.emit(_t("Select tab-separated lines first.")))
        self.a_table_to_text = A(_t("Table to Text"), "text_insert", lambda: features.table_to_text(self))
        self.a_distribute = A(_t("Distribute Columns"), "fit_width", lambda: features.distribute_columns(self))
        self.a_ai_write = A(_t("AI Writer"), "ai", lambda: features.ai_writer(self), "Ctrl+J")
        self.a_ai_grammar = A(_t("AI Spell Check"), "spell", lambda: features.ai_grammar(self))
        self.a_ai_translate = A(_t("Parallel Translate"), "translate", lambda: features.ai_translate(self))
        self.a_ai_settings = A(_t("AI Settings"), "settings", self._ai_settings)
        # view
        self.a_zoom_in = A(_t("Zoom In"), "zoom_in", ed.zoom_in, "Ctrl++")
        self.a_zoom_out = A(_t("Zoom Out"), "zoom_out", ed.zoom_out, "Ctrl+-")
        self.a_zoom_100 = A(_t("100%"), "zoom_in", lambda: ed.set_zoom(1.0), "Ctrl+0")
        self.a_fit_width = A(_t("Page Width"), "fit_width", ed.fit_width)
        self.a_fit_page = A(_t("One Page"), "fit_page", self.fit_page)
        self.a_nav = A(_t("Navigation Pane"), "menu", self.toggle_nav, checkable=True)
        self.a_read_mode = A(_t("Read Mode"), "book", self.toggle_read_mode, checkable=True)
        # tools
        self.a_mail_merge = A(_t("Mail Merge"), "mail_merge", self.mail_merge)
        self.a_save_template = A(_t("Save as Template"), "templates", self.save_as_template)
        self.a_collab = A(_t("Collaborate"), "collab", self.collaborate)
        self.a_macros = A(_t("Macros"), "macro", self.macros)
        self.a_export_pdf = A(_t("Export PDF"), "export_pdf", self.export_pdf)
        self.a_print_preview = A(_t("Print Preview"), "print", self.print_preview)
        self.a_save_as = A(_t("Save As / Export"), "save_as", self.save_as)

    def _build_ribbon(self) -> Ribbon:
        rb = Ribbon(self)
        # ---------------------------------------------------------------- Home
        home = rb.page(_t("Home"))
        g = home.group(_t("Clipboard"))
        g.large(self.a_paste, popup=True)
        g.small(self.a_cut)
        g.small(self.a_copy)
        g.small(self.a_painter)
        g = home.group(_t("Font"))
        self.font_combo = QFontComboBox()
        self.font_combo.setMaximumWidth(170)
        self.font_combo.activated.connect(lambda *_: self.set_family(self.font_combo.currentFont().family()))
        self.font_combo.lineEdit().returnPressed.connect(lambda: self.set_family(self.font_combo.currentText().strip()))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setMaximumWidth(64)
        for s in (8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72):
            self.size_combo.addItem(f"{s:g}")
        self.size_combo.activated.connect(lambda *_: self.set_size(self.size_combo.currentText()))
        self.size_combo.lineEdit().returnPressed.connect(lambda: self.set_size(self.size_combo.currentText()))
        g.row(self.font_combo, self.size_combo, self.a_grow, self.a_shrink)
        g.row(self.a_bold, self.a_italic, self.a_underline, self.a_strike, self.a_super, self.a_sub)
        g.row(self.a_color, self.a_highlight, self.a_clear)
        g = home.group(_t("Paragraph"))
        g.row(self.a_bullets, self.a_numbering, self.a_indent_less, self.a_indent_more, self.a_marks)
        g.row(self.a_left, self.a_center, self.a_right, self.a_justify, self.a_spacing)
        g.row(self.a_ltr, self.a_rtl, self.a_paragraph)
        g = home.group(_t("Styles"))
        self.style_combo = QComboBox()
        self.style_combo.addItems([_t(s) for s in STYLE_NAMES])
        self.style_combo.setMinimumWidth(130)
        self.style_combo.activated.connect(lambda i: self.set_style(STYLE_NAMES[i]))
        g.row(self.style_combo)
        quick = QWidget()
        ql = QHBoxLayout(quick)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(2)
        for name in ("Title", "Heading 1", "Heading 2", "Normal"):
            b = QPushButton(_t(name))
            b.setFlat(True)
            b.clicked.connect(lambda _=False, n=name: self.set_style(n))
            ql.addWidget(b)
        g.row(quick)
        g = home.group(_t("Editing"))
        g.small(self.a_find)
        g.small(self.a_replace)
        g.small(self.a_select_all)
        # ---------------------------------------------------------------- Insert
        ins = rb.page(_t("Insert"))
        g = ins.group(_t("Pages"))
        g.large(self.a_page_break)
        g = ins.group(_t("Tables"))
        g.large(self.a_table)
        g.small(self.a_table_style, popup=True)
        g.small(self.a_sort_table)
        g.small(self.a_text_to_table)
        g.small(self.a_table_to_text)
        g.small(self.a_distribute)
        g = ins.group(_t("Illustrations"))
        g.large(self.a_picture)
        g.large(self.a_screenshot)
        g = ins.group(_t("Links"))
        g.small(self.a_link)
        g.small(self.a_unlink)
        g.small(self.a_bookmark)
        g = ins.group(_t("Header && Footer"))
        g.small(self.a_header)
        g.small(self.a_footer)
        g.small(self.a_page_numbers)
        g = ins.group(_t("Symbols"))
        g.large(self.a_equation)
        g = ins.group(_t("Text"))
        g.small(self.a_date)
        g.small(self.a_symbol)
        g.small(self.a_hr)
        g.small(self.a_text_file)
        g.small(self.a_caption)
        g.small(self.a_comment)
        # ---------------------------------------------------------------- Page Layout
        lay = rb.page(_t("Page Layout"))
        g = lay.group(_t("Page Setup"))
        g.large(self.a_margins)
        g.large(self.a_orientation)
        g.large(self.a_size)
        g.small(self.a_page_setup)
        g = lay.group(_t("Page Background"))
        g.large(self.a_watermark)
        # ---------------------------------------------------------------- References
        ref = rb.page(_t("References"))
        g = ref.group(_t("Table of Contents"))
        g.large(self.a_toc)
        g.small(self.a_toc_update)
        g = ref.group(_t("Footnotes"))
        g.large(self.a_footnote)
        g.small(self.a_next_footnote)
        g = ref.group(_t("Citations && Bibliography"))
        g.large(self.a_cite)
        self.cite_style_combo = QComboBox()
        self.cite_style_combo.addItems(citations.STYLES)
        self.cite_style_combo.setCurrentText(self.citation_style)
        self.cite_style_combo.activated.connect(lambda *_: self.set_citation_style(self.cite_style_combo.currentText()))
        g.row(QLabel(_t("Style:")), self.cite_style_combo)
        g.row(self.a_sources)
        g.row(self.a_biblio)
        g.small(self.a_cite_update)
        g = ref.group(_t("Captions"))
        g.large(self.a_caption)
        # ---------------------------------------------------------------- Review
        rev = rb.page(_t("Review"))
        g = rev.group(_t("Proofing"))
        g.large(self.a_spell)
        g.small(self.a_spell_auto)
        g.small(self.a_spell_lang, popup=True)
        g.small(self.a_word_count)
        g = rev.group(_t("Comments"))
        g.large(self.a_comment)
        g.small(self.a_del_comment)
        g.small(self.a_comments_pane)
        g = rev.group(_t("Tracking"))
        g.large(self.a_track)
        g.large(self.a_accept, popup=True)
        g.large(self.a_reject, popup=True)
        g.small(self.a_prev_change)
        g.small(self.a_next_change)
        g = rev.group(_t("Compare"))
        g.large(self.a_compare)
        g = rev.group(_t("Protect"))
        g.large(self.a_protect)
        # ---------------------------------------------------------------- View
        view = rb.page(_t("View"))
        g = view.group(_t("Zoom"))
        g.large(self.a_zoom_100)
        g.small(self.a_zoom_in)
        g.small(self.a_zoom_out)
        g.small(self.a_fit_width)
        g.small(self.a_fit_page)
        g = view.group(_t("Show"))
        g.small(self.a_nav)
        g.small(self.a_comments_pane)
        g.small(self.a_marks)
        g = view.group(_t("Views"))
        g.large(self.a_read_mode)
        g.large(self.a_print_preview)
        # ---------------------------------------------------------------- Tools
        tools = rb.page(_t("Tools"))
        g = tools.group(_t("Mailings"))
        g.large(self.a_mail_merge)
        g = tools.group(_t("Documents"))
        g.large(self.a_merge_docs)
        g.large(self.a_split_doc)
        g = tools.group(_t("Templates"))
        g.large(self.a_save_template)
        g = tools.group(_t("Share"))
        g.large(self.a_collab)
        g = tools.group(_t("Automation"))
        g.large(self.a_macros)
        g = tools.group(_t("Export"))
        g.large(self.a_export_pdf)
        g.large(self.a_save_as)
        # ---------------------------------------------------------------- AI
        aip = rb.page(_t("AI"))
        g = aip.group(_t("Write"))
        g.large(self.a_ai_write)
        g = aip.group(_t("Proofing"))
        g.large(self.a_ai_grammar)
        g = aip.group(_t("Translate"))
        g.large(self.a_ai_translate)
        g = aip.group(_t("Setup"))
        g.large(self.a_ai_settings)
        return rb

    # ================================================================== state sync
    def _schedule_refresh(self):
        self._refresh_timer.start()

    def _deferred_refresh(self):
        self._update_info()
        if self.nav.isVisible():
            self._refresh_nav()
        if self.comments_panel.isVisible():
            self.comments_panel.refresh()

    def _update_info(self):
        self.info.setText(self.status_text())

    def _cursor_changed(self):
        ed = self.editor
        c = ed.textCursor()
        fmt = c.charFormat()
        fams = fmt.fontFamilies() or [ed.document().defaultFont().family()]
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(fams[0]))
        self.font_combo.setEditText(fams[0])
        self.font_combo.blockSignals(False)
        size = fmt.fontPointSize() or ed.document().defaultFont().pointSizeF()
        self.size_combo.blockSignals(True)
        self.size_combo.setEditText(f"{size:g}")
        self.size_combo.blockSignals(False)
        self.a_bold.setChecked(fmt.fontWeight() >= QFont.DemiBold)
        self.a_italic.setChecked(fmt.fontItalic())
        self.a_underline.setChecked(fmt.fontUnderline())
        self.a_strike.setChecked(fmt.fontStrikeOut())
        self.a_super.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSuperScript)
        self.a_sub.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSubScript)
        bf = c.blockFormat()
        al = bf.alignment() & Qt.AlignHorizontal_Mask
        rtl = c.block().textDirection() == Qt.RightToLeft
        if al & Qt.AlignHCenter:
            self.a_center.setChecked(True)
        elif al & Qt.AlignJustify:
            self.a_justify.setChecked(True)
        elif al & Qt.AlignRight:
            (self.a_left if rtl else self.a_right).setChecked(True)
        else:
            (self.a_right if rtl else self.a_left).setChecked(True)
        style = str(bf.property(P_STYLE)) if bf.hasProperty(P_STYLE) else (
            style_for_heading(bf.headingLevel()) if bf.headingLevel() else "Normal")
        if style in STYLE_NAMES:
            self.style_combo.blockSignals(True)
            self.style_combo.setCurrentIndex(STYLE_NAMES.index(style))
            self.style_combo.blockSignals(False)
        if self._painter_fmt is not None and c.hasSelection() and QApplication.mouseButtons() == Qt.NoButton:
            self._apply_painter()
        self._update_info()

    # ================================================================== formatting
    def _merge_char(self, fmt: QTextCharFormat):
        if self.read_only:
            return
        ed = self.editor
        c = ed.textCursor()
        if not c.hasSelection():
            w = QTextCursor(c)
            w.select(QTextCursor.WordUnderCursor)
            if w.hasSelection() and w.selectionStart() < c.position() < w.selectionEnd():
                w.mergeCharFormat(fmt)
        c.mergeCharFormat(fmt)
        ed.setTextCursor(c)
        ed.focus_editor()

    def toggle_bold(self, checked=None):
        f = QTextCharFormat()
        f.setFontWeight(QFont.Bold if self.a_bold.isChecked() else QFont.Normal)
        self._merge_char(f)

    def toggle_italic(self, checked=None):
        f = QTextCharFormat()
        f.setFontItalic(self.a_italic.isChecked())
        self._merge_char(f)

    def toggle_underline(self, checked=None):
        f = QTextCharFormat()
        f.setFontUnderline(self.a_underline.isChecked())
        self._merge_char(f)

    def toggle_strike(self, checked=None):
        f = QTextCharFormat()
        f.setFontStrikeOut(self.a_strike.isChecked())
        self._merge_char(f)

    def set_valign(self, checked, sup: bool):
        f = QTextCharFormat()
        if checked:
            f.setVerticalAlignment(QTextCharFormat.AlignSuperScript if sup else QTextCharFormat.AlignSubScript)
            (self.a_sub if sup else self.a_super).setChecked(False)
        else:
            f.setVerticalAlignment(QTextCharFormat.AlignNormal)
        self._merge_char(f)

    def set_family(self, family: str):
        f = QTextCharFormat()
        f.setFontFamilies([family])
        self._merge_char(f)

    def set_size(self, text: str):
        try:
            size = float(str(text).replace(",", "."))
        except ValueError:
            return
        if 1 <= size <= 1638:
            f = QTextCharFormat()
            f.setFontPointSize(size)
            self._merge_char(f)

    def step_font(self, direction: int):
        sizes = [8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72, 96]
        cur = self.editor.textCursor().charFormat().fontPointSize() or self.editor.document().defaultFont().pointSizeF()
        if direction > 0:
            nxt = next((s for s in sizes if s > cur), cur + 10)
        else:
            nxt = next((s for s in reversed(sizes) if s < cur), max(1, cur - 1))
        f = QTextCharFormat()
        f.setFontPointSize(nxt)
        self._merge_char(f)

    def set_color(self, color: QColor):
        f = QTextCharFormat()
        if color is not None and color.isValid():
            self._last_color = color
            f.setForeground(color)
        else:
            f.setForeground(QColor("#000000"))
        self._merge_char(f)

    def set_highlight(self, color: QColor):
        if color is not None and color.isValid():
            self._last_highlight = color
            f = QTextCharFormat()
            f.setBackground(color)
            self._merge_char(f)
        else:
            c = self.editor.textCursor()
            self._rewrite_selection(lambda fmt: (fmt.clearBackground(), fmt)[1], c)

    def _rewrite_selection(self, fn, c: QTextCursor):
        if not c.hasSelection():
            return
        doc = self.editor.document()
        from app.writer.revisions import fragments

        edit = QTextCursor(doc)
        edit.beginEditBlock()
        for pos, length, fmt in list(fragments(doc, c.selectionStart(), c.selectionEnd())):
            s = max(pos, c.selectionStart())
            e = min(pos + length, c.selectionEnd())
            if e <= s:
                continue
            edit.setPosition(s)
            edit.setPosition(e, QTextCursor.KeepAnchor)
            edit.setCharFormat(fn(QTextCharFormat(fmt)))
        edit.endEditBlock()

    def clear_formatting(self):
        if self.read_only:
            return
        c = self.editor.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.BlockUnderCursor)

        def strip(fmt: QTextCharFormat):
            out = QTextCharFormat()
            for p in PRESERVE_PROPS:
                if fmt.hasProperty(p):
                    out.setProperty(p, fmt.property(p))
            if fmt.isAnchor():
                out.setAnchor(True)
                out.setAnchorHref(fmt.anchorHref())
                out.setAnchorNames(fmt.anchorNames())
            return out

        self._rewrite_selection(strip, c)
        apply_style(c, "Normal", self.editor.document().defaultFont().pointSizeF())
        self._cursor_changed()

    def format_painter(self, checked: bool):
        c = self.editor.textCursor()
        if checked:
            self._painter_fmt = QTextCharFormat(c.charFormat())
            for p in PRESERVE_PROPS:
                self._painter_fmt.clearProperty(p)
            self._painter_block = QTextBlockFormat(c.blockFormat())
            self.statusChanged.emit(_t("Format Painter: select the text to format"))
        else:
            self._painter_fmt = None

    def _apply_painter(self):
        fmt = self._painter_fmt
        self._painter_fmt = None
        self.a_painter.setChecked(False)
        c = self.editor.textCursor()
        self._rewrite_selection(lambda f: self._painted(f, fmt), c)

    @staticmethod
    def _painted(orig: QTextCharFormat, fmt: QTextCharFormat) -> QTextCharFormat:
        out = QTextCharFormat(fmt)
        for p in PRESERVE_PROPS:
            if orig.hasProperty(p):
                out.setProperty(p, orig.property(p))
        return out

    def paste_plain(self):
        if self.read_only:
            return
        text = QApplication.clipboard().text()
        if text:
            from PySide6.QtCore import QMimeData

            md = QMimeData()
            md.setText(text)
            self.editor.insert_mime(md)

    # ---------------------------------------------------------------- paragraph
    def _set_block_formats(self, fn):
        if self.read_only:
            return
        c = self.editor.textCursor()
        edit = QTextCursor(self.editor.document())
        edit.beginEditBlock()
        for block in list(_selected_blocks(c)):
            bc = QTextCursor(block)
            bf = block.blockFormat()
            fn(block, bf)
            bc.setBlockFormat(bf)
        edit.endEditBlock()
        self.editor.setTextCursor(c)
        self.editor.focus_editor()

    def set_alignment(self, visual: str):
        def fn(block, bf):
            rtl = block.textDirection() == Qt.RightToLeft
            if visual == "center":
                bf.setAlignment(Qt.AlignHCenter)
            elif visual == "justify":
                bf.setAlignment(Qt.AlignJustify)
            elif visual == "left":
                bf.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
            else:
                bf.setAlignment(Qt.AlignLeft if rtl else Qt.AlignRight)
        self._set_block_formats(fn)
        self._cursor_changed()

    def set_direction(self, direction):
        def fn(block, bf):
            bf.setLayoutDirection(direction)
            bf.setAlignment(Qt.AlignLeft)  # logical "start" alignment in the new direction
        self._set_block_formats(fn)
        if direction == Qt.RightToLeft:
            c = self.editor.textCursor()
            fam = (c.charFormat().fontFamilies() or [""])[0]
            from app.writer.editor import _family_supports

            if not _family_supports(fam, "arabic"):
                f = QTextCharFormat()
                f.setFontFamilies([cfg().get("writer.urdu_font", "Noto Nastaliq Urdu")])
                c.mergeCharFormat(f)
                self.editor.setTextCursor(c)
        self._cursor_changed()

    def set_line_spacing(self, pct: int):
        self._set_block_formats(lambda b, bf: bf.setLineHeight(pct, QTextBlockFormat.ProportionalHeight.value))

    def paragraph_dialog(self):
        c = self.editor.textCursor()
        d = ParagraphDialog(self, c.blockFormat())
        if d.exec():
            new = d.block_format()

            def fn(block, bf):
                bf.merge(new)
                if not new.pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore:
                    bf.setPageBreakPolicy(QTextFormat.PageBreak_Auto)
            self._set_block_formats(fn)

    def toggle_marks(self, checked: bool):
        doc = self.editor.document()
        opt = doc.defaultTextOption()
        flags = opt.flags()
        if checked:
            flags |= QTextOption.ShowTabsAndSpaces | QTextOption.ShowLineAndParagraphSeparators
        else:
            flags &= ~(QTextOption.ShowTabsAndSpaces | QTextOption.ShowLineAndParagraphSeparators)
        opt.setFlags(flags)
        doc.setDefaultTextOption(opt)
        self.editor.viewport().update()

    def toggle_list(self, style):
        if self.read_only:
            return
        c = self.editor.textCursor()
        lst = c.currentList()
        c.beginEditBlock()
        if lst is not None and lst.format().style() == style:
            for block in list(_selected_blocks(c)):
                l2 = block.textList()
                if l2 is not None:
                    l2.remove(block)
                bc = QTextCursor(block)
                bf = block.blockFormat()
                bf.setIndent(0)
                bc.setBlockFormat(bf)
        elif lst is not None:
            lf = lst.format()
            lf.setStyle(style)
            lst.setFormat(lf)
        else:
            lf = QTextListFormat()
            lf.setStyle(style)
            lf.setIndent(1)
            c.createList(lf)
        c.endEditBlock()
        self.editor.setTextCursor(c)
        self.editor.focus_editor()

    def change_indent(self, delta: int):
        if self.read_only:
            return
        c = self.editor.textCursor()
        edit = QTextCursor(self.editor.document())
        edit.beginEditBlock()
        for block in list(_selected_blocks(c)):
            lst = block.textList()
            bc = QTextCursor(block)
            if lst is not None:
                lf = QTextListFormat(lst.format())
                new_indent = max(1, lf.indent() + delta)
                if new_indent == lf.indent():
                    continue
                lst.remove(block)
                lf.setIndent(new_indent)
                prev = block.previous()
                pl = prev.textList() if prev.isValid() else None
                if pl is not None and pl.format().indent() == new_indent and pl.format().style() == lf.style():
                    pl.add(block)
                else:
                    if delta > 0:
                        lf.setStyle({QTextListFormat.ListDisc: QTextListFormat.ListCircle,
                                     QTextListFormat.ListCircle: QTextListFormat.ListSquare,
                                     QTextListFormat.ListDecimal: QTextListFormat.ListLowerAlpha,
                                     QTextListFormat.ListLowerAlpha: QTextListFormat.ListLowerRoman}.get(lf.style(), lf.style()))
                    bc.createList(lf)
            else:
                bf = block.blockFormat()
                bf.setLeftMargin(max(0.0, bf.leftMargin() + delta * 48))
                bc.setBlockFormat(bf)
        edit.endEditBlock()
        self.editor.setTextCursor(c)

    def set_style(self, name: str):
        if self.read_only:
            return
        c = self.editor.textCursor()
        apply_style(c, name, self.editor.document().defaultFont().pointSizeF())
        self.editor.setTextCursor(c)
        self.editor.focus_editor()
        self._cursor_changed()

    def show_find(self, replace: bool):
        if self.find_dialog is None:
            self.find_dialog = FindReplaceDialog(self, replace)
        self.find_dialog.set_replace_mode(replace)
        sel = self.editor.textCursor().selectedText()
        if sel and "\u2029" not in sel:
            self.find_dialog.find_edit.setText(sel)
        self.find_dialog.show()
        self.find_dialog.raise_()
        self.find_dialog.find_edit.setFocus()
        self.find_dialog.find_edit.selectAll()

    def replace_selection(self, text: str, in_edit_block: bool = True) -> int:
        """Replace the current selection (tracked when Track Changes is on); returns end position."""
        ed = self.editor
        c = ed.textCursor()
        fmt = QTextCharFormat(c.charFormat())
        if in_edit_block:
            c.beginEditBlock()
        if ed.track_changes:
            s, e = c.selectionStart(), c.selectionEnd()
            ed.revisions.tracked_delete(s, e, c)
            c.setPosition(e)
            fmt = ed.revisions.insertion_format(fmt)
            fmt.clearProperty(P_DEL)
        c.insertText(text, fmt)
        if in_edit_block:
            c.endEditBlock()
        ed.setTextCursor(c)
        return c.position()

    # ================================================================== insert
    def insert_page_break(self):
        if self.read_only:
            return
        c = self.editor.textCursor()
        c.beginEditBlock()
        c.insertBlock()
        bf = c.blockFormat()
        bf.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
        c.setBlockFormat(bf)
        c.endEditBlock()
        self.editor.setTextCursor(c)

    def insert_table_dialog(self):
        d = InsertTableDialog(self)
        if d.exec():
            self.insert_table(d.rows.value(), d.cols.value(), d.border.value(), d.header.isChecked())

    def insert_table(self, rows: int, cols: int, border: float = 1.0, header: bool = True):
        if self.read_only:
            return
        c = self.editor.textCursor()
        tf = QTextTableFormat()
        tf.setBorder(border)
        tf.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        tf.setBorderBrush(QColor("#000000"))
        tf.setCellPadding(4)
        tf.setCellSpacing(0)
        try:
            tf.setBorderCollapse(True)
        except AttributeError:
            pass
        tf.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        tf.setColumnWidthConstraints([QTextLength(QTextLength.PercentageLength, 100.0 / cols)] * cols)
        c.beginEditBlock()
        table = c.insertTable(rows, cols, tf)
        if header:
            bold = QTextCharFormat()
            bold.setFontWeight(QFont.Bold)
            for col in range(cols):
                cell = table.cellAt(0, col)
                cf = cell.format()
                cf.setBackground(QColor("#d9e2f3"))
                cell.setFormat(cf)
                cell.firstCursorPosition().setBlockCharFormat(bold)
        c.endEditBlock()
        self.editor.setTextCursor(table.cellAt(0, 0).firstCursorPosition())

    def _table_op(self, op: str):
        c = self.editor.textCursor()
        table = c.currentTable()
        if table is None or self.read_only:
            return
        cell = table.cellAt(c)
        r, col = cell.row(), cell.column()
        c.beginEditBlock()
        if op == "row_above":
            table.insertRows(r, 1)
        elif op == "row_below":
            table.insertRows(r + cell.rowSpan(), 1)
        elif op in ("col_left", "col_right"):
            table.insertColumns(col if op == "col_left" else col + cell.columnSpan(), 1)
            self._even_columns(table)
        elif op == "del_row":
            if table.rows() <= 1:
                table.removeRows(0, table.rows())
            else:
                table.removeRows(r, 1)
        elif op == "del_col":
            if table.columns() <= 1:
                table.removeRows(0, table.rows())
            else:
                table.removeColumns(col, 1)
                self._even_columns(table)
        elif op == "del_table":
            table.removeRows(0, table.rows())
        elif op == "merge":
            table.mergeCells(c)
        elif op == "split":
            table.splitCell(r, col, 1, 1)
        elif op == "borders_none" or op == "borders_all":
            tf = table.format()
            tf.setBorder(0 if op == "borders_none" else 1)
            table.setFormat(tf)
        c.endEditBlock()

    @staticmethod
    def _even_columns(table):
        tf = table.format()
        n = table.columns()
        tf.setColumnWidthConstraints([QTextLength(QTextLength.PercentageLength, 100.0 / n)] * n)
        table.setFormat(tf)

    def _cell_shading(self, color: QColor):
        c = self.editor.textCursor()
        table = c.currentTable()
        if table is None:
            return
        if c.hasComplexSelection():
            r0, nr, c0, nc = c.selectedTableCells()
        else:
            cell = table.cellAt(c)
            r0, nr, c0, nc = cell.row(), 1, cell.column(), 1
        c.beginEditBlock()
        for r in range(r0, r0 + nr):
            for cc in range(c0, c0 + nc):
                cell = table.cellAt(r, cc)
                cf = cell.format()
                if color.isValid():
                    cf.setBackground(color)
                else:
                    cf.clearBackground()
                cell.setFormat(cf)
        c.endEditBlock()

    def insert_picture(self):
        if self.read_only:
            return
        path = get_open_file(self, _t("Insert Picture"), "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*)")
        if path:
            try:
                self.editor.insert_image_file(path)
            except Exception as exc:
                handle_exception(self, _t("Insert Picture"), exc)

    def insert_screenshot(self):
        if self.read_only:
            return
        from app.services.capture import capture_dialog

        img = capture_dialog(self.window())
        if img is not None and not img.isNull():
            self.editor.insert_qimage(img)

    def _image_at_cursor(self):
        c = self.editor.textCursor()
        doc = self.editor.document()
        positions = [c.selectionStart()] if c.hasSelection() else [c.position() - 1, c.position()]
        for p in positions:
            if 0 <= p < doc.characterCount() - 1 and doc.characterAt(p) == "\ufffc":
                t = QTextCursor(doc)
                t.setPosition(p)
                t.setPosition(p + 1, QTextCursor.KeepAnchor)
                fmt = t.charFormat()
                if fmt.isImageFormat():
                    return t, fmt.toImageFormat()
        return None, None

    def image_size_dialog(self):
        t, imf = self._image_at_cursor()
        if imf is None:
            return
        data = self.editor.image_bytes(imf.name())
        from PySide6.QtGui import QImage

        nat = QImage.fromData(data[0]) if data else QImage()
        natural = (nat.width(), nat.height()) if not nat.isNull() else (int(imf.width()), int(imf.height()))
        d = ImageSizeDialog(self, imf.width() or natural[0], imf.height() or natural[1], natural)
        if d.exec():
            imf.setWidth(d.w.value())
            imf.setHeight(d.h.value())
            t.setCharFormat(imf)

    def _link_range(self, c: QTextCursor):
        """Extent of the hyperlink under the cursor."""
        doc = self.editor.document()
        block = c.block()
        it = block.begin()
        pos = c.position()
        while not it.atEnd():
            f = it.fragment()
            it += 1
            if f.position() <= pos <= f.position() + f.length() and f.charFormat().isAnchor() and f.charFormat().anchorHref():
                href = f.charFormat().anchorHref()
                s, e = f.position(), f.position() + f.length()
                # extend over neighbours with the same href
                it2 = block.begin()
                frs = []
                while not it2.atEnd():
                    g = it2.fragment()
                    it2 += 1
                    frs.append(g)
                for g in frs:
                    if g.charFormat().anchorHref() == href and (g.position() + g.length() == s or g.position() == e):
                        s, e = min(s, g.position()), max(e, g.position() + g.length())
                return s, e, href
        del doc
        return None

    def bookmarks(self) -> list[str]:
        names = []
        from app.writer.revisions import fragments

        for _p, _l, fmt in fragments(self.editor.document()):
            for n in fmt.anchorNames() or []:
                if n not in names:
                    names.append(n)
        return names

    def insert_link(self):
        if self.read_only:
            return
        c = self.editor.textCursor()
        rng = self._link_range(c)
        if rng and not c.hasSelection():
            c.setPosition(rng[0])
            c.setPosition(rng[1], QTextCursor.KeepAnchor)
        d = HyperlinkDialog(self, c.selectedText(), rng[2] if rng else "", self.bookmarks())
        if not d.exec():
            return
        text, url = d.values()
        if not url:
            return
        fmt = QTextCharFormat(c.charFormat())
        fmt.setAnchor(True)
        fmt.setAnchorHref(url)
        fmt.setFontUnderline(True)
        fmt.setForeground(QColor("#0563c1"))
        if self.editor.track_changes:
            fmt = self.editor.revisions.insertion_format(fmt)
        c.beginEditBlock()
        if c.hasSelection() and self.editor.track_changes:
            s, e = c.selectionStart(), c.selectionEnd()
            self.editor.revisions.tracked_delete(s, e, c)
            c.setPosition(e)
        c.insertText(text, fmt)
        after = QTextCharFormat(fmt)
        after.setAnchor(False)
        after.setAnchorHref("")
        after.setFontUnderline(False)
        after.setForeground(QColor("#000000"))
        c.setCharFormat(after)
        c.endEditBlock()
        self.editor.setTextCursor(c)

    def remove_link(self):
        c = self.editor.textCursor()
        rng = self._link_range(c)
        if not rng:
            return
        sel = QTextCursor(self.editor.document())
        sel.setPosition(rng[0])
        sel.setPosition(rng[1], QTextCursor.KeepAnchor)

        def unlink(fmt: QTextCharFormat):
            fmt.setAnchor(bool(fmt.anchorNames()))
            fmt.setAnchorHref("")
            fmt.setFontUnderline(False)
            fmt.setForeground(QColor("#000000"))
            return fmt
        self._rewrite_selection(unlink, sel)

    def insert_bookmark(self):
        c = self.editor.textCursor()
        name, ok = QInputDialog.getText(self, _t("Bookmark"), _t("Bookmark name (no spaces):"))
        if not ok or not name.strip():
            return
        name = re.sub(r"\s+", "_", name.strip())
        if not c.hasSelection():
            c.select(QTextCursor.WordUnderCursor)
        if not c.hasSelection():
            c.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        f = QTextCharFormat()
        f.setAnchor(True)
        f.setAnchorNames([name])
        c.mergeCharFormat(f)
        self.statusChanged.emit(_t("Bookmark added: {0}").format(name))

    def set_page_numbers(self, where: str, align: str, simple: bool = False):
        text = "{PAGE}" if simple else _t("Page {PAGE} of {NUMPAGES}").replace("{0}", "{PAGE}")
        hf = self.editor.hf
        if where == "footer":
            hf.footer, hf.footer_align = text, align
        else:
            hf.header, hf.header_align = text, align
        self.editor.refresh_decorations()
        self.set_modified(True)

    def remove_page_numbers(self):
        hf = self.editor.hf
        for attr in ("header", "footer"):
            val = getattr(hf, attr)
            if "{PAGE}" in val or "{NUMPAGES}" in val:
                setattr(hf, attr, "")
        self.editor.refresh_decorations()
        self.set_modified(True)

    def insert_date(self):
        d = DateTimeDialog(self)
        if d.exec() and d.value():
            c = self.editor.textCursor()
            c.insertText(d.value())
            self.editor.setTextCursor(c)

    def insert_symbol(self):
        d = SymbolDialog(self, cfg().get("writer.urdu_font", "Noto Nastaliq Urdu"))
        if d.exec() and d.chosen:
            c = self.editor.textCursor()
            self.editor.item._prepare_typing(d.chosen)
            c = self.editor.textCursor()
            c.insertText(d.chosen)
            self.editor.setTextCursor(c)

    def insert_hr(self):
        if self.read_only:
            return
        c = self.editor.textCursor()
        c.beginEditBlock()
        c.movePosition(QTextCursor.EndOfBlock)
        c.insertBlock()
        bf = QTextBlockFormat()
        bf.setProperty(QTextFormat.BlockTrailingHorizontalRulerWidth, QTextLength(QTextLength.PercentageLength, 100))
        c.setBlockFormat(bf)
        c.insertBlock(QTextBlockFormat())
        c.endEditBlock()
        self.editor.setTextCursor(c)

    def insert_text_from_file(self):
        if self.read_only:
            return
        path = get_open_file(self, _t("Insert Text from File"), OPEN_FILTER)
        if not path:
            return
        try:
            from PySide6.QtGui import QTextDocument, QTextDocumentFragment

            tmp = QTextDocument()
            ext = Path(path).suffix.lower()
            if ext in DOCX_EXTS or ext == ".wpt":
                load_docx(path, tmp, self.editor.register_image)
            elif ext in (".html", ".htm"):
                tmp.setHtml(open(path, encoding="utf-8", errors="replace").read())
            elif ext in (".md", ".markdown"):
                tmp.setMarkdown(open(path, encoding="utf-8", errors="replace").read())
            elif ext in CONVERT_EXTS:
                from app.services.office_convert import convert

                load_docx(convert(path, "docx"), tmp, self.editor.register_image)
            else:
                tmp.setPlainText(open(path, encoding="utf-8", errors="replace").read())
            c = self.editor.textCursor()
            c.insertFragment(QTextDocumentFragment(tmp))
            self.editor.setTextCursor(c)
        except Exception as exc:
            handle_exception(self, _t("Insert Text from File"), exc)

    def insert_caption(self):
        if self.read_only:
            return
        label, ok = QInputDialog.getItem(self, _t("Insert Caption"), _t("Label:"),
                                         [_t("Figure"), _t("Table"), _t("Equation")], 0, False)
        if not ok:
            return
        text = self.editor.document().toPlainText()
        n = len(re.findall(rf"(?m)^{re.escape(label)} \d+", text)) + 1
        desc, ok = QInputDialog.getText(self, _t("Insert Caption"), _t("Caption text:"))
        if not ok:
            return
        c = self.editor.textCursor()
        c.beginEditBlock()
        c.movePosition(QTextCursor.EndOfBlock)
        bf = QTextBlockFormat()
        bf.setProperty(P_STYLE, "Caption")
        bf.setBottomMargin(10 * PX_PER_PT)
        cf = QTextCharFormat()
        cf.setFontItalic(True)
        cf.setFontPointSize(9)
        cf.setForeground(QColor("#44546a"))
        c.insertBlock(bf, cf)
        c.insertText(f"{label} {n}: {desc}", cf)
        c.insertBlock(QTextBlockFormat(), QTextCharFormat())
        c.endEditBlock()
        self.editor.setTextCursor(c)

    # ================================================================== page layout
    def _apply_page(self, page: PageSetup):
        self.editor.apply_page_setup(page)
        self.editor.refresh_decorations()
        self.set_modified(True)
        self._update_info()

    def set_margins(self, vals):
        p = PageSetup.from_dict(self.editor.page.to_dict())
        p.margins_mm = list(vals)
        self._apply_page(p)

    def set_orientation(self, orient: str):
        p = PageSetup.from_dict(self.editor.page.to_dict())
        p.orientation = orient
        self._apply_page(p)

    def toggle_orientation(self):
        self.set_orientation("portrait" if self.editor.page.orientation == "landscape" else "landscape")

    def set_page_size(self, name: str):
        p = PageSetup.from_dict(self.editor.page.to_dict())
        p.set_size(name)
        self._apply_page(p)

    def page_setup_dialog(self, tab: int = 0):
        d = PageSetupDialog(self, self.editor.page, self.editor.hf, self.editor.watermark)
        d.select_tab(tab)
        if d.exec():
            page, hf, wm = d.result_values()
            self.editor.hf = HeaderFooter(**hf)
            self.editor.watermark = wm
            self.editor.bg.update()
            self._apply_page(page)

    def fit_page(self):
        w, h = self.editor.page.page_px()
        vp = self.editor.viewport()
        self.editor.set_zoom(min((vp.width() - 40) / w, (vp.height() - 40) / h))

    # ================================================================== references
    def _heading_blocks(self, max_level: int = 3):
        out = []
        block = self.editor.document().begin()
        while block.isValid():
            bf = block.blockFormat()
            lvl = bf.headingLevel()
            if 0 < lvl <= max_level and block.text().strip() and not bf.hasProperty(P_TOC) \
                    and not bf.hasProperty(P_BIBLIO):
                out.append((lvl, block))
            block = block.next()
        return out

    def _toc_range(self):
        first = last = None
        block = self.editor.document().begin()
        while block.isValid():
            if block.blockFormat().hasProperty(P_TOC):
                if first is None:
                    first = block
                last = block
            elif first is not None:
                break
            block = block.next()
        return first, last

    def _write_toc(self, at_cursor: QTextCursor | None):
        doc = self.editor.document()
        heads = self._heading_blocks()
        c = QTextCursor(doc)
        c.beginEditBlock()
        # bookmark every heading so entries can link to it
        names = []
        for i, (lvl, block) in enumerate(heads):
            name = None
            it = block.begin()
            while not it.atEnd():
                f = it.fragment()
                it += 1
                for n in f.charFormat().anchorNames() or []:
                    if n.startswith("_Toc"):
                        name = n
                        break
                if name:
                    break
            if not name:
                name = f"_Toc{100000 + i}_{abs(hash(block.text())) % 10000}"
                bc = QTextCursor(block)
                bc.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                f = QTextCharFormat()
                f.setAnchor(True)
                f.setAnchorNames([name])
                bc.mergeCharFormat(f)
            names.append(name)
        first, last = self._toc_range()
        if first is not None:
            c.setPosition(first.position())
            c.setPosition(last.position() + last.length() - 1, QTextCursor.KeepAnchor)
            c.removeSelectedText()
        elif at_cursor is not None:
            c.setPosition(at_cursor.position())
            if c.block().length() > 1:
                c.movePosition(QTextCursor.StartOfBlock)
                c.insertBlock()
                c.movePosition(QTextCursor.PreviousBlock)
        else:
            c.endEditBlock()
            return
        text_w = self.editor.text_width_px()
        title_bf = QTextBlockFormat()
        title_bf.setProperty(P_TOC, True)
        title_bf.setBottomMargin(8 * PX_PER_PT)
        title_cf = QTextCharFormat()
        title_cf.setFontPointSize(16)
        title_cf.setFontWeight(QFont.Bold)
        title_cf.setForeground(QColor("#2f5496"))
        c.setBlockFormat(title_bf)
        c.setBlockCharFormat(title_cf)
        c.insertText(_t("Contents"), title_cf)
        for (lvl, block), name in zip(heads, names):
            bf = QTextBlockFormat()
            bf.setProperty(P_TOC, True)
            bf.setLeftMargin((lvl - 1) * 20)
            bf.setTabPositions([QTextOption.Tab(text_w - (lvl - 1) * 20 - 2, QTextOption.RightTab)])
            cf = QTextCharFormat()
            cf.setFontPointSize(11)
            cf.setFontWeight(QFont.Bold if lvl == 1 else QFont.Normal)
            cf.setAnchor(True)
            cf.setAnchorHref("#" + name)
            c.insertBlock(bf, cf)
            c.insertText(f"{block.text().strip()}\t{self.editor.block_page(block)}", cf)
        end_bf = QTextBlockFormat()
        c.insertBlock(end_bf, QTextCharFormat())
        c.endEditBlock()

    def insert_toc(self):
        if self.read_only:
            return
        if not self._heading_blocks():
            show_info(self, _t("Table of Contents"), _t("Apply Heading 1\u20133 styles to your headings first."))
            return
        self._write_toc(self.editor.textCursor())
        self.update_toc(quiet=True)

    def update_toc(self, quiet: bool = False):
        if self._toc_range()[0] is None:
            if not quiet:
                show_info(self, _t("Table of Contents"), _t("The document has no table of contents yet."))
            return
        # second pass: page numbers may shift after the TOC itself was (re)inserted
        self.editor.document().documentLayout().documentSize()
        self._write_toc(None)

    def set_citation_style(self, style: str):
        self.citation_style = style
        cfg().set("writer.citation_style", style)
        self.update_citations(quiet=True)

    def manage_sources(self):
        d = citations.SourceManagerDialog(self, self.sources)
        if d.exec():
            self.sources = d.sources
            self.set_modified(True)
            self.update_citations(quiet=True)

    def insert_citation(self):
        if self.read_only:
            return
        if not self.sources:
            if confirm(self, _t("Insert Citation"), _t("There are no sources yet. Add sources now?")):
                self.manage_sources()
            if not self.sources:
                return
        d = citations.InsertCitationDialog(self, self.sources)
        if d.exec() and d.keys():
            c = self.editor.textCursor()
            citations.insert_citation(c, d.keys(), d.locator.text().strip(), self.sources, self.citation_style)
            self.editor.setTextCursor(c)
            if self.citation_style == "IEEE":
                self.update_citations(quiet=True)

    def insert_bibliography(self):
        if self.read_only:
            return
        if not self.sources:
            show_info(self, _t("Bibliography"), _t("Add sources with Manage Sources first."))
            return
        citations.insert_or_update_bibliography(self.editor.document(), self.editor.textCursor(), self.sources,
                                                self.citation_style, self.editor.document().defaultFont().pointSizeF())

    def update_citations(self, quiet: bool = False):
        n = citations.update_citations(self.editor.document(), self.sources, self.citation_style)
        citations.insert_or_update_bibliography(self.editor.document(), None, self.sources, self.citation_style,
                                                self.editor.document().defaultFont().pointSizeF())
        if not quiet:
            self.statusChanged.emit(_t("{0} citation(s) updated").format(n))

    # ================================================================== review
    def word_count(self):
        ed = self.editor
        sel = ed.textCursor().selectedText()
        sel_stats = None
        if sel:
            t = sel.replace("\u2029", "\n")
            sel_stats = {"words": count_words(t), "characters": len(t.replace("\n", "")),
                         "characters_no_spaces": len("".join(t.split())), "paragraphs": t.count("\n") + 1}
        WordCountDialog(self, ed.statistics(), sel_stats).exec()

    def toggle_track(self, checked: bool):
        self.editor.track_changes = checked
        self._update_info()

    def accept_change(self):
        n = self.editor.revisions.accept_at(self.editor.textCursor())
        if not n:
            self.goto_change(1)

    def reject_change(self):
        n = self.editor.revisions.reject_at(self.editor.textCursor())
        if not n:
            self.goto_change(1)

    def accept_all(self):
        n = self.editor.revisions.accept_all()
        self.statusChanged.emit(_t("{0} change(s) accepted").format(n))

    def reject_all(self):
        n = self.editor.revisions.reject_all()
        self.statusChanged.emit(_t("{0} change(s) rejected").format(n))

    def goto_change(self, direction: int):
        ranges = self.editor.revisions.revision_ranges()
        if not ranges:
            self.statusChanged.emit(_t("The document contains no tracked changes."))
            return
        pos = self.editor.textCursor().position()
        if direction > 0:
            target = next((r for r in ranges if r[0] > pos), ranges[0])
        else:
            target = next((r for r in reversed(ranges) if r[1] < pos), ranges[-1])
        c = self.editor.textCursor()
        c.setPosition(target[0])
        c.setPosition(target[1], QTextCursor.KeepAnchor)
        self.editor.setTextCursor(c)
        self.statusChanged.emit(_t("{0} by {1}").format(_t("Insertion") if target[2] == "ins" else _t("Deletion"),
                                                        target[3]))

    def add_comment(self):
        c = self.editor.textCursor()
        if not c.hasSelection():
            c.select(QTextCursor.WordUnderCursor)
        if not c.hasSelection():
            show_info(self, _t("New Comment"), _t("Select the text you want to comment on."))
            return
        text, ok = QInputDialog.getMultiLineText(self, _t("New Comment"), _t("Comment:"))
        if not ok or not text.strip():
            return
        author = cfg().get("author", "User")
        cid = new_comment_id()
        initials = "".join(p[0] for p in author.split()[:2]).upper()
        self.editor.comments[cid] = Comment(cid, author, text.strip(), initials=initials)
        add_comment_anchor(self.editor.document(), c.selectionStart(), c.selectionEnd(), cid)
        self.comments_panel.show()
        self.a_comments_pane.setChecked(True)
        self.comments_panel.refresh()
        self.set_modified(True)

    def delete_comment(self):
        cid = self.comments_panel.current_id() if self.comments_panel.isVisible() else None
        if cid is None:
            fmt = self.editor.textCursor().charFormat()
            ids = [x for x in str(fmt.property(P_COMMENTS) or "").split(",") if x] if fmt.hasProperty(P_COMMENTS) else []
            cid = ids[0] if ids else None
        if cid is None:
            return
        remove_comment_anchor(self.editor.document(), cid)
        self.editor.comments.pop(cid, None)
        self.comments_panel.refresh()
        self.set_modified(True)

    def toggle_comments_pane(self, checked: bool):
        self.comments_panel.setVisible(checked)
        if checked:
            self.comments_panel.refresh()

    def compare_documents(self):
        from app.writer.compare import CompareDialog, compare_into, document_paragraphs, file_paragraphs

        d = CompareDialog(self, str(self.path) if self.path else "")
        if not d.exec():
            return
        try:
            orig_path, rev_path = d.orig.text().strip(), d.rev.text().strip()
            if not rev_path:
                return
            old = file_paragraphs(orig_path) if orig_path else document_paragraphs(self.editor.document())
            new = file_paragraphs(rev_path)
        except Exception as exc:
            handle_exception(self, _t("Compare Documents"), exc)
            return
        result = WriterDocument()
        stats = compare_into(old, new, result.editor.document(), d.author.text().strip() or "Compare")
        result.editor.document().clearUndoRedoStacks()
        result.title_text = _t("Comparison")
        result.set_modified(True)
        self.openRequested.emit(result)
        show_info(self, _t("Compare Documents"), stats.summary())

    def toggle_protect(self, checked: bool):
        if checked:
            if features.start_protection(self):
                self.statusChanged.emit(_t("Editing restricted: the document is read-only"))
            else:
                self.a_protect.setChecked(False)
        elif not features.stop_protection(self):
            self.a_protect.setChecked(True)
        self._update_info()

    def _table_style(self, name: str):
        if not features.apply_table_style(self, name):
            self.statusChanged.emit(_t("Place the cursor in a table first."))

    def _sync_spell_menu(self):
        cur = cfg().get("writer.spell_lang", "en_US")
        for act, code in zip(self.spell_lang_menu.actions(), features.spell.LANGS):
            act.setChecked(code == cur)

    def _ai_settings(self):
        from app.shell.ai_dialogs import AISettingsDialog

        AISettingsDialog(self).exec()

    # ================================================================== view
    def toggle_nav(self, checked: bool):
        self.nav.setVisible(checked)
        if checked:
            self._refresh_nav()

    def _refresh_nav(self):
        self.nav.clear()
        parents = {0: None}
        for lvl, block in self._heading_blocks(6):
            item = QTreeWidgetItem([block.text().strip()[:80]])
            item.setData(0, Qt.UserRole, block.position())
            parent = None
            for l in range(lvl - 1, -1, -1):
                if parents.get(l) is not None:
                    parent = parents[l]
                    break
            if parent is None:
                self.nav.addTopLevelItem(item)
            else:
                parent.addChild(item)
            parents[lvl] = item
            for deeper in [k for k in parents if k > lvl]:
                parents[deeper] = None
        self.nav.expandAll()

    def _nav_goto(self, item):
        pos = item.data(0, Qt.UserRole)
        c = self.editor.textCursor()
        c.setPosition(min(pos, self.editor.document().characterCount() - 1))
        self.editor.setTextCursor(c)
        self.editor.focus_editor()

    def toggle_read_mode(self, checked: bool):
        self.editor.set_read_only(checked or self.a_protect.isChecked())
        if checked:
            self.fit_page()
        else:
            self.editor.set_zoom(1.0)

    # ================================================================== tools
    def mail_merge(self):
        from app.writer.mailmerge import MailMergeDialog

        MailMergeDialog(self).exec()

    def save_as_template(self):
        import tempfile

        from app.services import templates

        name, ok = QInputDialog.getText(self, _t("Save as Template"), _t("Template name:"),
                                        text=self.path.stem if self.path else _t("My Template"))
        if not ok or not name.strip():
            return
        try:
            fd, tmp = tempfile.mkstemp(suffix=".docx")
            os.close(fd)
            self.save_to(tmp)
            img = render.page_image(self.editor.document(), self.editor.page, self.editor.hf, 0, 240,
                                    watermark=self.editor.watermark)
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice

            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            t = templates.save_user_template("writer", name.strip(), tmp, bytes(ba.data()))
            os.unlink(tmp)
            show_info(self, _t("Save as Template"), _t("Template saved:\n{0}").format(t.path))
        except Exception as exc:
            handle_exception(self, _t("Save as Template"), exc)

    def collaborate(self):
        from app.collab.dialog import CollabDialog

        CollabDialog(self).exec()

    def macros(self):
        from app.macros.manager import MacroDialog

        MacroDialog(self.window()).exec()

    # ================================================================== context menu
    def _context_menu(self, screen_pos):
        m = QMenu(self)
        m.addAction(self.a_cut)
        m.addAction(self.a_copy)
        m.addAction(self.a_paste)
        m.addAction(self.a_paste_plain)
        m.addSeparator()
        c = self.editor.textCursor()
        rng = self._link_range(c)
        if rng:
            m.addAction(icon("link"), _t("Open Hyperlink"), lambda: self.editor.open_link(rng[2]))
            m.addAction(icon("edit"), _t("Edit Hyperlink\u2026"), self.insert_link)
            m.addAction(self.a_unlink)
            m.addSeparator()
        t, imf = self._image_at_cursor()
        if imf is not None and imf.hasProperty(P_EQUATION):
            m.addAction(icon("equation"), _t("Edit Equation\u2026"), lambda: features.insert_equation(self))
            m.addSeparator()
        elif imf is not None:
            m.addAction(icon("image"), _t("Picture Size\u2026"), self.image_size_dialog)
            m.addSeparator()
        if features.notes.footnote_at(c) is not None:
            m.addAction(icon("footnote"), _t("Edit Footnote\u2026"), lambda: features.edit_footnote(self))
            m.addAction(icon("delete"), _t("Delete Footnote"), lambda: features.delete_footnote(self))
            m.addSeparator()
        if c.currentTable() is not None:
            tm = m.addMenu(icon("table"), _t("Table"))
            for label, op in ((_t("Insert Row Above"), "row_above"), (_t("Insert Row Below"), "row_below"),
                              (_t("Insert Column Left"), "col_left"), (_t("Insert Column Right"), "col_right"),
                              (None, None), (_t("Delete Row"), "del_row"), (_t("Delete Column"), "del_col"),
                              (_t("Delete Table"), "del_table"), (None, None), (_t("Merge Cells"), "merge"),
                              (_t("Split Cells"), "split"), (None, None), (_t("No Borders"), "borders_none"),
                              (_t("All Borders"), "borders_all")):
                if label is None:
                    tm.addSeparator()
                else:
                    tm.addAction(label, lambda o=op: self._table_op(o))
            shade = ColorMenu(tm, _t("No Color"))
            shade.setTitle(_t("Cell Shading"))
            shade.colorPicked.connect(self._cell_shading)
            tm.addMenu(shade)
            m.addSeparator()
        if self.editor.revisions.range_at(c):
            m.addAction(self.a_accept)
            m.addAction(self.a_reject)
            m.addSeparator()
        m.addAction(self.a_comment)
        fmt = c.charFormat()
        if fmt.hasProperty(P_COMMENTS):
            m.addAction(self.a_del_comment)
        m.addAction(self.a_link)
        m.addSeparator()
        m.addAction(self.a_paragraph)
        m.addSeparator()
        m.addAction(self.a_ai_write)
        m.addAction(self.a_ai_translate)
        features.spelling_menu_items(self, m)
        sm = m.addMenu(_t("Style"))
        for name in STYLE_NAMES:
            sm.addAction(_t(name), lambda n=name: self.set_style(n))
        m.addAction(self.a_select_all)
        m.exec(screen_pos)

    # ================================================================== macro / plugin API
    def text(self) -> str:
        return self.editor.document().toPlainText()

    def insert_text(self, text: str) -> None:
        c = self.editor.textCursor()
        self.editor.item._prepare_typing(text)
        c = self.editor.textCursor()
        c.insertText(text)
        self.editor.setTextCursor(c)

    def replace_all(self, old: str, new: str, case: bool = False) -> int:
        from PySide6.QtGui import QTextDocument

        doc = self.editor.document()
        flags = QTextDocument.FindCaseSensitively if case else QTextDocument.FindFlag(0)
        count = 0
        cur = QTextCursor(doc)
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        while True:
            found = doc.find(old, cur, flags)
            if found.isNull():
                break
            self.editor.setTextCursor(found)
            end = self.replace_selection(new, in_edit_block=False)
            cur = QTextCursor(doc)
            cur.setPosition(end)
            count += 1
        edit.endEditBlock()
        return count

    def set_page_size_mm(self, w: float, h: float):
        p = PageSetup.from_dict(self.editor.page.to_dict())
        p.size, p.width_mm, p.height_mm = "Custom", min(w, h), max(w, h)
        p.orientation = "landscape" if w > h else "portrait"
        self._apply_page(p)

    def page_size_px(self) -> QSizeF:
        w, h = self.editor.page.page_px()
        return QSizeF(w, h)


def show_corrupt_hint(parent, path: str, exc: Exception) -> None:
    show_error(parent, _t("Cannot open document"),
               _t("MyOffice could not open \u201c{0}\u201d.").format(os.path.basename(path)), str(exc))
