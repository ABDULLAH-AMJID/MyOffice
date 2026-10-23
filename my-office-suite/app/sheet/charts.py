"""Charts: data extraction, matplotlib rendering, floating chart widgets and the chart dialog."""
from __future__ import annotations

import io
import logging
import re
import uuid

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget)

from app.services.i18n import _t
from app.sheet.formula.parser import parse_ref_text
from app.sheet.formula.values import XLError
from app.sheet.model import ChartSpec
from app.sheet.numfmt import format_value

log = logging.getLogger(__name__)

PALETTE = ["#4472c4", "#ed7d31", "#a5a5a5", "#ffc000", "#5b9bd5", "#70ad47", "#264478", "#9e480e", "#636363", "#997300"]
KINDS = [("column", "Column"), ("bar", "Bar"), ("line", "Line"), ("area", "Area"), ("pie", "Pie"),
         ("doughnut", "Doughnut"), ("scatter", "Scatter")]
_RTL = re.compile("[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")


def shape_label(text: str) -> str:
    """Make Urdu/Arabic labels readable in matplotlib (joining forms + right-to-left order)."""
    if not text or not _RTL.search(text):
        return text
    try:
        import arabic_reshaper

        reshaped = arabic_reshaper.reshape(text)
    except Exception:
        reshaped = text
    # reverse runs of RTL characters while keeping digits/latin words in reading order
    tokens = re.findall(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\s]+|[^\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]+",
                        reshaped)
    out = [t[::-1] if _RTL.search(t) else t for t in tokens]
    return "".join(reversed(out))


def chart_data(book, spec: ChartSpec):
    """Return (categories, series[(name, values)], x_values_for_scatter)."""
    ref = parse_ref_text(spec.data)
    sh = book.sheet_by_name(ref.sheet) if ref.sheet else None
    if sh is None:
        raise ValueError(_t("The chart data range refers to a missing sheet."))
    r1, c1, r2, c2 = ref.r1, ref.c1, min(ref.r2, max(sh.max_row, ref.r1)), min(ref.c2, max(sh.max_col, ref.c1))

    def val(r, c):
        cell = sh.cells.get((r, c))
        return None if cell is None else cell.value

    def text(r, c):
        return sh.display(r, c)[0]

    def num(v):
        if isinstance(v, bool) or v is None or isinstance(v, XLError):
            return float("nan")
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return float("nan")

    hdr = spec.has_header
    if spec.kind == "scatter":
        xs = [num(val(r, c1)) for r in range(r1 + (1 if hdr else 0), r2 + 1)]
        series = []
        for c in range(c1 + 1, c2 + 1):
            name = text(r1, c) if hdr else f"Series {c - c1}"
            series.append((name, [num(val(r, c)) for r in range(r1 + (1 if hdr else 0), r2 + 1)]))
        return [], series, xs
    first_col_labels = c2 > c1 and any(isinstance(val(r, c1), str) for r in range(r1 + (1 if hdr else 0), r2 + 1))
    if not spec.series_in_rows:
        data_c1 = c1 + 1 if first_col_labels or (c2 > c1 and hdr and val(r1, c1) in (None, "")) else c1
        start_r = r1 + (1 if hdr else 0)
        cats = [text(r, c1) for r in range(start_r, r2 + 1)] if data_c1 > c1 else [str(i + 1) for i in range(r2 - start_r + 1)]
        series = []
        for c in range(data_c1, c2 + 1):
            name = text(r1, c) if hdr else f"Series {c - data_c1 + 1}"
            series.append((name, [num(val(r, c)) for r in range(start_r, r2 + 1)]))
    else:
        start_c = c1 + 1 if first_col_labels or c2 > c1 else c1
        cats = [text(r1, c) for c in range(start_c, c2 + 1)] if hdr else [str(i + 1) for i in range(c2 - start_c + 1)]
        series = []
        for r in range(r1 + (1 if hdr else 0), r2 + 1):
            name = text(r, c1) if start_c > c1 else f"Series {r - r1}"
            series.append((name, [num(val(r, c)) for c in range(start_c, c2 + 1)]))
    return cats, series, None


