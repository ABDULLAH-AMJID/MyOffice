"""Small reusable widgets: colour menu, zoom slider, table-size picker."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QColorDialog, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSlider,
                               QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from app.services.i18n import _t

PALETTE = [
    "#000000", "#434343", "#666666", "#999999", "#b7b7b7", "#d9d9d9", "#efefef", "#ffffff",
    "#980000", "#ff0000", "#ff9900", "#ffff00", "#00ff00", "#00ffff", "#4a86e8", "#0000ff",
    "#9900ff", "#ff00ff", "#e6b8af", "#f4cccc", "#fce5cd", "#fff2cc", "#d9ead3", "#d0e0e3",
    "#c9daf8", "#cfe2f3", "#d9d2e9", "#ead1dc", "#cc4125", "#e06666", "#f6b26b", "#ffd966",
    "#93c47d", "#76a5af", "#6d9eeb", "#6fa8dc", "#8e7cc3", "#c27ba0", "#1f3864", "#2f5496",
    "#c00000", "#00b050", "#0070c0", "#7030a0", "#375623", "#833c0b", "#3a3838", "#1e8449",
]


def swatch_icon(color: QColor | str | None, base: QIcon | None = None, size: int = 20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    if base is not None and not base.isNull():
        p.drawPixmap(0, 0, base.pixmap(size, size - 4))
        if color is not None:
            p.fillRect(1, size - 4, size - 2, 4, QColor(color))
    else:
        p.fillRect(2, 2, size - 4, size - 4, QColor(color) if color is not None else Qt.white)
        p.setPen(QColor("#888"))
        p.drawRect(2, 2, size - 5, size - 5)
    p.end()
    return QIcon(pm)


class ColorMenu(QMenu):
    """A drop-down colour grid.  Emits ``colorPicked(QColor)``; an invalid QColor means "automatic/none"."""
    colorPicked = Signal(QColor)

    def __init__(self, parent=None, none_label: str | None = None):
        super().__init__(parent)
        host = QWidget(self)
        v = QVBoxLayout(host)
        v.setContentsMargins(6, 6, 6, 6)
        if none_label:
            b = QPushButton(none_label)
            b.setFlat(True)
            b.clicked.connect(lambda: self._emit(QColor()))
            v.addWidget(b)
        grid = QGridLayout()
        grid.setSpacing(2)
        for i, c in enumerate(PALETTE):
            btn = QToolButton()
            btn.setFixedSize(18, 18)
            btn.setStyleSheet(f"QToolButton{{background:{c};border:1px solid #bbb;}}"
                              f"QToolButton:hover{{border:1px solid #1a73e8;}}")
            btn.setToolTip(c)
            btn.clicked.connect(lambda _=False, col=c: self._emit(QColor(col)))
            grid.addWidget(btn, i // 8, i % 8)
        v.addLayout(grid)
        more = QPushButton(_t("More Colors\u2026"))
        more.setFlat(True)
        more.clicked.connect(self._more)
        v.addWidget(more)
        act = QWidgetAction(self)
        act.setDefaultWidget(host)
        self.addAction(act)

    def _emit(self, color: QColor):
        self.hide()
        self.colorPicked.emit(color)

    def _more(self):
        self.hide()
        c = QColorDialog.getColor(parent=self.parentWidget())
        if c.isValid():
            self.colorPicked.emit(c)


class TableGridPicker(QMenu):
    """Hover-to-size table picker (like Word's Insert Table grid)."""
    picked = Signal(int, int)

    def __init__(self, parent=None, rows: int = 8, cols: int = 10):
        super().__init__(parent)
        self._rows, self._cols = rows, cols
        host = QWidget(self)
        v = QVBoxLayout(host)
        v.setContentsMargins(6, 6, 6, 6)
        self.label = QLabel(_t("Insert Table"))
        v.addWidget(self.label)
        grid = QGridLayout()
        grid.setSpacing(2)
        self._cells = {}
        for r in range(rows):
            for c in range(cols):
                b = QToolButton()
                b.setFixedSize(16, 16)
                b.setMouseTracking(True)
                b.installEventFilter(self)
                b.setProperty("rc", (r, c))
                b.clicked.connect(lambda _=False, rr=r, cc=c: self._pick(rr, cc))
                grid.addWidget(b, r, c)
                self._cells[(r, c)] = b
        v.addLayout(grid)
        act = QWidgetAction(self)
        act.setDefaultWidget(host)
        self.addAction(act)
        self._highlight(-1, -1)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent

        if ev.type() == QEvent.Enter and obj.property("rc") is not None:
            r, c = obj.property("rc")
            self._highlight(r, c)
        return False

    def _highlight(self, rr, cc):
        for (r, c), b in self._cells.items():
            on = r <= rr and c <= cc
            b.setStyleSheet("QToolButton{background:%s;border:1px solid %s;}" % (
                "#cfe0fc" if on else "#ffffff", "#1a73e8" if on else "#c0c0c0"))
        if rr >= 0:
            self.label.setText(_t("{0} \u00d7 {1} Table").format(cc + 1, rr + 1))

    def _pick(self, r, c):
        self.hide()
        self.picked.emit(r + 1, c + 1)


class ZoomSlider(QWidget):
    zoomRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.minus = QToolButton()
        self.minus.setText("\u2212")
        self.plus = QToolButton()
        self.plus.setText("+")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(25, 400)
        self.slider.setValue(100)
        self.slider.setFixedWidth(120)
        self.label = QLabel("100%")
        self.label.setMinimumWidth(42)
        for w in (self.minus, self.slider, self.plus, self.label):
            h.addWidget(w)
        self.slider.valueChanged.connect(lambda v: self.zoomRequested.emit(v / 100.0))
        self.minus.clicked.connect(lambda: self.slider.setValue(max(25, self.slider.value() - 10)))
        self.plus.clicked.connect(lambda: self.slider.setValue(min(400, self.slider.value() + 10)))

    def set_zoom(self, z: float):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(z * 100)))
        self.slider.blockSignals(False)
        self.label.setText(f"{int(round(z * 100))}%")

    def sizeHint(self):
        return QSize(220, 22)
