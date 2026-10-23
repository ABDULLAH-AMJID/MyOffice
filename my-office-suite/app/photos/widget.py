"""MyOffice Photos - image viewer and editor (a document tab like Writer or PDF).

Home: browse the folder, zoom, rotate/flip, crop, resize, undo.  Adjust: light and colour sliders with live
preview.  Filters: one-click looks.  Retouch: enhance, HD upscale, eraser (objects, watermarks), coloured pen
removal, background removal, mosaic/blur.  Draw: pen, shapes, arrows, text.  Tools: ID photo, compress, convert,
batch processing, collage, image to PDF, picture to text (OCR), information, print.
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                           QTransform)
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.photos import ops
from app.services.i18n import _t
from app.shell.document import DocumentError, DocumentWidget
from app.shell.icons import icon
from app.shell.ribbon import Ribbon

log = logging.getLogger(__name__)

SAVE_FILTER = ("PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;TIFF (*.tif *.tiff);;BMP (*.bmp);;GIF (*.gif);;"
               "Icon (*.ico);;PDF (*.pdf)")
ASPECTS = [("Free", None), ("1:1", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2), ("16:9", 16 / 9), ("9:16", 9 / 16),
           ("A4 portrait", 210 / 297), ("ID 35\u00d745 mm", 35 / 45), ("Passport 2\u00d72 in", 1.0)]


def pil_to_qimage(img: Image.Image) -> QImage:
    if img.mode == "RGBA":
        data = img.tobytes("raw", "RGBA")
        return QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888).copy()
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    return QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()


def checker_brush_pixmap() -> QPixmap:
    pm = QPixmap(16, 16)
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    p.fillRect(0, 0, 8, 8, QColor("#d9d9d9"))
    p.fillRect(8, 8, 8, 8, QColor("#d9d9d9"))
    p.end()
    return pm


# ============================================================================ canvas
class PhotoCanvas(QGraphicsView):
    zoomChanged = Signal(float)
    cropChanged = Signal()
    strokeDone = Signal(object)            # list of QPointF (image coordinates)
    shapeDone = Signal(str, object, object)  # kind, start QPointF, end QPointF
    clicked = Signal(object)               # QPointF in image coordinates

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#2b2c2f"))
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.checker = QGraphicsRectItem()
        self.checker.setBrush(checker_brush_pixmap())
        self.checker.setPen(Qt.NoPen)
        self.scene().addItem(self.checker)
        self.scene().addItem(self.pix)
        self.mask_item = QGraphicsPixmapItem()
        self.mask_item.setOpacity(0.45)
        self.mask_item.setZValue(2)
        self.scene().addItem(self.mask_item)
        self.crop_item = QGraphicsRectItem()
        self.crop_item.setPen(QPen(QColor("#ffffff"), 0, Qt.DashLine))
        self.crop_item.setBrush(QColor(255, 255, 255, 25))
        self.crop_item.setZValue(3)
        self.crop_item.hide()
        self.scene().addItem(self.crop_item)
        self.preview = QGraphicsPathItem()
        self.preview.setZValue(4)
        self.scene().addItem(self.preview)
        self.mode = "view"
        self.aspect: float | None = None
        self.brush = 30
        self.color = QColor("#e53935")
        self.width_ = 6
        self.mask: QImage | None = None
        self._pts: list[QPointF] = []
        self._start: QPointF | None = None
        self._zoom = 1.0

    # -- image
    def set_image(self, qimg: QImage, keep_view: bool = False):
        self.pix.setPixmap(QPixmap.fromImage(qimg))
        r = QRectF(0, 0, qimg.width(), qimg.height())
        self.checker.setRect(r)
        self.scene().setSceneRect(r.adjusted(-40, -40, 40, 40))
        if self.mask is not None and self.mask.size() != qimg.size():
            self.clear_mask()
        if not keep_view:
            self._auto_fit = True
            self.fit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_auto_fit", False):  # keep fitting until the user zooms
            self.fit()

    def image_size(self) -> QSize:
        return self.pix.pixmap().size()

    def fit(self):
        if self.pix.pixmap().isNull():
            return
        self.fitInView(self.pix, Qt.KeepAspectRatio)
        if self.transform().m11() > 1.0:  # small pictures: actual size, not blown up
            self.setTransform(QTransform())
            self.centerOn(self.pix)
        self._zoom = self.transform().m11()
        self.zoomChanged.emit(self._zoom)

    def set_zoom(self, z: float):
        self._auto_fit = False
        z = max(0.02, min(32.0, z))
        self.setTransform(QTransform.fromScale(z, z))
        self._zoom = z
        self.zoomChanged.emit(z)

    def zoom(self) -> float:
        return self._zoom

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier or self.mode == "view":
            self.set_zoom(self._zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15))
        else:
            super().wheelEvent(e)

    # -- modes
    def set_mode(self, mode: str):
        self.mode = mode
        self.setDragMode(QGraphicsView.ScrollHandDrag if mode == "view" else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.CrossCursor if mode != "view" else Qt.OpenHandCursor)
        self.crop_item.setVisible(mode == "crop")
        if mode == "crop" and self.crop_item.rect().isEmpty():
            s = self.image_size()
            self.crop_item.setRect(QRectF(s.width() * 0.1, s.height() * 0.1, s.width() * 0.8, s.height() * 0.8))
            if self.aspect:
                self._fix_aspect()
        self.preview.setPath(QPainterPath())

    def set_aspect(self, aspect: float | None):
        self.aspect = aspect
        if aspect:
            self._fix_aspect()

    def _fix_aspect(self):
        r = self.crop_item.rect()
        s = self.image_size()
        w = r.width()
        h = w / self.aspect
        if h > s.height():
            h = s.height() * 0.9
            w = h * self.aspect
        cx, cy = r.center().x(), r.center().y()
        r = QRectF(cx - w / 2, cy - h / 2, w, h)
        r.moveLeft(max(0, min(r.left(), s.width() - w)))
        r.moveTop(max(0, min(r.top(), s.height() - h)))
        self.crop_item.setRect(r)
        self.cropChanged.emit()

    def crop_rect(self) -> QRectF:
        return self.crop_item.rect().intersected(QRectF(0, 0, self.image_size().width(), self.image_size().height()))

    def ensure_mask(self):
        s = self.image_size()
        if self.mask is None or self.mask.size() != s:
            self.mask = QImage(s, QImage.Format_ARGB32_Premultiplied)
            self.mask.fill(Qt.transparent)

    def clear_mask(self):
        self.mask = None
        self.mask_item.setPixmap(QPixmap())

    def mask_pil(self) -> Image.Image | None:
        if self.mask is None:
            return None
        m = self.mask.convertToFormat(QImage.Format_RGBA8888)
        img = Image.frombuffer("RGBA", (m.width(), m.height()), bytes(m.constBits()), "raw", "RGBA", 0, 1)
        return img.getchannel("A").point(lambda v: 255 if v > 10 else 0)

    def _paint_mask(self, a: QPointF, b: QPointF):
        self.ensure_mask()
        p = QPainter(self.mask)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 40, 40, 255), self.brush, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(a, b)
        p.end()
        self.mask_item.setPixmap(QPixmap.fromImage(self.mask))

    # -- mouse
    def mousePressEvent(self, e):
        if self.mode == "view" or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        pt = self.mapToScene(e.position().toPoint())
        self._start = pt
        self._pts = [pt]
        if self.mode == "crop":
            r = self.crop_item.rect()
            self._crop_drag = "move" if r.contains(pt) else "new"
            self._crop_origin = (pt, QRectF(r))
        elif self.mode == "brush":
            self._paint_mask(pt, pt)
        elif self.mode in ("text", "wand", "picker"):
            self.clicked.emit(pt)
            self._start = None

    def mouseMoveEvent(self, e):
        if self._start is None:
            return super().mouseMoveEvent(e)
        pt = self.mapToScene(e.position().toPoint())
        if self.mode == "crop":
            origin, r0 = self._crop_origin
            if self._crop_drag == "move":
                r = QRectF(r0)
                r.translate(pt - origin)
                s = self.image_size()
                r.moveLeft(max(0, min(r.left(), s.width() - r.width())))
                r.moveTop(max(0, min(r.top(), s.height() - r.height())))
            else:
                r = QRectF(origin, pt).normalized()
                if self.aspect:
                    h = r.width() / self.aspect
                    r.setHeight(h)
                    if pt.y() < origin.y():
                        r.moveBottom(origin.y())
                    if pt.x() < origin.x():
                        r.moveRight(origin.x())
            self.crop_item.setRect(r)
            self.cropChanged.emit()
        elif self.mode == "brush":
            self._paint_mask(self._pts[-1], pt)
            self._pts.append(pt)
        elif self.mode == "pen":
            self._pts.append(pt)
            path = QPainterPath(self._pts[0])
            for q in self._pts[1:]:
                path.lineTo(q)
            self.preview.setPen(QPen(self.color, self.width_, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            self.preview.setPath(path)
        elif self.mode in ("rect", "ellipse", "arrow", "line", "mosaic", "blur"):
            path = QPainterPath()
            r = QRectF(self._start, pt).normalized()
            if self.mode == "ellipse":
                path.addEllipse(r)
            elif self.mode in ("arrow", "line"):
                path.moveTo(self._start)
                path.lineTo(pt)
            else:
                path.addRect(r)
            pen = QPen(self.color if self.mode not in ("mosaic", "blur") else QColor("#1a73e8"),
                       self.width_ if self.mode not in ("mosaic", "blur") else 0, Qt.SolidLine if self.mode not in
                       ("mosaic", "blur") else Qt.DashLine)
            self.preview.setPen(pen)
            self.preview.setPath(path)

    def mouseReleaseEvent(self, e):
        if self._start is None:
            return super().mouseReleaseEvent(e)
        pt = self.mapToScene(e.position().toPoint())
        start, self._start = self._start, None
        self.preview.setPath(QPainterPath())
        if self.mode == "pen" and len(self._pts) > 1:
            self.strokeDone.emit(list(self._pts))
        elif self.mode in ("rect", "ellipse", "arrow", "line", "mosaic", "blur"):
            if abs(pt.x() - start.x()) > 3 or abs(pt.y() - start.y()) > 3:
                self.shapeDone.emit(self.mode, start, pt)
        self._pts = []


# ============================================================================ document
class PhotosDocument(DocumentWidget):
    kind = "photos"
    default_ext = ".png"
    writable_exts = (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".ico")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: Image.Image | None = None
        self.exif: bytes | None = None
        self.history: list[Image.Image] = []
        self.future: list[Image.Image] = []
        self.jpeg_quality = 92
        self.adj = ops.Adjust()
        self.canvas = PhotoCanvas(self)
        self.canvas.strokeDone.connect(self._pen_stroke)
        self.canvas.shapeDone.connect(self._shape_drawn)
        self.canvas.clicked.connect(self._canvas_click)
        self.side = QStackedWidget()
        self.side.setMinimumWidth(260)
        self.side.setMaximumWidth(340)
        self._build_panels()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.canvas)
        split.addWidget(self.side)
        split.setStretchFactor(0, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(split, 1)
        strip = QWidget()
        strip.setObjectName("StatusStrip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(8, 1, 8, 1)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        sl.addWidget(self.info, 1)
        self.zoom_label = QLabel("100%")
        sl.addWidget(self.zoom_label)
        lay.addWidget(strip)
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        self._preview_timer = QTimer(self, singleShot=True, interval=120, timeout=self._preview_adjust)
        self._build_actions()
        self.ribbon = self._build_ribbon()
        self.side.hide()
        self._update_actions()

    # ------------------------------------------------------------------ DocumentWidget API
    def untitled_label(self) -> str:
        return _t("Photo")

    def ribbon_widget(self):
        return self.ribbon

    def save_filters(self) -> str:
        return SAVE_FILTER

    @staticmethod
    def open_filter() -> str:
        return "Images (*.png *.jpg *.jpeg *.jfif *.webp *.bmp *.tif *.tiff *.gif *.ico *.heic *.heif)"

    def load(self, path: str) -> None:
        try:
            img = ops.load(path)
        except Exception as exc:
            raise DocumentError(_t("The picture could not be opened: {0}").format(exc)) from exc
        self.exif = None
        try:
            self.exif = Image.open(path).info.get("exif")
        except Exception:
            pass
        self.path = Path(path)
        self.read_only = Path(path).suffix.lower() not in self.writable_exts
        self.history.clear()
        self.future.clear()
        self.set_image(img, record=False)
        self.set_modified(False)
        self.titleChanged.emit()

    def new_from_image(self, img: Image.Image, title: str):
        self.title_override = title
        self.set_image(img, record=False)
        self.set_modified(True)

    def save_to(self, path: str) -> None:
        if self.image is None:
            raise DocumentError(_t("There is no picture to save."))
        ops.save(self.image, path, quality=self.jpeg_quality, exif=self.exif)

    def export_pdf_to(self, path: str) -> None:
        ops.save(self.image, path)

    def print_document(self) -> None:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        if self.image is None:
            return
        printer = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(printer, self).exec() != QDialog.Accepted:
            return
        qimg = pil_to_qimage(self.image)
        p = QPainter(printer)
        r = p.viewport()
        size = qimg.size().scaled(r.size(), Qt.KeepAspectRatio)
        p.setViewport(r.x() + (r.width() - size.width()) // 2, r.y() + (r.height() - size.height()) // 2,
                      size.width(), size.height())
        p.setWindow(qimg.rect())
        p.drawImage(0, 0, qimg)
        p.end()

    def status_text(self) -> str:
        if self.image is None:
            return ""
        size = ""
        if self.path and self.path.exists():
            size = f" \u2022 {self.path.stat().st_size / 1024:,.0f} KB"
        return f"{self.image.width} \u00d7 {self.image.height} px \u2022 {self.image.mode}{size}"

    # ------------------------------------------------------------------ image state
    def set_image(self, img: Image.Image, record: bool = True, keep_view: bool = False):
        if record and self.image is not None:
            self.history.append(self.image)
            if len(self.history) > 25:
                self.history.pop(0)
            self.future.clear()
            self.set_modified(True)
        self.image = img
        self.canvas.set_image(pil_to_qimage(img), keep_view)
        self.info.setText(self.status_text())
        self._update_actions()

    def apply(self, fn, *args, label: str = "", busy: bool = False, **kw):
        if self.image is None:
            return
        if busy:
            from app.shell.tasks import TaskError, run_task

            try:
                out = run_task(self, label or _t("Working\u2026"), lambda: fn(self.image, *args, **kw))
            except TaskError as exc:
                from app.shell.dialogs import show_error

                show_error(self, label or _t("Photos"), str(exc.original), exc.tb)
                return
        else:
            out = fn(self.image, *args, **kw)
        if out is not None:
            self.set_image(out, keep_view=out.size == self.image.size)
            if label:
                self.statusChanged.emit(label)

    def undo(self):
        if self.history:
            self.future.append(self.image)
            img = self.history.pop()
            self.image = img
            self.canvas.set_image(pil_to_qimage(img), True)
            self.set_modified(True)
            self.info.setText(self.status_text())

    def redo(self):
        if self.future:
            self.history.append(self.image)
            img = self.future.pop()
            self.image = img
            self.canvas.set_image(pil_to_qimage(img), True)
            self.info.setText(self.status_text())

    def _update_actions(self):
        has = self.image is not None
        for a in getattr(self, "_image_actions", []):
            a.setEnabled(has)

    # ------------------------------------------------------------------ folder browsing
    def _siblings(self) -> list[Path]:
        if not self.path:
            return []
        return sorted((p for p in self.path.parent.iterdir() if p.suffix.lower() in ops.OPEN_EXTS),
                      key=lambda p: p.name.lower())

    def browse(self, step: int):
        files = self._siblings()
        if not files or self.path not in files:
            return
        if self.is_modified():
            from app.shell.dialogs import ask_save_changes

            choice = ask_save_changes(self, self.display_name())
            if choice == "save" and not self.save():
                return
            if choice == "cancel":
                return
        nxt = files[(files.index(self.path) + step) % len(files)]
        self.load(str(nxt))

    # ------------------------------------------------------------------ actions / ribbon
    def _act(self, text, icon_name=None, slot=None, shortcut=None, checkable=False, needs_image=True) -> QAction:
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
        if needs_image:
            self._image_actions = getattr(self, "_image_actions", []) + [a]
        return a

    def _build_actions(self):
        A = self._act
        self.a_open = A(_t("Open"), "open", self._open, "Ctrl+O", needs_image=False)
        self.a_prev = A(_t("Previous"), "prev", lambda: self.browse(-1), "Left")
        self.a_next = A(_t("Next"), "next", lambda: self.browse(1), "Right")
        self.a_undo = A(_t("Undo"), "undo", self.undo, "Ctrl+Z")
        self.a_redo = A(_t("Redo"), "redo", self.redo, "Ctrl+Y")
        self.a_fit = A(_t("Fit"), "zoom_fit", self.canvas.fit, "Ctrl+0")
        self.a_100 = A(_t("100%"), "zoom_in", lambda: self.canvas.set_zoom(1.0), "Ctrl+1")
        self.a_rot_l = A(_t("Rotate Left"), "rotate_ccw", lambda: self.apply(ops.rotate, 90), "Ctrl+L")
        self.a_rot_r = A(_t("Rotate Right"), "rotate_cw", lambda: self.apply(ops.rotate, -90), "Ctrl+R")
        self.a_flip_h = A(_t("Flip Horizontal"), "flip_h", lambda: self.apply(ops.flip, True))
        self.a_flip_v = A(_t("Flip Vertical"), "flip_v", lambda: self.apply(ops.flip, False))
        self.a_crop = A(_t("Crop"), "crop", lambda on: self._tool("crop", on), "C", checkable=True)
        self.a_resize = A(_t("Resize"), "resize", self.resize_dialog)
        self.a_straighten = A(_t("Straighten"), "rotate_cw", self.straighten_dialog)
        self.a_adjust = A(_t("Adjust"), "adjust", lambda on: self._panel("adjust", on), checkable=True)
        self.a_filters = A(_t("Filters"), "filter", lambda on: self._panel("filters", on), checkable=True)
        self.a_enhance = A(_t("Enhance"), "enhance", lambda: self.apply(ops.auto_enhance, label=_t("Enhanced"),
                                                                        busy=True), "Ctrl+E")
        self.a_upscale2 = A(_t("HD Upscale 2\u00d7"), "magic", lambda: self.apply(ops.upscale, 2, busy=True,
                                                                                    label=_t("Enlarged 2\u00d7")))
        self.a_upscale4 = A(_t("HD Upscale 4\u00d7"), "magic", lambda: self.apply(ops.upscale, 4, busy=True,
                                                                                    label=_t("Enlarged 4\u00d7")))
        self.a_denoise = A(_t("Reduce Noise"), "enhance", lambda: self.apply(ops.denoise, label=_t("Noise reduced")))
        self.a_sharpen = A(_t("Sharpen"), "enhance", lambda: self.apply(ops.sharpen, label=_t("Sharpened")))
        self.a_eraser = A(_t("Eraser"), "eraser", lambda on: self._tool("brush", on), "E", checkable=True)
        self.a_wm = A(_t("Remove Watermark"), "watermark_remove", lambda on: self._tool("brush", on, watermark=True),
                      checkable=True)
        self.a_pen_marks = A(_t("Remove Pen Marks"), "eraser", self.pen_marks_dialog)
        self.a_cutout = A(_t("Remove Background"), "cutout", lambda on: self._panel("cutout", on), checkable=True)
        self.a_mosaic = A(_t("Mosaic"), "mosaic", lambda on: self._tool("mosaic", on), checkable=True)
        self.a_blur = A(_t("Blur Area"), "redact_img", lambda on: self._tool("blur", on), checkable=True)
        self.a_pen = A(_t("Pen"), "pen_tool", lambda on: self._tool("pen", on), "P", checkable=True)
        self.a_rect = A(_t("Rectangle"), "rect_tool", lambda on: self._tool("rect", on), checkable=True)
        self.a_ellipse = A(_t("Ellipse"), "ellipse_tool", lambda on: self._tool("ellipse", on), checkable=True)
        self.a_arrow = A(_t("Arrow"), "arrow_tool", lambda on: self._tool("arrow", on), checkable=True)
        self.a_text = A(_t("Text"), "text_tool", lambda on: self._tool("text", on), "T", checkable=True)
        self.a_color = A(_t("Colour"), "picker", self.pick_color)
        self.a_watermark = A(_t("Add Watermark"), "watermark", self.watermark_dialog)
        self.a_id = A(_t("ID Photo"), "id_photo", self.id_photo_dialog)
        self.a_compress = A(_t("Compress"), "compress_img", self.compress_dialog)
        self.a_convert = A(_t("Convert"), "convert", self.save_as)
        self.a_batch = A(_t("Batch Process"), "batch", lambda: BatchDialog(self).exec(), needs_image=False)
        self.a_collage = A(_t("Collage"), "collage", self.collage_dialog, needs_image=False)
        self.a_pdf = A(_t("Images to PDF"), "pdf", self.images_to_pdf, needs_image=False)
        self.a_ocr = A(_t("Picture to Text"), "ocr_image", self.picture_to_text)
        self.a_info = A(_t("Information"), "info_img", self.info_dialog)
        self.a_print = A(_t("Print"), "print", self.print_document, "Ctrl+P")
        self.a_translate = A(_t("Translate Text in Picture"), "translate", self.translate_picture)
        self.tool_actions = {"crop": self.a_crop, "brush": self.a_eraser, "mosaic": self.a_mosaic,
                             "blur": self.a_blur, "pen": self.a_pen, "rect": self.a_rect, "ellipse": self.a_ellipse,
                             "arrow": self.a_arrow, "text": self.a_text}

    def _build_ribbon(self) -> Ribbon:
        rb = Ribbon(self)
        home = rb.page(_t("Home"))
        g = home.group(_t("File"))
        g.large(self.a_open)
        g.small(self.a_prev)
        g.small(self.a_next)
        g.small(self.a_print)
        g = home.group(_t("History"))
        g.small(self.a_undo)
        g.small(self.a_redo)
        g = home.group(_t("View"))
        g.large(self.a_fit)
        g.small(self.a_100)
        g = home.group(_t("Rotate && Flip"))
        g.small(self.a_rot_l)
        g.small(self.a_rot_r)
        g.small(self.a_straighten)
        g.small(self.a_flip_h)
        g.small(self.a_flip_v)
        g = home.group(_t("Size"))
        g.large(self.a_crop)
        g.large(self.a_resize)
        adj = rb.page(_t("Adjust"))
        g = adj.group(_t("Light && Colour"))
        g.large(self.a_adjust)
        g.large(self.a_filters)
        g.large(self.a_enhance)
        ret = rb.page(_t("Retouch"))
        g = ret.group(_t("Quality"))
        g.large(self.a_enhance)
        g.small(self.a_upscale2)
        g.small(self.a_upscale4)
        g.small(self.a_denoise)
        g.small(self.a_sharpen)
        g = ret.group(_t("Remove"))
        g.large(self.a_eraser)
        g.large(self.a_wm)
        g.large(self.a_cutout)
        g.small(self.a_pen_marks)
        g = ret.group(_t("Privacy"))
        g.small(self.a_mosaic)
        g.small(self.a_blur)
        dr = rb.page(_t("Draw"))
        g = dr.group(_t("Tools"))
        for a in (self.a_pen, self.a_rect, self.a_ellipse, self.a_arrow, self.a_text):
            g.large(a)
        g = dr.group(_t("Style"))
        g.large(self.a_color)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 200)
        self.width_spin.setValue(6)
        self.width_spin.valueChanged.connect(self._set_width)
        g.row(QLabel(_t("Width:")), self.width_spin)
        g.small(self.a_watermark)
        tl = rb.page(_t("Tools"))
        g = tl.group(_t("Create"))
        g.large(self.a_id)
        g.large(self.a_collage)
        g = tl.group(_t("Files"))
        g.large(self.a_compress)
        g.large(self.a_convert)
        g.large(self.a_batch)
        g.large(self.a_pdf)
        g = tl.group(_t("Inspect"))
        g.large(self.a_ocr)
        g.large(self.a_info)
        aip = rb.page(_t("AI"))
        g = aip.group(_t("Smart Tools"))
        g.large(self.a_enhance)
        g.large(self.a_eraser)
        g.large(self.a_cutout)
        g.large(self.a_upscale2)
        g = aip.group(_t("Text"))
        g.large(self.a_ocr)
        g.large(self.a_translate)
        return rb

    # ------------------------------------------------------------------ side panels
    def _build_panels(self):
        # tool options (crop, brush, draw)
        self.tool_panel = QWidget()
        tv = QVBoxLayout(self.tool_panel)
        self.tool_title = QLabel("")
        self.tool_title.setStyleSheet("font-weight:600;font-size:14px")
        tv.addWidget(self.tool_title)
        self.tool_hint = QLabel("")
        self.tool_hint.setWordWrap(True)
        self.tool_hint.setStyleSheet("color:#5f6368")
        tv.addWidget(self.tool_hint)
        self.aspect_combo = QComboBox()
        for label, val in ASPECTS:
            self.aspect_combo.addItem(_t(label), val)
        self.aspect_combo.currentIndexChanged.connect(lambda _i: self.canvas.set_aspect(self.aspect_combo.currentData()))
        tv.addWidget(self.aspect_combo)
        self.brush_row = QWidget()
        br = QHBoxLayout(self.brush_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(QLabel(_t("Brush size:")))
        self.brush_slider = QSlider(Qt.Horizontal)
        self.brush_slider.setRange(4, 200)
        self.brush_slider.setValue(30)
        self.brush_slider.valueChanged.connect(lambda v: setattr(self.canvas, "brush", v))
        br.addWidget(self.brush_slider)
        tv.addWidget(self.brush_row)
        row = QHBoxLayout()
        self.tool_apply = QPushButton(_t("Apply"))
        self.tool_apply.setObjectName("Primary")
        self.tool_apply.clicked.connect(self._tool_apply)
        self.tool_clear = QPushButton(_t("Clear"))
        self.tool_clear.clicked.connect(self.canvas.clear_mask)
        self.tool_done = QPushButton(_t("Done"))
        self.tool_done.clicked.connect(lambda: self._tool(self.canvas.mode, False))
        row.addWidget(self.tool_apply)
        row.addWidget(self.tool_clear)
        row.addWidget(self.tool_done)
        tv.addLayout(row)
        tv.addStretch(1)
        self.side.addWidget(self.tool_panel)
        # adjust
        self.adjust_panel = QWidget()
        av = QVBoxLayout(self.adjust_panel)
        av.addWidget(QLabel("<b>%s</b>" % _t("Adjust")))
        self.sliders: dict[str, QSlider] = {}
        for key, label, lo in (("brightness", "Brightness", -100), ("contrast", "Contrast", -100),
                               ("exposure", "Exposure", -100), ("highlights", "Highlights", -100),
                               ("shadows", "Shadows", -100), ("saturation", "Saturation", -100),
                               ("temperature", "Temperature", -100), ("tint", "Tint", -100),
                               ("sharpness", "Sharpness", 0), ("vignette", "Vignette", 0)):
            lab = QLabel(_t(label))
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, 100)
            sl.setValue(0)
            sl.valueChanged.connect(lambda v, k=key: self._adjust_changed(k, v))
            av.addWidget(lab)
            av.addWidget(sl)
            self.sliders[key] = sl
        row = QHBoxLayout()
        ap = QPushButton(_t("Apply"))
        ap.setObjectName("Primary")
        ap.clicked.connect(self._adjust_apply)
        rs = QPushButton(_t("Reset"))
        rs.clicked.connect(self._adjust_reset)
        row.addWidget(ap)
        row.addWidget(rs)
        av.addLayout(row)
        av.addStretch(1)
        self.side.addWidget(self.adjust_panel)
        # filters
        self.filter_panel = QWidget()
        fv = QVBoxLayout(self.filter_panel)
        fv.addWidget(QLabel("<b>%s</b>" % _t("Filters")))
        self.filter_list = QListWidget()
        self.filter_list.setViewMode(QListWidget.IconMode)
        self.filter_list.setIconSize(QSize(100, 75))
        self.filter_list.setGridSize(QSize(116, 104))
        self.filter_list.setResizeMode(QListWidget.Adjust)
        self.filter_list.itemClicked.connect(self._filter_clicked)
        fv.addWidget(self.filter_list, 1)
        self.side.addWidget(self.filter_panel)
        # background removal
        self.cutout_panel = QWidget()
        cv = QVBoxLayout(self.cutout_panel)
        cv.addWidget(QLabel("<b>%s</b>" % _t("Remove Background")))
        hint = QLabel(_t("Automatic works for plain backgrounds. Or click on the background parts you want removed "
                         "(magic wand), then Apply."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5f6368")
        cv.addWidget(hint)
        cv.addWidget(QLabel(_t("Tolerance:")))
        self.tol = QSlider(Qt.Horizontal)
        self.tol.setRange(5, 120)
        self.tol.setValue(40)
        cv.addWidget(self.tol)
        self.seed_label = QLabel(_t("Clicked points: 0"))
        cv.addWidget(self.seed_label)
        self.bg_color = QComboBox()
        for label, val in (("Transparent", ""), ("White", "#ffffff"), ("Blue (ID photo)", "#1e64c8"),
                           ("Light blue", "#bcd7f2"), ("Red", "#d32f2f"), ("Grey", "#9e9e9e")):
            self.bg_color.addItem(_t(label), val)
        cv.addWidget(QLabel(_t("New background:")))
        cv.addWidget(self.bg_color)
        b1 = QPushButton(_t("Remove Automatically"))
        b1.setObjectName("Primary")
        b1.clicked.connect(lambda: self._cutout(False))
        b2 = QPushButton(_t("Apply Clicked Points"))
        b2.clicked.connect(lambda: self._cutout(True))
        b3 = QPushButton(_t("Clear Points"))
        b3.clicked.connect(self._clear_seeds)
        for b in (b1, b2, b3):
            cv.addWidget(b)
        cv.addStretch(1)
        self.side.addWidget(self.cutout_panel)
        self._seeds: list[tuple[int, int]] = []

    def _uncheck_others(self, keep: QAction | None):
        for a in list(self.tool_actions.values()) + [self.a_adjust, self.a_filters, self.a_cutout, self.a_wm]:
            if a is not keep and a.isChecked():
                a.blockSignals(True)
                a.setChecked(False)
                a.blockSignals(False)

    def _tool(self, mode: str, on: bool, watermark: bool = False):
        if not on or self.image is None:
            self.canvas.set_mode("view")
            self.canvas.clear_mask()
            self._uncheck_others(None)
            self.side.hide()
            return
        self._uncheck_others(self.a_wm if watermark else self.tool_actions.get(mode))
        self.canvas.set_mode(mode)
        titles = {"crop": (_t("Crop"), _t("Drag on the picture to choose the area, or move the frame. Choose an "
                                         "aspect ratio below, then Apply.")),
                  "brush": (_t("Remove Watermark") if watermark else _t("Eraser"),
                            _t("Paint over the watermark, text or object to remove, then Apply. The area is "
                               "filled from its surroundings.")),
                  "mosaic": (_t("Mosaic"), _t("Drag over the area to pixelate (faces, number plates).")),
                  "blur": (_t("Blur Area"), _t("Drag over the area to blur.")),
                  "pen": (_t("Pen"), _t("Draw freehand. Colour and width: Draw tab.")),
                  "rect": (_t("Rectangle"), _t("Drag to draw a rectangle.")),
                  "ellipse": (_t("Ellipse"), _t("Drag to draw an ellipse.")),
                  "arrow": (_t("Arrow"), _t("Drag from the start to the tip of the arrow.")),
                  "text": (_t("Text"), _t("Click where the text should start."))}
        t, h = titles.get(mode, ("", ""))
        self.tool_title.setText(t)
        self.tool_hint.setText(h)
        self.aspect_combo.setVisible(mode == "crop")
        self.brush_row.setVisible(mode == "brush")
        self.tool_apply.setVisible(mode in ("crop", "brush"))
        self.tool_clear.setVisible(mode == "brush")
        self.side.setCurrentWidget(self.tool_panel)
        self.side.show()

    def _panel(self, name: str, on: bool):
        self.canvas.set_mode("view")
        if not on or self.image is None:
            self._uncheck_others(None)
            self.side.hide()
            if name == "adjust":
                self._adjust_reset()
            if name == "cutout":
                self._clear_seeds()
            return
        keep = {"adjust": self.a_adjust, "filters": self.a_filters, "cutout": self.a_cutout}[name]
        self._uncheck_others(keep)
        if name == "adjust":
            self._adjust_base = self.image
            self._adjust_proxy = self.image.copy()
            self._adjust_proxy.thumbnail((1400, 1400))
            self.side.setCurrentWidget(self.adjust_panel)
        elif name == "filters":
            self._fill_filters()
            self.side.setCurrentWidget(self.filter_panel)
        else:
            self.canvas.set_mode("wand")
            self.side.setCurrentWidget(self.cutout_panel)
        self.side.show()

    # ------------------------------------------------------------------ tool results
    def _tool_apply(self):
        mode = self.canvas.mode
        if mode == "crop":
            r = self.canvas.crop_rect()
            if r.width() >= 2 and r.height() >= 2:
                self.apply(ops.crop, (r.left(), r.top(), r.right(), r.bottom()), label=_t("Cropped"))
                self.canvas.crop_item.setRect(QRectF())
            self._tool("crop", False)
        elif mode == "brush":
            mask = self.canvas.mask_pil()
            if mask is None or not mask.getbbox():
                return
            self.canvas.clear_mask()
            self.apply(ops.inpaint, mask, busy=True, label=_t("Removed"))

    def _set_width(self, v: int):
        self.canvas.width_ = v

    def pick_color(self):
        c = QColorDialog.getColor(self.canvas.color, self, _t("Colour"), QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self.canvas.color = c

    def _rgba(self) -> tuple:
        c = self.canvas.color
        return (c.red(), c.green(), c.blue(), c.alpha())

    def _draw(self, fn):
        img = self.image.convert("RGBA")
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        fn(ImageDraw.Draw(layer))
        img.alpha_composite(layer)
        self.set_image(img if self.image.mode == "RGBA" else img.convert("RGB"), keep_view=True)

    def _pen_stroke(self, pts):
        w = self.canvas.width_
        xy = [(p.x(), p.y()) for p in pts]
        self._draw(lambda d: (d.line(xy, fill=self._rgba(), width=w, joint="curve"),
                              [d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=self._rgba())
                               for x, y in (xy[0], xy[-1])]))

    def _shape_drawn(self, kind, a: QPointF, b: QPointF):
        box = (int(min(a.x(), b.x())), int(min(a.y(), b.y())), int(max(a.x(), b.x())), int(max(a.y(), b.y())))
        if kind == "mosaic":
            self.apply(ops.mosaic, box, label=_t("Mosaic applied"))
            return
        if kind == "blur":
            self.apply(ops.blur_region, box, label=_t("Area blurred"))
            return
        w, col = self.canvas.width_, self._rgba()
        if kind == "rect":
            self._draw(lambda d: d.rectangle(box, outline=col, width=w))
        elif kind == "ellipse":
            self._draw(lambda d: d.ellipse(box, outline=col, width=w))
        elif kind in ("arrow", "line"):
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = max(12, w * 4)
            p1 = (b.x() - head * math.cos(ang - 0.45), b.y() - head * math.sin(ang - 0.45))
            p2 = (b.x() - head * math.cos(ang + 0.45), b.y() - head * math.sin(ang + 0.45))
            self._draw(lambda d: (d.line((a.x(), a.y(), b.x(), b.y()), fill=col, width=w),
                                  d.polygon([(b.x(), b.y()), p1, p2], fill=col)))

    def _canvas_click(self, pt: QPointF):
        if self.canvas.mode == "text":
            d = TextDialog(self)
            if d.exec() == QDialog.Accepted and d.text.toPlainText().strip():
                c = self.canvas.color
                self.apply(ops.add_text, d.text.toPlainText(), (pt.x(), pt.y()), d.size.value(), c.name(),
                           d.bold.isChecked(), d.stroke.value())
        elif self.canvas.mode == "wand":
            self._seeds.append((int(pt.x()), int(pt.y())))
            self.seed_label.setText(_t("Clicked points: {0}").format(len(self._seeds)))

    def _clear_seeds(self):
        self._seeds = []
        self.seed_label.setText(_t("Clicked points: 0"))

    def _cutout(self, use_seeds: bool):
        if use_seeds and not self._seeds:
            self.statusChanged.emit(_t("Click on the background first."))
            return
        seeds = list(self._seeds) if use_seeds else None
        color = self.bg_color.currentData()
        tol = self.tol.value()

        def work(img):
            out = ops.remove_background(img, tolerance=tol, seeds=seeds)
            if color:
                bg = Image.new("RGBA", out.size, color)
                bg.alpha_composite(out)
                return bg.convert("RGB")
            return out
        self.apply(work, busy=True, label=_t("Background removed"))
        self._clear_seeds()

    # ------------------------------------------------------------------ adjust / filters
    def _adjust_changed(self, key, value):
        setattr(self.adj, key, value)
        self._preview_timer.start()

    def _preview_adjust(self):
        proxy = getattr(self, "_adjust_proxy", None)
        if proxy is None:
            return
        out = ops.adjust(proxy, self.adj)
        q = pil_to_qimage(out).scaled(self.image.width, self.image.height, Qt.IgnoreAspectRatio,
                                      Qt.SmoothTransformation) if out.size != self.image.size else pil_to_qimage(out)
        self.canvas.set_image(q, True)

    def _adjust_apply(self):
        if self.adj.is_neutral():
            return
        a = ops.Adjust(**self.adj.__dict__)
        self.apply(ops.adjust, a, busy=True, label=_t("Adjusted"))
        self._adjust_reset(apply_view=False)
        self._adjust_proxy = self.image.copy()
        self._adjust_proxy.thumbnail((1400, 1400))

    def _adjust_reset(self, apply_view: bool = True):
        self.adj = ops.Adjust()
        for sl in self.sliders.values():
            sl.blockSignals(True)
            sl.setValue(0)
            sl.blockSignals(False)
        if apply_view and self.image is not None:
            self.canvas.set_image(pil_to_qimage(self.image), True)

    def _fill_filters(self):
        self.filter_list.clear()
        thumb = self.image.copy()
        thumb.thumbnail((200, 150))
        for name in ops.FILTERS:
            it = QListWidgetItem(QIcon(QPixmap.fromImage(pil_to_qimage(ops.apply_filter(thumb, name)))), _t(name))
            it.setData(Qt.UserRole, name)
            self.filter_list.addItem(it)

    def _filter_clicked(self, it):
        name = it.data(Qt.UserRole)
        if name != "Original":
            self.apply(ops.apply_filter, name, busy=True, label=_t("Filter: {0}").format(_t(name)))

    # ------------------------------------------------------------------ dialogs
    def _open(self):
        from app.shell.dialogs import get_open_files

        files = get_open_files(self, _t("Open Picture"), self.open_filter())
        if files:
            main = self.window()
            if self.image is None and not self.is_modified():
                self.load(files[0])
                files = files[1:]
            for f in files:
                if hasattr(main, "open_path"):
                    main.open_path(f)

    def resize_dialog(self):
        d = ResizeDialog(self, self.image.size)
        if d.exec() == QDialog.Accepted:
            self.apply(ops.resize, d.w.value(), d.h.value(), d.quality.currentData(), busy=True,
                       label=_t("Resized to {0} \u00d7 {1}").format(d.w.value(), d.h.value()))

    def straighten_dialog(self):
        angle, ok = _ask_float(self, _t("Straighten"), _t("Rotate by (degrees, positive = anticlockwise):"), 0, -45, 45)
        if ok and angle:
            self.apply(ops.rotate, angle, busy=True, label=_t("Rotated"))

    def pen_marks_dialog(self):
        d = QDialog(self)
        d.setWindowTitle(_t("Remove Pen Marks"))
        form = QFormLayout(d)
        info = QLabel(_t("Removes notes, ticks and signatures written with a blue, red or green pen from a scanned "
                         "page. Printed black text stays."))
        info.setWordWrap(True)
        form.addRow(info)
        boxes = {}
        for c, label in (("blue", "Blue ink"), ("red", "Red ink"), ("green", "Green ink")):
            cb = QCheckBox(_t(label))
            cb.setChecked(True)
            boxes[c] = cb
            form.addRow(cb)
        strength = QSlider(Qt.Horizontal)
        strength.setRange(10, 100)
        strength.setValue(60)
        form.addRow(_t("Strength:"), strength)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec() == QDialog.Accepted:
            cols = tuple(c for c, cb in boxes.items() if cb.isChecked())
            if cols:
                self.apply(ops.remove_pen_marks, cols, strength.value(), busy=True, label=_t("Pen marks removed"))

    def watermark_dialog(self):
        d = QDialog(self)
        d.setWindowTitle(_t("Add Watermark"))
        form = QFormLayout(d)
        text = QLineEdit("\u00a9 " + (os.environ.get("USERNAME") or os.environ.get("USER") or "MyOffice"))
        opacity = QSpinBox()
        opacity.setRange(5, 100)
        opacity.setValue(35)
        opacity.setSuffix(" %")
        tiled = QCheckBox(_t("Repeat across the picture"))
        tiled.setChecked(True)
        form.addRow(_t("Text:"), text)
        form.addRow(_t("Opacity:"), opacity)
        form.addRow(tiled)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec() == QDialog.Accepted and text.text().strip():
            self.apply(ops.text_watermark, text.text(), opacity.value(), 6, tiled.isChecked(),
                       label=_t("Watermark added"))

    def id_photo_dialog(self):
        d = IdPhotoDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        size = ops.ID_SIZES[d.size.currentText()]
        from app.shell.tasks import TaskError, run_task

        try:
            photo = run_task(self, _t("Making the ID photo\u2026"), lambda: ops.id_photo(
                self.image, size, d.color.currentData(), 300, d.remove.isChecked()))
        except TaskError as exc:
            from app.shell.dialogs import show_error

            show_error(self, _t("ID Photo"), str(exc.original), exc.tb)
            return
        result = photo
        if d.sheet.isChecked():
            result, n = ops.print_sheet(photo, (152.4, 101.6) if d.sheet_size.currentIndex() == 0 else (210, 297))
            self.statusChanged.emit(_t("{0} photos on the sheet").format(n))
        main = self.window()
        if hasattr(main, "new_photo"):
            main.new_photo(result, _t("ID Photo"))
        else:
            self.set_image(result)

    def compress_dialog(self):
        d = CompressDialog(self)
        d.exec()

    def collage_dialog(self):
        from app.shell.dialogs import get_open_files

        files = get_open_files(self, _t("Choose pictures for the collage"), self.open_filter())
        if len(files) < 2:
            return
        cols, ok = _ask_int(self, _t("Collage"), _t("Columns:"), 2 if len(files) <= 4 else 3, 1, 8)
        if not ok:
            return
        imgs = [ops.load(f) for f in files[:36]]
        result = ops.collage(imgs, cols)
        main = self.window()
        if hasattr(main, "new_photo"):
            main.new_photo(result, _t("Collage"))
        else:
            self.set_image(result)

    def images_to_pdf(self):
        from app.pdf.ops import images_to_pdf
        from app.shell.dialogs import get_open_files, get_save_file, show_info

        files = get_open_files(self, _t("Images to PDF"), self.open_filter())
        if not files:
            return
        out = get_save_file(self, _t("Save PDF"), "PDF (*.pdf)", "images.pdf", "pdf")
        if out:
            images_to_pdf(files, out)
            show_info(self, _t("Images to PDF"), _t("Saved:\n{0}").format(out))

    def picture_to_text(self):
        from app.pdf.ops import ocr_image, usable_ocr_choices
        from app.shell.tasks import TaskError, run_task

        lang, ok = _ask_choice(self, _t("Picture to Text"), _t("Language of the text:"), usable_ocr_choices(
            [("urd+eng", _t("Urdu + English")), ("eng", _t("English")), ("hin+eng", _t("Hindi + English")),
             ("ara+eng", _t("Arabic + English"))]))
        if not ok:
            return
        img = self.image.convert("RGB")
        try:
            text = run_task(self, _t("Reading the text\u2026"), lambda: ocr_image(img, lang))
        except TaskError as exc:
            from app.shell.dialogs import show_error

            show_error(self, _t("Picture to Text"), str(exc.original), exc.tb)
            return
        TextResultDialog(self, text).exec()

    def translate_picture(self):
        from app.pdf.ops import ocr_image
        from app.shell.ai_dialogs import TranslateDialog, ensure_ai
        from app.shell.tasks import TaskError, run_task

        if not ensure_ai(self):
            return
        img = self.image.convert("RGB")
        try:
            text = run_task(self, _t("Reading the text\u2026"), lambda: ocr_image(img, "urd+eng"))
        except TaskError as exc:
            from app.shell.dialogs import show_error

            show_error(self, _t("Translate"), str(exc.original), exc.tb)
            return
        paras = [p.strip() for p in text.split("\n\n") if p.strip()] or [text.strip()]
        TranslateDialog(self, paras, None, _t("Translate Text in Picture")).exec()

    def info_dialog(self):
        d = QDialog(self)
        d.setWindowTitle(_t("Information"))
        d.resize(560, 520)
        v = QVBoxLayout(d)
        hist = HistogramWidget(ops.histogram(self.image))
        v.addWidget(hist)
        rows = ops.exif_info(self.image)
        if self.path:
            rows.insert(0, (_t("File"), str(self.path)))
            rows.insert(1, (_t("File size"), f"{self.path.stat().st_size / 1024:,.1f} KB" if self.path.exists() else ""))
        t = QTableWidget(len(rows), 2)
        t.setHorizontalHeaderLabels([_t("Property"), _t("Value")])
        t.horizontalHeader().setStretchLastSection(True)
        for i, (k, val) in enumerate(rows):
            t.setItem(i, 0, QTableWidgetItem(k))
            t.setItem(i, 1, QTableWidgetItem(val))
        v.addWidget(t, 1)
        strip = QPushButton(_t("Remove Metadata (EXIF, GPS) When Saving"))
        strip.clicked.connect(lambda: (setattr(self, "exif", None), self.set_modified(True), d.accept()))
        v.addWidget(strip)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        d.exec()


# ============================================================================ small dialogs
def _ask_float(parent, title, label, value, lo, hi):
    d = QDialog(parent)
    d.setWindowTitle(title)
    form = QFormLayout(d)
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setDecimals(1)
    sp.setValue(value)
    form.addRow(label, sp)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    form.addRow(bb)
    ok = d.exec() == QDialog.Accepted
    return sp.value(), ok


def _ask_int(parent, title, label, value, lo, hi):
    d = QDialog(parent)
    d.setWindowTitle(title)
    form = QFormLayout(d)
    sp = QSpinBox()
    sp.setRange(lo, hi)
    sp.setValue(value)
    form.addRow(label, sp)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    form.addRow(bb)
    ok = d.exec() == QDialog.Accepted
    return sp.value(), ok


def _ask_choice(parent, title, label, items):
    d = QDialog(parent)
    d.setWindowTitle(title)
    form = QFormLayout(d)
    cb = QComboBox()
    for key, text in items:
        cb.addItem(text, key)
    form.addRow(label, cb)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    form.addRow(bb)
    ok = d.exec() == QDialog.Accepted
    return cb.currentData(), ok


class TextDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Text"))
        form = QFormLayout(self)
        self.text = QPlainTextEdit()
        self.text.setMaximumHeight(90)
        self.size = QSpinBox()
        self.size.setRange(6, 600)
        self.size.setValue(48)
        self.bold = QCheckBox(_t("Bold"))
        self.bold.setChecked(True)
        self.stroke = QSpinBox()
        self.stroke.setRange(0, 20)
        self.stroke.setValue(2)
        form.addRow(_t("Text:"), self.text)
        form.addRow(_t("Size:"), self.size)
        form.addRow(self.bold)
        form.addRow(_t("Outline:"), self.stroke)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class ResizeDialog(QDialog):
    def __init__(self, parent, size):
        super().__init__(parent)
        self.setWindowTitle(_t("Resize"))
        self.ratio = size[0] / size[1]
        form = QFormLayout(self)
        self.pct = QSpinBox()
        self.pct.setRange(1, 800)
        self.pct.setValue(100)
        self.pct.setSuffix(" %")
        self.w = QSpinBox()
        self.w.setRange(1, 40000)
        self.w.setValue(size[0])
        self.h = QSpinBox()
        self.h.setRange(1, 40000)
        self.h.setValue(size[1])
        self.lock = QCheckBox(_t("Keep proportions"))
        self.lock.setChecked(True)
        self.quality = QComboBox()
        for key, label in (("high", "Best (Lanczos)"), ("medium", "Bicubic"), ("fast", "Fast"), ("pixel", "Pixel art")):
            self.quality.addItem(_t(label), key)
        form.addRow(_t("Percentage:"), self.pct)
        form.addRow(_t("Width (px):"), self.w)
        form.addRow(_t("Height (px):"), self.h)
        form.addRow(self.lock)
        form.addRow(_t("Quality:"), self.quality)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._orig = size
        self.pct.valueChanged.connect(lambda p: (self.w.setValue(round(self._orig[0] * p / 100)),
                                                 self.h.setValue(round(self._orig[1] * p / 100))))
        self.w.valueChanged.connect(lambda v: self.lock.isChecked() and self.h.blockSignals(True) is not None and
                                    (self.h.setValue(max(1, round(v / self.ratio))), self.h.blockSignals(False)))
        self.h.valueChanged.connect(lambda v: self.lock.isChecked() and self.w.blockSignals(True) is not None and
                                    (self.w.setValue(max(1, round(v * self.ratio))), self.w.blockSignals(False)))


class IdPhotoDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("ID Photo"))
        form = QFormLayout(self)
        info = QLabel(_t("Tip: first crop to the head and shoulders (Crop with the ID aspect ratio). The background "
                         "is replaced automatically when it is plain."))
        info.setWordWrap(True)
        info.setStyleSheet("color:#5f6368")
        form.addRow(info)
        self.size = QComboBox()
        self.size.addItems(list(ops.ID_SIZES))
        form.addRow(_t("Size:"), self.size)
        self.color = QComboBox()
        for label, val in (("White", "#ffffff"), ("Blue", "#1e64c8"), ("Light blue", "#bcd7f2"), ("Red", "#d32f2f"),
                           ("Light grey", "#e6e6e6")):
            self.color.addItem(_t(label), val)
        form.addRow(_t("Background:"), self.color)
        self.remove = QCheckBox(_t("Replace the background"))
        self.remove.setChecked(True)
        form.addRow(self.remove)
        self.sheet = QCheckBox(_t("Print sheet with several copies"))
        self.sheet.setChecked(True)
        self.sheet_size = QComboBox()
        self.sheet_size.addItems([_t("6 \u00d7 4 in photo paper"), _t("A4 paper")])
        form.addRow(self.sheet, self.sheet_size)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class CompressDialog(QDialog):
    def __init__(self, doc: PhotosDocument):
        super().__init__(doc)
        self.doc = doc
        self.setWindowTitle(_t("Compress Picture"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.mode = QComboBox()
        for key, label in (("lossless", "Lossless (PNG/WebP, no quality change)"), ("high", "High quality (JPEG 90)"),
                           ("balanced", "Balanced (JPEG 80)"), ("small", "Smallest (JPEG 65)")):
            self.mode.addItem(_t(label), key)
        self.mode.setCurrentIndex(2)
        form.addRow(_t("Quality:"), self.mode)
        self.max_side = QComboBox()
        for label, val in (("Keep size", 0), ("Max 3840 px (4K)", 3840), ("Max 1920 px (Full HD)", 1920),
                           ("Max 1280 px (web, e-mail)", 1280), ("Max 800 px", 800)):
            self.max_side.addItem(_t(label), val)
        form.addRow(_t("Dimensions:"), self.max_side)
        self.result = QLabel("")
        form.addRow(self.result)
        bb = QDialogButtonBox()
        go = bb.addButton(_t("Save Compressed Copy\u2026"), QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Close)
        go.clicked.connect(self._save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _save(self):
        from app.shell.dialogs import get_save_file

        mode = self.mode.currentData()
        stem = self.doc.path.stem if self.doc.path else "picture"
        ext = ".webp" if mode == "lossless" else ".jpg"
        path = get_save_file(self, _t("Save Compressed Copy"),
                             "WebP (*.webp);;PNG (*.png)" if mode == "lossless" else "JPEG (*.jpg);;WebP (*.webp)",
                             stem + "_compressed" + ext, ext.lstrip("."))
        if not path:
            return
        before = self.doc.path.stat().st_size if self.doc.path and self.doc.path.exists() else ops.encoded_size(
            self.doc.image, "PNG")
        after = ops.compress(self.doc.image, path, mode, self.max_side.currentData() or None, self.doc.exif)
        saved = 100 * (1 - after / max(1, before))
        self.result.setText(_t("{0:,.0f} KB \u2192 {1:,.0f} KB ({2:.0f}% smaller)").format(before / 1024, after / 1024,
                                                                                            max(0.0, saved)))


class TextResultDialog(QDialog):
    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Picture to Text"))
        self.resize(640, 480)
        v = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        v.addWidget(self.edit, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        copy = bb.addButton(_t("Copy"), QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication
                             .clipboard().setText(self.edit.toPlainText()))
        save = bb.addButton(_t("Save as Text\u2026"), QDialogButtonBox.ActionRole)
        save.clicked.connect(self._save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _save(self):
        from app.shell.dialogs import get_save_file

        path = get_save_file(self, _t("Save Text"), "Text (*.txt)", "text.txt", "txt")
        if path:
            Path(path).write_text(self.edit.toPlainText(), encoding="utf-8")


class HistogramWidget(QWidget):
    def __init__(self, hist, parent=None):
        super().__init__(parent)
        self.hist = hist
        self.setMinimumHeight(120)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#202124"))
        peak = max(max(ch[1:-1] or [1]) for ch in self.hist) or 1
        w, h = self.width(), self.height()
        for ch, col in zip(self.hist, (QColor(229, 57, 53, 140), QColor(67, 160, 71, 140), QColor(30, 136, 229, 140))):
            path = QPainterPath(QPointF(0, h))
            for i, v in enumerate(ch):
                path.lineTo(QPointF(i * w / 255, h - min(1.0, v / peak) * (h - 4)))
            path.lineTo(QPointF(w, h))
            p.fillPath(path, col)
        p.end()


# ============================================================================ batch processing
class BatchDialog(QDialog):
    """The same steps for many pictures: resize, convert, compress, watermark, rename."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Batch Process Pictures"))
        self.resize(640, 560)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        add = QPushButton(_t("Add Pictures\u2026"))
        add.clicked.connect(self._add)
        clear = QPushButton(_t("Clear"))
        row.addWidget(add)
        row.addWidget(clear)
        row.addStretch(1)
        v.addLayout(row)
        self.list = QListWidget()
        clear.clicked.connect(self.list.clear)
        v.addWidget(self.list, 1)
        form = QFormLayout()
        self.max_side = QSpinBox()
        self.max_side.setRange(0, 20000)
        self.max_side.setSpecialValueText(_t("keep"))
        self.max_side.setSuffix(" px")
        form.addRow(_t("Longest side:"), self.max_side)
        self.fmt = QComboBox()
        self.fmt.addItem(_t("Same as original"), "")
        for name, ext in ops.FORMATS.items():
            if name not in ("PDF", "ICO"):
                self.fmt.addItem(name, ext)
        form.addRow(_t("Format:"), self.fmt)
        self.quality = QSpinBox()
        self.quality.setRange(30, 100)
        self.quality.setValue(85)
        form.addRow(_t("JPEG/WebP quality:"), self.quality)
        self.wm = QLineEdit()
        self.wm.setPlaceholderText(_t("optional watermark text"))
        form.addRow(_t("Watermark:"), self.wm)
        self.enhance = QCheckBox(_t("Auto enhance"))
        form.addRow(self.enhance)
        self.strip = QCheckBox(_t("Remove metadata (EXIF, GPS)"))
        self.strip.setChecked(True)
        form.addRow(self.strip)
        self.pattern = QLineEdit("{name}")
        self.pattern.setToolTip(_t("{name} original name, {n} number, {date} today"))
        form.addRow(_t("File names:"), self.pattern)
        v.addLayout(form)
        self.status = QLabel("")
        v.addWidget(self.status)
        bb = QDialogButtonBox()
        go = bb.addButton(_t("Process\u2026"), QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Close)
        go.clicked.connect(self.run)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add(self):
        from app.shell.dialogs import get_open_files

        for f in get_open_files(self, _t("Add Pictures"), PhotosDocument.open_filter()):
            self.list.addItem(f)

    def files(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def run(self):
        from datetime import date

        from app.shell.dialogs import get_directory, show_info
        from app.shell.tasks import TaskError, run_task

        files = self.files()
        if not files:
            return
        folder = get_directory(self, _t("Output folder"))
        if not folder:
            return
        opts = dict(max_side=self.max_side.value(), ext=self.fmt.currentData(), quality=self.quality.value(),
                    wm=self.wm.text().strip(), enhance=self.enhance.isChecked(), strip=self.strip.isChecked(),
                    pattern=self.pattern.text().strip() or "{name}")

        def work(progress=None):
            done, failed = 0, []
            for n, f in enumerate(files, 1):
                try:
                    img = ops.load(f)
                    exif = None if opts["strip"] else Image.open(f).info.get("exif")
                    if opts["max_side"] and max(img.size) > opts["max_side"]:
                        s = opts["max_side"] / max(img.size)
                        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
                    if opts["enhance"]:
                        img = ops.auto_enhance(img)
                    if opts["wm"]:
                        img = ops.text_watermark(img, opts["wm"], 35)
                    stem = Path(f).stem
                    name = opts["pattern"].format(name=stem, n=f"{n:03d}", date=date.today().isoformat())
                    ext = opts["ext"] or Path(f).suffix.lower()
                    target = os.path.join(folder, name + ext)
                    k = 2
                    while os.path.exists(target):
                        target = os.path.join(folder, f"{name} ({k}){ext}")
                        k += 1
                    ops.save(img, target, quality=opts["quality"], strip_metadata=opts["strip"], exif=exif)
                    done += 1
                except Exception as exc:
                    failed.append(f"{Path(f).name}: {exc}")
                if progress:
                    progress(int(100 * n / len(files)), Path(f).name)
            return done, failed
        try:
            done, failed = run_task(self, _t("Processing pictures\u2026"), work)
        except TaskError as exc:
            from app.shell.dialogs import show_error

            show_error(self, _t("Batch Process"), str(exc.original), exc.tb)
            return
        msg = _t("{0} pictures saved in:\n{1}").format(done, folder)
        if failed:
            msg += "\n\n" + _t("Not processed:") + "\n" + "\n".join(failed[:8])
        show_info(self, _t("Batch Process"), msg)


def open_photos(main) -> None:
    """Tools gallery entry: open pictures in MyOffice Photos."""
    from app.shell.dialogs import get_open_files

    for f in get_open_files(main, _t("Open Picture"), PhotosDocument.open_filter()):
        main.open_path(f)
