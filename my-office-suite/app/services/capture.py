"""Screen Capture (QScreen.grabWindow): full screen, active window or a dragged region."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QSpinBox, QWidget)

from app.services.i18n import _t
from app.services.paths import user_subdir


def grab_screen(screen=None) -> QPixmap:
    screen = screen or QGuiApplication.primaryScreen()
    return screen.grabWindow(0)


def grab_virtual_desktop() -> tuple[QPixmap, QRect]:
    """Grab all screens composited into one pixmap (multi-monitor)."""
    screens = QGuiApplication.screens()
    total = QRect()
    for s in screens:
        total = total.united(s.geometry())
    pm = QPixmap(total.size())
    pm.fill(Qt.black)
    p = QPainter(pm)
    for s in screens:
        g = s.geometry()
        shot = s.grabWindow(0)
        p.drawPixmap(g.topLeft() - total.topLeft(), shot.scaled(g.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    p.end()
    return pm, total


def grab_window(widget: QWidget) -> QPixmap:
    win = widget.window()
    screen = win.screen() or QGuiApplication.primaryScreen()
    return screen.grabWindow(int(win.winId()))


class RegionSelector(QWidget):
    """Full-screen overlay to drag a rectangle; Esc cancels."""

    def __init__(self, shot: QPixmap, origin: QRect):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.shot = shot
        self.origin = origin
        self.setGeometry(origin)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self.result: QPixmap | None = None
        self._loop = QEventLoop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self._start and self._end:
            r = QRect(self._start, self._end).normalized()
            src = QRect(r.topLeft() * self.shot.width() // max(1, self.width()), r.size() * self.shot.width() // max(1, self.width()))
            p.drawPixmap(r, self.shot, src)
            p.setPen(QPen(QColor("#1a73e8"), 2))
            p.drawRect(r)
            p.setPen(Qt.white)
            p.drawText(r.topLeft() + QPoint(4, -6), f"{r.width()} \u00d7 {r.height()}")
        else:
            p.setPen(Qt.white)
            p.drawText(self.rect(), Qt.AlignCenter, _t("Drag to select an area \u2022 Esc to cancel"))

    def mousePressEvent(self, e):
        self._start = self._end = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._start is None:
            return
        r = QRect(self._start, e.position().toPoint()).normalized()
        if r.width() > 3 and r.height() > 3:
            sx = self.shot.width() / max(1, self.width())
            sy = self.shot.height() / max(1, self.height())
            self.result = self.shot.copy(QRect(int(r.x() * sx), int(r.y() * sy), int(r.width() * sx), int(r.height() * sy)))
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.result = None
            self.close()

    def closeEvent(self, e):
        self._loop.quit()
        super().closeEvent(e)

    def select(self) -> QPixmap | None:
        self.showFullScreen() if len(QGuiApplication.screens()) == 1 else self.show()
        self.activateWindow()
        self._loop.exec()
        return self.result


class CaptureOptions(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_t("Screen Capture"))
        f = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem(_t("Region (drag a rectangle)"), "region")
        self.mode.addItem(_t("Full screen"), "screen")
        self.mode.addItem(_t("All screens"), "all")
        self.mode.addItem(_t("MyOffice window"), "window")
        f.addRow(_t("Capture:"), self.mode)
        self.delay = QSpinBox()
        self.delay.setRange(0, 10)
        self.delay.setSuffix(" s")
        f.addRow(_t("Delay:"), self.delay)
        self.hide_me = QCheckBox(_t("Hide MyOffice while capturing"))
        self.hide_me.setChecked(True)
        f.addRow(self.hide_me)
        self.save_copy = QCheckBox(_t("Also save a PNG to the Captures folder"))
        f.addRow(self.save_copy)
        f.addRow(QLabel(_t("The image is copied to the clipboard as well.")))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


def _wait(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def capture(mode: str, main_window: QWidget | None = None, delay: int = 0, hide: bool = True) -> QImage | None:
    win = main_window.window() if main_window is not None else None
    if mode == "window" and win is not None:
        return grab_window(win).toImage()
    if hide and win is not None:
        win.hide()
        QApplication.processEvents()
        _wait(350)
    try:
        if delay:
            _wait(delay * 1000)
        if mode == "screen":
            screen = (win.screen() if win is not None else None) or QGuiApplication.primaryScreen()
            return grab_screen(screen).toImage()
        if mode == "all":
            return grab_virtual_desktop()[0].toImage()
        pm, rect = grab_virtual_desktop()
        sel = RegionSelector(pm, rect).select()
        return sel.toImage() if sel is not None else None
    finally:
        if hide and win is not None:
            win.show()
            win.raise_()
            win.activateWindow()


def save_capture(img: QImage, folder: str | None = None) -> str:
    folder = folder or str(user_subdir("captures"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, datetime.now().strftime("capture_%Y%m%d_%H%M%S.png"))
    if not img.save(path, "PNG"):
        raise OSError(f"Could not save {path}")
    return path


def capture_dialog(main_window: QWidget | None = None) -> QImage | None:
    """Ask for options, capture, copy to clipboard (and optionally save).  Returns the image."""
    d = CaptureOptions(main_window)
    if not d.exec():
        return None
    img = capture(d.mode.currentData(), main_window, d.delay.value(), d.hide_me.isChecked())
    if img is None or img.isNull():
        return None
    QApplication.clipboard().setImage(img)
    if d.save_copy.isChecked():
        save_capture(img)
    return img
