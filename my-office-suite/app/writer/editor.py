"""Paginated WYSIWYG editor canvas.

The text lives in a ``QTextDocument`` shown by an editable ``QGraphicsTextItem``
inside a ``QGraphicsView``.  The document is paginated by Qt's layout engine
(``setPageSize`` + root-frame margins); page backgrounds, page gaps and
headers/footers are painted by separate scene items.  Because the view is
scaled for zooming, line breaks stay identical at every zoom level
(``useDesignMetrics``), exactly like a real word processor.
"""
from __future__ import annotations

import os
import uuid
from functools import lru_cache

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QImage, QKeySequence,
                           QPainter, QPen, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat,
                           QTransform)
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
                               QStyle, QStyleOptionGraphicsItem)

from app.services.config import cfg
from app.writer.model import (HeaderFooter, P_BIBLIO, P_DEL, P_FOOTNOTE, P_FOOTNOTE_ID, P_INS, P_STYLE, P_TOC,
                              PageSetup, apply_style, count_words,
                              has_arabic, has_devanagari)
from app.writer.revisions import RevisionEngine, RevisionHighlighter

GAP = 18.0


@lru_cache(maxsize=256)
def _family_supports(family: str, system: str) -> bool:
    ws = {"arabic": QFontDatabase.Arabic, "devanagari": QFontDatabase.Devanagari}[system]
    try:
        return ws in QFontDatabase.writingSystems(family)
    except Exception:
        return False


