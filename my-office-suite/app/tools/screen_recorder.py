"""Screen Recorder: the whole screen, one screen of several, or a region - with microphone, optional camera
picture-in-picture, click highlighting and on-screen drawing (annotations) - saved as MP4."""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QRubberBand, QSpinBox, QWidget)

from app.services import recorder
from app.services.i18n import _t
from app.services.paths import user_subdir


class RegionPicker(QWidget):
    """Full-screen translucent layer: drag a rectangle to choose the recording region."""

    def __init__(self, screen):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.band = QRubberBand(QRubberBand.Rectangle, self)
        self.origin = QPoint()
        self.region: QRect | None = None
        self._loop = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        p.setPen(QColor("#ffffff"))
        p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                   _t("Drag to choose the area to record - Esc to cancel"))

    def mousePressEvent(self, e):
        self.origin = e.position().toPoint()
        self.band.setGeometry(QRect(self.origin, QSize()))
        self.band.show()

    def mouseMoveEvent(self, e):
        self.band.setGeometry(QRect(self.origin, e.position().toPoint()).normalized())

    def mouseReleaseEvent(self, e):
        r = QRect(self.origin, e.position().toPoint()).normalized()
        if r.width() > 40 and r.height() > 40:
            self.region = r
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()

    def pick(self) -> QRect | None:
        from PySide6.QtCore import QEventLoop

        self.showFullScreen()
        self._loop = QEventLoop()
        self.destroyed.connect(self._loop.quit)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._loop.exec()
        return self.region


