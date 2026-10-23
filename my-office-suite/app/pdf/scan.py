"""Scan to PDF: pages from a scanner (Windows WIA), a camera or photos (for example from a phone), cleaned up like
a document scanner app (crop to the paper, straighten, contrast, black & white) and saved as a PDF - optionally
searchable with OCR (Urdu, Hindi, English)."""
from __future__ import annotations

import io
import logging
import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from app.services.i18n import _t

log = logging.getLogger(__name__)

WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"


# ============================================================================ image clean-up
def _otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = gray.size
    sum_all = (np.arange(256) * hist).sum()
    w_b = sum_b = 0.0
    best, thr = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def crop_to_paper(img: Image.Image) -> Image.Image:
    """Cut away a darker background around a sheet of paper (photo of a page on a table)."""
    small = img.convert("L")
    scale = 600 / max(small.size)
    if scale < 1:
        small = small.resize((max(1, int(small.width * scale)), max(1, int(small.height * scale))))
    else:
        scale = 1.0
    a = np.asarray(small)
    t = _otsu(a)
    bright = a > t
    if bright.mean() > 0.97 or bright.mean() < 0.25:  # no clear paper/background split
        return img
    rows = np.where(bright.mean(axis=1) > 0.5)[0]
    cols = np.where(bright.mean(axis=0) > 0.5)[0]
    if len(rows) < 10 or len(cols) < 10:
        return img
    box = (int(cols[0] / scale), int(rows[0] / scale), int((cols[-1] + 1) / scale), int((rows[-1] + 1) / scale))
    if (box[2] - box[0]) * (box[3] - box[1]) < 0.3 * img.width * img.height:
        return img
    return img.crop(box)


def deskew(img: Image.Image, max_angle: float = 6.0) -> Image.Image:
    """Straighten text lines (projection-profile method on a reduced, binarised copy)."""
    g = img.convert("L")
    scale = 800 / max(g.size)
    if scale < 1:
        g = g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))))
    a = np.asarray(g)
    ink = (a < _otsu(a)).astype(np.uint8) * 255
    if ink.mean() < 1:
        return img
    base = Image.fromarray(ink)
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_angle, max_angle + 0.01, 0.5):
        rows = np.asarray(base.rotate(float(angle), resample=Image.NEAREST, fillcolor=0)).sum(axis=1).astype(float)
        score = float(np.var(rows))
        if score > best_score:
            best_score, best_angle = score, float(angle)
    if abs(best_angle) < 0.25:
        return img
    for fine in np.arange(best_angle - 0.4, best_angle + 0.41, 0.1):
        rows = np.asarray(base.rotate(float(fine), resample=Image.NEAREST, fillcolor=0)).sum(axis=1).astype(float)
        score = float(np.var(rows))
        if score > best_score:
            best_score, best_angle = score, float(fine)
    fill = (255, 255, 255) if img.mode == "RGB" else 255
    return img.rotate(best_angle, resample=Image.BICUBIC, expand=True, fillcolor=fill)