class PageTextItem(QGraphicsTextItem):
    cursorChanged = Signal()
    contextMenuAt = Signal(object)

    def __init__(self, editor: "WriterEditor"):
        super().__init__()
        self.editor = editor
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)
        self.setDefaultTextColor(QColor("#000000"))
        self.setZValue(1)

    # no dashed focus rectangle
    def paint(self, painter, option, widget=None):
        opt = QStyleOptionGraphicsItem(option)
        opt.state = opt.state & ~QStyle.State_HasFocus & ~QStyle.State_Selected
        super().paint(painter, opt, widget)

    def _prepare_typing(self, text: str) -> None:
        """Before text is typed: script-aware font switch and tracked-insert format."""
        ed = self.editor
        cur = self.textCursor()
        fmt = cur.charFormat()
        changed = False
        fams = fmt.fontFamilies() or [self.document().defaultFont().family()]
        fam = fams[0] if fams else ""
        if ed.auto_script_fonts and text:
            if has_arabic(text) and not _family_supports(fam, "arabic"):
                fmt.setFontFamilies([cfg().get("writer.urdu_font", "Noto Nastaliq Urdu")])
                changed = True
            elif has_devanagari(text) and not _family_supports(fam, "devanagari"):
                fmt.setFontFamilies([cfg().get("writer.hindi_font", "Noto Sans Devanagari")])
                changed = True
        if fmt.hasProperty(P_FOOTNOTE):  # typing right after a footnote number: ordinary text, not part of it
            fmt.clearProperty(P_FOOTNOTE)
            fmt.clearProperty(P_FOOTNOTE_ID)
            fmt.setVerticalAlignment(QTextCharFormat.AlignNormal)
            changed = True
        if ed.track_changes:
            fmt = ed.revisions.insertion_format(fmt)
            changed = True
        elif fmt.hasProperty(P_INS) or fmt.hasProperty(P_DEL):
            fmt.clearProperty(P_INS)
            fmt.clearProperty(P_DEL)
            changed = True
        if changed:
            cur.setCharFormat(fmt)
            self.setTextCursor(cur)

    def keyPressEvent(self, event):
        ed = self.editor
        if ed.read_only:
            if event.matches(QKeySequence.Copy) or event.matches(QKeySequence.SelectAll) or \
                    event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp,
                                    Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
                super().keyPressEvent(event)
                self.cursorChanged.emit()
            return
        if event.matches(QKeySequence.Paste):
            ed.paste()
            return
        if event.matches(QKeySequence.Cut) and ed.track_changes:
            ed.cut()
            return
        if ed.track_changes:
            cur = self.textCursor()
            key = event.key()
            if key in (Qt.Key_Backspace, Qt.Key_Delete):
                if cur.hasSelection():
                    s, e = cur.selectionStart(), cur.selectionEnd()
                elif key == Qt.Key_Backspace:
                    e = cur.position()
                    c2 = QTextCursor(cur)
                    c2.movePosition(QTextCursor.PreviousCharacter)
                    s = c2.position()
                else:
                    s = cur.position()
                    c2 = QTextCursor(cur)
                    c2.movePosition(QTextCursor.NextCharacter)
                    e = c2.position()
                ed.revisions.tracked_delete(s, e, cur)
                nc = self.textCursor()
                nc.setPosition(s if key == Qt.Key_Backspace else min(e, self.document().characterCount() - 1))
                self.setTextCursor(nc)
                self.cursorChanged.emit()
                return
            text = event.text()
            if text and text.isprintable() and cur.hasSelection():
                s, e = cur.selectionStart(), cur.selectionEnd()
                ed.revisions.tracked_delete(s, e, cur)
                nc = self.textCursor()
                nc.setPosition(e)
                self.setTextCursor(nc)
        text = event.text()
        if text and (text.isprintable() or text in ("\r", "\t")):
            self._prepare_typing(text)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            cur = self.textCursor()
            bf = cur.block().blockFormat()
            style = str(bf.property(P_STYLE) or "") if bf.hasProperty(P_STYLE) else ""
            heading_like = bf.headingLevel() > 0 or style in ("Title", "Subtitle", "Quote", "Code")
            at_end = cur.atBlockEnd() and not cur.hasSelection()
            super().keyPressEvent(event)
            c = self.textCursor()
            nbf = c.blockFormat()
            if (nbf.pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore) or nbf.hasProperty(P_TOC) \
                    or nbf.hasProperty(P_BIBLIO):
                nbf.setPageBreakPolicy(QTextFormat.PageBreak_Auto)
                nbf.clearProperty(P_TOC)
                nbf.clearProperty(P_BIBLIO)
                c.joinPreviousEditBlock()
                c.setBlockFormat(nbf)
                c.endEditBlock()
                self.setTextCursor(c)
            if heading_like and at_end:
                c = self.textCursor()
                c.joinPreviousEditBlock()
                fresh = QTextCharFormat()
                if self.editor.track_changes:
                    fresh = self.editor.revisions.insertion_format(fresh)
                c.setBlockCharFormat(fresh)
                c.endEditBlock()
                apply_style(c, "Normal", self.document().defaultFont().pointSizeF())
                c.setCharFormat(fresh)
                self.setTextCursor(c)
            self.cursorChanged.emit()
            return
        super().keyPressEvent(event)
        self.cursorChanged.emit()

    def inputMethodEvent(self, event):
        if self.editor.read_only:
            return
        if event.commitString():
            self._prepare_typing(event.commitString())
        super().inputMethodEvent(event)
        self.cursorChanged.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier:
            href = self.document().documentLayout().anchorAt(event.pos())
            if href:
                self.editor.open_link(href)
                event.accept()
                return
        super().mousePressEvent(event)
        self.cursorChanged.emit()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.cursorChanged.emit()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if event.buttons():
            self.cursorChanged.emit()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        self.cursorChanged.emit()

    def contextMenuEvent(self, event):
        # move the caret to the click position unless clicking inside the selection
        cur = self.textCursor()
        pos = self.document().documentLayout().hitTest(event.pos(), Qt.FuzzyHit)
        if pos >= 0 and not (cur.hasSelection() and cur.selectionStart() <= pos <= cur.selectionEnd()):
            cur.setPosition(pos)
            self.setTextCursor(cur)
            self.cursorChanged.emit()
        self.contextMenuAt.emit(event.screenPos())
        event.accept()

    def dropEvent(self, event):
        if self.editor.read_only:
            event.ignore()
            return
        md = event.mimeData()
        if md.hasUrls() and any(u.isLocalFile() and u.toLocalFile().lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")) for u in md.urls()):
            pos = self.document().documentLayout().hitTest(event.pos(), Qt.FuzzyHit)
            cur = self.textCursor()
            if pos >= 0:
                cur.setPosition(pos)
                self.setTextCursor(cur)
            for u in md.urls():
                if u.isLocalFile():
                    self.editor.insert_image_file(u.toLocalFile())
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class PageBackground(QGraphicsItem):
    def __init__(self, editor: "WriterEditor"):
        super().__init__()
        self.editor = editor
        self.setZValue(-1)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)

    def boundingRect(self):
        w, h = self.editor.page.page_px()
        return QRectF(-8, -8, w + 16, h * self.editor.page_count() + 16)

    def paint(self, p: QPainter, option, widget=None):
        w, h = self.editor.page.page_px()
        exposed = option.exposedRect
        shadow = QColor(0, 0, 0, 28)
        for i in range(self.editor.page_count()):
            r = QRectF(0, i * h, w, h)
            if not r.adjusted(-8, -8, 8, 8).intersects(exposed):
                continue
            p.fillRect(r.translated(2, 3), shadow)
            p.fillRect(r, Qt.white)
            if self.editor.watermark:
                from app.writer.render import draw_watermark

                draw_watermark(p, w, h, self.editor.watermark, i * h)


