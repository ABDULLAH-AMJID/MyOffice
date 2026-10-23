"""The Presentation document tab."""
from __future__ import annotations

import copy
import json
import logging
import os
import zipfile
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPixmap, QTextCharFormat, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from app.services.i18n import _t
from app.shell.dialogs import confirm, get_directory, get_open_file, handle_exception, show_error, show_info
from app.shell.document import DocumentError, DocumentWidget
from app.shell.icons import icon
from app.shell.ribbon import Ribbon
from app.shell.widgets import ColorMenu, ZoomSlider
from app.slide.canvas import ShapeItem, SlideCanvas, export_pdf, render_slide
from app.slide import features as slide_features
from app.slide.model import (ANIM_EFFECTS, LAYOUTS, SIZE_4_3, SIZE_16_9, THEMES, TRANSITIONS, Animation, Paragraph,
                             Run, Shape, Slide, Theme, Transition, clone_shape, emu, make_slide, px, shape_from_dict,
                             shape_to_dict)
from app.slide.pptx_io import PresentationError, load_pptx, new_presentation, save_pptx

log = logging.getLogger(__name__)
SHAPES_MIME = "application/x-myoffice-shapes"
CONVERT_EXTS = (".ppt", ".pps", ".odp", ".dps", ".pot", ".fodp")


class SnapshotCommand(QUndoCommand):
    def __init__(self, doc, before, after, text):
        super().__init__(text)
        self.doc, self.before, self.after = doc, before, after
        self._first = True

    def redo(self):
        if self._first:
            self._first = False
            return
        self.doc.restore(self.after)

    def undo(self):
        self.doc.restore(self.before)