def adaptive_bw(img: Image.Image, block: int = 31, offset: int = 12) -> Image.Image:
    """Black & white with a local threshold (keeps text crisp under uneven light)."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    pad = block // 2
    padded = np.pad(g, pad + 1, mode="edge")
    integ = padded.cumsum(0).cumsum(1)
    h, w = g.shape
    y0, x0 = np.arange(h), np.arange(w)
    s = (integ[y0[:, None] + block, x0[None, :] + block] - integ[y0[:, None], x0[None, :] + block]
         - integ[y0[:, None] + block, x0[None, :]] + integ[y0[:, None], x0[None, :]])
    mean = s / (block * block)
    out = np.where(g > mean - offset, 255, 0).astype(np.uint8)
    return Image.fromarray(out, "L")


def enhance(img: Image.Image, mode: str = "document", auto_crop: bool = True, straighten: bool = True) -> Image.Image:
    """mode: "document" (grey, high contrast), "bw" (black & white), "color" (colour, balanced), "original"."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    if mode == "original":
        return img
    if auto_crop:
        img = crop_to_paper(img)
    if straighten:
        img = deskew(img)
    if mode == "bw":
        return adaptive_bw(img)
    if mode == "document":
        g = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        # whiten the paper: divide by a heavily blurred background estimate
        bg = g.filter(ImageFilter.GaussianBlur(max(8, min(g.size) // 40)))
        a = np.asarray(g, dtype=np.float32) / np.maximum(np.asarray(bg, dtype=np.float32), 1) * 235
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L").filter(ImageFilter.SHARPEN)
    return ImageOps.autocontrast(img, cutoff=0.5).filter(ImageFilter.UnsharpMask(radius=1.5, percent=60))


def pil_to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    return QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888).copy()


def qimage_to_pil(qimg: QImage) -> Image.Image:
    q = qimg.convertToFormat(QImage.Format_RGB888)
    ptr = q.constBits()
    arr = np.frombuffer(ptr, np.uint8, count=q.sizeInBytes()).reshape(q.height(), q.bytesPerLine())
    return Image.fromarray(arr[:, :q.width() * 3].reshape(q.height(), q.width(), 3).copy(), "RGB")


# ============================================================================ sources
def wia_scan() -> Image.Image | None:  # pragma: no cover - Windows scanners only
    """The Windows scanner dialog (WIA).  None if the user cancelled."""
    if sys.platform != "win32":
        raise OSError("Scanning with WIA is available on Windows.")
    import comtypes.client

    comtypes.CoInitialize()
    try:
        dlg = comtypes.client.CreateObject("WIA.CommonDialog", dynamic=True)
        item = dlg.ShowAcquireImage(1, 0, 131072, WIA_FORMAT_PNG, False, True, False)
        if item is None:
            return None
        path = tempfile.mktemp(suffix=".png")
        item.SaveFile(path)
        img = Image.open(path)
        img.load()
        os.unlink(path)
        return img
    finally:
        comtypes.CoUninitialize()


class CameraDialog(QDialog):
    """Live camera view with a capture button."""

    def __init__(self, parent):
        super().__init__(parent)
        from PySide6.QtMultimedia import QImageCapture, QMediaCaptureSession, QMediaDevices
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.setWindowTitle(_t("Camera"))
        self.resize(820, 640)
        v = QVBoxLayout(self)
        self.devices = list(QMediaDevices.videoInputs())
        self.combo = QComboBox()
        for d in self.devices:
            self.combo.addItem(d.description())
        v.addWidget(self.combo)
        self.video = QVideoWidget()
        v.addWidget(self.video, 1)
        row = QHBoxLayout()
        self.count = QLabel(_t("0 pages captured"))
        snap = QPushButton(_t("Capture Page"))
        snap.setObjectName("Primary")
        snap.clicked.connect(self._snap)
        done = QPushButton(_t("Done"))
        done.clicked.connect(self.accept)
        row.addWidget(self.count)
        row.addStretch(1)
        row.addWidget(snap)
        row.addWidget(done)
        v.addLayout(row)
        self.images: list[Image.Image] = []
        self.session = QMediaCaptureSession(self)
        self.capture = QImageCapture(self)
        self.session.setImageCapture(self.capture)
        self.session.setVideoOutput(self.video)
        self.capture.imageCaptured.connect(lambda _id, img: self._got(img))
        self.camera = None
        self.combo.currentIndexChanged.connect(self._start)
        self._start(0)

    def _start(self, i: int):
        from PySide6.QtMultimedia import QCamera

        if self.camera is not None:
            self.camera.stop()
        if not self.devices:
            self.count.setText(_t("No camera found."))
            return
        self.camera = QCamera(self.devices[i], self)
        self.session.setCamera(self.camera)
        self.camera.start()

    def _snap(self):
        if self.capture.isReadyForCapture():
            self.capture.capture()

    def _got(self, qimg: QImage):
        self.images.append(qimage_to_pil(qimg))
        self.count.setText(_t("{0} pages captured").format(len(self.images)))

    def done(self, r):
        if self.camera is not None:
            self.camera.stop()
        super().done(r)


# ============================================================================ dialog
class ScanDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Scan to PDF"))
        self.resize(980, 660)
        self.raw: list[Image.Image] = []
        v = QVBoxLayout(self)
        src = QHBoxLayout()
        for label, slot, tip in ((_t("Scanner\u2026"), self.from_scanner, _t("Windows scanner (WIA)")),
                                 (_t("Camera\u2026"), self.from_camera, _t("Webcam or document camera")),
                                 (_t("Add Photos\u2026"), self.from_files, _t("Photos of pages, e.g. from a phone"))):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            src.addWidget(b)
        src.addStretch(1)
        src.addWidget(QLabel(_t("Clean-up:")))
        self.mode = QComboBox()
        for key, label in (("document", "Document (grey, sharp)"), ("bw", "Black & white"),
                           ("color", "Colour"), ("original", "Original")):
            self.mode.addItem(_t(label), key)
        self.mode.currentIndexChanged.connect(self._refresh)
        src.addWidget(self.mode)
        self.crop = QCheckBox(_t("Crop to paper"))
        self.crop.setChecked(True)
        self.straight = QCheckBox(_t("Straighten"))
        self.straight.setChecked(True)
        for cb in (self.crop, self.straight):
            cb.toggled.connect(self._refresh)
            src.addWidget(cb)
        v.addLayout(src)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(170, 220))
        self.list.setGridSize(QSize(200, 260))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        rot = QPushButton(_t("Rotate"))
        rot.clicked.connect(self._rotate)
        rm = QPushButton(_t("Remove"))
        rm.clicked.connect(self._remove)
        self.ocr = QCheckBox(_t("Make searchable (OCR)"))
        self.ocr.setChecked(True)
        self.lang = QComboBox()
        from app.pdf.ops import usable_ocr_choices

        for code, label in usable_ocr_choices([("urd+eng", "Urdu + English"), ("eng", "English"),
                                               ("hin+eng", "Hindi + English"), ("urd", "Urdu"), ("hin", "Hindi")]):
            self.lang.addItem(_t(label), code)
        save = QPushButton(_t("Save PDF\u2026"))
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        for w_ in (rot, rm):
            bottom.addWidget(w_)
        bottom.addStretch(1)
        bottom.addWidget(self.ocr)
        bottom.addWidget(self.lang)
        bottom.addWidget(save)
        v.addLayout(bottom)
        self.saved_path: str | None = None

    # -- sources
    def _add(self, images):
        for img in images:
            self.raw.append(ImageOps.exif_transpose(img).convert("RGB"))
        self._refresh()

    def from_scanner(self):
        from app.shell.dialogs import show_error

        try:
            img = wia_scan()
        except Exception as exc:
            show_error(self, _t("Scanner"), _t("The scanner could not be used: {0}").format(exc))
            return
        if img is not None:
            self._add([img])

    def from_camera(self):
        from app.services.recorder import cameras
        from app.shell.dialogs import show_error

        if not cameras():
            show_error(self, _t("Camera"), _t("No camera was found."))
            return
        d = CameraDialog(self)
        d.exec()
        self._add(d.images)

    def from_files(self):
        from app.shell.dialogs import get_open_files

        files = get_open_files(self, _t("Add Photos"), "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.heic)")
        imgs = []
        for f in files:
            try:
                im = Image.open(f)
                im.load()
                imgs.append(im)
            except Exception as exc:
                log.warning("skipped %s: %s", f, exc)
        self._add(imgs)

    # -- pages
    def processed(self, i: int) -> Image.Image:
        return enhance(self.raw[i], self.mode.currentData(), self.crop.isChecked(), self.straight.isChecked())

    def _refresh(self):
        self.list.clear()
        for i in range(len(self.raw)):
            thumb = self.processed(i).copy()
            thumb.thumbnail((340, 440))
            it = QListWidgetItem(QIcon(QPixmap.fromImage(pil_to_qimage(thumb))), _t("Page {0}").format(i + 1))
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)

    def _order(self) -> list[int]:
        return [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())]

    def _rotate(self):
        for it in self.list.selectedItems():
            i = it.data(Qt.UserRole)
            self.raw[i] = self.raw[i].rotate(-90, expand=True)
        self._refresh()

    def _remove(self):
        drop = {it.data(Qt.UserRole) for it in self.list.selectedItems()}
        self.raw = [im for i, im in enumerate(self.raw) if i not in drop]
        self._refresh()

    def save(self):
        from app.shell.dialogs import get_save_file, show_error
        from app.shell.tasks import TaskError, run_task

        if not self.raw:
            return
        path = get_save_file(self, _t("Save PDF"), "PDF (*.pdf)", "scan.pdf", "pdf")
        if not path:
            return
        order = self._order()
        pages = [self.processed(i) for i in order]
        ocr, lang = self.ocr.isChecked(), self.lang.currentData()

        def work(progress=None):
            return save_scan(pages, path, ocr, lang, progress)
        try:
            run_task(self, _t("Creating the PDF\u2026"), work)
        except TaskError as exc:
            show_error(self, _t("Scan to PDF"), str(exc.original), exc.tb)
            return
        self.saved_path = path
        self.accept()