class PageDecor(QGraphicsItem):
    """Page gaps, crop marks and header/footer text - painted above the text."""

    def __init__(self, editor: "WriterEditor"):
        super().__init__()
        self.editor = editor
        self.setZValue(5)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setFlag(QGraphicsItem.ItemUsesExtendedStyleOption, True)

    def boundingRect(self):
        w, h = self.editor.page.page_px()
        return QRectF(-40, -GAP, w + 80, h * self.editor.page_count() + 2 * GAP)

    def paint(self, p: QPainter, option, widget=None):
        ed = self.editor
        w, h = ed.page.page_px()
        top, right, bottom, left = ed.page.margins_px()
        n = ed.page_count()
        exposed = option.exposedRect
        canvas = ed.backgroundBrush().color()
        mark_pen = QPen(QColor("#b9bec5"), 0.8)
        font = QFont(ed.header_font_family())
        font.setPixelSize(12)
        p.setFont(font)
        for i in range(n):
            page = QRectF(0, i * h, w, h)
            if not page.adjusted(-40, -GAP, 40, GAP).intersects(exposed):
                continue
            if i > 0:
                band = QRectF(-40, i * h - GAP / 2, w + 80, GAP)
                p.fillRect(band, canvas)
                p.fillRect(QRectF(2, i * h - GAP / 2, w, 3), QColor(0, 0, 0, 22))
            # crop marks at the text-area corners
            p.setPen(mark_pen)
            x0, y0, x1, y1 = left, i * h + top, w - right, (i + 1) * h - bottom
            L = 14
            for (x, y, dx, dy) in ((x0, y0, -1, -1), (x1, y0, 1, -1), (x0, y1, -1, 1), (x1, y1, 1, 1)):
                p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))
            # header / footer
            p.setPen(QColor("#5f6368"))
            hf = ed.hf
            text_rect_w = w - left - right
            if hf.header:
                txt = hf.render(hf.header, i + 1, n, ed.title)
                r = QRectF(left, i * h + ed.mm(ed.page.header_mm), text_rect_w, 20)
                p.drawText(r, _align(hf.header_align) | Qt.AlignTop, txt)
            if hf.footer:
                txt = hf.render(hf.footer, i + 1, n, ed.title)
                r = QRectF(left, (i + 1) * h - ed.mm(ed.page.footer_mm) - 20, text_rect_w, 20)
                p.drawText(r, _align(hf.footer_align) | Qt.AlignBottom, txt)
            page_notes = ed.footnotes_by_page().get(i)
            if page_notes:
                from app.writer.notes import draw_notes

                draw_notes(p, page_notes, left, (i + 1) * h - bottom - ed.notes_reserve, text_rect_w,
                           ed.document().defaultFont().family())
                p.setFont(font)


def _align(name: str):
    return {"left": Qt.AlignLeft | Qt.AlignAbsolute, "right": Qt.AlignRight | Qt.AlignAbsolute}.get(name, Qt.AlignHCenter)


