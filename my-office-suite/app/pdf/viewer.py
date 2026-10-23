"""PDF viewer & editor tab (PyMuPDF rendering, annotations, text editing, redaction, page tools)."""
from __future__ import annotations

import math

import html
import logging
import os
import tempfile
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QSpinBox, QSplitter,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.pdf import ops
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_file, get_save_file, handle_exception, show_error, show_info
from app.shell.document import DocumentError, DocumentWidget, OpenCancelled
from app.shell.icons import icon
from app.shell.ribbon import Ribbon
from app.shell.widgets import ColorMenu, ZoomSlider

log = logging.getLogger(__name__)
GAP = 14.0
PT2PX = 96 / 72
MODES = ["select", "hand", "highlight", "underline", "strike", "note", "rect", "ellipse", "line", "arrow", "ink",
         "text", "image", "redact", "edit_text", "signature", "callout", "measure", "measure_area"]


class PdfView(QGraphicsView):
    pageChanged = Signal(int)
    modeFinished = Signal()

    def __init__(self, doc_widget):
        super().__init__()
        self.dw = doc_widget
        self.setObjectName("PageCanvas")
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setScene(QGraphicsScene(self))
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.zoom = 1.0
        self.items: list[QGraphicsPixmapItem] = []
        self.offsets: list[float] = []
        self.rendered: dict[int, float] = {}
        self.mode = "select"
        self._drag = None
        self._sel_words: list = []
        self._sel_page = -1
        self._overlay: list = []
        self.verticalScrollBar().valueChanged.connect(self._schedule)
        self._timer = QTimer(self, singleShot=True, interval=60, timeout=self.render_visible)
        self.ink_color = QColor("#d93025")
        self.ink_width = 2.0

    # -- layout
    def rebuild(self):
        sc = self.scene()
        sc.clear()
        self.items, self.offsets, self.rendered = [], [], {}
        self._overlay = []
        doc = self.dw.doc
        if doc is None:
            return
        y = GAP
        maxw = 0.0
        for i in range(doc.page_count):
            r = doc[i].rect
            bg = QGraphicsRectItem(0, y, r.width, r.height)
            bg.setBrush(QColor("white"))
            bg.setPen(QPen(QColor("#c8ccd0"), 0.6))
            sc.addItem(bg)
            it = QGraphicsPixmapItem()
            it.setPos(0, y)
            it.setTransformationMode(Qt.SmoothTransformation)
            sc.addItem(it)
            self.items.append(it)
            self.offsets.append(y)
            y += r.height + GAP
            maxw = max(maxw, r.width)
        for it, off in zip(self.items, self.offsets):
            pass
        # center pages horizontally
        for i, it in enumerate(self.items):
            w = doc[i].rect.width
            dx = (maxw - w) / 2
            it.setX(dx)
        for item in sc.items():
            if isinstance(item, QGraphicsRectItem):
                pass
        sc.setSceneRect(QRectF(-GAP, 0, maxw + 2 * GAP, y))
        self.apply_zoom()

    def apply_zoom(self):
        self.resetTransform()
        self.scale(self.zoom * PT2PX, self.zoom * PT2PX)
        self.rendered.clear()
        self._schedule()

    def set_zoom(self, z: float):
        self.zoom = max(0.1, min(8.0, z))
        self.apply_zoom()
        self.dw.zoom_slider.set_zoom(self.zoom)

    def fit_width(self):
        if not self.items:
            return
        w = self.sceneRect().width()
        self.set_zoom((self.viewport().width() - 24) / (w * PT2PX))

    def fit_page(self):
        if not self.items or self.dw.doc is None:
            return
        r = self.dw.doc[self.current_page()].rect
        self.set_zoom(min((self.viewport().width() - 24) / (r.width * PT2PX),
                          (self.viewport().height() - 24) / ((r.height + GAP) * PT2PX)))

    def _schedule(self, *_):
        self._timer.start()
        self.pageChanged.emit(self.current_page())

    def visible_pages(self) -> list[int]:
        if not self.items:
            return []
        top = self.mapToScene(0, 0).y()
        bottom = self.mapToScene(0, self.viewport().height()).y()
        doc = self.dw.doc
        out = []
        for i, off in enumerate(self.offsets):
            h = doc[i].rect.height
            if off + h >= top - 400 and off <= bottom + 400:
                out.append(i)
        return out

    def render_visible(self):
        doc = self.dw.doc
        if doc is None:
            return
        scale = min(4.0, self.zoom * PT2PX * self.devicePixelRatioF())
        vis = self.visible_pages()
        for i in vis:
            if abs(self.rendered.get(i, 0) - scale) < 1e-3:
                continue
            try:
                pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
            except Exception as exc:
                log.warning("render page %s failed: %s", i, exc)
                continue
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            it = self.items[i]
            it.setPixmap(QPixmap.fromImage(img))
            it.setScale(1 / scale)
            self.rendered[i] = scale
        for i in list(self.rendered):  # free far-away pages
            if i not in vis and len(self.rendered) > 12:
                self.items[i].setPixmap(QPixmap())
                del self.rendered[i]

    def refresh_page(self, i: int):
        self.rendered.pop(i, None)
        self._schedule()

    def current_page(self) -> int:
        if not self.items:
            return 0
        y = self.mapToScene(0, self.viewport().height() // 3).y()
        for i in range(len(self.offsets) - 1, -1, -1):
            if self.offsets[i] <= y:
                return i
        return 0

    def goto_page(self, i: int, y_pt: float = 0.0):
        if 0 <= i < len(self.offsets):
            self.verticalScrollBar().setValue(int((self.offsets[i] + y_pt - GAP / 2) * self.zoom * PT2PX))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._schedule()

    # -- coordinates
    def page_at(self, scene_pt: QPointF):
        doc = self.dw.doc
        for i, off in enumerate(self.offsets):
            r = doc[i].rect
            x0 = self.items[i].x()
            if off <= scene_pt.y() <= off + r.height and x0 <= scene_pt.x() <= x0 + r.width:
                return i, pymupdf.Point(scene_pt.x() - x0, scene_pt.y() - off)
        return None, None

    def page_rect_to_scene(self, i: int, r: pymupdf.Rect) -> QRectF:
        return QRectF(self.items[i].x() + r.x0, self.offsets[i] + r.y0, r.width, r.height)

    # -- overlays (selection, drag rectangles)
    def clear_overlay(self):
        for it in self._overlay:
            self.scene().removeItem(it)
        self._overlay = []

    def show_selection(self):
        self.clear_overlay()
        if self._sel_page < 0:
            return
        for w in self._sel_words:
            r = self.page_rect_to_scene(self._sel_page, pymupdf.Rect(w[:4]))
            it = self.scene().addRect(r, QPen(Qt.NoPen), QColor(26, 115, 232, 70))
            self._overlay.append(it)

    def selected_text(self) -> str:
        from app.pdf.ops import fix_rtl_text

        return fix_rtl_text(self._selected_raw())

    def _selected_raw(self) -> str:
        if not self._sel_words:
            return ""
        lines, last = [], None
        for w in self._sel_words:
            key = (w[5], w[6])
            if last is not None and key != last:
                lines.append("\n")
            elif lines:
                lines.append(" ")
            lines.append(w[4])
            last = key
        return "".join(lines)

    # -- mouse
    def mousePressEvent(self, e):
        if self.mode == "hand" or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        i, pt = self.page_at(sp)
        if i is None:
            self._sel_words, self._sel_page = [], -1
            self.clear_overlay()
            return super().mousePressEvent(e)
        if self.mode == "measure_area":
            poly = getattr(self, "_poly", None)
            if poly is None or poly["page"] != i:
                self._poly = poly = {"page": i, "points": []}
            poly["points"].append(pt)
            self._draw_poly()
            return
        self._drag = {"page": i, "start": pt, "end": pt, "points": [pt], "scene_start": sp}
        if self.mode == "note":
            self.dw.add_note(i, pt)
            self._drag = None
        elif self.mode == "edit_text":
            self.dw.edit_text_at(i, pt)
            self._drag = None

    def _draw_poly(self, hover=None):
        poly = getattr(self, "_poly", None)
        self.clear_overlay()
        if not poly or not poly["points"]:
            return
        i = poly["page"]
        x0, y0 = self.items[i].x(), self.offsets[i]
        pts = poly["points"] + ([hover] if hover is not None else [])
        path = QPainterPath(QPointF(x0 + pts[0].x, y0 + pts[0].y))
        for q in pts[1:]:
            path.lineTo(QPointF(x0 + q.x, y0 + q.y))
        if len(pts) > 2:
            path.closeSubpath()
        self._overlay.append(self.scene().addPath(path, QPen(QColor("#1a73e8"), 1.2, Qt.DashLine),
                                                  QColor(26, 115, 232, 30)))

    def mouseDoubleClickEvent(self, e):
        poly = getattr(self, "_poly", None)
        if self.mode == "measure_area" and poly and len(poly["points"]) >= 3:
            pts = poly["points"]
            if len(pts) > 3 and abs(pts[-1].x - pts[-2].x) < 2 and abs(pts[-1].y - pts[-2].y) < 2:
                pts = pts[:-1]  # the double click's first press added a duplicate vertex
            self._poly = None
            self.clear_overlay()
            self.dw.add_area_measure(poly["page"], pts)
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self.mode == "measure_area" and getattr(self, "_poly", None) and self._drag is None:
            sp = self.mapToScene(e.position().toPoint())
            i, pt = self.page_at(sp)
            if i == self._poly["page"]:
                self._draw_poly(pt)
            return super().mouseMoveEvent(e)
        if self._drag is None:
            return super().mouseMoveEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        i = self._drag["page"]
        x0 = self.items[i].x()
        pt = pymupdf.Point(sp.x() - x0, sp.y() - self.offsets[i])
        self._drag["end"] = pt
        mode = self.mode
        if mode in ("select", "highlight", "underline", "strike"):
            self._select_words(i, self._drag["start"], pt)
        elif mode in ("measure", "callout"):
            self.clear_overlay()
            a = self.page_rect_to_scene(i, pymupdf.Rect(self._drag["start"], self._drag["start"]))
            b = self.page_rect_to_scene(i, pymupdf.Rect(pt, pt))
            self._overlay.append(self.scene().addLine(a.x(), a.y(), b.x(), b.y(), QPen(QColor("#1a73e8"), 1.2)))
            if mode == "measure":
                lab = self.scene().addSimpleText(self.dw.distance_label(self._drag["start"], pt))
                lab.setBrush(QColor("#1a73e8"))
                lab.setPos(b.x() + 8, b.y() - 18)
                self._overlay.append(lab)
        elif mode == "ink":
            self._drag["points"].append(pt)
            self.clear_overlay()
            path = QPainterPath()
            pts = self._drag["points"]
            path.moveTo(QPointF(x0 + pts[0].x, self.offsets[i] + pts[0].y))
            for p in pts[1:]:
                path.lineTo(QPointF(x0 + p.x, self.offsets[i] + p.y))
            self._overlay.append(self.scene().addPath(path, QPen(self.ink_color, self.ink_width)))
        else:
            self.clear_overlay()
            r = pymupdf.Rect(self._drag["start"], pt).normalize()
            self._overlay.append(self.scene().addRect(self.page_rect_to_scene(i, r), QPen(QColor("#1a73e8"), 0.8, Qt.DashLine),
                                                      QColor(26, 115, 232, 25)))

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return super().mouseReleaseEvent(e)
        d, self._drag = self._drag, None
        i = d["page"]
        r = pymupdf.Rect(d["start"], d["end"]).normalize()
        mode = self.mode
        if mode == "select":
            return
        if mode in ("highlight", "underline", "strike"):
            if self._sel_words:
                self.dw.annotate_words(mode, self._sel_page, self._sel_words)
            self._sel_words, self._sel_page = [], -1
            self.clear_overlay()
            return
        self.clear_overlay()
        if mode == "ink":
            if len(d["points"]) > 2:
                self.dw.add_ink(i, d["points"])
            return
        if r.width < 4 or r.height < 4:
            if mode in ("text", "image", "signature"):
                w, h = {"text": (220, 40), "image": (180, 140), "signature": (200, 70)}[mode]
                r = pymupdf.Rect(d["start"].x, d["start"].y, d["start"].x + w, d["start"].y + h)
            elif mode in ("line", "arrow"):
                return
            elif mode in ("measure", "callout") and (abs(d["end"].x - d["start"].x) > 4 or
                                                    abs(d["end"].y - d["start"].y) > 4):
                pass
            elif mode == "callout":  # a click: point at it with a short default line
                d["end"] = pymupdf.Point(d["start"].x + 60, d["start"].y - 50)
            else:
                return
        self.dw.rect_action(mode, i, r, d["start"], d["end"])

    def _select_words(self, i, a: pymupdf.Point, b: pymupdf.Point):
        words = self.dw.words(i)
        r = pymupdf.Rect(a, b).normalize()
        if r.height < 6:  # single line drag: take words intersecting the band
            r = pymupdf.Rect(r.x0, min(a.y, b.y) - 3, r.x1, max(a.y, b.y) + 3)
        self._sel_words = [w for w in words if pymupdf.Rect(w[:4]).intersects(r)]
        self._sel_page = i
        self.show_selection()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
            return
        super().wheelEvent(e)

    def contextMenuEvent(self, e):
        sp = self.mapToScene(e.pos())
        i, pt = self.page_at(sp)
        m = QMenu(self)
        if self._sel_words:
            m.addAction(icon("copy"), _t("Copy Text"), self.dw.copy_text)
            m.addAction(icon("highlight_annot"), _t("Highlight"), lambda: self.dw.annotate_words("highlight", self._sel_page, self._sel_words))
            m.addAction(icon("underline_annot"), _t("Underline"), lambda: self.dw.annotate_words("underline", self._sel_page, self._sel_words))
            m.addAction(icon("strike_annot"), _t("Strikethrough"), lambda: self.dw.annotate_words("strike", self._sel_page, self._sel_words))
            m.addAction(icon("redact"), _t("Redact"), lambda: self.dw.redact_words(self._sel_page, self._sel_words))
            m.addSeparator()
        if i is not None:
            annot = self.dw.annot_at(i, pt)
            if annot is not None:
                m.addAction(icon("delete"), _t("Delete Annotation"), lambda: self.dw.delete_annot(i, annot))
                if annot.type[0] in (pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT):
                    m.addAction(icon("edit"), _t("Edit Annotation Text"), lambda: self.dw.edit_annot(i, annot))
                m.addSeparator()
            m.addAction(icon("note"), _t("Add Note Here"), lambda: self.dw.add_note(i, pt))
            m.addAction(icon("edit"), _t("Edit Text Here"), lambda: self.dw.edit_text_at(i, pt))
            m.addAction(icon("rotate"), _t("Rotate Page"), lambda: self.dw.rotate_pages([i], 90))
        m.exec(e.globalPos())


class TextInsertDialog(QDialog):
    def __init__(self, parent, title: str, text: str = "", size: float = 12.0):
        super().__init__(parent)
        self.setWindowTitle(title)
        f = QFormLayout(self)
        self.text = QPlainTextEdit(text)
        self.text.setMinimumHeight(90)
        self.size = QSpinBox()
        self.size.setRange(4, 144)
        self.size.setValue(int(round(size)))
        self.color = QComboBox()
        for name, c in ((_t("Black"), "#000000"), (_t("Blue"), "#1a56db"), (_t("Red"), "#d93025"), (_t("Green"), "#188038"),
                        (_t("Gray"), "#5f6368")):
            self.color.addItem(name, c)
        self.font = QComboBox()
        self.font.addItem("Sans (Helvetica)", "sans-serif")
        self.font.addItem("Serif (Times)", "serif")
        self.font.addItem("Mono (Courier)", "monospace")
        self.font.addItem("Urdu (Noto Nastaliq)", "urdu")
        self.font.addItem("Hindi (Noto Sans Devanagari)", "hindi")
        f.addRow(_t("Text:"), self.text)
        f.addRow(_t("Font:"), self.font)
        f.addRow(_t("Size (pt):"), self.size)
        f.addRow(_t("Color:"), self.color)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


def insert_rich_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str, size: float, color: str, font: str) -> None:
    """Insert text with HarfBuzz shaping (works for Urdu Nastaliq and Devanagari)."""
    from app.services.paths import resource_path

    css_font = {"sans-serif": "sans-serif", "serif": "serif", "monospace": "monospace"}.get(font, "sans-serif")
    archive = None
    css = ""
    rtl = any("\u0600" <= ch <= "\u06ff" for ch in text)
    if font in ("urdu", "hindi") or rtl:
        fname = "NotoNastaliqUrdu.ttf" if (font == "urdu" or (rtl and font != "hindi")) else "NotoSansDevanagari.ttf"
        archive = pymupdf.Archive(str(resource_path("fonts")))
        css = f"@font-face {{font-family: myfont; src: url({fname});}}"
        css_font = "myfont"
    body = html.escape(text).replace("\n", "<br>")
    direction = "rtl" if rtl else "ltr"
    align = "right" if rtl else "left"
    content = (f"<div style='font-family:{css_font}; font-size:{size}pt; color:{color}; direction:{direction}; "
               f"text-align:{align}'>{body}</div>")
    page.insert_htmlbox(rect, content, css=css, archive=archive)