class ChartDataDialog(QDialog):
    def __init__(self, parent, chart: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Chart Data"))
        self.resize(620, 420)
        chart = chart or {"kind": "column", "title": _t("Chart Title"), "categories": ["Q1", "Q2", "Q3", "Q4"],
                          "series": [["Series 1", [4.3, 2.5, 3.5, 4.5]], ["Series 2", [2.4, 4.4, 1.8, 2.8]]], "legend": True}
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.kind = QComboBox()
        for k in ("column", "bar", "line", "area", "pie", "doughnut", "scatter"):
            self.kind.addItem(_t(k.capitalize()), k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(chart.get("kind", "column"))))
        self.title = QLineEdit(chart.get("title", ""))
        f.addRow(_t("Type:"), self.kind)
        f.addRow(_t("Title:"), self.title)
        v.addLayout(f)
        cats, series = chart.get("categories", []), chart.get("series", [])
        self.table = QTableWidget(max(len(cats), 1) + 1, len(series) + 1)
        self.table.setItem(0, 0, QTableWidgetItem(""))
        for j, (name, vals) in enumerate(series, start=1):
            self.table.setItem(0, j, QTableWidgetItem(str(name)))
            for i, val in enumerate(vals, start=1):
                self.table.setItem(i, j, QTableWidgetItem("" if val is None else f"{val:g}"))
        for i, c in enumerate(cats, start=1):
            self.table.setItem(i, 0, QTableWidgetItem(str(c)))
        v.addWidget(QLabel(_t("First row: series names \u2022 first column: categories (X values for scatter)")))
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, fn in ((_t("Add Row"), lambda: self.table.insertRow(self.table.rowCount())),
                         (_t("Add Series"), lambda: self.table.insertColumn(self.table.columnCount())),
                         (_t("Paste from Clipboard"), self._paste)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _paste(self):
        text = QApplication.clipboard().text().strip("\n")
        rows = [r.split("\t") for r in text.split("\n") if r]
        if not rows:
            return
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(max(len(r) for r in rows))
        for i, r in enumerate(rows):
            for j, val in enumerate(r):
                self.table.setItem(i, j, QTableWidgetItem(val.strip()))

    def chart(self) -> dict:
        t = self.table

        def txt(i, j):
            it = t.item(i, j)
            return it.text().strip() if it else ""
        series = []
        cats = [txt(i, 0) for i in range(1, t.rowCount()) if txt(i, 0) or any(txt(i, j) for j in range(1, t.columnCount()))]
        for j in range(1, t.columnCount()):
            name = txt(0, j)
            vals = []
            for i in range(1, len(cats) + 1):
                try:
                    vals.append(float(txt(i, j).replace(",", "")))
                except ValueError:
                    vals.append(None)
            if name or any(v is not None for v in vals):
                series.append([name or f"Series {j}", vals])
        return {"kind": self.kind.currentData(), "title": self.title.text().strip(), "categories": cats,
                "series": series, "legend": True}


class TableDialog(QDialog):
    def __init__(self, parent, rows: list | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Table"))
        self.resize(560, 360)
        rows = rows or [[_t("Header 1"), _t("Header 2"), _t("Header 3")], ["", "", ""], ["", "", ""]]
        v = QVBoxLayout(self)
        self.table = QTableWidget(len(rows), max(len(r) for r in rows))
        for i, r in enumerate(rows):
            for j, val in enumerate(r):
                self.table.setItem(i, j, QTableWidgetItem(str(val)))
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, fn in ((_t("Add Row"), lambda: self.table.insertRow(self.table.rowCount())),
                         (_t("Add Column"), lambda: self.table.insertColumn(self.table.columnCount())),
                         (_t("Remove Row"), lambda: self.table.removeRow(self.table.rowCount() - 1) if self.table.rowCount() > 1 else None),
                         (_t("Remove Column"), lambda: self.table.removeColumn(self.table.columnCount() - 1) if self.table.columnCount() > 1 else None)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def rows(self) -> list:
        t = self.table
        return [[(t.item(i, j).text() if t.item(i, j) else "") for j in range(t.columnCount())] for i in range(t.rowCount())]


class SlideDocument(DocumentWidget):
    kind = "slide"
    default_ext = ".pptx"
    writable_exts = (".pptx", ".dpt")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pres = new_presentation()
        self.current = 0
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(self._clean_changed)
        self._stable = self.snapshot()

        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.ListMode)
        self.thumbs.setIconSize(QSize(176, 99))
        self.thumbs.setSpacing(4)
        self.thumbs.setMinimumWidth(210)
        self.thumbs.setMaximumWidth(260)
        self.thumbs.setDragDropMode(QAbstractItemView.InternalMove)
        self.thumbs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.thumbs.currentRowChanged.connect(self._thumb_selected)
        self.thumbs.model().rowsMoved.connect(self._thumbs_moved)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        self.canvas = SlideCanvas(self)
        self.canvas.changed.connect(lambda desc: self.commit(_t(desc)))
        self.canvas.selectionChangedSig.connect(self._selection_changed)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(_t("Click to add speaker notes"))
        self.notes.setMaximumHeight(140)
        self.notes.textChanged.connect(self._notes_changed)
        self._notes_timer = QTimer(self, singleShot=True, interval=800, timeout=lambda: self.commit(_t("Edit Notes")))
        self.anim_pane = QWidget()
        ap = QVBoxLayout(self.anim_pane)
        ap.setContentsMargins(4, 4, 4, 4)
        t = QLabel(_t("Animation Pane"))
        t.setObjectName("H2")
        ap.addWidget(t)
        self.anim_list = QListWidget()
        ap.addWidget(self.anim_list, 1)
        row = QHBoxLayout()
        for ic, tip, fn in (("up", _t("Move Earlier"), lambda: self.move_animation(-1)),
                            ("down", _t("Move Later"), lambda: self.move_animation(1)),
                            ("delete", _t("Remove"), self.remove_animation), ("play", _t("Preview"), self.preview_animations)):
            b = QToolButton()
            b.setIcon(icon(ic))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        ap.addLayout(row)
        self.anim_pane.setMinimumWidth(220)
        self.anim_pane.hide()
        center = QSplitter(Qt.Vertical)
        center.addWidget(self.canvas)
        center.addWidget(self.notes)
        center.setStretchFactor(0, 1)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.thumbs)
        self.splitter.addWidget(center)
        self.splitter.addWidget(self.anim_pane)
        self.splitter.setStretchFactor(1, 1)
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
        self.zoom_slider.zoomRequested.connect(self._zoom_requested)
        sl.addWidget(self.zoom_slider)
        lay.addWidget(strip)
        self._thumb_timer = QTimer(self, singleShot=True, interval=250, timeout=self._update_current_thumb)
        self._build_actions()
        self.ribbon = self._build_ribbon()
        self.rebuild_all()

    # ================================================================== DocumentWidget API
    def untitled_label(self) -> str:
        return _t("Presentation")

    def ribbon_widget(self):
        return self.ribbon

    def save_filters(self) -> str:
        return "PowerPoint Presentation (*.pptx);;Presentation Template (*.dpt);;OpenDocument Presentation (*.odp)"

    @staticmethod
    def open_filter() -> str:
        return "Presentations (*.pptx *.pptm *.potx *.ppt *.pps *.ppsx *.dpt *.odp);;All files (*)"

    def load(self, path: str) -> None:
        ext = Path(path).suffix.lower()
        src = path
        if ext in CONVERT_EXTS or (ext == ".dpt" and not zipfile.is_zipfile(path)):
            from app.services.office_convert import ConversionError, convert
            from app.shell.tasks import TaskError, run_task

            try:
                src = run_task(self, _t("Converting presentation\u2026"), convert, path, "pptx")
            except (TaskError, ConversionError) as exc:
                raise DocumentError(str(getattr(exc, "original", exc))) from exc
        try:
            pres = load_pptx(src)
        except PresentationError as exc:
            raise DocumentError(str(exc)) from exc
        if not pres.slides:
            pres.slides.append(make_slide(pres, "title"))
        self.pres = pres
        self.current = 0
        self.path = Path(path)
        self.read_only = ext not in self.writable_exts
        self.undo.clear()
        self._stable = self.snapshot()
        self.rebuild_all()
        super().set_modified(False)
        self.titleChanged.emit()

    def save_to(self, path: str) -> None:
        self.canvas.commit_text_edits()
        self._sync_all()
        ext = Path(path).suffix.lower()
        if ext in (".pptx", ".dpt", ".potx"):
            save_pptx(path, self.pres)
            self.undo.setClean()
        elif ext == ".odp":
            import tempfile

            from app.services.office_convert import convert

            d = tempfile.mkdtemp()
            tmp = os.path.join(d, Path(path).stem + ".pptx")
            save_pptx(tmp, self.pres)
            os.replace(convert(tmp, "odp", d), path)
        elif ext == ".pdf":
            self.export_pdf_to(path)
        else:
            raise DocumentError(_t("Cannot save as {0}").format(ext))

    def set_modified(self, value: bool = True) -> None:
        if not value:
            try:
                self.undo.setClean()
            except RuntimeError:
                return
        super().set_modified(value)

    def export_pdf_to(self, path: str) -> None:
        self.canvas.commit_text_edits()
        export_pdf(self.pres, path, self.display_name())

    def print_document(self) -> None:
        from PySide6.QtCore import QRectF as _R
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        from app.slide.canvas import build_scene

        printer = QPrinter(QPrinter.HighResolution)
        from PySide6.QtGui import QPageLayout

        printer.setPageOrientation(QPageLayout.Landscape)
        if not QPrintDialog(printer, self).exec():
            return
        p = QPainter(printer)
        try:
            W, H = self.pres.size_px
            page = printer.pageLayout().paintRectPixels(printer.resolution())
            for i, s in enumerate(self.pres.slides):
                if i:
                    printer.newPage()
                scene, _ = build_scene(self.pres, s, False)
                sc = min(page.width() / W, page.height() / H)
                scene.render(p, _R(0, 0, W * sc, H * sc), _R(0, 0, W, H))
        finally:
            p.end()

    def status_text(self) -> str:
        return _t("Slide {0} of {1}").format(self.current + 1, len(self.pres.slides))

    def write_recovery(self, path: str) -> None:
        save_pptx(path, self.pres)

    # ================================================================== state / undo
    def snapshot(self):
        return (copy.deepcopy(self.pres), self.current)

    def restore(self, snap):
        pres, cur = snap
        self.pres = copy.deepcopy(pres)
        self.current = min(cur, len(self.pres.slides) - 1)
        self._stable = self.snapshot()
        self.rebuild_all()

    def commit(self, text: str):
        self._sync_all()
        after = self.snapshot()
        self.undo.push(SnapshotCommand(self, self._stable, after, text))
        self._stable = after
        self._thumb_timer.start()
        self._refresh_anim_list()

    def _sync_all(self):
        for it in self.canvas.items_:
            it.sync_model()

    @property
    def slide(self) -> Slide:
        return self.pres.slides[self.current]

    # ================================================================== views
    def rebuild_all(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i, s in enumerate(self.pres.slides):
            it = QListWidgetItem(QIcon(QPixmap.fromImage(render_slide(self.pres, s, 176))), f"{i + 1}")
            if s.hidden:
                it.setForeground(QColor("#9aa0a6"))
            self.thumbs.addItem(it)
        self.thumbs.setCurrentRow(self.current)
        self.thumbs.blockSignals(False)
        self.show_slide(self.current)

    def show_slide(self, i: int):
        self.canvas.commit_text_edits()
        self.current = max(0, min(i, len(self.pres.slides) - 1))
        self.canvas.show_slide(self.slide)
        self.notes.blockSignals(True)
        self.notes.setPlainText(self.slide.notes)
        self.notes.blockSignals(False)
        self.info.setText(self.status_text())
        self.zoom_slider.set_zoom(self.canvas.zoom)
        self._refresh_anim_list()
        self._sync_transition_controls()

    def _update_current_thumb(self):
        it = self.thumbs.item(self.current)
        if it is not None and self.current < len(self.pres.slides):
            it.setIcon(QIcon(QPixmap.fromImage(render_slide(self.pres, self.slide, 176))))

    def _thumb_selected(self, row: int):
        if 0 <= row < len(self.pres.slides) and row != self.current:
            if self.canvas.commit_text_edits():
                self.commit(_t("Edit Text"))
            self.show_slide(row)

    def _thumbs_moved(self, _parent, start, _end, _dest, row):
        to = row - 1 if row > start else row
        s = self.pres.slides.pop(start)
        self.pres.slides.insert(to, s)
        self.current = to
        self.commit(_t("Move Slide"))
        QTimer.singleShot(0, self.rebuild_all)

    def _thumb_menu(self, pos):
        m = QMenu(self)
        m.addAction(icon("new"), _t("New Slide"), lambda: self.new_slide())
        m.addAction(icon("duplicate"), _t("Duplicate Slide"), self.duplicate_slide)
        m.addAction(icon("delete"), _t("Delete Slide"), self.delete_slide)
        m.addAction(_t("Hide / Show Slide"), self.toggle_hidden)
        m.addSeparator()
        m.addAction(icon("up"), _t("Move Up"), lambda: self.move_slide(-1))
        m.addAction(icon("down"), _t("Move Down"), lambda: self.move_slide(1))
        m.exec(self.thumbs.mapToGlobal(pos))

    def _notes_changed(self):
        self.slide.notes = self.notes.toPlainText()
        self._notes_timer.start()

    def _zoom_requested(self, z):
        self.canvas._auto_fit = False
        self.canvas.set_zoom(z)

    def _selection_changed(self):
        sel = self.canvas.selected_items()
        if len(sel) == 1:
            an = sel[0].model.animation
            for w, val in ((self.anim_effect, an.effect), (self.anim_trigger, an.trigger), (self.anim_dir, an.direction)):
                w.blockSignals(True)
                w.setCurrentIndex(max(0, w.findData(val)))
                w.blockSignals(False)
            self.anim_duration.blockSignals(True)
            self.anim_duration.setValue(an.duration)
            self.anim_duration.blockSignals(False)
            self.anim_delay.blockSignals(True)
            self.anim_delay.setValue(an.delay)
            self.anim_delay.blockSignals(False)
            runs = [r for p in sel[0].model.paragraphs for r in p.runs]
            if runs:
                self.font_combo.blockSignals(True)
                fam = runs[0].font or self.pres.theme.body_font
                self.font_combo.setCurrentFont(QFont(fam))
                self.font_combo.setEditText(fam)
                self.font_combo.blockSignals(False)
                self.size_combo.blockSignals(True)
                self.size_combo.setEditText(f"{runs[0].size or 18:g}")
                self.size_combo.blockSignals(False)

    # ================================================================== slides
    def new_slide(self, layout: str = "title_content"):
        s = make_slide(self.pres, layout)
        self.pres.slides.insert(self.current + 1, s)
        self.current += 1
        self.commit(_t("New Slide"))
        self.rebuild_all()

    def duplicate_slide(self):
        self._sync_all()
        s = copy.deepcopy(self.slide)
        from app.slide.model import new_id

        s.id = new_id()
        for sh in s.shapes:
            sh.id = new_id()
        self.pres.slides.insert(self.current + 1, s)
        self.current += 1
        self.commit(_t("Duplicate Slide"))
        self.rebuild_all()

    def delete_slide(self):
        if len(self.pres.slides) <= 1:
            show_info(self, _t("Delete Slide"), _t("A presentation must have at least one slide."))
            return
        self.pres.slides.pop(self.current)
        self.current = max(0, self.current - 1)
        self.commit(_t("Delete Slide"))
        self.rebuild_all()

    def move_slide(self, d: int):
        j = self.current + d
        if 0 <= j < len(self.pres.slides):
            s = self.pres.slides
            s[self.current], s[j] = s[j], s[self.current]
            self.current = j
            self.commit(_t("Move Slide"))
            self.rebuild_all()

    def toggle_hidden(self):
        self.slide.hidden = not self.slide.hidden
        self.commit(_t("Hide Slide"))
        self.rebuild_all()

    def apply_layout(self, layout: str):
        """Add the layout's placeholders that are missing on the current slide."""
        tmpl = make_slide(self.pres, layout)
        have = {sh.placeholder for sh in self.slide.shapes if sh.placeholder}
        for sh in tmpl.shapes:
            if sh.placeholder not in have:
                self.slide.shapes.append(sh)
        self.slide.layout = layout
        self.commit(_t("Layout"))
        self.show_slide(self.current)

    # ================================================================== shapes
    def add_shape_at(self, kind: str, rect: QRectF):
        th = self.pres.theme
        sh = Shape(kind, emu(rect.x()), emu(rect.y()), emu(rect.width()), emu(rect.height()))
        if kind == "text":
            sh.set_text("", size=18, color=th.text_color)
            for p in sh.paragraphs:
                for r in p.runs:
                    r.font = th.body_font
        elif kind in ("line", "arrow_line"):
            sh.line = th.text_color
            sh.line_width = 2.0
        else:
            sh.fill = th.accent
            sh.set_text("", size=18, color="#ffffff", align="center")
            sh.valign = "middle"
        sh.name = f"{kind.capitalize()} {len(self.slide.shapes) + 1}"
        self._insert_shape(sh, edit=kind == "text")

    def _insert_shape(self, sh: Shape, edit: bool = False):
        self.slide.shapes.append(sh)
        self.commit(_t("Insert"))
        self.canvas.show_slide(self.slide)
        it = self.canvas.item_for(sh)
        if it is not None:
            it.setSelected(True)
            if edit:
                it.start_text_edit()

    def begin_insert(self, kind: str):
        self.canvas.insert_kind = kind
        self.canvas.viewport().setCursor(Qt.CrossCursor)
        self.statusChanged.emit(_t("Click or drag on the slide to place the shape."))
        self.canvas.setFocus()

    def _centered_rect(self, w_px: float, h_px: float) -> QRectF:
        W, H = self.pres.size_px
        return QRectF((W - w_px) / 2, (H - h_px) / 2, w_px, h_px)

    def insert_picture(self):
        path = get_open_file(self, _t("Insert Picture"), "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        if not path:
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self._add_image_bytes(data, Path(path).suffix.lstrip(".").lower() or "png")

    def _add_image_bytes(self, data: bytes, ext: str):
        pm = QPixmap()
        if not pm.loadFromData(data):
            show_error(self, _t("Insert Picture"), _t("The image could not be read."))
            return
        W, H = self.pres.size_px
        s = min(1.0, W * 0.6 / pm.width(), H * 0.6 / pm.height())
        r = self._centered_rect(pm.width() * s, pm.height() * s)
        sh = Shape("image", emu(r.x()), emu(r.y()), emu(r.width()), emu(r.height()), image=data, image_ext=ext,
                   name=_t("Picture"))
        self._insert_shape(sh)

    def insert_image(self, qimage):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        qimage.save(buf, "PNG")
        self._add_image_bytes(bytes(ba.data()), "png")

    def insert_screenshot(self):
        from app.services.capture import capture_dialog

        img = capture_dialog(self.window())
        if img is not None:
            self.insert_image(img)

    def insert_table(self):
        d = TableDialog(self)
        if d.exec():
            rows = d.rows()
            W, H = self.pres.size_px
            r = self._centered_rect(W * 0.7, min(H * 0.6, 40 * len(rows)))
            sh = Shape("table", emu(r.x()), emu(r.y()), emu(r.width()), emu(r.height()), name=_t("Table"))
            sh.table = rows
            sh.table_style["color"] = self.pres.theme.accent
            self._insert_shape(sh)

    def edit_table(self, item: ShapeItem):
        d = TableDialog(self, item.model.table)
        if d.exec():
            item.model.table = d.rows()
            item.update()
            self.commit(_t("Edit Table"))

    def insert_chart(self, kind: str | None = None):
        d = ChartDataDialog(self)
        if kind:
            d.kind.setCurrentIndex(max(0, d.kind.findData(kind)))
        if d.exec():
            W, H = self.pres.size_px
            r = self._centered_rect(W * 0.6, H * 0.6)
            sh = Shape("chart", emu(r.x()), emu(r.y()), emu(r.width()), emu(r.height()), name=_t("Chart"))
            sh.chart = d.chart()
            self._insert_shape(sh)

    def edit_chart(self, item: ShapeItem):
        d = ChartDataDialog(self, item.model.chart)
        if d.exec():
            item.model.chart = d.chart()
            item._chart_key = None
            item.update()
            self.commit(_t("Edit Chart"))

    def insert_symbol(self):
        from app.writer.dialogs import SymbolDialog

        d = SymbolDialog(self, "Noto Nastaliq Urdu")
        if d.exec() and d.chosen:
            self._insert_text_in_edit(d.chosen)

    def insert_slide_number(self):
        self._insert_text_in_edit(str(self.current + 1))

    def insert_date(self):
        from app.writer.dialogs import DateTimeDialog

        d = DateTimeDialog(self)
        if d.exec() and d.value():
            self._insert_text_in_edit(d.value())

    def _insert_text_in_edit(self, text: str):
        for it in self.canvas.items_:
            if it.is_editing_text():
                it.text_item.textCursor().insertText(text)
                return
        W, H = self.pres.size_px
        r = self._centered_rect(W * 0.3, H * 0.1)
        sh = Shape("text", emu(r.x()), emu(r.y()), emu(r.width()), emu(r.height()))
        sh.set_text(text, size=20, color=self.pres.theme.text_color)
        self._insert_shape(sh)

    def delete_selected(self):
        sel = self.canvas.selected_items()
        if not sel:
            return
        ids = {it.model.id for it in sel}
        self.slide.shapes = [s for s in self.slide.shapes if s.id not in ids]
        self.commit(_t("Delete"))
        self.canvas.show_slide(self.slide)

    def select_all(self):
        for it in self.canvas.items_:
            it.setSelected(True)

    # ================================================================== clipboard
    def copy(self, cut: bool = False):
        for it in self.canvas.items_:
            if it.is_editing_text():
                it.text_item.copy() if hasattr(it.text_item, "copy") else None
                return
        self._sync_all()
        sel = self.canvas.selected_items()
        if not sel:
            return
        md = QMimeData()
        md.setData(SHAPES_MIME, json.dumps([shape_to_dict(it.model) for it in sel]).encode())
        md.setText("\n".join(it.model.text for it in sel if it.model.text))
        if len(sel) == 1 and sel[0].model.kind == "image" and sel[0].model.image:
            from PySide6.QtGui import QImage

            md.setImageData(QImage.fromData(sel[0].model.image))
        QApplication.clipboard().setMimeData(md)
        if cut:
            self.delete_selected()

    def paste(self):
        editing = next((it for it in self.canvas.items_ if it.is_editing_text()), None)
        md = QApplication.clipboard().mimeData()
        if editing is not None:
            editing.text_item.textCursor().insertText(md.text())
            return
        if md.hasFormat(SHAPES_MIME):
            shapes = [shape_from_dict(d) for d in json.loads(bytes(md.data(SHAPES_MIME)).decode())]
            new = [clone_shape(s, emu(16), emu(16)) for s in shapes]
            self.slide.shapes.extend(new)
            self.commit(_t("Paste"))
            self.canvas.show_slide(self.slide)
            for s in new:
                it = self.canvas.item_for(s)
                if it:
                    it.setSelected(True)
        elif md.hasImage():
            self.insert_image(md.imageData())
        elif md.hasText() and md.text().strip():
            W, H = self.pres.size_px
            r = self._centered_rect(W * 0.5, H * 0.2)
            sh = Shape("text", emu(r.x()), emu(r.y()), emu(r.width()), emu(r.height()))
            sh.set_text(md.text().strip(), size=18, color=self.pres.theme.text_color)
            self._insert_shape(sh)

    def duplicate_shapes(self):
        self.copy()
        self.paste()

    # ================================================================== formatting
    def _editing_item(self):
        return next((it for it in self.canvas.items_ if it.is_editing_text()), None)

    def _apply_runs(self, fn, char_fmt: QTextCharFormat | None, desc: str):
        ed = self._editing_item()
        if ed is not None and char_fmt is not None:
            c = ed.text_item.textCursor()
            if not c.hasSelection():
                from PySide6.QtGui import QTextCursor

                c.select(QTextCursor.WordUnderCursor)
            c.mergeCharFormat(char_fmt)
            return
        sel = self.canvas.selected_items()
        if not sel:
            return
        for it in sel:
            for p in it.model.paragraphs:
                for r in p.runs:
                    fn(r)
            it.refresh_text()
        self.commit(desc)

    def set_font(self, family: str):
        cf = QTextCharFormat()
        cf.setFontFamilies([family])
        self._apply_runs(lambda r: setattr(r, "font", family), cf, _t("Font"))

    def set_size(self, text: str):
        try:
            size = float(str(text).replace(",", "."))
        except ValueError:
            return
        cf = QTextCharFormat()
        f = QFont()
        f.setPixelSize(int(round(size * 96 / 72)))
        cf.setFont(f, QTextCharFormat.FontPropertiesSpecifiedOnly)
        self._apply_runs(lambda r: setattr(r, "size", size), cf, _t("Font Size"))

    def step_size(self, d: int):
        sel = self.canvas.selected_items()
        if not sel:
            return
        runs = [r for p in sel[0].model.paragraphs for r in p.runs]
        cur = (runs[0].size if runs and runs[0].size else 18)
        self.set_size(str(max(6, cur + 2 * d)))

    def toggle_attr(self, attr: str):
        sel = self.canvas.selected_items()
        runs = [r for it in sel for p in it.model.paragraphs for r in p.runs]
        new = not (runs and all(getattr(r, attr) for r in runs))
        cf = QTextCharFormat()
        ed = self._editing_item()
        if ed is not None:
            cur = ed.text_item.textCursor().charFormat()
            new = {"bold": not cur.font().bold(), "italic": not cur.fontItalic(), "underline": not cur.fontUnderline()}[attr]
        if attr == "bold":
            cf.setFontWeight(QFont.Bold if new else QFont.Normal)
        elif attr == "italic":
            cf.setFontItalic(new)
        else:
            cf.setFontUnderline(new)
        self._apply_runs(lambda r: setattr(r, attr, new), cf, _t("Format"))

    def set_text_color(self, color: QColor):
        if not color.isValid():
            color = QColor(self.pres.theme.text_color)
        cf = QTextCharFormat()
        cf.setForeground(color)
        self._apply_runs(lambda r: setattr(r, "color", color.name()), cf, _t("Font Color"))

    def set_align(self, align: str):
        ed = self._editing_item()
        if ed is not None:
            from PySide6.QtGui import QTextBlockFormat

            bf = QTextBlockFormat()
            bf.setAlignment({"center": Qt.AlignHCenter, "right": Qt.AlignRight, "justify": Qt.AlignJustify}.get(align, Qt.AlignLeft))
            ed.text_item.textCursor().mergeBlockFormat(bf)
            return
        sel = self.canvas.selected_items()
        for it in sel:
            for p in it.model.paragraphs:
                p.align = align
            it.refresh_text()
        if sel:
            self.commit(_t("Align"))

    def set_valign(self, va: str):
        sel = self.canvas.selected_items()
        for it in sel:
            it.model.valign = va
            it._layout_text()
        if sel:
            self.commit(_t("Align"))

    def toggle_bullets(self):
        ed = self._editing_item()
        if ed is not None:
            from PySide6.QtGui import QTextListFormat

            c = ed.text_item.textCursor()
            lst = c.currentList()
            if lst is not None:
                for i in range(lst.count() - 1, -1, -1):
                    lst.remove(lst.item(i))
            else:
                lf = QTextListFormat()
                lf.setStyle(QTextListFormat.ListDisc)
                c.createList(lf)
            return
        sel = self.canvas.selected_items()
        for it in sel:
            on = not all(p.bullet for p in it.model.paragraphs)
            for p in it.model.paragraphs:
                p.bullet = on
            it.refresh_text()
        if sel:
            self.commit(_t("Bullets"))

    def toggle_rtl(self):
        sel = self.canvas.selected_items()
        for it in sel:
            on = not all(p.rtl for p in it.model.paragraphs)
            for p in it.model.paragraphs:
                p.rtl = on
                if on:
                    for r in p.runs:
                        if not r.font or r.font in (self.pres.theme.body_font, self.pres.theme.heading_font):
                            r.font = "Noto Nastaliq Urdu"
            it.refresh_text()
        if sel:
            self.commit(_t("Text Direction"))

    def set_fill(self, color: QColor):
        sel = self.canvas.selected_items()
        for it in sel:
            it.model.fill = color.name() if color.isValid() else None
            it.update()
        if sel:
            self.commit(_t("Fill"))

    def set_outline(self, color: QColor):
        sel = self.canvas.selected_items()
        for it in sel:
            it.model.line = color.name() if color.isValid() else None
            it.update()
        if sel:
            self.commit(_t("Outline"))

    def set_outline_width(self, w: float):
        sel = self.canvas.selected_items()
        for it in sel:
            it.model.line_width = w
            if not it.model.line:
                it.model.line = self.pres.theme.text_color
            it.prepareGeometryChange()
            it.update()
        if sel:
            self.commit(_t("Outline"))

    def toggle_shadow(self):
        sel = self.canvas.selected_items()
        for it in sel:
            it.model.shadow = not it.model.shadow
            it.update()
        if sel:
            self.commit(_t("Shadow"))

    def arrange(self, how: str):
        sel = self.canvas.selected_items()
        if not sel:
            return
        shapes = self.slide.shapes
        ids = {it.model.id for it in sel}
        chosen = [s for s in shapes if s.id in ids]
        rest = [s for s in shapes if s.id not in ids]
        if how == "front":
            self.slide.shapes = rest + chosen
        elif how == "back":
            self.slide.shapes = chosen + rest
        elif how in ("forward", "backward"):
            for s in (reversed(chosen) if how == "forward" else chosen):
                i = shapes.index(s)
                j = i + (1 if how == "forward" else -1)
                if 0 <= j < len(shapes):
                    shapes[i], shapes[j] = shapes[j], shapes[i]
        else:
            W, H = self.pres.size_px
            for it in sel:
                p = it.pos()
                x = {"left": 0, "center": (W - it._w) / 2, "right": W - it._w}.get(how, p.x())
                y = {"top": 0, "middle": (H - it._h) / 2, "bottom": H - it._h}.get(how, p.y())
                it.setPos(QPointF(x, y))
                it.sync_model()
            if how == "rotate":
                for it in sel:
                    it.setRotation((it.rotation() + 90) % 360)
                    it.sync_model()
        self.commit(_t("Arrange"))
        self.canvas.show_slide(self.slide)
        for s in chosen:
            it = self.canvas.item_for(s)
            if it:
                it.setSelected(True)

    # ================================================================== design
    def apply_theme(self, name: str):
        th = Theme(**THEMES[name].__dict__)
        old = self.pres.theme
        self.pres.theme = th
        for s in self.pres.slides:
            s.background = None
            for sh in s.shapes:
                for p in sh.paragraphs:
                    for r in p.runs:
                        if sh.placeholder == "title":
                            r.color = th.title_color
                            r.font = th.heading_font if r.font in (None, old.heading_font, "Calibri Light") else r.font
                        elif sh.placeholder in ("body", "subtitle") or r.color in (None, old.text_color, old.title_color):
                            r.color = th.text_color
                        if sh.placeholder in ("body", "subtitle") and r.font in (None, old.body_font):
                            r.font = th.body_font
                if sh.fill == old.accent:
                    sh.fill = th.accent
                if sh.kind == "table":
                    sh.table_style["color"] = th.accent
        self.commit(_t("Theme"))
        self.rebuild_all()

    def set_slide_size(self, size: tuple):
        ow, oh = self.pres.width, self.pres.height
        nw, nh = size
        if (ow, oh) == (nw, nh):
            return
        sx, sy = nw / ow, nh / oh
        for s in self.pres.slides:
            for sh in s.shapes:
                sh.x, sh.w = int(sh.x * sx), int(sh.w * sx)
                sh.y, sh.h = int(sh.y * sy), int(sh.h * sy)
        self.pres.width, self.pres.height = nw, nh
        self.commit(_t("Slide Size"))
        self.thumbs.setIconSize(QSize(176, int(176 * nh / nw)))
        self.rebuild_all()

    def set_background(self, color: QColor, all_slides: bool = False):
        for s in (self.pres.slides if all_slides else [self.slide]):
            s.background = color.name() if color.isValid() else None
        self.commit(_t("Background"))
        self.rebuild_all()

    def background_picture(self):
        path = get_open_file(self, _t("Background Picture"), "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            with open(path, "rb") as fh:
                self.slide.background_image = fh.read()
            self.commit(_t("Background"))
            self.rebuild_all()

    def reset_background(self):
        self.slide.background = None
        self.slide.background_image = None
        self.commit(_t("Background"))
        self.rebuild_all()

    # ================================================================== transitions & animations
    def set_transition(self, kind: str | None = None, all_slides: bool = False):
        kind = kind or self.trans_kind.currentData()
        tr = Transition(kind, self.trans_duration.value(), self.trans_dir.currentData())
        for s in (self.pres.slides if all_slides else [self.slide]):
            s.transition = copy.deepcopy(tr)
        self.trans_kind.blockSignals(True)
        self.trans_kind.setCurrentIndex(max(0, self.trans_kind.findData(kind)))
        self.trans_kind.blockSignals(False)
        self.commit(_t("Transition"))

    def _sync_transition_controls(self):
        if not hasattr(self, "trans_kind"):
            return
        tr = self.slide.transition
        for w, val in ((self.trans_kind, tr.kind), (self.trans_dir, tr.direction)):
            w.blockSignals(True)
            w.setCurrentIndex(max(0, w.findData(val)))
            w.blockSignals(False)
        self.trans_duration.blockSignals(True)
        self.trans_duration.setValue(tr.duration)
        self.trans_duration.blockSignals(False)

    def set_animation(self, effect: str | None = None):
        sel = self.canvas.selected_items()
        if not sel:
            self.statusChanged.emit(_t("Select a shape to animate."))
            return
        effect = effect or self.anim_effect.currentData()
        order = max([s.animation.order for s in self.slide.shapes if s.animation.effect != "none"] + [-1]) + 1
        for it in sel:
            an = it.model.animation
            was_none = an.effect == "none"
            it.model.animation = Animation(effect, self.anim_trigger.currentData(), self.anim_duration.value(),
                                           self.anim_delay.value(), self.anim_dir.currentData(),
                                           order if was_none else an.order)
            order += 1 if was_none else 0
        self.anim_effect.blockSignals(True)
        self.anim_effect.setCurrentIndex(max(0, self.anim_effect.findData(effect)))
        self.anim_effect.blockSignals(False)
        self.commit(_t("Animation"))

    def _refresh_anim_list(self):
        self.anim_list.clear()
        for i, sh in enumerate(self.slide.animated_shapes()):
            trig = {"click": "\u25b6", "with": "\u2194", "after": "\u23f1"}[sh.animation.trigger]
            label = sh.name or sh.kind
            if sh.text.strip():
                label += f" \u2014 {sh.text.strip().splitlines()[0][:24]}"
            it = QListWidgetItem(f"{i + 1}. {trig} {_t(sh.animation.effect.replace('_', ' ').title())}: {label}")
            it.setData(Qt.UserRole, sh.id)
            self.anim_list.addItem(it)

    def move_animation(self, d: int):
        it = self.anim_list.currentItem()
        if it is None:
            return
        anims = self.slide.animated_shapes()
        idx = next(i for i, s in enumerate(anims) if s.id == it.data(Qt.UserRole))
        j = idx + d
        if not 0 <= j < len(anims):
            return
        anims[idx], anims[j] = anims[j], anims[idx]
        for k, s in enumerate(anims):
            s.animation.order = k
        self.commit(_t("Reorder Animation"))
        self.anim_list.setCurrentRow(j)

    def remove_animation(self):
        it = self.anim_list.currentItem()
        if it is None:
            return
        for s in self.slide.shapes:
            if s.id == it.data(Qt.UserRole):
                s.animation = Animation()
        self.commit(_t("Remove Animation"))

    def preview_animations(self):
        self.slideshow(self.current)

    # ================================================================== show / export
    def slideshow(self, start: int = 0):
        from app.slide.show import SlideShow

        self.canvas.commit_text_edits()
        self._sync_all()
        self._show = SlideShow(self, start)
        self._show.start()

    def _clean_changed(self, clean: bool):
        try:
            super().set_modified(not clean)
        except (RuntimeError, TypeError):  # the undo stack can signal while the window is being destroyed
            pass

    def keep_ink(self, ink):
        slide_features.keep_ink(self, ink)

    def _ai_settings(self):
        from app.shell.ai_dialogs import AISettingsDialog

        AISettingsDialog(self).exec()

    def export_images(self):
        folder = get_directory(self, _t("Export Slides as Images"))
        if not folder:
            return
        self.canvas.commit_text_edits()
        base = self.path.stem if self.path else "slide"
        try:
            for i, s in enumerate(self.pres.slides, start=1):
                render_slide(self.pres, s, 1920).save(os.path.join(folder, f"{base}_{i:02d}.png"))
        except Exception as exc:
            handle_exception(self, _t("Export Images"), exc)
            return
        show_info(self, _t("Export Images"), _t("{0} slide image(s) saved to:\n{1}").format(len(self.pres.slides), folder))

    # ================================================================== actions & ribbon
    def _act(self, text, icon_name=None, slot=None, shortcut=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        a.setCheckable(checkable)
        a.setToolTip(text.replace("&&", "&") + (f" ({QKeySequence(shortcut).toString(QKeySequence.NativeText)})" if shortcut else ""))
        if slot is not None:
            a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_undo = self.undo.createUndoAction(self, _t("Undo"))
        self.a_undo.setShortcut(QKeySequence.Undo)
        self.a_undo.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.addAction(self.a_undo)
        self.a_redo = self.undo.createRedoAction(self, _t("Redo"))
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence.Redo])
        self.a_redo.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.addAction(self.a_redo)
        self.a_new_slide = A(_t("New Slide"), "new", lambda: self.new_slide(), "Ctrl+M")
        m = QMenu(self)
        for key, label in LAYOUTS.items():
            m.addAction(_t(label), lambda k=key: self.new_slide(k))
        self.a_new_slide.setMenu(m)
        self.a_dup_slide = A(_t("Duplicate Slide"), "duplicate", self.duplicate_slide)
        self.a_del_slide = A(_t("Delete Slide"), "delete", self.delete_slide)
        self.a_layout = A(_t("Layout"), "layout", lambda: self.apply_layout("title_content"))
        lm = QMenu(self)
        for key, label in LAYOUTS.items():
            lm.addAction(_t(label), lambda k=key: self.apply_layout(k))
        self.a_layout.setMenu(lm)
        self.a_cut = A(_t("Cut"), "cut", lambda: self.copy(True), "Ctrl+X")
        self.a_copy = A(_t("Copy"), "copy", lambda: self.copy(False), "Ctrl+C")
        self.a_paste = A(_t("Paste"), "paste", self.paste, "Ctrl+V")
        self.a_dup = A(_t("Duplicate"), "duplicate", self.duplicate_shapes, "Ctrl+D")
        self.a_select_all = A(_t("Select All"), "select", self.select_all, "Ctrl+A")
        self.a_bold = A(_t("Bold"), "bold", lambda: self.toggle_attr("bold"), "Ctrl+B")
        self.a_italic = A(_t("Italic"), "italic", lambda: self.toggle_attr("italic"), "Ctrl+I")
        self.a_underline = A(_t("Underline"), "underline", lambda: self.toggle_attr("underline"), "Ctrl+U")
        self.a_grow = A(_t("Grow Font"), "plus", lambda: self.step_size(1), "Ctrl+]")
        self.a_shrink = A(_t("Shrink Font"), "minus", lambda: self.step_size(-1), "Ctrl+[")
        self.a_color = A(_t("Font Color"), "font_color", lambda: self.set_text_color(QColor("#c00000")))
        cm = ColorMenu(self, _t("Automatic"))
        cm.colorPicked.connect(self.set_text_color)
        self.a_color.setMenu(cm)
        self.a_left = A(_t("Align Left"), "align_left", lambda: self.set_align("left"), "Ctrl+L")
        self.a_center = A(_t("Center"), "align_center", lambda: self.set_align("center"), "Ctrl+E")
        self.a_right = A(_t("Align Right"), "align_right", lambda: self.set_align("right"), "Ctrl+R")
        self.a_justify = A(_t("Justify"), "align_justify", lambda: self.set_align("justify"), "Ctrl+J")
        self.a_bullets = A(_t("Bullets"), "bullets", self.toggle_bullets)
        self.a_rtl = A(_t("Right-to-Left"), "rtl", self.toggle_rtl)
        self.a_vtop = A(_t("Align Top"), "up", lambda: self.set_valign("top"))
        self.a_vmid = A(_t("Align Middle"), "rows", lambda: self.set_valign("middle"))
        self.a_vbot = A(_t("Align Bottom"), "down", lambda: self.set_valign("bottom"))
        self.a_fill = A(_t("Shape Fill"), "fill_color", lambda: self.set_fill(QColor(self.pres.theme.accent)))
        fm = ColorMenu(self, _t("No Fill"))
        fm.colorPicked.connect(self.set_fill)
        self.a_fill.setMenu(fm)
        self.a_outline = A(_t("Shape Outline"), "border", lambda: self.set_outline(QColor("#404040")))
        om = ColorMenu(self, _t("No Outline"))
        om.colorPicked.connect(self.set_outline)
        om.addSeparator()
        for w in (0.75, 1.5, 3.0, 4.5, 6.0):
            om.addAction(_t("Weight {0} pt").format(w), lambda ww=w: self.set_outline_width(ww))
        self.a_outline.setMenu(om)
        self.a_shadow = A(_t("Shadow"), "square", self.toggle_shadow)
        self.a_arrange = A(_t("Arrange"), "front", lambda: self.arrange("front"))
        am = QMenu(self)
        for label, how in ((_t("Bring to Front"), "front"), (_t("Send to Back"), "back"), (_t("Bring Forward"), "forward"),
                           (_t("Send Backward"), "backward"), (None, None), (_t("Align Left"), "left"),
                           (_t("Align Center"), "center"), (_t("Align Right"), "right"), (_t("Align Top"), "top"),
                           (_t("Align Middle"), "middle"), (_t("Align Bottom"), "bottom"), (None, None),
                           (_t("Rotate 90\u00b0"), "rotate")):
            if label is None:
                am.addSeparator()
            else:
                am.addAction(label, lambda h=how: self.arrange(h))
        self.a_arrange.setMenu(am)
        self.a_shapes = A(_t("Shapes"), "square", lambda: self.begin_insert("rect"))
        sm = QMenu(self)
        for kind, label, ic in (("rect", _t("Rectangle"), "square"), ("roundrect", _t("Rounded Rectangle"), "square"),
                                ("ellipse", _t("Oval"), "circle"), ("triangle", _t("Triangle"), "triangle"),
                                ("diamond", _t("Diamond"), "square"), ("pentagon", _t("Pentagon"), "star"),
                                ("hexagon", _t("Hexagon"), "star"), ("star5", _t("Star"), "star"),
                                ("arrow_right", _t("Right Arrow"), "next"), ("arrow_left", _t("Left Arrow"), "prev"),
                                ("line", _t("Line"), "minus"), ("arrow_line", _t("Arrow"), "arrow")):
            sm.addAction(icon(ic), label, lambda k=kind: self.begin_insert(k))
        self.a_shapes.setMenu(sm)
        self.a_textbox = A(_t("Text Box"), "textbox", lambda: self.begin_insert("text"))
        self.a_picture = A(_t("Picture"), "image", self.insert_picture)
        self.a_screenshot = A(_t("Screenshot"), "capture", self.insert_screenshot)
        self.a_table = A(_t("Table"), "table", self.insert_table)
        self.a_chart = A(_t("Chart"), "chart_bar", lambda: self.insert_chart())
        self.a_symbol = A(_t("Symbol"), "symbol", self.insert_symbol)
        self.a_slide_no = A(_t("Slide Number"), "number", self.insert_slide_number)
        self.a_date = A(_t("Date && Time"), "date", self.insert_date)
        self.a_bg = A(_t("Background"), "fill_color", lambda: self.set_background(QColor("#ffffff")))
        bm = ColorMenu(self, _t("Theme Background"))
        bm.colorPicked.connect(lambda c: self.set_background(c))
        bm.addSeparator()
        bm.addAction(_t("Picture\u2026"), self.background_picture)
        bm.addAction(_t("Apply Color to All Slides"), lambda: self.set_background(QColor(self.slide.background or self.pres.theme.background), True))
        bm.addAction(_t("Reset Background"), self.reset_background)
        self.a_bg.setMenu(bm)
        self.a_size = A(_t("Slide Size"), "page_setup", lambda: self.set_slide_size(SIZE_16_9))
        szm = QMenu(self)
        szm.addAction(_t("Widescreen (16:9)"), lambda: self.set_slide_size(SIZE_16_9))
        szm.addAction(_t("Standard (4:3)"), lambda: self.set_slide_size(SIZE_4_3))
        self.a_size.setMenu(szm)
        self.a_show = A(_t("From Beginning"), "play", lambda: self.slideshow(0), "F5")
        self.a_presenter = A(_t("Presenter View"), "presenter", lambda: slide_features.start_presenter_view(self),
                             "Alt+F5")
        self.a_record_show = A(_t("Record Slide Show"), "record", lambda: slide_features.record_slideshow(self))
        self.a_diagram = A(_t("Smart Diagram"), "diagram", lambda: slide_features.insert_diagram(self))
        self.a_icons = A(_t("Icons"), "icons", lambda: slide_features.insert_icon(self))
        self.a_wordart = A(_t("WordArt"), "wordart", lambda: slide_features.insert_wordart(self))
        self.a_countdown = A(_t("Countdown"), "timer", lambda: slide_features.insert_countdown(self))
        self.a_design_ideas = A(_t("Design Ideas"), "ai", lambda: slide_features.design_ideas(self))
        self.anim_painter = slide_features.AnimationPainter(self)
        self.a_anim_painter = A(_t("Animation Painter"), "painter", self.anim_painter.toggle, checkable=True)
        self.a_ai_slides = A(_t("AI Slides"), "ai", lambda: slide_features.ai_slides(self), "Ctrl+J")
        self.a_ai_translate = A(_t("Parallel Translate"), "translate", lambda: slide_features.ai_translate(self))
        self.a_ai_spell = A(_t("Spell Check"), "spell", lambda: slide_features.ai_spell(self))
        self.a_ai_settings = A(_t("AI Settings"), "settings", self._ai_settings)
        self.a_show_cur = A(_t("From Current Slide"), "play", lambda: self.slideshow(self.current), "Shift+F5")
        self.a_hide = A(_t("Hide Slide"), "close", self.toggle_hidden)
        self.a_anim_pane = A(_t("Animation Pane"), "menu", lambda c: self.anim_pane.setVisible(c), checkable=True)
        self.a_notes = A(_t("Notes"), "notes", lambda c: self.notes.setVisible(c), checkable=True)
        self.a_notes.setChecked(True)
        self.a_thumbs = A(_t("Slides Pane"), "pages", lambda c: self.thumbs.setVisible(c), checkable=True)
        self.a_thumbs.setChecked(True)
        self.a_fit = A(_t("Fit to Window"), "fit_page", lambda: (setattr(self.canvas, "_auto_fit", True), self.canvas.fit()))
        self.a_zoom_in = A(_t("Zoom In"), "zoom_in", lambda: self._zoom_requested(self.canvas.zoom * 1.15), "Ctrl++")
        self.a_zoom_out = A(_t("Zoom Out"), "zoom_out", lambda: self._zoom_requested(self.canvas.zoom / 1.15), "Ctrl+-")
        self.a_export_pdf = A(_t("Export PDF"), "export_pdf", self.export_pdf)
        self.a_export_img = A(_t("Export Images"), "image_out", self.export_images)
        self.a_save_as = A(_t("Save As"), "save_as", self.save_as)

    def _build_ribbon(self) -> Ribbon:
        rb = Ribbon(self)
        home = rb.page(_t("Home"))
        g = home.group(_t("Clipboard"))
        g.large(self.a_paste)
        g.small(self.a_cut)
        g.small(self.a_copy)
        g.small(self.a_dup)
        g = home.group(_t("Slides"))
        g.large(self.a_new_slide, popup=True)
        g.small(self.a_layout)
        g.small(self.a_dup_slide)
        g.small(self.a_del_slide)
        g = home.group(_t("Font"))
        self.font_combo = QFontComboBox()
        self.font_combo.setMaximumWidth(160)
        self.font_combo.activated.connect(lambda *_: self.set_font(self.font_combo.currentFont().family()))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setMaximumWidth(60)
        for s in (10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 54, 60, 72, 96):
            self.size_combo.addItem(str(s))
        self.size_combo.activated.connect(lambda *_: self.set_size(self.size_combo.currentText()))
        self.size_combo.lineEdit().returnPressed.connect(lambda: self.set_size(self.size_combo.currentText()))
        g.row(self.font_combo, self.size_combo, self.a_grow, self.a_shrink)
        g.row(self.a_bold, self.a_italic, self.a_underline, self.a_color)
        g = home.group(_t("Paragraph"))
        g.row(self.a_left, self.a_center, self.a_right, self.a_justify)
        g.row(self.a_bullets, self.a_rtl, self.a_vtop, self.a_vmid, self.a_vbot)
        g = home.group(_t("Drawing"))
        g.large(self.a_shapes, popup=True)
        g.small(self.a_fill)
        g.small(self.a_outline)
        g.small(self.a_arrange)
        ins = rb.page(_t("Insert"))
        g = ins.group(_t("Slides"))
        g.large(self.a_new_slide, popup=True)
        g = ins.group(_t("Images"))
        g.large(self.a_picture)
        g.large(self.a_screenshot)
        g = ins.group(_t("Illustrations"))
        g.large(self.a_shapes, popup=True)
        g.large(self.a_table)
        g.large(self.a_chart)
        g.large(self.a_diagram)
        g.large(self.a_icons)
        g = ins.group(_t("Effects"))
        g.large(self.a_wordart)
        g.large(self.a_countdown)
        g = ins.group(_t("Text"))
        g.large(self.a_textbox)
        g.small(self.a_symbol)
        g.small(self.a_slide_no)
        g.small(self.a_date)
        de = rb.page(_t("Design"))
        g = de.group(_t("Themes"))
        row = []
        for name, th in THEMES.items():
            b = QToolButton()
            b.setText(name)
            b.setToolTip(_t("Apply theme {0}").format(name))
            b.setStyleSheet(f"QToolButton{{background:{th.background};color:{th.title_color};border:2px solid {th.accent};"
                            f"border-radius:6px;padding:6px 8px;font-weight:600;}}")
            b.clicked.connect(lambda _=False, n=name: self.apply_theme(n))
            row.append(b)
        g.row(*row[:3])
        g.row(*row[3:])
        g = de.group(_t("Designer"))
        g.large(self.a_design_ideas)
        g = de.group(_t("Customize"))
        g.large(self.a_size, popup=True)
        g.large(self.a_bg, popup=True)
        g.small(self.a_shadow)
        tr = rb.page(_t("Transitions"))
        g = tr.group(_t("Transition to This Slide"))
        self.trans_kind = QComboBox()
        for k in TRANSITIONS:
            self.trans_kind.addItem(_t(k.capitalize()), k)
        self.trans_kind.activated.connect(lambda *_: self.set_transition())
        self.trans_dir = QComboBox()
        for d in ("left", "right", "top", "bottom"):
            self.trans_dir.addItem(_t(d.capitalize()), d)
        self.trans_dir.activated.connect(lambda *_: self.set_transition())
        self.trans_duration = QDoubleSpinBox()
        self.trans_duration.setRange(0.1, 5.0)
        self.trans_duration.setSingleStep(0.1)
        self.trans_duration.setValue(0.6)
        self.trans_duration.setSuffix(" s")
        self.trans_duration.editingFinished.connect(lambda: self.set_transition())
        g.row(QLabel(_t("Effect:")), self.trans_kind)
        g.row(QLabel(_t("Direction:")), self.trans_dir)
        g.row(QLabel(_t("Duration:")), self.trans_duration)
        g = tr.group(_t("Timing"))
        apply_all = QPushButton(_t("Apply to All"))
        apply_all.clicked.connect(lambda: self.set_transition(None, True))
        preview = QPushButton(_t("Preview"))
        preview.clicked.connect(lambda: self.slideshow(max(0, self.current - 1)))
        g.row(apply_all)
        g.row(preview)
        an = rb.page(_t("Animations"))
        g = an.group(_t("Animation"))
        self.anim_effect = QComboBox()
        for e in ANIM_EFFECTS:
            self.anim_effect.addItem(_t(e.replace("_", " ").title()), e)
        self.anim_effect.activated.connect(lambda *_: self.set_animation())
        self.anim_dir = QComboBox()
        for d in ("bottom", "left", "right", "top"):
            self.anim_dir.addItem(_t("From " + d.capitalize()), d)
        self.anim_dir.activated.connect(lambda *_: self.set_animation())
        g.row(QLabel(_t("Effect:")), self.anim_effect)
        g.row(QLabel(_t("Direction:")), self.anim_dir)
        g = an.group(_t("Timing"))
        self.anim_trigger = QComboBox()
        for t, label in (("click", "On Click"), ("with", "With Previous"), ("after", "After Previous")):
            self.anim_trigger.addItem(_t(label), t)
        self.anim_trigger.activated.connect(lambda *_: self.set_animation())
        self.anim_duration = QDoubleSpinBox()
        self.anim_duration.setRange(0.1, 10.0)
        self.anim_duration.setSingleStep(0.1)
        self.anim_duration.setValue(0.5)
        self.anim_duration.setSuffix(" s")
        self.anim_duration.editingFinished.connect(lambda: self.set_animation() if self.canvas.selected_items() else None)
        self.anim_delay = QDoubleSpinBox()
        self.anim_delay.setRange(0.0, 30.0)
        self.anim_delay.setSingleStep(0.25)
        self.anim_delay.setSuffix(" s")
        self.anim_delay.editingFinished.connect(lambda: self.set_animation() if self.canvas.selected_items() else None)
        g.row(QLabel(_t("Start:")), self.anim_trigger)
        g.row(QLabel(_t("Duration:")), self.anim_duration)
        g.row(QLabel(_t("Delay:")), self.anim_delay)
        g = an.group(_t("Advanced"))
        g.large(self.a_anim_pane)
        g.large(self.a_anim_painter)
        pv = QAction(icon("play"), _t("Preview"), self)
        pv.triggered.connect(self.preview_animations)
        g.large(pv)
        ss = rb.page(_t("Slide Show"))
        g = ss.group(_t("Start Slide Show"))
        g.large(self.a_show)
        g.large(self.a_show_cur)
        g.large(self.a_presenter)
        g = ss.group(_t("Record"))
        g.large(self.a_record_show)
        g = ss.group(_t("Set Up"))
        g.large(self.a_hide)
        vw = rb.page(_t("View"))
        g = vw.group(_t("Show"))
        g.small(self.a_thumbs)
        g.small(self.a_notes)
        g.small(self.a_anim_pane)
        g = vw.group(_t("Zoom"))
        g.large(self.a_fit)
        g.small(self.a_zoom_in)
        g.small(self.a_zoom_out)
        tl = rb.page(_t("Tools"))
        g = tl.group(_t("Export"))
        g.large(self.a_export_pdf)
        g.large(self.a_export_img)
        g.large(self.a_save_as)
        aip = rb.page(_t("AI"))
        g = aip.group(_t("Create"))
        g.large(self.a_ai_slides)
        g.large(self.a_design_ideas)
        g = aip.group(_t("Language"))
        g.large(self.a_ai_translate)
        g.large(self.a_ai_spell)
        g = aip.group(_t("Setup"))
        g.large(self.a_ai_settings)
        return rb


__all__ = ["SlideDocument", "Paragraph", "Run", "QSpinBox", "px", "confirm"]