def save_scan(pages: list[Image.Image], path: str, ocr: bool = True, lang: str = "urd+eng", progress=None) -> str:
    """Write the page images as a PDF (A4-independent: each page keeps its own size at 200 dpi); with ``ocr`` an
    invisible text layer makes it searchable."""
    import pymupdf

    from app.pdf import ops

    doc = pymupdf.open()
    for k, img in enumerate(pages):
        buf = io.BytesIO()
        if img.mode == "L" and np.asarray(img).max() <= 255 and len(np.unique(np.asarray(img)[::7, ::7])) <= 2:
            img.convert("1").save(buf, "PNG", optimize=True)
        elif img.mode == "L":
            img.save(buf, "JPEG", quality=85, optimize=True)
        else:
            img.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
        dpi = 200
        w, h = img.width * 72 / dpi, img.height * 72 / dpi
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=buf.getvalue())
        if progress:
            progress(int(40 * (k + 1) / len(pages)), "")
    tmp = path + ".plain.pdf"
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    if ocr and ops.tesseract_cmd():
        try:
            ops.ocr_pdf(tmp, path, lang=lang, dpi=200, skip_text_pages=False,
                        progress=(lambda p, m="": progress(40 + int(p * 0.6), m)) if progress else None)
            os.unlink(tmp)
            return path
        except Exception as exc:
            log.warning("OCR failed, saving without text layer: %s", exc)
    os.replace(tmp, path)
    return path


def scan_to_pdf(main) -> None:
    """Tool entry point (also opens the result)."""
    d = ScanDialog(main)
    if d.exec() == QDialog.Accepted and d.saved_path and hasattr(main, "open_path"):
        main.open_path(d.saved_path)