class PdfDocument(DocumentWidget):
    kind = "pdf"
    default_ext = ".pdf"
    writable_exts = (".pdf",)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: pymupdf.Document | None = None
        self.password: str | None = None
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []
        self._words_cache: dict = {}
        self.view = PdfView(self)
        self.view.pageChanged.connect(self._page_changed)
        self.side = QTabWidget()
        self.side.setMinimumWidth(190)
        self.side.setMaximumWidth(280)
        self.thumbs = QListWidget()
        self.thumbs.setIconSize(QPixmap(120, 160).size())
        self.thumbs.itemClicked.connect(lambda it: self.view.goto_page(self.thumbs.row(it)))
        self.thumbs.setSelectionMode(QListWidget.ExtendedSelection)
        self.side.addTab(self.thumbs, _t("Pages"))
        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.itemClicked.connect(lambda it: self.view.goto_page(it.data(0, Qt.UserRole)))
        self.side.addTab(self.toc, _t("Bookmarks"))
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(2, 2, 2, 2)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search text\u2026"))
        self.search.returnPressed.connect(self.do_search)
        sl.addWidget(self.search)
        self.results = QListWidget()
        self.results.itemClicked.connect(self._goto_result)
        sl.addWidget(self.results, 1)
        self.side.addTab(sw, _t("Search"))
        self.annots = QListWidget()
        self.annots.itemClicked.connect(lambda it: self.view.goto_page(it.data(Qt.UserRole)[0], it.data(Qt.UserRole)[1]))
        self.side.addTab(self.annots, _t("Comments"))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.side)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        from app.shell.ai_dialogs import ChatPdfPanel

        self.chat = ChatPdfPanel(self, self.page_texts, lambda i: self.view.goto_page(max(0, min(i, self.doc.page_count - 1))),
                                 self.ocr)
        self.chat.setMinimumWidth(300)
        self.chat.hide()
        split.addWidget(self.chat)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(split, 1)
        strip = QWidget()
        strip.setObjectName("StatusStrip")
        hl = QHBoxLayout(strip)
        hl.setContentsMargins(8, 1, 8, 1)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        hl.addWidget(self.info, 1)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.editingFinished.connect(lambda: self.view.goto_page(self.page_spin.value() - 1))
        hl.addWidget(QLabel(_t("Page")))
        hl.addWidget(self.page_spin)
        self.zoom_slider = ZoomSlider()
        self.zoom_slider.zoomRequested.connect(self.view.set_zoom)
        hl.addWidget(self.zoom_slider)
        lay.addWidget(strip)
        self._build_actions()
        self.ribbon = self._build_ribbon()

    # ================================================================== DocumentWidget API
    def untitled_label(self) -> str:
        return _t("PDF")

    def ribbon_widget(self):
        return self.ribbon

    def save_filters(self) -> str:
        return "PDF (*.pdf)"

    @staticmethod
    def open_filter() -> str:
        return "PDF (*.pdf)"

    def load(self, path: str) -> None:
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            raise DocumentError(_t("The file is not a valid PDF or is damaged: {0}").format(exc)) from exc
        if not doc.is_pdf:
            raise DocumentError(_t("Not a PDF document."))
        if doc.needs_pass:
            for _attempt in range(3):
                pw, ok = QInputDialog.getText(self.window() if self.window() is not self else None, _t("Password"),
                                              _t("\u201c{0}\u201d is protected. Enter the password:").format(os.path.basename(path)),
                                              QLineEdit.Password)
                if not ok:
                    raise OpenCancelled()
                if doc.authenticate(pw):
                    self.password = pw
                    break
            else:
                raise DocumentError(_t("Wrong password."))
        self.doc = doc
        self.path = Path(path)
        self._undo.clear()
        self._redo.clear()
        self.reload_views()
        super().set_modified(False)
        QTimer.singleShot(0, self.view.fit_width)
        self.titleChanged.emit()

    def save_to(self, path: str) -> None:
        if self.doc is None:
            return
        same = self.path is not None and os.path.abspath(path) == os.path.abspath(str(self.path))
        if same and self.doc.can_save_incrementally():
            try:
                self.doc.saveIncr()
                return
            except Exception as exc:
                log.info("Incremental save failed (%s); doing a full save", exc)
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".pdf.tmp")
        os.close(fd)
        try:
            self.doc.save(tmp, garbage=3, deflate=True)
            from app.services.fileutil import finish_atomic

            if same:
                self.doc.close()
                finish_atomic(tmp, path)
                self.doc = pymupdf.open(path)
                if self.password:
                    self.doc.authenticate(self.password)
                self.reload_views()
            else:
                finish_atomic(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def export_pdf_to(self, path: str) -> None:
        self.doc.save(path, garbage=3, deflate=True)

    def print_document(self) -> None:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.HighResolution)
        dlg = QPrintDialog(printer, self)
        dlg.setMinMax(1, self.doc.page_count)
        if not dlg.exec():
            return
        p = QPainter(printer)
        try:
            res = min(300, printer.resolution())
            first, last = 1, self.doc.page_count
            if printer.printRange() == QPrinter.PageRange:
                first, last = printer.fromPage() or 1, printer.toPage() or self.doc.page_count
            for k, i in enumerate(range(first - 1, last)):
                if k:
                    printer.newPage()
                page = self.doc[i]
                pix = page.get_pixmap(dpi=res, alpha=False, annots=True)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                target = printer.pageLayout().paintRectPixels(printer.resolution())
                sc = min(target.width() / img.width(), target.height() / img.height())
                p.drawImage(QRectF(0, 0, img.width() * sc, img.height() * sc), img)
        finally:
            p.end()

    def status_text(self) -> str:
        if self.doc is None:
            return ""
        return _t("Page {0} of {1}").format(self.view.current_page() + 1, self.doc.page_count)

    def recovery_ext(self) -> str:
        return ".pdf"

    def write_recovery(self, path: str) -> None:
        self.doc.save(path)

    # ================================================================== views
    def reload_views(self):
        self._words_cache.clear()
        self.view.rebuild()
        self.page_spin.setMaximum(max(1, self.doc.page_count))
        self.thumbs.clear()
        for i in range(self.doc.page_count):
            it = QListWidgetItem(f"{i + 1}")
            it.setTextAlignment(Qt.AlignHCenter)
            self.thumbs.addItem(it)
        QTimer.singleShot(0, self._render_thumbs)
        self.toc.clear()
        parents = {0: None}
        for lvl, title, page, *_rest in self.doc.get_toc(simple=False):
            it = QTreeWidgetItem([title])
            it.setData(0, Qt.UserRole, max(0, page - 1))
            par = parents.get(lvl - 1)
            (par.addChild(it) if par is not None else self.toc.addTopLevelItem(it))
            parents[lvl] = it
        self.toc.expandAll()
        self._refresh_annots()
        self._page_changed(self.view.current_page())

    def _render_thumbs(self):
        if self.doc is None:
            return
        for i in range(min(self.doc.page_count, 400)):
            try:
                pix = self.doc[i].get_pixmap(matrix=pymupdf.Matrix(0.2, 0.2), alpha=False)
            except Exception:
                continue
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            framed = QPixmap(img.width() + 2, img.height() + 2)  # 1 px frame: white pages stay visible
            framed.fill(QColor("#a9b1bb"))
            painter = QPainter(framed)
            painter.drawImage(1, 1, img)
            painter.end()
            item = self.thumbs.item(i)
            if item is not None:
                item.setIcon(QIcon(framed))
            if i % 20 == 19:
                QApplication.processEvents()

    def _refresh_annots(self):
        self.annots.clear()
        for i, page in enumerate(self.doc):
            for a in page.annots() or []:
                content = a.info.get("content", "") or ""
                it = QListWidgetItem(f"p{i + 1} \u2022 {a.type[1]}" + (f": {content[:40]}" if content else ""))
                it.setData(Qt.UserRole, (i, a.rect.y0))
                self.annots.addItem(it)

    def _page_changed(self, i: int):
        if self.doc is None:
            return
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        self.info.setText(self.status_text())

    def words(self, i: int) -> list:
        if i not in self._words_cache:
            self._words_cache[i] = self.doc[i].get_text("words", sort=True)
        return self._words_cache[i]

    # ================================================================== undo
    def _checkpoint(self):
        self._undo.append(self.doc.tobytes())
        if len(self._undo) > 15:
            self._undo.pop(0)
        self._redo.clear()

    def _edited(self, pages=None, structure: bool = False):
        self._words_cache.clear()
        self.set_modified(True)
        if structure:
            self.reload_views()
        else:
            for i in (pages or range(self.doc.page_count)):
                self.view.refresh_page(i)
            self._refresh_annots()

    def _reopen(self, data: bytes):
        cur = self.view.current_page()
        self.doc = pymupdf.open("pdf", data)
        if self.password and self.doc.needs_pass:
            self.doc.authenticate(self.password)
        self.reload_views()
        self.view.goto_page(min(cur, self.doc.page_count - 1))
        self.set_modified(True)

    def undo(self):
        if self._undo:
            self._redo.append(self.doc.tobytes())
            self._reopen(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self.doc.tobytes())
            self._reopen(self._redo.pop())

    # ================================================================== modes & annotations
    def set_mode(self, mode: str):
        self.view.mode = mode
        self.view.setDragMode(QGraphicsView.ScrollHandDrag if mode == "hand" else QGraphicsView.NoDrag)
        self.view.viewport().setCursor(Qt.OpenHandCursor if mode == "hand" else
                                       (Qt.IBeamCursor if mode in ("select", "highlight", "underline", "strike", "edit_text")
                                        else Qt.CrossCursor))
        for m, a in self.mode_actions.items():
            a.setChecked(m == mode)
        hints = {"edit_text": _t("Click on text to edit it."), "text": _t("Click or drag where the text should go."),
                 "image": _t("Drag a rectangle for the image."), "redact": _t("Drag over content to mark it for redaction."),
                 "signature": _t("Drag a rectangle where the signature should appear."),
                 "note": _t("Click to place a note.")}
        if mode in hints:
            self.statusChanged.emit(hints[mode])

    def enter_edit_mode(self):
        self.ribbon.select_page(_t("Edit"))
        self.set_mode("edit_text")

    def annot_at(self, i: int, pt):
        for a in self.doc[i].annots() or []:
            if a.rect.contains(pt):
                return a
        return None

    def annotate_words(self, kind: str, i: int, words: list):
        if self.read_only:
            return
        self._checkpoint()
        page = self.doc[i]
        quads = [pymupdf.Rect(w[:4]).quad for w in words]
        if kind == "highlight":
            a = page.add_highlight_annot(quads)
        elif kind == "underline":
            a = page.add_underline_annot(quads)
        else:
            a = page.add_strikeout_annot(quads)
        a.set_info(title=self._author())
        a.update()
        self.view._sel_words, self.view._sel_page = [], -1
        self.view.clear_overlay()
        self._edited([i])

    def redact_words(self, i: int, words: list):
        self._checkpoint()
        for w in words:
            self.doc[i].add_redact_annot(pymupdf.Rect(w[:4]), fill=(0, 0, 0))
        self.view._sel_words, self.view._sel_page = [], -1
        self.view.clear_overlay()
        self._edited([i])
        self.statusChanged.emit(_t("Marked for redaction. Use Apply Redactions to remove the content permanently."))

    def _author(self) -> str:
        from app.services.config import cfg

        return cfg().get("author", "")

    def add_note(self, i: int, pt):
        text, ok = QInputDialog.getMultiLineText(self, _t("Note"), _t("Note text:"))
        if not ok or not text.strip():
            return
        self._checkpoint()
        a = self.doc[i].add_text_annot(pt, text, icon="Comment")
        a.set_info(title=self._author(), content=text)
        a.update()
        self._edited([i])

    def edit_annot(self, i: int, annot):
        text, ok = QInputDialog.getMultiLineText(self, _t("Edit Annotation"), _t("Text:"), annot.info.get("content", ""))
        if ok:
            self._checkpoint()
            annot.set_info(content=text)
            annot.update()
            self._edited([i])

    def delete_annot(self, i: int, annot):
        self._checkpoint()
        self.doc[i].delete_annot(annot)
        self._edited([i])

    def add_ink(self, i: int, points: list):
        self._checkpoint()
        a = self.doc[i].add_ink_annot([[(p.x, p.y) for p in points]])
        c = self.view.ink_color
        a.set_colors(stroke=(c.redF(), c.greenF(), c.blueF()))
        a.set_border(width=self.view.ink_width)
        a.set_info(title=self._author())
        a.update()
        self._edited([i])

    def rect_action(self, mode: str, i: int, r, start, end):
        page = self.doc[i]
        c = self.view.ink_color
        rgb = (c.redF(), c.greenF(), c.blueF())
        if mode == "text":
            d = TextInsertDialog(self, _t("Add Text"))
            if not d.exec() or not d.text.toPlainText().strip():
                return
            self._checkpoint()
            try:
                insert_rich_text(page, pymupdf.Rect(r.x0, r.y0, max(r.x1, r.x0 + 60), max(r.y1, r.y0 + 20) + 200),
                                 d.text.toPlainText(), d.size.value(), d.color.currentData(), d.font.currentData())
            except Exception as exc:
                handle_exception(self, _t("Add Text"), exc)
                return
            self._edited([i])
            return
        if mode == "image":
            path = get_open_file(self, _t("Insert Image"), "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
            if not path:
                return
            self._checkpoint()
            page.insert_image(r, filename=path, keep_proportion=True)
            self._edited([i])
            return
        if mode == "signature":
            self.sign_document(i, r)
            return
        if mode == "callout":
            self.add_callout(i, start, end)
            return
        if mode == "measure":
            self.add_distance_measure(i, start, end)
            return
        self._checkpoint()
        if mode == "rect":
            a = page.add_rect_annot(r)
        elif mode == "ellipse":
            a = page.add_circle_annot(r)
        elif mode in ("line", "arrow"):
            a = page.add_line_annot(start, end)
            if mode == "arrow":
                a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
        elif mode == "redact":
            a = page.add_redact_annot(r, fill=(0, 0, 0))
            self._edited([i])
            self.statusChanged.emit(_t("Marked for redaction. Use Apply Redactions to remove the content permanently."))
            return
        else:
            return
        a.set_colors(stroke=rgb)
        a.set_border(width=self.view.ink_width)
        a.set_info(title=self._author())
        a.update()
        self._edited([i])

    # -- callouts and measurement --------------------------------------------------------------------------------
    def add_callout(self, i: int, target, box_at):
        """Text box with a leader line pointing at ``target`` (PDF FreeText callout annotation)."""
        text, ok = QInputDialog.getMultiLineText(self, _t("Callout"), _t("Callout text:"))
        if not ok or not text.strip():
            return
        page = self.doc[i]
        c = self.view.ink_color
        rgb = (c.redF(), c.greenF(), c.blueF())
        lines = text.strip().split("\n")
        w = min(260, max(80, 6.2 * max(len(ln) for ln in lines) + 14))
        h = 15 * len(lines) + 12
        x = min(max(box_at.x, 2), page.rect.width - w - 2)
        y = min(max(box_at.y - h / 2, 2), page.rect.height - h - 2)
        box = pymupdf.Rect(x, y, x + w, y + h)
        attach = pymupdf.Point(box.x0 if target.x < box.x0 else box.x1 if target.x > box.x1 else box.x0 + w / 2,
                               min(max(target.y, box.y0), box.y1))
        knee = pymupdf.Point((target.x + attach.x) / 2, attach.y)
        self._checkpoint()
        a = page.add_freetext_annot(box, text.strip(), fontsize=11, text_color=rgb, fill_color=(1, 1, 0.85),
                                    border_width=1, callout=(target, knee, attach),
                                    line_end=pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        a.set_info(title=self._author())
        a.update()
        self._edited([i])

    def measure_scale(self) -> tuple[float, str, float]:
        """(page points per drawing unit on paper, unit name, real units per paper unit)."""
        sc = getattr(self, "_scale", None) or (72 / 2.54, "cm", 1.0)
        return sc

    def set_measure_scale(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout

        d = QDialog(self)
        d.setWindowTitle(_t("Measurement Scale"))
        form = QFormLayout(d)
        unit = QComboBox()
        for u in ("cm", "mm", "in", "m", "ft", "km"):
            unit.addItem(u)
        paper = QComboBox()
        paper.addItem("1 cm", 72 / 2.54)
        paper.addItem("1 in", 72.0)
        ratio = QDoubleSpinBox()
        ratio.setRange(0.0001, 1_000_000)
        ratio.setDecimals(4)
        cur_pts, cur_unit, cur_ratio = self.measure_scale()
        unit.setCurrentText(cur_unit)
        ratio.setValue(cur_ratio)
        form.addRow(_t("On paper:"), paper)
        form.addRow(_t("equals in reality:"), ratio)
        form.addRow(_t("Unit:"), unit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec():
            self._scale = (paper.currentData(), unit.currentText(), ratio.value())

    def _real(self, length_pts: float, power: int = 1) -> tuple[float, str]:
        pts_per_paper, unit, ratio = self.measure_scale()
        v = length_pts / pts_per_paper ** power * ratio ** power   # power 2: an area in square points
        return v, unit + ("\u00b2" if power == 2 else "")

    def distance_label(self, a, b) -> str:
        v, u = self._real(math.hypot(b.x - a.x, b.y - a.y))
        return f"{v:.2f} {u}"

    def add_distance_measure(self, i: int, a, b):
        page = self.doc[i]
        label = self.distance_label(a, b)
        self._checkpoint()
        ln = page.add_line_annot(a, b)
        ln.set_line_ends(pymupdf.PDF_ANNOT_LE_OPEN_ARROW, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        ln.set_colors(stroke=(0.86, 0.15, 0.15))
        ln.set_border(width=1)
        ln.set_info(title=self._author(), content=_t("Distance: {0}").format(label), subject=_t("Measurement"))
        ln.update()
        mid = pymupdf.Point((a.x + b.x) / 2, (a.y + b.y) / 2)
        box = pymupdf.Rect(mid.x + 4, mid.y - 16, mid.x + 8 + 7 * len(label), mid.y)
        t = page.add_freetext_annot(box, label, fontsize=10, text_color=(0.86, 0.15, 0.15), fill_color=(1, 1, 1))
        t.set_info(title=self._author(), subject=_t("Measurement"))
        t.update()
        self._edited([i])

    def add_area_measure(self, i: int, pts: list):
        page = self.doc[i]
        n = len(pts)
        area = abs(sum(pts[k].x * pts[(k + 1) % n].y - pts[(k + 1) % n].x * pts[k].y for k in range(n))) / 2
        perim = sum(math.hypot(pts[(k + 1) % n].x - pts[k].x, pts[(k + 1) % n].y - pts[k].y) for k in range(n))
        av, au = self._real(area, 2)
        pv, pu = self._real(perim)
        label = _t("Area {0:.2f} {1}, perimeter {2:.2f} {3}").format(av, au, pv, pu)
        self._checkpoint()
        pg = page.add_polygon_annot([(q.x, q.y) for q in pts])
        pg.set_colors(stroke=(0.1, 0.45, 0.91), fill=(0.1, 0.45, 0.91))
        pg.set_opacity(0.35)
        pg.set_border(width=1)
        pg.set_info(title=self._author(), content=label, subject=_t("Measurement"))
        pg.update()
        cx, cy = sum(q.x for q in pts) / n, sum(q.y for q in pts) / n
        box = pymupdf.Rect(cx - 3.2 * len(label), cy - 8, cx + 3.2 * len(label) + 6, cy + 8)
        t = page.add_freetext_annot(box, label, fontsize=9, text_color=(0.05, 0.25, 0.6), fill_color=(1, 1, 1))
        t.set_info(title=self._author(), subject=_t("Measurement"))
        t.update()
        self._edited([i])
        self.statusChanged.emit(label)

    def apply_redactions(self):
        count = sum(1 for page in self.doc for a in (page.annots() or []) if a.type[0] == pymupdf.PDF_ANNOT_REDACT)
        if not count:
            show_info(self, _t("Redact"), _t("No areas are marked for redaction."))
            return
        if not confirm(self, _t("Apply Redactions"), _t("Permanently remove the content under {0} redaction mark(s)? "
                                                         "This cannot be undone after saving.").format(count)):
            return
        self._checkpoint()
        for page in self.doc:
            page.apply_redactions()
        self._edited()

    def edit_text_at(self, i: int, pt):
        """Edit an existing text line: redact it and write the new text in the same place and size."""
        page = self.doc[i]
        target = None
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                if pymupdf.Rect(line["bbox"]).contains(pt):
                    target = line
                    break
            if target:
                break
        if target is None:
            self.statusChanged.emit(_t("No text at this position."))
            return
        spans = target["spans"]
        text = "".join(s["text"] for s in spans)
        size = spans[0]["size"]
        col = spans[0]["color"]
        color = f"#{col:06x}"
        d = TextInsertDialog(self, _t("Edit Text"), text, size)
        idx = d.color.findData(color)
        if idx < 0:
            d.color.addItem(color, color)
            idx = d.color.count() - 1
        d.color.setCurrentIndex(idx)
        if "Times" in spans[0]["font"] or "Serif" in spans[0]["font"]:
            d.font.setCurrentIndex(1)
        elif "Courier" in spans[0]["font"] or "Mono" in spans[0]["font"]:
            d.font.setCurrentIndex(2)
        if not d.exec():
            return
        new = d.text.toPlainText()
        if new == text:
            return
        self._checkpoint()
        r = pymupdf.Rect(target["bbox"])
        # Sample paper background color just outside the text bbox so cream/shaded PDFs never get a white patch!
        bg_fill = (1.0, 1.0, 1.0)
        try:
            sample_pix = page.get_pixmap(clip=pymupdf.Rect(max(0, r.x0 - 4), max(0, r.y0 - 4), r.x0 + 2, r.y0 + 2), alpha=False)
            if sample_pix.width > 0 and sample_pix.height > 0:
                px = sample_pix.pixel(0, 0)
                bg_fill = (px[0] / 255.0, px[1] / 255.0, px[2] / 255.0)
        except Exception:
            pass
        page.add_redact_annot(r, fill=bg_fill)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        fontname = {0: "helv", 1: "tiro", 2: "cour"}.get(d.font.currentIndex())
        c = QColor(d.color.currentData())
        if fontname and not any(ord(ch) > 255 for ch in new):
            page.insert_text(pymupdf.Point(r.x0, spans[0]["origin"][1]), new, fontsize=d.size.value(), fontname=fontname,
                             color=(c.redF(), c.greenF(), c.blueF()))
        else:
            insert_rich_text(page, pymupdf.Rect(r.x0, r.y0 - 2, page.rect.width - 20, r.y1 + 60), new, d.size.value(),
                             d.color.currentData(), d.font.currentData())
        self._edited([i])

    def find_replace(self):
        find, ok = QInputDialog.getText(self, _t("Replace Text"), _t("Find:"))
        if not ok or not find:
            return
        repl, ok = QInputDialog.getText(self, _t("Replace Text"), _t("Replace with:"))
        if not ok:
            return
        self._checkpoint()
        tmp_in = tempfile.mktemp(suffix=".pdf")
        tmp_out = tempfile.mktemp(suffix=".pdf")
        self.doc.save(tmp_in)
        try:
            n = ops.replace_text(tmp_in, tmp_out, find, repl, self.password)
            self._reopen(open(tmp_out, "rb").read())
            show_info(self, _t("Replace Text"), _t("{0} occurrence(s) replaced.").format(n))
        except Exception as exc:
            handle_exception(self, _t("Replace Text"), exc)
        finally:
            for p in (tmp_in, tmp_out):
                if os.path.exists(p):
                    os.unlink(p)

    def copy_text(self):
        text = self.view.selected_text()
        if text:
            QApplication.clipboard().setText(text)
            self.statusChanged.emit(_t("Copied {0} characters").format(len(text)))

    def select_all_text(self):
        i = self.view.current_page()
        self.view._sel_words = self.words(i)
        self.view._sel_page = i
        self.view.show_selection()

    # ================================================================== search
    def do_search(self):
        q = self.search.text().strip()
        self.results.clear()
        if not q:
            return
        total = 0
        for i, page in enumerate(self.doc):
            hits = []
            for variant in ops.search_variants(q):
                hits += [r for r in page.search_for(variant) if all(not r.intersects(h) for h in hits)]
            for r in hits:
                total += 1
                ctx = ops.fix_rtl_text(page.get_textbox(r + (-60, -2, 160, 2))).replace("\n", " ").strip()
                it = QListWidgetItem(f"p{i + 1}: \u2026{ctx[:70]}\u2026")
                it.setData(Qt.UserRole, (i, r.y0, tuple(r)))
                self.results.addItem(it)
                if total >= 2000:
                    break
        self.side.setCurrentIndex(2)
        self.statusChanged.emit(_t("{0} match(es)").format(total))
        if total:
            self._goto_result(self.results.item(0))

    def _goto_result(self, it):
        i, y, r = it.data(Qt.UserRole)
        self.view.goto_page(i, max(0, y - 80))
        self.view.clear_overlay()
        rect = self.view.page_rect_to_scene(i, pymupdf.Rect(r))
        self.view._overlay.append(self.view.scene().addRect(rect, QPen(QColor("#f9ab00"), 1.2), QColor(249, 171, 0, 80)))

    def show_find(self):
        self.side.setCurrentIndex(2)
        self.search.setFocus()
        self.search.selectAll()

    # ================================================================== pages
    def selected_pages(self) -> list[int]:
        rows = sorted({self.thumbs.row(it) for it in self.thumbs.selectedItems()})
        return rows or [self.view.current_page()]

    # -- page tools ------------------------------------------------------------------------------------------------
    def crop_pages(self):
        from app.pdf.pagetools import CropDialog, crop_pages

        d = CropDialog(self, self.doc.page_count, self.view.current_page())
        if not d.exec():
            return
        try:
            pages = d.pages()
        except ValueError as exc:
            show_error(self, _t("Crop Pages"), str(exc))
            return
        self._checkpoint()
        n = crop_pages(self.doc, pages, tuple(sp.value() for sp in d.m), d.auto.isChecked())
        self._edited(structure=True)
        self.statusChanged.emit(_t("{0} page(s) cropped").format(n))

    def replace_pages(self):
        from app.pdf.ops import parse_ranges
        from app.pdf.pagetools import ReplaceDialog, replace_pages

        d = ReplaceDialog(self, self.doc.page_count, self.view.current_page())
        if not d.exec() or not d.src.text().strip():
            return
        try:
            src = pymupdf.open(d.src.text().strip())
            targets = sorted({p for g in parse_ranges(d.targets.text(), self.doc.page_count) for p in g})
            src_pages = [p for g in parse_ranges(d.src_pages.text(), src.page_count) for p in g]
        except Exception as exc:
            handle_exception(self, _t("Replace Pages"), exc)
            return
        self._checkpoint()
        n = replace_pages(self.doc, targets, src, src_pages)
        src.close()
        self._edited(structure=True)
        self.statusChanged.emit(_t("{0} page(s) inserted").format(n))

    def organize_pages(self):
        from app.pdf.pagetools import OrganizeDialog, apply_plan

        d = OrganizeDialog(self, self.doc)
        if not d.exec():
            return
        plan = d.plan()
        if plan == [{"src": i, "rot": 0} for i in range(self.doc.page_count)]:
            return
        self._checkpoint()
        new = apply_plan(self.doc, plan)
        self._reopen(new.tobytes())

    # -- MyOffice AI -----------------------------------------------------------------------------------------------
    def page_texts(self) -> list[str]:
        from app.pdf.ops import fix_rtl_text

        return [fix_rtl_text(p.get_text()) for p in self.doc]

    def toggle_chat(self, on: bool):
        self.chat.setVisible(on)
        if on:
            self.chat.reset()
            self.chat.question.setFocus()

    def summarize(self):
        self.a_chat.setChecked(True)
        self.toggle_chat(True)
        self.chat.summarize()

    def translate_pdf(self):
        from app.shell.ai_dialogs import TranslateDialog

        paras = []
        for i, page in enumerate(self.doc):
            for b in page.get_text("blocks"):
                text = " ".join(b[4].split())
                if text:
                    paras.append(text)
        if not paras:
            show_info(self, _t("Parallel Translate"), _t("This PDF has no text layer. Run OCR first."))
            return
        TranslateDialog(self, paras, None, _t("Parallel Translate") + f" - {self.display_name()}").exec()

    def _ai_settings(self):
        from app.shell.ai_dialogs import AISettingsDialog

        AISettingsDialog(self).exec()

    def rotate_pages(self, pages=None, deg: int = 90):
        self._checkpoint()
        for i in pages or self.selected_pages():
            p = self.doc[i]
            p.set_rotation((p.rotation + deg) % 360)
        self._edited(structure=True)

    def delete_pages(self):
        pages = self.selected_pages()
        if len(pages) >= self.doc.page_count:
            show_error(self, _t("Delete Pages"), _t("A PDF must keep at least one page."))
            return
        if not confirm(self, _t("Delete Pages"), _t("Delete {0} page(s)?").format(len(pages))):
            return
        self._checkpoint()
        self.doc.delete_pages(pages)
        self._edited(structure=True)

    def insert_blank(self):
        i = self.view.current_page()
        r = self.doc[i].rect
        self._checkpoint()
        self.doc.new_page(i + 1, width=r.width, height=r.height)
        self._edited(structure=True)
        self.view.goto_page(i + 1)

    def insert_from_file(self):
        path = get_open_file(self, _t("Insert Pages from PDF"), "PDF (*.pdf);;Documents (*.docx *.xlsx *.pptx *.png *.jpg)")
        if not path:
            return
        try:
            if not path.lower().endswith(".pdf"):
                path = ops.to_pdf(path, tempfile.mktemp(suffix=".pdf"))
            other = pymupdf.open(path)
        except Exception as exc:
            handle_exception(self, _t("Insert Pages"), exc)
            return
        i = self.view.current_page()
        self._checkpoint()
        self.doc.insert_pdf(other, start_at=i + 1)
        other.close()
        self._edited(structure=True)

    def move_page(self, d: int):
        i = self.view.current_page()
        j = i + d
        if not 0 <= j < self.doc.page_count:
            return
        self._checkpoint()
        self.doc.move_page(i, j if d < 0 else j + 1)
        self._edited(structure=True)
        self.view.goto_page(j)

    def duplicate_page(self):
        i = self.view.current_page()
        self._checkpoint()
        self.doc.fullcopy_page(i, i + 1)
        self._edited(structure=True)

    def extract_selected_pages(self):
        pages = self.selected_pages()
        out = get_save_file(self, _t("Extract Pages"), "PDF (*.pdf)", f"{self.path.stem if self.path else 'pages'}_extract.pdf", "pdf")
        if not out:
            return
        new = pymupdf.open()
        for i in pages:
            new.insert_pdf(self.doc, from_page=i, to_page=i)
        new.save(out, garbage=3, deflate=True)
        new.close()
        if confirm(self, _t("Extract Pages"), _t("Pages saved to {0}. Open it now?").format(out)):
            self.openPathRequested.emit(out)

    # ================================================================== protect / sign / convert
    def _current_file(self) -> str:
        """Save pending edits to a temp file for whole-file operations."""
        tmp = tempfile.mktemp(suffix=".pdf")
        self.doc.save(tmp)
        return tmp

    def _run_file_op(self, title: str, fn, *args, suffix: str = ".pdf", open_result: bool = True, **kw):
        from app.shell.tasks import TaskError, run_task

        src = self._current_file()
        base = self.path.stem if self.path else "document"
        default = f"{base}_{title.lower().replace(' ', '_')}{suffix}"
        out = get_save_file(self, title, f"{suffix.upper().lstrip('.')} (*{suffix})", default, suffix.lstrip("."))
        if not out:
            os.unlink(src)
            return None
        try:
            res = run_task(self, title + "\u2026", fn, src, out, *args, **kw)
        except TaskError as exc:
            show_error(self, title, str(exc.original))
            return None
        finally:
            if os.path.exists(src):
                os.unlink(src)
        if open_result and out.lower().endswith(".pdf"):
            if confirm(self, title, _t("Saved to {0}. Open it now?").format(out)):
                self.openPathRequested.emit(out)
        elif open_result:
            if confirm(self, title, _t("Saved to {0}. Open it now?").format(out)):
                self.openPathRequested.emit(out)
        return res

    def protect(self):
        pw, ok = QInputDialog.getText(self, _t("Protect PDF"), _t("Password to open the document:"), QLineEdit.Password)
        if not ok or not pw:
            return
        pw2, ok = QInputDialog.getText(self, _t("Protect PDF"), _t("Repeat the password:"), QLineEdit.Password)
        if not ok or pw2 != pw:
            show_error(self, _t("Protect PDF"), _t("The passwords do not match."))
            return
        self._run_file_op(_t("Protected"), ops.protect_pdf, pw, open_result=False)

    def unlock(self):
        if not self.doc.is_encrypted and not self.password:
            show_info(self, _t("Remove Password"), _t("This PDF is not encrypted."))
            return
        self._run_file_op(_t("Unlocked"), ops.unlock_pdf, self.password or "", open_result=True)

    def sign_document(self, page: int | None = None, rect=None):
        from app.pdf.toolbox import SignDialog

        if self.is_modified():
            if not confirm(self, _t("Sign PDF"), _t("The document has unsaved changes. Save them before signing?")):
                return
            if not self.save():
                return
        d = SignDialog(self, page if page is not None else self.view.current_page(), rect, self.doc[0].rect.height)
        if d.exec():
            src = str(self.path)
            out = get_save_file(self, _t("Sign PDF"), "PDF (*.pdf)", f"{self.path.stem}_signed.pdf", "pdf")
            if not out:
                return
            try:
                d.apply(src, out)
            except Exception as exc:
                handle_exception(self, _t("Sign PDF"), exc)
                return
            if confirm(self, _t("Sign PDF"), _t("Signed document saved to {0}. Open it now?").format(out)):
                self.openPathRequested.emit(out)

    def verify(self):
        if self.path is None:
            return
        try:
            sigs = ops.verify_signatures(str(self.path))
        except Exception as exc:
            handle_exception(self, _t("Verify Signatures"), exc)
            return
        if not sigs:
            show_info(self, _t("Verify Signatures"), _t("The document has no digital signatures."))
            return
        lines = []
        for s in sigs:
            state = _t("VALID") if (s.intact and s.valid) else _t("INVALID")
            lines.append(f"{s.field}: {state} \u2014 {s.signer}\n  {_t('Coverage')}: {s.coverage}  {_t('Signed')}: {s.signed_at}")
        show_info(self, _t("Verify Signatures"), "\n\n".join(lines))

    def watermark(self):
        from app.pdf.toolbox import WatermarkDialog

        d = WatermarkDialog(self)
        if d.exec():
            self._run_file_op(_t("Watermarked"), ops.watermark_pdf, password=self.password, **d.options())

    def add_page_numbers(self):
        fmt, ok = QInputDialog.getItem(
            self, _t("Page Numbers"), _t("Format:"),
            ["Page {n} of {total}", "{n} / {total}", "- {n} -", "BATES-{n:06d}"], 0, True
        )
        if not ok or not fmt.strip():
            return
        pos, ok = QInputDialog.getItem(
            self, _t("Page Numbers"), _t("Position:"),
            ["bottom-center", "bottom-right", "bottom-left", "top-right", "top-center"], 0, False
        )
        if not ok:
            return
        self._checkpoint()
        src = self._current_file()
        out = tempfile.mktemp(suffix=".pdf")
        try:
            ops.add_page_numbers_pdf(src, out, fmt=fmt.strip(), position=pos, password=self.password)
            self._reopen(Path(out).read_bytes())
        except Exception as exc:
            handle_exception(self, _t("Page Numbers"), exc)
        finally:
            for p in (src, out):
                if os.path.exists(p):
                    os.unlink(p)

    def add_rubber_stamp(self):
        label, ok = QInputDialog.getItem(
            self, _t("Rubber Stamp"), _t("Stamp text:"),
            ["APPROVED", "CONFIDENTIAL", "PAID", "DRAFT", "FINAL", "REVIEWED"], 0, True
        )
        if not ok or not label.strip():
            return
        self._checkpoint()
        src = self._current_file()
        out = tempfile.mktemp(suffix=".pdf")
        try:
            ops.stamp_pdf(src, out, label=label.strip(), pages=str(self.view.current_page() + 1), password=self.password)
            self._reopen(Path(out).read_bytes())
        except Exception as exc:
            handle_exception(self, _t("Rubber Stamp"), exc)
        finally:
            for p in (src, out):
                if os.path.exists(p):
                    os.unlink(p)

    def flatten_annotations(self):
        if not confirm(self, _t("Flatten PDF"),
                       _t("Bake all annotations, stamps, and interactive form fields permanently into page graphics?")):
            return
        self._checkpoint()
        src = self._current_file()
        out = tempfile.mktemp(suffix=".pdf")
        try:
            ops.flatten_pdf(src, out, password=self.password)
            self._reopen(Path(out).read_bytes())
        except Exception as exc:
            handle_exception(self, _t("Flatten PDF"), exc)
        finally:
            for p in (src, out):
                if os.path.exists(p):
                    os.unlink(p)

    def fill_form_fields(self):
        from PySide6.QtWidgets import QCheckBox, QScrollArea

        widgets = []
        for pno, page in enumerate(self.doc):
            for w in page.widgets() or []:
                widgets.append((pno, w))
        if not widgets:
            show_info(self, _t("Form Fields"), _t("This PDF contains no interactive AcroForm fields."))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(_t("Fill Interactive PDF Form Fields"))
        dlg.resize(480, min(560, 140 + 42 * len(widgets)))
        outer = QVBoxLayout(dlg)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        body = QWidget()
        fl = QFormLayout(body)
        editors = []
        for pno, w in widgets:
            title = f"{w.field_label or w.field_name or 'Field'} (p.{pno + 1})"
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                ed = QCheckBox()
                ed.setChecked(bool(w.field_value and str(w.field_value).lower() not in ("off", "false", "0", "no")))
            else:
                ed = QLineEdit(str(w.field_value or ""))
            fl.addRow(title, ed)
            editors.append((pno, w, ed))
        sc.setWidget(body)
        outer.addWidget(sc, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        outer.addWidget(bb)
        if not dlg.exec():
            return
        self._checkpoint()
        changed_pages = set()
        for pno, w, ed in editors:
            val = ed.isChecked() if hasattr(ed, "isChecked") else ed.text()
            w.field_value = val
            w.update()
            changed_pages.add(pno)
        self._edited(sorted(changed_pages))

    def convert_to(self, target: str):
        suffix = {"docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "txt": ".txt"}.get(target)
        if target == "png":
            outdir = QFileDialogDir(self)
            if not outdir:
                return
            from app.shell.tasks import TaskError, run_task

            src = self._current_file()
            try:
                outs = run_task(self, _t("Exporting images\u2026"), ops.pdf_to_images, src, outdir, 150)
            except TaskError as exc:
                show_error(self, _t("Export Images"), str(exc.original))
                return
            finally:
                os.unlink(src)
            show_info(self, _t("Export Images"), _t("{0} image(s) saved to {1}").format(len(outs), outdir))
            return
        fn = {"docx": ops.pdf_to_docx, "xlsx": ops.pdf_to_xlsx, "pptx": ops.pdf_to_pptx, "txt": ops.pdf_to_text}[target]
        self._run_file_op(_t("Converted"), fn, suffix=suffix)

    def ocr(self):
        langs = ops.ocr_languages()
        if not langs:
            show_error(self, _t("OCR"), _t("Tesseract OCR is not installed. Install it with Urdu and English language "
                                          "data, or set its path in Settings."))
            return
        default = "urd+eng" if {"urd", "eng"} <= set(langs) else "+".join(l for l in langs if l != "osd")[:20]
        lang, ok = QInputDialog.getItem(self, _t("OCR"), _t("Languages:"), [default] + [l for l in langs if l != "osd"], 0, True)
        if ok:
            self._run_file_op(_t("Searchable"), ops.ocr_pdf, lang=lang)

    def compress(self):
        level, ok = QInputDialog.getItem(self, _t("Compress PDF"), _t("Quality:"),
                                         ["ebook", "screen", "printer", "prepress", "lossless"], 0, False)
        if ok:
            res = self._run_file_op(_t("Compressed"), ops.compress_pdf, level, open_result=False)
            if res is not None:
                show_info(self, _t("Compress PDF"), _t("{0:,} \u2192 {1:,} bytes ({2:.1f}% smaller) using {3}.").format(
                    res.before, res.after, res.saved_pct, res.method))

    def properties(self):
        m = self.doc.metadata or {}
        lines = [f"{k}: {v}" for k, v in m.items() if v]
        lines.append(f"{_t('Pages')}: {self.doc.page_count}")
        lines.append(f"{_t('Encrypted')}: {self.doc.is_encrypted}")
        if self.path:
            lines.append(f"{_t('File size')}: {os.path.getsize(self.path):,} bytes")
        show_info(self, _t("Document Properties"), "\n".join(lines))

    # ================================================================== actions & ribbon
    def _act(self, text, icon_name=None, slot=None, shortcut=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        a.setCheckable(checkable)
        if slot is not None:
            a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.mode_actions = {}
        labels = {"select": (_t("Select Text"), "select"), "hand": (_t("Hand"), "hand"),
                  "highlight": (_t("Highlight"), "highlight_annot"), "underline": (_t("Underline"), "underline_annot"),
                  "strike": (_t("Strikethrough"), "strike_annot"), "note": (_t("Note"), "note"),
                  "rect": (_t("Rectangle"), "rect_annot"), "ellipse": (_t("Ellipse"), "circle"),
                  "line": (_t("Line"), "minus"), "arrow": (_t("Arrow"), "arrow"), "ink": (_t("Draw"), "ink"),
                  "text": (_t("Add Text"), "text_insert"), "image": (_t("Add Image"), "image"),
                  "redact": (_t("Mark for Redaction"), "redact"), "edit_text": (_t("Edit Text"), "edit"),
                  "signature": (_t("Sign"), "sign"), "callout": (_t("Callout"), "callout"),
                  "measure": (_t("Distance"), "ruler"), "measure_area": (_t("Area & Perimeter"), "rect_tool")}
        for m in MODES:
            text, ic = labels[m]
            self.mode_actions[m] = A(text, ic, lambda _c=False, mm=m: self.set_mode(mm), checkable=True)
        self.mode_actions["select"].setChecked(True)
        self.a_undo = A(_t("Undo"), "undo", self.undo, "Ctrl+Z")
        self.a_redo = A(_t("Redo"), "redo", self.redo, "Ctrl+Y")
        self.a_copy = A(_t("Copy"), "copy", self.copy_text, "Ctrl+C")
        self.a_select_all = A(_t("Select All"), "select", self.select_all_text, "Ctrl+A")
        self.a_find = A(_t("Find"), "find", self.show_find, "Ctrl+F")
        self.a_replace = A(_t("Replace Text"), "replace", self.find_replace, "Ctrl+H")
        self.a_zoom_in = A(_t("Zoom In"), "zoom_in", lambda: self.view.set_zoom(self.view.zoom * 1.2), "Ctrl++")
        self.a_zoom_out = A(_t("Zoom Out"), "zoom_out", lambda: self.view.set_zoom(self.view.zoom / 1.2), "Ctrl+-")
        self.a_fit_w = A(_t("Fit Width"), "fit_width", self.view.fit_width)
        self.a_fit_p = A(_t("Fit Page"), "fit_page", self.view.fit_page)
        self.a_actual = A(_t("Actual Size"), "zoom_in", lambda: self.view.set_zoom(1.0), "Ctrl+0")
        self.a_prev = A(_t("Previous Page"), "prev", lambda: self.view.goto_page(self.view.current_page() - 1), "PgUp")
        self.a_next = A(_t("Next Page"), "next", lambda: self.view.goto_page(self.view.current_page() + 1), "PgDown")
        self.a_color = A(_t("Color"), "font_color", None)
        cm = ColorMenu(self)
        cm.colorPicked.connect(lambda c: setattr(self.view, "ink_color", c) if c.isValid() else None)
        self.a_color.setMenu(cm)
        self.a_apply_redact = A(_t("Apply Redactions"), "redact", self.apply_redactions)
        self.a_rot_l = A(_t("Rotate Left"), "rotate", lambda: self.rotate_pages(None, -90))
        self.a_rot_r = A(_t("Rotate Right"), "rotate", lambda: self.rotate_pages(None, 90))
        self.a_del_page = A(_t("Delete Pages"), "delete", self.delete_pages)
        self.a_blank = A(_t("Insert Blank Page"), "new", self.insert_blank)
        self.a_insert_file = A(_t("Insert from File"), "open", self.insert_from_file)
        self.a_extract = A(_t("Extract Pages"), "extract", self.extract_selected_pages)
        self.a_dup_page = A(_t("Duplicate Page"), "duplicate", self.duplicate_page)
        self.a_page_up = A(_t("Move Page Up"), "up", lambda: self.move_page(-1))
        self.a_page_down = A(_t("Move Page Down"), "down", lambda: self.move_page(1))
        self.a_protect = A(_t("Encrypt"), "lock", self.protect)
        self.a_unlock = A(_t("Remove Password"), "unlock", self.unlock)
        self.a_sign = A(_t("Digital Signature"), "sign", lambda: self.sign_document())
        self.a_verify = A(_t("Verify Signatures"), "check", self.verify)
        self.a_watermark = A(_t("Watermark"), "watermark", self.watermark)
        self.a_page_nums = A(_t("Page Numbers"), "list_num", self.add_page_numbers)
        self.a_stamp = A(_t("Rubber Stamp"), "stamp", self.add_rubber_stamp)
        self.a_flatten = A(_t("Flatten PDF"), "layers", self.flatten_annotations)
        self.a_fill_form = A(_t("Fill Forms"), "forms", self.fill_form_fields)
        self.a_to_word = A(_t("To Word"), "word_out", lambda: self.convert_to("docx"))
        self.a_to_excel = A(_t("To Excel"), "excel_out", lambda: self.convert_to("xlsx"))
        self.a_to_ppt = A(_t("To PowerPoint"), "slide", lambda: self.convert_to("pptx"))
        self.a_to_img = A(_t("To Images"), "image_out", lambda: self.convert_to("png"))
        self.a_to_txt = A(_t("To Text"), "text_out", lambda: self.convert_to("txt"))
        self.a_ocr = A(_t("OCR (Searchable PDF)"), "ocr", self.ocr)
        self.a_compress = A(_t("Compress"), "compress", self.compress)
        self.a_props = A(_t("Properties"), "info", self.properties)
        self.a_scale = A(_t("Scale\u2026"), "ruler", self.set_measure_scale)
        self.a_crop = A(_t("Crop Pages"), "crop", self.crop_pages)
        self.a_replace_pages = A(_t("Replace Pages"), "replace_page", self.replace_pages)
        self.a_organize = A(_t("Organize Pages"), "organize", self.organize_pages)
        self.a_chat = A(_t("Chat with PDF"), "chat", self.toggle_chat, checkable=True)
        self.a_summary = A(_t("Summarize"), "summary", self.summarize)
        self.a_translate_pdf = A(_t("Parallel Translate"), "translate", self.translate_pdf)
        self.a_ai_settings = A(_t("AI Settings"), "settings", self._ai_settings)
        self.a_toolbox = A(_t("PDF Toolbox"), "addins", lambda: self.window().open_toolbox() if hasattr(self.window(), "open_toolbox") else None)

    def _build_ribbon(self) -> Ribbon:
        rb = Ribbon(self)
        M = self.mode_actions
        home = rb.page(_t("Home"))
        g = home.group(_t("Tools"))
        g.large(M["select"])
        g.large(M["hand"])
        g.small(self.a_copy)
        g.small(self.a_select_all)
        g = home.group(_t("View"))
        g.small(self.a_zoom_in)
        g.small(self.a_zoom_out)
        g.small(self.a_actual)
        g.small(self.a_fit_w)
        g.small(self.a_fit_p)
        g = home.group(_t("Navigate"))
        g.small(self.a_prev)
        g.small(self.a_next)
        g.small(self.a_find)
        g = home.group(_t("History"))
        g.small(self.a_undo)
        g.small(self.a_redo)
        ed = rb.page(_t("Edit"))
        g = ed.group(_t("Content"))
        g.large(M["edit_text"])
        g.large(M["text"])
        g.large(M["image"])
        g.large(self.a_fill_form)
        g.small(self.a_replace)
        g = ed.group(_t("Redact"))
        g.large(M["redact"])
        g.large(self.a_apply_redact)
        cm = rb.page(_t("Comment"))
        g = cm.group(_t("Markup"))
        g.large(M["highlight"])
        g.small(M["underline"])
        g.small(M["strike"])
        g.small(M["note"])
        g = cm.group(_t("Drawing"))
        g.small(M["rect"])
        g.small(M["ellipse"])
        g.small(M["line"])
        g.small(M["arrow"])
        g.small(M["ink"])
        g.small(self.a_color)
        g.small(M["callout"])
        g = cm.group(_t("Measure"))
        g.large(M["measure"])
        g.small(M["measure_area"])
        g.small(self.a_scale)
        pg = rb.page(_t("Pages"))
        g = pg.group(_t("Organize"))
        g.large(self.a_organize)
        g.large(self.a_rot_l)
        g.large(self.a_rot_r)
        g.large(self.a_del_page)
        g.small(self.a_blank)
        g.small(self.a_insert_file)
        g.small(self.a_dup_page)
        g = pg.group(_t("Order"))
        g.small(self.a_page_up)
        g.small(self.a_page_down)
        g.small(self.a_extract)
        g = pg.group(_t("Page Tools"))
        g.large(self.a_page_nums)
        g.large(self.a_crop)
        g.large(self.a_replace_pages)
        pr = rb.page(_t("Protect"))
        g = pr.group(_t("Security"))
        g.large(self.a_protect)
        g.large(self.a_unlock)
        g.large(self.a_flatten)
        g = pr.group(_t("Signatures"))
        g.large(self.a_sign)
        g.large(M["signature"])
        g.small(self.a_verify)
        g = pr.group(_t("Marks"))
        g.large(self.a_watermark)
        g.large(self.a_stamp)
        cv = rb.page(_t("Convert"))
        g = cv.group(_t("Export PDF to"))
        g.large(self.a_to_word)
        g.large(self.a_to_excel)
        g.large(self.a_to_ppt)
        g.large(self.a_to_img)
        g.large(self.a_to_txt)
        g = cv.group(_t("Recognize"))
        g.large(self.a_ocr)
        tl = rb.page(_t("Tools"))
        g = tl.group(_t("Optimize"))
        g.large(self.a_compress)
        g = tl.group(_t("More"))
        g.large(self.a_toolbox)
        g.large(self.a_props)
        aip = rb.page(_t("AI"))
        g = aip.group(_t("Chat PDF"))
        g.large(self.a_chat)
        g.large(self.a_summary)
        g = aip.group(_t("Translate"))
        g.large(self.a_translate_pdf)
        g = aip.group(_t("Setup"))
        g.large(self.a_ai_settings)
        return rb


def QFileDialogDir(parent) -> str:
    from app.shell.dialogs import get_directory

    return get_directory(parent, _t("Choose output folder"))


__all__ = ["PdfDocument", "insert_rich_text"]