class WriterEditor(QGraphicsView):
    zoomChanged = Signal(float)
    pageCountChanged = Signal(int)
    cursorChanged = Signal()
    contentChanged = Signal()
    contextMenuRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCanvas")
        self.page = PageSetup()
        self.hf = HeaderFooter()
        self.title = ""
        self.read_only = False
        self.watermark = ""
        self.track_changes = False
        self.auto_script_fonts = True
        self.comments: dict = {}
        self.image_store: dict[str, tuple[bytes, str]] = {}
        self._zoom = 1.0
        self._page_count = 1

        self._scene = QGraphicsScene(self)
        self._scene.setItemIndexMethod(QGraphicsScene.NoIndex)
        self.setScene(self._scene)
        self.setBackgroundBrush(QColor("#e6e8eb"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setDragMode(QGraphicsView.NoDrag)

        self.item = PageTextItem(self)
        self.bg = PageBackground(self)
        self.decor = PageDecor(self)
        for it in (self.bg, self.item, self.decor):
            self._scene.addItem(it)

        doc = self.item.document()
        doc.setUseDesignMetrics(True)
        doc.setDocumentMargin(0)
        base = QFont(cfg().get("writer.font_family", "Calibri"))
        base.setPointSizeF(float(cfg().get("writer.font_size", 11)))
        doc.setDefaultFont(base)
        self.revisions = RevisionEngine(doc, lambda: cfg().get("author", "User"))
        self.highlighter = RevisionHighlighter(doc)
        self.notes_reserve = 0.0
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(450)
        self._notes_timer.timeout.connect(self.update_notes_reserve)
        doc.documentLayout().pageCountChanged.connect(self._on_page_count)
        doc.contentsChanged.connect(self._on_contents_changed)
        self.item.cursorChanged.connect(self.cursorChanged)
        self.item.contextMenuAt.connect(self.contextMenuRequested)
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(60)
        self._relayout_timer.timeout.connect(self._sync_geometry)
        self.apply_page_setup(clear_undo=True)
        self.item.setFocus()

    # ------------------------------------------------------------------ helpers
    def document(self) -> QTextDocument:
        return self.item.document()

    def textCursor(self) -> QTextCursor:  # noqa: N802 (Qt naming)
        return self.item.textCursor()

    def setTextCursor(self, cursor: QTextCursor) -> None:  # noqa: N802
        self.item.setTextCursor(cursor)
        self.ensure_cursor_visible()
        self.cursorChanged.emit()

    def mm(self, value: float) -> float:
        return value * 96.0 / 25.4

    def header_font_family(self) -> str:
        return self.document().defaultFont().family()

    def page_count(self) -> int:
        return max(1, self._page_count)

    # ------------------------------------------------------------------ page setup
    def apply_page_setup(self, page: PageSetup | None = None, clear_undo: bool = False) -> None:
        if page is not None:
            self.page = page
        doc = self.document()
        w, h = self.page.page_px()
        top, right, bottom, left = self.page.margins_px()
        doc.setPageSize(QSizeF(w, h))
        bottom += getattr(self, "notes_reserve", 0.0)   # room for the footnotes of the fullest page
        ff = doc.rootFrame().frameFormat()
        if (abs(ff.topMargin() - top) > 0.01 or abs(ff.bottomMargin() - bottom) > 0.01
                or abs(ff.leftMargin() - left) > 0.01 or abs(ff.rightMargin() - right) > 0.01):
            ff.setTopMargin(top)
            ff.setBottomMargin(bottom)
            ff.setLeftMargin(left)
            ff.setRightMargin(right)
            ff.setPadding(0)
            ff.setBorder(0)
            doc.rootFrame().setFrameFormat(ff)
        if clear_undo:
            doc.clearUndoRedoStacks()
        self._page_count = doc.pageCount()
        self._sync_geometry()

    def _on_page_count(self, n: int) -> None:
        self._page_count = max(1, n)
        self._relayout_timer.start()
        self.pageCountChanged.emit(self._page_count)

    def _on_contents_changed(self) -> None:
        if self.document().pageSize().height() <= 0:  # something reset the pagination
            self.apply_page_setup()
        if getattr(self, "_notes_timer", None) is not None:
            self._notes_timer.start()
        self.contentChanged.emit()

    def footnotes_by_page(self) -> dict:
        from app.writer import notes

        doc = self.document()
        key = (doc.revision(), doc.pageCount(), getattr(self, "notes_reserve", 0.0))
        cache = getattr(self, "_notes_cache", None)
        if cache is None or cache[0] != key:
            self._notes_cache = (key, notes.notes_by_page(doc, self.page.page_px()[1]))
        return self._notes_cache[1]

    def update_notes_reserve(self) -> None:
        """Keep the text clear of the footnote area (recomputed shortly after edits)."""
        from app.writer import notes

        w, h = self.page.page_px()
        top, right, bottom, left = self.page.margins_px()
        doc = self.document()
        new = notes.reserve_px(doc, h, w - left - right, self.header_font_family(), 0.45 * (h - top - bottom)) \
            if notes.has_footnotes(doc) else 0.0
        if abs(new - getattr(self, "notes_reserve", 0.0)) > 3:
            self.notes_reserve = new
            self.apply_page_setup()
        self.decor.update()

    def _sync_geometry(self) -> None:
        w, h = self.page.page_px()
        n = self.page_count()
        self.bg.prepareGeometryChange()
        self.decor.prepareGeometryChange()
        self._scene.setSceneRect(QRectF(-30, -30, w + 60, h * n + 60))
        self.bg.update()
        self.decor.update()

    def refresh_decorations(self) -> None:
        self.decor.update()

    # ------------------------------------------------------------------ zoom
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, z: float) -> None:
        z = max(0.25, min(5.0, float(z)))
        self._zoom = z
        self.setTransform(QTransform.fromScale(z, z))
        self.ensure_cursor_visible()
        self.zoomChanged.emit(z)

    def zoom_in(self):
        self.set_zoom(self._zoom * 1.15)

    def zoom_out(self):
        self.set_zoom(self._zoom / 1.15)

    def fit_width(self):
        w, _ = self.page.page_px()
        avail = max(200, self.viewport().width() - 40)
        self.set_zoom(avail / w)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------ cursor/page info
    def cursor_rect(self, cursor: QTextCursor | None = None) -> QRectF:
        cursor = cursor or self.textCursor()
        block = cursor.block()
        layout = self.document().documentLayout()
        br = layout.blockBoundingRect(block)
        bl = block.layout()
        if bl is None or bl.lineCount() == 0:
            return QRectF(br.left(), br.top(), 2, 16)
        line = bl.lineForTextPosition(cursor.position() - block.position())
        if not line.isValid():
            return QRectF(br.left(), br.top(), 2, 16)
        x = line.cursorToX(cursor.position() - block.position())[0]
        return QRectF(br.left() + x, br.top() + line.y(), 2, line.height())

    def ensure_cursor_visible(self) -> None:
        r = self.cursor_rect()
        self.ensureVisible(r.adjusted(-10, -20, 10, 20), 10, 20)

    def current_page(self) -> int:
        _, h = self.page.page_px()
        return int(self.cursor_rect().top() // h) + 1

    def block_page(self, block) -> int:
        _, h = self.page.page_px()
        r = self.document().documentLayout().blockBoundingRect(block)
        return int(r.top() // h) + 1

    def statistics(self) -> dict:
        text = self.document().toPlainText()
        return {"words": count_words(text), "characters": len(text.replace("\n", "")),
                "characters_no_spaces": len("".join(text.split())), "paragraphs": self.document().blockCount(),
                "pages": self.page_count()}

    # ------------------------------------------------------------------ editing commands
    def focus_editor(self):
        self.setFocus()
        self.item.setFocus()

    def set_read_only(self, ro: bool):
        self.read_only = ro
        self.item.setTextInteractionFlags(Qt.TextBrowserInteraction if ro else Qt.TextEditorInteraction)

    def undo(self):
        c = self.textCursor()
        self.document().undo(c)
        self.setTextCursor(c)

    def redo(self):
        c = self.textCursor()
        self.document().redo(c)
        self.setTextCursor(c)

    def copy(self):
        c = self.textCursor()
        if c.hasSelection():
            md = QMimeData()
            frag = c.selection()
            md.setHtml(frag.toHtml())
            md.setText(c.selectedText().replace("\u2029", "\n").replace("\u2028", "\n"))
            QApplication.clipboard().setMimeData(md)

    def cut(self):
        if self.read_only:
            return
        c = self.textCursor()
        if not c.hasSelection():
            return
        self.copy()
        if self.track_changes:
            s, e = c.selectionStart(), c.selectionEnd()
            self.revisions.tracked_delete(s, e, c)
            nc = self.textCursor()
            nc.setPosition(s)
            self.setTextCursor(nc)
        else:
            c.removeSelectedText()
            self.setTextCursor(c)

    def paste(self):
        if self.read_only:
            return
        md = QApplication.clipboard().mimeData()
        self.insert_mime(md)

    def insert_mime(self, md) -> None:
        c = self.textCursor()
        c.beginEditBlock()
        if self.track_changes and c.hasSelection():
            s, e = c.selectionStart(), c.selectionEnd()
            self.revisions.tracked_delete(s, e, c)
            c.setPosition(e)
        elif c.hasSelection():
            c.removeSelectedText()
        start = c.position()
        if md.hasImage():
            img = QImage(md.imageData())
            if not img.isNull():
                self._insert_qimage(c, img, None, "png")
        elif md.hasHtml():
            c.insertHtml(md.html())
        elif md.hasText():
            c.insertText(md.text())
        end = c.position()
        if self.track_changes and end > start:
            sel = QTextCursor(self.document())
            sel.setPosition(start)
            sel.setPosition(end, QTextCursor.KeepAnchor)
            f = QTextCharFormat()
            from app.writer.model import revision_stamp
            f.setProperty(P_INS, revision_stamp(cfg().get("author", "User")))
            sel.mergeCharFormat(f)
        c.endEditBlock()
        self.setTextCursor(c)

    def select_all(self):
        c = self.textCursor()
        c.select(QTextCursor.Document)
        self.setTextCursor(c)

    def open_link(self, href: str) -> None:
        if href.startswith("#"):
            name = href[1:]
            block = self.document().begin()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    f = it.fragment()
                    it += 1
                    if name in (f.charFormat().anchorNames() or []):
                        c = QTextCursor(self.document())
                        c.setPosition(f.position())
                        self.setTextCursor(c)
                        return
                block = block.next()
            return
        QDesktopServices.openUrl(QUrl(href))

    # ------------------------------------------------------------------ images
    def text_width_px(self) -> float:
        w, _ = self.page.page_px()
        _, right, _, left = self.page.margins_px()
        return w - left - right

    def register_image(self, data: bytes, ext: str, img: QImage | None = None) -> str:
        name = f"img_{uuid.uuid4().hex[:10]}.{ext}"
        if img is None:
            img = QImage.fromData(data)
        self.document().addResource(QTextDocument.ImageResource, QUrl(name), img)
        self.image_store[name] = (data, ext)
        return name

    def _insert_qimage(self, cursor: QTextCursor, img: QImage, data: bytes | None, ext: str,
                       width: float | None = None, height: float | None = None) -> None:
        if data is None:
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice

            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            data, ext = bytes(ba.data()), "png"
        name = self.register_image(data, ext, img)
        fmt = QTextImageFormat()
        fmt.setName(name)
        w = width or img.width()
        h = height or img.height()
        maxw = self.text_width_px()
        if w > maxw:
            h = h * maxw / w
            w = maxw
        fmt.setWidth(w)
        fmt.setHeight(h)
        cursor.insertImage(fmt)

    def insert_image_file(self, path: str) -> None:
        with open(path, "rb") as fh:
            data = fh.read()
        img = QImage.fromData(data)
        if img.isNull():
            raise ValueError(f"Unsupported image: {os.path.basename(path)}")
        ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
        if ext == "jpeg":
            ext = "jpg"
        if ext not in ("png", "jpg", "gif", "bmp"):
            data, ext = None, "png"
        c = self.textCursor()
        self._insert_qimage(c, img, data, ext)
        self.setTextCursor(c)

    def insert_qimage(self, img: QImage) -> None:
        c = self.textCursor()
        self._insert_qimage(c, img, None, "png")
        self.setTextCursor(c)

    def image_bytes(self, name: str) -> tuple[bytes, str] | None:
        if name in self.image_store:
            return self.image_store[name]
        res = self.document().resource(QTextDocument.ImageResource, QUrl(name))
        img = res if isinstance(res, QImage) else (QImage(res) if res is not None else QImage())
        if img.isNull():
            return None
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        return bytes(ba.data()), "png"

    # ------------------------------------------------------------------ find
    def find(self, text: str, backward=False, case=False, whole=False, regex=False, from_cursor=None):
        from PySide6.QtCore import QRegularExpression

        doc = self.document()
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindBackward
        if case:
            flags |= QTextDocument.FindCaseSensitively
        if whole:
            flags |= QTextDocument.FindWholeWords
        cur = from_cursor or self.textCursor()
        if regex:
            rx = QRegularExpression(text)
            if not case:
                rx.setPatternOptions(QRegularExpression.CaseInsensitiveOption)
            res = doc.find(rx, cur, flags)
        else:
            res = doc.find(text, cur, flags)
        if res.isNull():  # wrap around
            start = QTextCursor(doc)
            if backward:
                start.movePosition(QTextCursor.End)
            res = doc.find(QRegularExpression(text) if regex else text, start, flags)
        return None if res.isNull() else res