class DrawLayer(QWidget):
    """Transparent always-on-top layer for drawing on the screen while recording (toggle with the toolbar)."""

    def __init__(self, geometry: QRect):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(geometry)
        self.strokes: list[list[QPointF]] = []
        self.drawing = False
        self.set_drawing(False)

    def set_drawing(self, on: bool):
        self.drawing = on
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
        self.setWindowFlag(Qt.WindowTransparentForInput, not on)
        self.show()
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def clear(self):
        self.strokes.clear()
        self.update()

    def mousePressEvent(self, e):
        self.strokes.append([e.position()])

    def mouseMoveEvent(self, e):
        if self.strokes:
            self.strokes[-1].append(e.position())
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.drawing:
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))  # catch the mouse everywhere
        p.setPen(QPen(QColor("#e53935"), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for s in self.strokes:
            if len(s) > 1:
                path = QPainterPath(s[0])
                for q in s[1:]:
                    path.lineTo(q)
                p.drawPath(path)


class RecorderBar(QWidget):
    """Small floating control bar while recording."""

    def __init__(self, dialog):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.dialog = dialog
        self.setStyleSheet("QWidget{background:#202124;color:#fff;border-radius:8px} QPushButton{background:#3c4043;"
                           "border:none;padding:6px 10px;border-radius:6px;color:#fff} QPushButton:checked{background:#1a73e8}")
        h = QHBoxLayout(self)
        self.dot = QLabel("\u25cf")
        self.dot.setStyleSheet("color:#ea4335;font-size:16px")
        self.time = QLabel("00:00")
        self.pause = QPushButton(_t("Pause"))
        self.pause.setCheckable(True)
        self.draw = QPushButton(_t("Draw"))
        self.draw.setCheckable(True)
        clear = QPushButton(_t("Clear"))
        stop = QPushButton(_t("Stop"))
        for w in (self.dot, self.time, self.pause, self.draw, clear, stop):
            h.addWidget(w)
        self.pause.toggled.connect(dialog.set_paused)
        self.draw.toggled.connect(dialog.set_drawing)
        clear.clicked.connect(dialog.clear_drawing)
        stop.clicked.connect(dialog.stop)
        self._blink = QTimer(self, interval=600, timeout=lambda: self.dot.setVisible(not self.dot.isVisible()))
        self._blink.start()

    def set_time(self, ms: int):
        s = ms // 1000
        self.time.setText(f"{s // 60:02d}:{s % 60:02d}")


class ScreenRecorderDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(_t("Screen Recorder"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        ok, why = recorder.multimedia_available()
        self.area = QComboBox()
        for i, scr in enumerate(QGuiApplication.screens()):
            g = scr.geometry()
            self.area.addItem(_t("Screen {0} ({1} \u00d7 {2})").format(i + 1, g.width(), g.height()), ("screen", i))
        self.area.addItem(_t("Choose a region\u2026"), ("region", 0))
        form.addRow(_t("Record:"), self.area)
        self.mic = QComboBox()
        self.mic.addItem(_t("No sound"), None)
        for dev in recorder.audio_inputs():
            self.mic.addItem(dev.description(), dev)
        if self.mic.count() > 1:
            self.mic.setCurrentIndex(1)
        form.addRow(_t("Microphone:"), self.mic)
        self.cam = QComboBox()
        self.cam.addItem(_t("No camera"), None)
        for dev in recorder.cameras():
            self.cam.addItem(dev.description(), dev)
        form.addRow(_t("Camera (picture in picture):"), self.cam)
        self.clicks = QCheckBox(_t("Highlight the mouse pointer and clicks"))
        self.clicks.setChecked(True)
        form.addRow(self.clicks)
        self.fps = QSpinBox()
        self.fps.setRange(5, 30)
        self.fps.setValue(15)
        form.addRow(_t("Frames per second:"), self.fps)
        self.countdown = QCheckBox(_t("3-second countdown before recording"))
        self.countdown.setChecked(True)
        form.addRow(self.countdown)
        self.status = QLabel("" if ok else _t("Video recording is not available: {0}").format(why))
        self.status.setWordWrap(True)
        form.addRow(self.status)
        bb = QDialogButtonBox()
        self.start_btn = bb.addButton(_t("Start Recording"), QDialogButtonBox.AcceptRole)
        self.start_btn.setEnabled(ok)
        bb.addButton(QDialogButtonBox.Close)
        self.start_btn.clicked.connect(self.start)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.loop = None
        self.bar = None
        self.layer = None
        self.path = ""

    def start(self):
        kind, idx = self.area.currentData()
        screens = QGuiApplication.screens()
        screen = screens[idx] if kind == "screen" else QGuiApplication.primaryScreen()
        region = None
        if kind == "region":
            self.hide()
            region = RegionPicker(screen).pick()
            if region is None:
                self.show()
                return
        folder = user_subdir("recordings")
        self.path = os.path.join(str(folder), datetime.now().strftime("Recording %Y-%m-%d %H-%M-%S.mp4"))
        geo = screen.geometry()
        rect = region if region is not None else QRect(0, 0, geo.width(), geo.height())
        dpr = screen.devicePixelRatio()
        size = QSize(int(rect.width() * dpr), int(rect.height() * dpr))
        if size.width() > 1920:
            size = QSize(1920, int(size.height() * 1920 / size.width()))
        try:
            rec = recorder.FrameRecorder(self.path, size, self.fps.value(), self.mic.currentData(), parent=self)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.loop = recorder.CaptureLoop(rec, ("screen", screen, region), self.fps.value(), self.cam.currentData(),
                                         self.clicks.isChecked(), parent=self)
        self.errors: list[str] = []
        rec.error.connect(self.errors.append)
        self.layer = DrawLayer(QRect(geo.topLeft() + rect.topLeft(), rect.size()))
        self.bar = RecorderBar(self)
        self.bar.adjustSize()
        self.bar.move(geo.right() - self.bar.width() - 30, geo.top() + 30)
        self.loop.tick.connect(self.bar.set_time)
        self.hide()
        if self.main is not None:
            self.main.showMinimized()
        delay = 3000 if self.countdown.isChecked() else 300
        self.bar.time.setText(_t("starting\u2026"))
        self.bar.show()
        QTimer.singleShot(delay, self.loop.start)

    def set_paused(self, on: bool):
        if self.loop:
            self.loop.set_paused(on)

    def set_drawing(self, on: bool):
        if self.layer:
            self.layer.set_drawing(on)

    def clear_drawing(self):
        if self.layer:
            self.layer.clear()

    def stop(self):
        if self.loop:
            self.loop.stop()
        for w in (self.bar, self.layer):
            if w is not None:
                w.close()
        if self.main is not None:  # back as it was before recording (maximized stays maximized)
            from app.shell.dialogs import restore_window

            restore_window(self.main)
        QTimer.singleShot(1200, self._finished)

    def _finished(self):
        from app.shell.dialogs import show_error

        if self.errors or not os.path.exists(self.path) or os.path.getsize(self.path) < 1000:
            show_error(self.main, _t("Screen Recorder"), _t("The video could not be written.") + "\n" +
                       "\n".join(self.errors[:3]))
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(QMessageBox.Information, _t("Screen Recorder"),
                          _t("The recording was saved:\n{0}").format(self.path), QMessageBox.NoButton, self.main)
        play = box.addButton(_t("Play"), QMessageBox.AcceptRole)
        folder = box.addButton(_t("Open Folder"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is play:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))
        elif box.clickedButton() is folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.path)))
        self.accept()


def open_dialog(main) -> None:
    d = ScreenRecorderDialog(main)
    main._screen_recorder = d  # keep it alive while recording
    d.show()
