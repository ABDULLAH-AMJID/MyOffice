"""Video recording (MP4: H.264 video + AAC audio) with Qt Multimedia.

MyOffice composes every frame itself - a widget (slide show), a screen or a screen region, plus an optional
camera picture-in-picture - and pushes it into ``QVideoFrameInput``; the microphone is recorded alongside.
Qt Multimedia's FFmpeg backend encodes (on Windows with the system's Media Foundation H.264 encoder), so no extra
codec has to be installed.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen

log = logging.getLogger(__name__)


def multimedia_available() -> tuple[bool, str]:
    try:
        import importlib

        mm = importlib.import_module("PySide6.QtMultimedia")
        for name in ("QMediaFormat", "QMediaRecorder", "QVideoFrameInput"):
            getattr(mm, name)
    except Exception as exc:  # module or plugin missing
        return False, str(exc)
    return True, ""


def audio_inputs() -> list:
    try:
        from PySide6.QtMultimedia import QMediaDevices

        return list(QMediaDevices.audioInputs())
    except Exception:
        return []


def cameras() -> list:
    try:
        from PySide6.QtMultimedia import QMediaDevices

        return list(QMediaDevices.videoInputs())
    except Exception:
        return []


class FrameRecorder(QObject):
    """Encodes pushed QImages (+ microphone) into an MP4 file."""
    error = Signal(str)
    stateChanged = Signal(str)       # "recording", "paused", "stopped"
    durationChanged = Signal(int)    # milliseconds

    def __init__(self, path: str, size: QSize, fps: int = 15, audio_device=None, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import (QAudioInput, QMediaCaptureSession, QMediaFormat, QMediaRecorder,
                                          QVideoFrameFormat, QVideoFrameInput)

        self.path = path
        self.fps = max(1, int(fps))
        w, h = size.width() & ~1, size.height() & ~1  # H.264 wants even dimensions
        self.size = QSize(max(2, w), max(2, h))
        self.session = QMediaCaptureSession(self)
        fmt = QVideoFrameFormat(self.size, QVideoFrameFormat.PixelFormat.Format_BGRA8888)
        fmt.setStreamFrameRate(float(self.fps))
        self.video_in = QVideoFrameInput(fmt, self)
        self.session.setVideoFrameInput(self.video_in)
        self.audio_in = None
        if audio_device is not None:
            self.audio_in = QAudioInput(audio_device, self)
            self.session.setAudioInput(self.audio_in)
        self.recorder = QMediaRecorder(self)
        self.session.setRecorder(self.recorder)
        mf = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
        mf.setVideoCodec(QMediaFormat.VideoCodec.H264)
        if self.audio_in is not None:
            mf.setAudioCodec(QMediaFormat.AudioCodec.AAC)
        self.recorder.setMediaFormat(mf)
        self.recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        self.recorder.setVideoFrameRate(float(self.fps))
        self.recorder.setVideoResolution(self.size)
        self.recorder.setOutputLocation(QUrl.fromLocalFile(path))
        self.recorder.errorOccurred.connect(lambda _e, msg: self.error.emit(msg))
        self.recorder.durationChanged.connect(lambda ms: self.durationChanged.emit(int(ms)))
        self.recorder.recorderStateChanged.connect(self._state)
        self._t0 = 0.0
        self._frames = 0
        self._paused_at = 0.0
        self._paused_total = 0.0

    def _state(self, st):
        from PySide6.QtMultimedia import QMediaRecorder

        self.stateChanged.emit({QMediaRecorder.RecorderState.RecordingState: "recording",
                                QMediaRecorder.RecorderState.PausedState: "paused"}.get(st, "stopped"))

    def start(self):
        self._t0 = time.monotonic()
        self.recorder.record()

    def pause(self, on: bool):
        if on:
            self._paused_at = time.monotonic()
            self.recorder.pause()
        else:
            self._paused_total += time.monotonic() - self._paused_at
            self.recorder.record()

    def push(self, img: QImage) -> bool:
        """Send one frame (scaled to the recording size).  Returns False if the encoder is busy."""
        from PySide6.QtMultimedia import QVideoFrame

        if img.size() != self.size:
            img = img.scaled(self.size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if img.size() != self.size:
                canvas = QImage(self.size, QImage.Format_ARGB32)
                canvas.fill(Qt.black)
                p = QPainter(canvas)
                p.drawImage((self.size.width() - img.width()) // 2, (self.size.height() - img.height()) // 2, img)
                p.end()
                img = canvas
        frame = QVideoFrame(img.convertToFormat(QImage.Format_ARGB32))
        now_us = int((time.monotonic() - self._t0 - self._paused_total) * 1_000_000)
        frame.setStartTime(now_us)
        frame.setEndTime(now_us + int(1_000_000 / self.fps))
        ok = self.video_in.sendVideoFrame(frame)
        if ok:
            self._frames += 1
        return ok

    def stop(self):
        self.recorder.stop()


class CaptureLoop(QObject):
    """Grabs a widget or the screen at a fixed rate, adds camera picture-in-picture and pushes frames."""
    tick = Signal(int)   # elapsed ms

    def __init__(self, recorder: FrameRecorder, source, fps: int = 15, camera_device=None, show_clicks=True,
                 parent=None):
        super().__init__(parent)
        self.rec = recorder
        self.source = source          # QWidget, or ("screen", QScreen, QRect|None)
        self.show_clicks = show_clicks
        self.timer = QTimer(self, interval=max(15, int(1000 / fps)), timeout=self._grab)
        self.paused = False
        self._t0 = 0.0
        self.camera = None
        self.cam_image: QImage | None = None
        if camera_device is not None:
            from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QVideoSink

            self.camera = QCamera(camera_device, self)
            self._cam_session = QMediaCaptureSession(self)
            self._cam_session.setCamera(self.camera)
            self._sink = QVideoSink(self)
            self._cam_session.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._cam_frame)

    def _cam_frame(self, frame):
        img = frame.toImage()
        if not img.isNull():
            self.cam_image = img

    def start(self):
        if self.camera is not None:
            self.camera.start()
        self._t0 = time.monotonic()
        self.rec.start()
        self.timer.start()

    def set_paused(self, on: bool):
        self.paused = on
        self.rec.pause(on)

    def stop(self):
        self.timer.stop()
        if self.camera is not None:
            self.camera.stop()
        self.rec.stop()

    def _grab(self) -> None:
        if self.paused:
            return
        img = self.grab_image()
        if img is None or img.isNull():
            return
        if self.cam_image is not None:
            p = QPainter(img)
            w = img.width() // 5
            cam = self.cam_image.scaledToWidth(w, Qt.SmoothTransformation)
            x, y = img.width() - cam.width() - 16, img.height() - cam.height() - 16
            p.setPen(QPen(QColor("#ffffff"), 3))
            p.drawImage(x, y, cam)
            p.drawRect(x, y, cam.width(), cam.height())
            p.end()
        self.rec.push(img)
        self.tick.emit(int((time.monotonic() - self._t0) * 1000))

    def grab_image(self) -> QImage | None:
        src = self.source
        if isinstance(src, tuple):
            _kind, screen, region = src
            geo = screen.geometry()
            r = region if region is not None else QRect(0, 0, geo.width(), geo.height())
            pm = screen.grabWindow(0, r.x(), r.y(), r.width(), r.height())
            img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
            if self.show_clicks:
                pos = QCursor.pos() - geo.topLeft() - r.topLeft()
                if 0 <= pos.x() < r.width() and 0 <= pos.y() < r.height():
                    p = QPainter(img)
                    p.setRenderHint(QPainter.Antialiasing)
                    pressed = bool(QGuiApplication.mouseButtons() & Qt.LeftButton)
                    p.setPen(QPen(QColor(255, 193, 7, 230), 3))
                    p.setBrush(QColor(255, 235, 59, 90 if pressed else 50))
                    rad = 18 if pressed else 12
                    p.drawEllipse(QPoint(pos.x(), pos.y()), rad, rad)
                    p.end()
            return img
        if src is None or not src.isVisible():
            return None
        return src.grab().toImage().convertToFormat(QImage.Format_ARGB32)
