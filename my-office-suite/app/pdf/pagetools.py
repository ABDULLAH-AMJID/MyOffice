"""PDF page tools: crop pages (also automatically to the content), replace pages from another PDF, and a visual
page organizer (drag thumbnails to reorder, rotate, delete, duplicate, insert blank pages)."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

from app.pdf.ops import parse_ranges
from app.services.i18n import _t

MM = 72 / 25.4


def content_box(page: pymupdf.Page, pad: float = 6.0) -> pymupdf.Rect:
    """Bounding box of everything drawn on the page (text, images, vector graphics) plus a small margin."""
    box = pymupdf.Rect()
    try:
        for item in page.get_bboxlog():
            r = pymupdf.Rect(item[1])
            if r.is_valid and not r.is_empty and r.width < page.rect.width * 1.01:
                box |= r
    except Exception:
        for b in page.get_text("blocks"):
            box |= pymupdf.Rect(b[:4])
    if box.is_empty:
        return page.rect
    box = pymupdf.Rect(box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad) & page.rect
    return box


def crop_pages(doc: pymupdf.Document, pages: list[int], margins_mm: tuple[float, float, float, float] | None,
               auto: bool = False) -> int:
    """Set the visible area (CropBox) of ``pages``.  ``margins_mm`` = (top, right, bottom, left) to cut away."""
    done = 0
    for i in pages:
        page = doc[i]
        page.set_cropbox(page.mediabox)  # start from the full page
        mb = page.rect
        if auto:
            r = content_box(page)
        else:
            t, rgt, b, lft = (m * MM for m in margins_mm)
            r = pymupdf.Rect(mb.x0 + lft, mb.y0 + t, mb.x1 - rgt, mb.y1 - b)
        if r.width < 20 or r.height < 20:
            continue
        # CropBox is in unrotated page coordinates
        r = r * page.derotation_matrix if page.rotation else r
        page.set_cropbox(r.normalize() & page.mediabox)
        done += 1
    return done


def replace_pages(doc: pymupdf.Document, targets: list[int], src: pymupdf.Document, src_pages: list[int]) -> int:
    """Replace the pages ``targets`` (0-based, consecutive or not) with ``src_pages`` of ``src``."""
    if not targets or not src_pages:
        return 0
    first = min(targets)
    for i in sorted(targets, reverse=True):
        doc.delete_page(i)
    at = first
    for sp in src_pages:
        doc.insert_pdf(src, from_page=sp, to_page=sp, start_at=at)
        at += 1
    return len(src_pages)


class CropDialog(QDialog):
    def __init__(self, parent, page_count: int, current: int):
        super().__init__(parent)
        self.setWindowTitle(_t("Crop Pages"))
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.auto = QCheckBox(_t("Crop automatically to the content (remove white margins)"))
        form.addRow(self.auto)
        self.m = []
        for label in (_t("Top (mm):"), _t("Right (mm):"), _t("Bottom (mm):"), _t("Left (mm):")):
            sp = QDoubleSpinBox()
            sp.setRange(0, 500)
            sp.setDecimals(1)
            sp.setValue(10)
            form.addRow(label, sp)
            self.m.append(sp)
        self.auto.toggled.connect(lambda on: [sp.setEnabled(not on) for sp in self.m])
        self.ranges = QLineEdit(str(current + 1))
        self.ranges.setPlaceholderText(_t("e.g. 1-3, 5 (empty = all pages)"))
        form.addRow(_t("Pages:"), self.ranges)
        hint = QLabel(_t("Cropping hides the margins; the content stays in the file and can be restored by cropping "
                         "with 0 mm."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5f6368")
        form.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.page_count = page_count

    def pages(self) -> list[int]:
        spec = self.ranges.text().strip()
        if not spec:
            return list(range(self.page_count))
        return sorted({p for grp in parse_ranges(spec, self.page_count) for p in grp})


class ReplaceDialog(QDialog):
    def __init__(self, parent, page_count: int, current: int):
        super().__init__(parent)
        self.setWindowTitle(_t("Replace Pages"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.targets = QLineEdit(str(current + 1))
        form.addRow(_t("Replace pages:"), self.targets)
        row = QHBoxLayout()
        self.src = QLineEdit()
        browse = QPushButton(_t("Browse\u2026"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.src, 1)
        row.addWidget(browse)
        form.addRow(_t("With pages from:"), row)
        self.src_pages = QLineEdit("1")
        form.addRow(_t("Pages of that file:"), self.src_pages)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.page_count = page_count

    def _browse(self):
        from app.shell.dialogs import get_open_file

        path = get_open_file(self, _t("Choose a PDF"), "PDF (*.pdf)")
        if path:
            self.src.setText(path)


class OrganizeDialog(QDialog):
    """Page thumbnails: drag to reorder; rotate, delete, duplicate, insert blank pages; Apply changes the PDF."""

    def __init__(self, parent, doc: pymupdf.Document):
        super().__init__(parent)
        self.setWindowTitle(_t("Organize Pages"))
        self.resize(980, 680)
        self.doc = doc
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        for label, slot in ((_t("Rotate Left"), lambda: self._rotate(-90)), (_t("Rotate Right"), lambda: self._rotate(90)),
                            (_t("Duplicate"), self._duplicate), (_t("Insert Blank Page"), self._blank),
                            (_t("Delete"), self._delete), (_t("Reverse Order"), self._reverse)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(130, 170))
        self.list.setGridSize(QSize(160, 210))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Snap)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.model().rowsMoved.connect(lambda *_: self._renumber())
        v.addWidget(self.list, 1)
        hint = QLabel(_t("Drag pages to change their order. Ctrl+click or Shift+click selects several pages."))
        hint.setStyleSheet("color:#5f6368")
        v.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_t("Apply"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._thumbs: dict[int, QImage] = {}
        for i in range(doc.page_count):
            self._add_item({"src": i, "rot": 0})
        self._renumber()

    def _thumb(self, src: int) -> QImage:
        if src not in self._thumbs:
            if src < 0:
                img = QImage(120, 160, QImage.Format_RGB32)
                img.fill(Qt.white)
            else:
                pix = self.doc[src].get_pixmap(matrix=pymupdf.Matrix(0.25, 0.25), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            self._thumbs[src] = img
        return self._thumbs[src]

    def _icon(self, data: dict) -> QIcon:
        img = self._thumb(data["src"])
        if data["rot"]:
            img = img.transformed(QTransform().rotate(data["rot"]))
        return QIcon(QPixmap.fromImage(img))

    def _add_item(self, data: dict, row: int | None = None):
        it = QListWidgetItem(self._icon(data), "")
        it.setData(Qt.UserRole, dict(data))
        it.setTextAlignment(Qt.AlignHCenter)
        if row is None:
            self.list.addItem(it)
        else:
            self.list.insertItem(row, it)

    def _renumber(self):
        for r in range(self.list.count()):
            it = self.list.item(r)
            d = it.data(Qt.UserRole)
            orig = _t("blank") if d["src"] < 0 else _t("p. {0}").format(d["src"] + 1)
            it.setText(f"{r + 1}  ({orig})")

    def _selected_rows(self) -> list[int]:
        return sorted(self.list.row(it) for it in self.list.selectedItems())

    def _rotate(self, deg: int):
        for r in self._selected_rows():
            it = self.list.item(r)
            d = it.data(Qt.UserRole)
            d["rot"] = (d["rot"] + deg) % 360
            it.setData(Qt.UserRole, d)
            it.setIcon(self._icon(d))

    def _duplicate(self):
        for r in reversed(self._selected_rows()):
            self._add_item(self.list.item(r).data(Qt.UserRole), r + 1)
        self._renumber()

    def _blank(self):
        rows = self._selected_rows()
        self._add_item({"src": -1, "rot": 0}, (rows[-1] + 1) if rows else self.list.count())
        self._renumber()

    def _delete(self):
        rows = self._selected_rows()
        if len(rows) >= self.list.count():
            return
        for r in reversed(rows):
            self.list.takeItem(r)
        self._renumber()

    def _reverse(self):
        items = [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())]
        self.list.clear()
        for d in reversed(items):
            self._add_item(d)
        self._renumber()

    def plan(self) -> list[dict]:
        return [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())]


def apply_plan(doc: pymupdf.Document, plan: list[dict]) -> pymupdf.Document:
    """A new document built from ``plan`` [{src: page index or -1 for blank, rot: extra degrees}]."""
    out = pymupdf.open()
    for step in plan:
        if step["src"] < 0:
            ref = doc[0].rect
            out.new_page(width=ref.width, height=ref.height)
        else:
            out.insert_pdf(doc, from_page=step["src"], to_page=step["src"])
            if step["rot"]:
                p = out[-1]
                p.set_rotation((p.rotation + step["rot"]) % 360)
    try:
        toc = doc.get_toc()
        if toc:
            mapping = {}
            for new_i, step in enumerate(plan):
                if step["src"] >= 0 and step["src"] + 1 not in mapping:
                    mapping[step["src"] + 1] = new_i + 1
            out.set_toc([[lvl, title, mapping[pg]] for lvl, title, pg, *_ in toc if pg in mapping])
    except Exception:
        pass
    return out