def render_chart_png(book, spec: ChartSpec, size: tuple | None = None, dpi: int = 96) -> bytes:
    try:
        cats, series, xs = chart_data(book, spec)
    except Exception as exc:
        return render_data_png(spec, [], [], None, size, dpi, error=str(exc))
    return render_data_png(spec, cats, series, xs, size, dpi)


def render_data_png(spec, cats, series, xs, size: tuple | None = None, dpi: int = 96, error: str | None = None,
                    background: str = "white") -> bytes:
    """Render a chart from in-memory data.  ``spec`` needs kind/title/x_title/y_title/legend/size attributes."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    w, h = size or spec.size
    fig = Figure(figsize=(max(2.0, w / dpi), max(1.5, h / dpi)), dpi=dpi)
    fig.patch.set_facecolor(background)
    ax = fig.add_subplot(111)
    if background != "white":
        ax.set_facecolor(background)
    if error:
        ax.text(0.5, 0.5, error, ha="center", va="center", wrap=True, color="#c00000")
        ax.set_axis_off()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    cats = [shape_label(str(c)) for c in cats]
    kind = spec.kind
    if not series or not any(any(v == v for v in vals) for _n, vals in series):
        ax.text(0.5, 0.5, _t("No numeric data in the selected range"), ha="center", va="center", color="#5f6368")
        ax.set_axis_off()
    elif kind in ("pie", "doughnut"):
        name, vals = series[0]
        pairs = [(c, v) for c, v in zip(cats, vals) if v == v and v > 0]
        if pairs:
            labels, values = zip(*pairs)
            wedges, _t1, _t2 = ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90, counterclock=False,
                                      colors=PALETTE[:len(values)] * (len(values) // len(PALETTE) + 1),
                                      wedgeprops=dict(width=0.45 if kind == "doughnut" else 1, edgecolor="white"),
                                      textprops=dict(fontsize=8))
            ax.axis("equal")
    else:
        import numpy as np

        n = len(series)
        x = np.arange(len(cats))
        for i, (name, vals) in enumerate(series):
            color = PALETTE[i % len(PALETTE)]
            label = shape_label(name)
            if kind == "column":
                width = 0.8 / max(1, n)
                ax.bar(x - 0.4 + width * (i + 0.5), vals, width, label=label, color=color)
            elif kind == "bar":
                height = 0.8 / max(1, n)
                ax.barh(x - 0.4 + height * (i + 0.5), vals, height, label=label, color=color)
            elif kind == "line":
                ax.plot(x, vals, marker="o", markersize=4, linewidth=2, label=label, color=color)
            elif kind == "area":
                ax.fill_between(x, vals, alpha=0.55, label=label, color=color, step=None)
                ax.plot(x, vals, linewidth=1.2, color=color)
            elif kind == "scatter":
                ax.scatter(xs, vals, s=22, label=label, color=color)
        if kind in ("column", "line", "area"):
            ax.set_xticks(x)
            ax.set_xticklabels(cats, rotation=0 if len(cats) <= 8 else 45, ha="center" if len(cats) <= 8 else "right",
                               fontsize=8)
        elif kind == "bar":
            ax.set_yticks(x)
            ax.set_yticklabels(cats, fontsize=8)
            ax.invert_yaxis()
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#bfbfbf")
        ax.spines["bottom"].set_color("#bfbfbf")
        ax.tick_params(colors="#404040", labelsize=8)
        ax.grid(axis="x" if kind == "bar" else "y", color="#e5e5e5", linewidth=0.8)
        ax.set_axisbelow(True)
        if spec.x_title:
            ax.set_xlabel(shape_label(spec.x_title), fontsize=9, color="#404040")
        if spec.y_title:
            ax.set_ylabel(shape_label(spec.y_title), fontsize=9, color="#404040")
        if spec.legend and n > 0:
            ax.legend(fontsize=8, frameon=False, loc="best")
    if spec.title:
        ax.set_title(shape_label(spec.title), fontsize=12, color="#262626", pad=10)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=fig.get_facecolor())
    return buf.getvalue()


class _DataSpec:
    def __init__(self, d: dict, size):
        self.kind = d.get("kind", "column")
        self.title = d.get("title", "")
        self.x_title = d.get("x_title", "")
        self.y_title = d.get("y_title", "")
        self.legend = d.get("legend", True)
        self.size = size


def render_chart_dict_image(d: dict, size: tuple, background: str = "white") -> QImage:
    """Render a slide chart ({"kind","categories","series","title"}) to a QImage."""
    spec = _DataSpec(d, size)
    series = [(name, [float("nan") if v is None else float(v) for v in vals]) for name, vals in d.get("series", [])]
    cats = d.get("categories", [])
    xs = [float(c) for c in cats] if spec.kind == "scatter" else None
    return QImage.fromData(render_data_png(spec, [] if xs else cats, series, xs, size, background=background))


def render_chart_image(book, spec: ChartSpec, size=None) -> QImage:
    return QImage.fromData(render_chart_png(book, spec, size))


# ============================================================================= floating chart widget
class ChartWidget(QLabel):
    """A chart floating over the grid; drag to move, drag the corner to resize, double-click to edit."""
    changed = Signal()
    editRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, view, doc, spec: ChartSpec):
        super().__init__(view.viewport())
        self.view = view
        self.doc = doc
        self.spec = spec
        if not spec.id:
            spec.id = uuid.uuid4().hex[:8]
        self.setFrameShape(QLabel.Box)
        self.setStyleSheet("QLabel{background:white;border:1px solid #c8ccd0;}")
        self.setMouseTracking(True)
        self.setCursor(Qt.SizeAllCursor)
        self._drag = None
        self._pix = QPixmap()
        self.refresh()

    def refresh(self):
        try:
            self._pix = QPixmap.fromImage(render_chart_image(self.doc.book, self.spec))
        except Exception as exc:
            log.warning("Chart render failed: %s", exc)
            self._pix = QPixmap()
        self.setPixmap(self._pix)
        self.reposition()

    def reposition(self):
        r, c = self.spec.anchor
        rect = self.view.cell_rect(r, c)
        self.setGeometry(QRect(rect.topLeft(), QSize(*self.spec.size)))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.hasFocus() or self._drag:
            p = QPainter(self)
            p.setPen(QPen(QColor("#1a73e8"), 2))
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
            p.fillRect(QRect(self.width() - 9, self.height() - 9, 8, 8), QColor("#1a73e8"))
            p.end()

    def _in_grip(self, pos: QPoint) -> bool:
        return pos.x() > self.width() - 14 and pos.y() > self.height() - 14

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            self._drag = ("resize" if self._in_grip(pos) else "move", e.globalPosition().toPoint(), self.geometry())
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._drag is None:
            self.setCursor(Qt.SizeFDiagCursor if self._in_grip(pos) else Qt.SizeAllCursor)
            return
        mode, start, geo = self._drag
        delta = e.globalPosition().toPoint() - start
        if mode == "move":
            self.move(geo.topLeft() + delta)
        else:
            self.resize(max(160, geo.width() + delta.x()), max(110, geo.height() + delta.y()))
            self.setPixmap(self._pix.scaled(self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        mode, _s, _g = self._drag
        self._drag = None
        if mode == "move":
            idx = self.view.indexAt(self.geometry().topLeft() + QPoint(2, 2))
            if idx.isValid():
                self.spec.anchor = (idx.row() + 1, idx.column() + 1)
            self.reposition()
        else:
            self.spec.size = (self.width(), self.height())
            self.refresh()
        self.changed.emit()

    def mouseDoubleClickEvent(self, e):
        self.editRequested.emit(self)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.deleteRequested.emit(self)
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction(_t("Edit Chart\u2026"), lambda: self.editRequested.emit(self))
        m.addAction(_t("Copy as Image"), self.copy_image)
        m.addAction(_t("Save as Image\u2026"), self.save_image)
        m.addSeparator()
        m.addAction(_t("Delete Chart"), lambda: self.deleteRequested.emit(self))
        m.exec(e.globalPos())

    def copy_image(self):
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setImage(self._pix.toImage())

    def save_image(self):
        from app.shell.dialogs import get_save_file

        path = get_save_file(self, _t("Save Chart"), "PNG (*.png);;JPEG (*.jpg)", (self.spec.title or "chart") + ".png", "png")
        if path:
            self._pix.save(path)


# ============================================================================= dialog
class ChartDialog(QDialog):
    def __init__(self, parent, book, spec: ChartSpec):
        super().__init__(parent)
        self.setWindowTitle(_t("Chart"))
        self.book = book
        self.spec = ChartSpec(**spec.__dict__)
        self.resize(860, 480)
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.kinds = QListWidget()
        self.kinds.setMaximumWidth(150)
        from app.shell.icons import icon

        icons = {"column": "chart_bar", "bar": "chart_bar", "line": "chart_line", "area": "chart_line",
                 "pie": "chart_pie", "doughnut": "chart_pie", "scatter": "chart_scatter"}
        for k, label in KINDS:
            it = QListWidgetItem(icon(icons[k]), _t(label))
            it.setData(Qt.UserRole, k)
            self.kinds.addItem(it)
            if k == self.spec.kind:
                self.kinds.setCurrentItem(it)
        self.kinds.currentItemChanged.connect(self._update)
        left.addWidget(self.kinds)
        h.addLayout(left)
        mid = QFormLayout()
        self.data = QLineEdit(self.spec.data)
        self.title = QLineEdit(self.spec.title)
        self.xt = QLineEdit(self.spec.x_title)
        self.yt = QLineEdit(self.spec.y_title)
        self.rows = QComboBox()
        self.rows.addItems([_t("Columns"), _t("Rows")])
        self.rows.setCurrentIndex(1 if self.spec.series_in_rows else 0)
        self.header = QCheckBox(_t("First row/column contains labels"))
        self.header.setChecked(self.spec.has_header)
        self.legend = QCheckBox(_t("Show legend"))
        self.legend.setChecked(self.spec.legend)
        mid.addRow(_t("Data range:"), self.data)
        mid.addRow(_t("Series in:"), self.rows)
        mid.addRow(self.header)
        mid.addRow(_t("Title:"), self.title)
        mid.addRow(_t("X-axis title:"), self.xt)
        mid.addRow(_t("Y-axis title:"), self.yt)
        mid.addRow(self.legend)
        for w in (self.data, self.title, self.xt, self.yt):
            w.editingFinished.connect(self._update)
        self.rows.currentIndexChanged.connect(self._update)
        self.header.toggled.connect(self._update)
        self.legend.toggled.connect(self._update)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        mid.addRow(bb)
        midw = QWidget()
        midw.setLayout(mid)
        midw.setMaximumWidth(330)
        h.addWidget(midw)
        self.preview = QLabel()
        self.preview.setMinimumSize(360, 260)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:white;border:1px solid #dfe1e5;")
        h.addWidget(self.preview, 1)
        self._update()

    def _collect(self) -> ChartSpec:
        s = self.spec
        it = self.kinds.currentItem()
        s.kind = it.data(Qt.UserRole) if it else "column"
        s.data = self.data.text().strip()
        s.title = self.title.text().strip()
        s.x_title = self.xt.text().strip()
        s.y_title = self.yt.text().strip()
        s.series_in_rows = self.rows.currentIndex() == 1
        s.has_header = self.header.isChecked()
        s.legend = self.legend.isChecked()
        return s

    def _update(self, *_):
        s = self._collect()
        try:
            img = render_chart_image(self.book, s, (440, 300))
            self.preview.setPixmap(QPixmap.fromImage(img))
        except Exception as exc:
            self.preview.setText(str(exc))

    def result_spec(self) -> ChartSpec:
        return self._collect()


__all__ = ["ChartWidget", "ChartDialog", "render_chart_png", "render_chart_image", "chart_data", "format_value"]
