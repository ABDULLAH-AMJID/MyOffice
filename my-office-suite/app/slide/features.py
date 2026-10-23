"""Presentation features: presenter view, slide-show recording, countdown timers, smart diagrams, icon library,
WordArt, design ideas, animation painter and the MyOffice AI commands."""
from __future__ import annotations

import copy
import logging
import math
import os
import zipfile
from datetime import datetime

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSpinBox, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from app.services.i18n import _t
from app.services.paths import resource_path
from app.slide.model import THEMES, Animation, Paragraph, Run, Shape, emu, make_slide

log = logging.getLogger(__name__)


def _png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


# ============================================================================ presenter view
class PresenterView(QWidget):
    """Current slide, next slide, speaker notes, timer and clock - follows the running slide show."""

    def __init__(self, doc, show):
        super().__init__(None, Qt.Window)
        self.doc, self.show_ = doc, show
        self.setWindowTitle(_t("Presenter View") + " - MyOffice")
        self.setStyleSheet("QWidget{background:#202124;color:#e8eaed} QPushButton{background:#3c4043;color:#e8eaed;"
                           "border:none;padding:8px 14px;border-radius:6px} QPushButton:hover{background:#5f6368}"
                           "QTextBrowser{background:#2b2c2f;border:none;font-size:18px;padding:10px}")
        grid = QGridLayout(self)
        self.current = QLabel()
        self.current.setAlignment(Qt.AlignCenter)
        self.current.setMinimumSize(560, 315)
        self.next = QLabel()
        self.next.setAlignment(Qt.AlignCenter)
        self.next.setMinimumSize(300, 170)
        self.next_caption = QLabel(_t("Next slide"))
        self.notes = QTextBrowser()
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setStyleSheet("font-size:34px;font-weight:600")
        self.clock = QLabel("")
        self.clock.setStyleSheet("font-size:20px;color:#9aa0a6")
        self.counter = QLabel("")
        self.counter.setStyleSheet("font-size:20px")
        side = QVBoxLayout()
        side.addWidget(self.next_caption)
        side.addWidget(self.next)
        side.addWidget(QLabel(_t("Notes")))
        side.addWidget(self.notes, 1)
        grid.addWidget(self.current, 0, 0)
        grid.addLayout(side, 0, 1)
        bar = QHBoxLayout()
        for label, slot in ((_t("\u25c0 Previous"), show.prev_step), (_t("Next \u25b6"), show.next_step),
                            (_t("Pen"), lambda: show.set_ink_tool("pen")),
                            (_t("Laser"), lambda: show.set_ink_tool("laser")),
                            (_t("Arrow"), lambda: show.set_ink_tool(None)),
                            (_t("Black Screen"), lambda: show.keyPressEvent(_Key(Qt.Key_B))),
                            (_t("Pause Timer"), self._toggle_timer), (_t("Reset Timer"), self._reset_timer),
                            (_t("End Show"), self._end)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
            if label == _t("Pause Timer"):
                self.pause_btn = b
        bar.addStretch(1)
        bar.addWidget(self.counter)
        bar.addSpacing(16)
        bar.addWidget(self.timer_label)
        bar.addSpacing(16)
        bar.addWidget(self.clock)
        grid.addLayout(bar, 1, 0, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(0, 1)
        self._elapsed = 0
        self._running = True
        self._tick = QTimer(self, interval=1000, timeout=self._second)
        self._tick.start()
        show.positionChanged.connect(self.refresh)
        show.finished.connect(self.close)
        self._second()
        self.refresh(show.pos, show.step)

    def refresh(self, pos: int, step: int):
        from app.slide.canvas import render_slide

        pres = self.doc.pres
        idx = self.show_.slides[pos]
        w = max(400, self.current.width())
        self.current.setPixmap(QPixmap.fromImage(render_slide(pres, pres.slides[idx], w)).scaled(
            self.current.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if pos + 1 < len(self.show_.slides):
            nidx = self.show_.slides[pos + 1]
            self.next.setPixmap(QPixmap.fromImage(render_slide(pres, pres.slides[nidx], 420)).scaled(
                self.next.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.next_caption.setText(_t("Next slide"))
        else:
            self.next.clear()
            self.next_caption.setText(_t("End of the slide show"))
        notes = pres.slides[idx].notes.strip()
        self.notes.setPlainText(notes or _t("(no notes for this slide)"))
        self.counter.setText(_t("Slide {0} of {1}").format(pos + 1, len(self.show_.slides)))

    def _second(self):
        if self._running:
            self._elapsed += 1
        e = self._elapsed
        self.timer_label.setText(f"{e // 3600:02d}:{e % 3600 // 60:02d}:{e % 60:02d}")
        self.clock.setText(datetime.now().strftime("%H:%M"))

    def _toggle_timer(self):
        self._running = not self._running
        self.pause_btn.setText(_t("Pause Timer") if self._running else _t("Resume Timer"))

    def _reset_timer(self):
        self._elapsed = -1 if self._running else 0
        self._second()

    def _end(self):
        self.show_.close()

    def keyPressEvent(self, e):
        self.show_.keyPressEvent(e)

    def closeEvent(self, e):
        self._tick.stop()
        if self.show_.isVisible():
            self.show_.close()
        super().closeEvent(e)


class _Key:
    """Minimal key event for forwarding a shortcut to the slide show."""

    def __init__(self, key):
        self._k = key

    def key(self):
        return self._k

    def modifiers(self):
        return Qt.NoModifier


def start_presenter_view(w):
    from app.slide.show import SlideShow

    w.canvas.commit_text_edits()
    w._sync_all()
    show = SlideShow(w, w.current)
    screens = QApplication.screens()
    pv = PresenterView(w, show)
    if len(screens) > 1:
        primary = QApplication.primaryScreen()
        other = next(s for s in screens if s is not primary)
        show.setGeometry(other.geometry())
        show.showFullScreen()
        pv.setGeometry(primary.availableGeometry())
        pv.showMaximized()
    else:  # one screen: the audience view in a window that can be moved to a projector later
        show.resize(960, 540)
        show.show()
        pv.showMaximized()
    w._show, w._presenter = show, pv


# ============================================================================ recording
class RecordOptionsDialog(QDialog):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        from app.services import recorder

        self.setWindowTitle(title)
        form = QFormLayout(self)
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
        self.fps = QSpinBox()
        self.fps.setRange(5, 30)
        self.fps.setValue(15)
        form.addRow(_t("Frames per second:"), self.fps)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_t("Start Recording"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


def record_slideshow(w):
    """Slide Show > Record: the show (with ink and animations) and the narration into an MP4."""
    from app.services import recorder
    from app.shell.dialogs import get_save_file, show_error
    from app.slide.show import SlideShow

    ok, why = recorder.multimedia_available()
    if not ok:
        show_error(w, _t("Record Slide Show"), _t("Video recording is not available: {0}").format(why))
        return
    base = w.path.stem if w.path else w.display_name()
    path = get_save_file(w, _t("Record Slide Show"), "MP4 video (*.mp4)", base + ".mp4", "mp4")
    if not path:
        return
    opts = RecordOptionsDialog(w, _t("Record Slide Show"))
    if opts.exec() != QDialog.Accepted:
        return
    w.canvas.commit_text_edits()
    w._sync_all()
    show = SlideShow(w, w.current)
    show.start()
    QApplication.processEvents()
    size = show.size() if show.width() > 100 else QSize(1280, 720)
    size = QSize(min(1920, size.width()), min(1080, size.height()))
    try:
        rec = recorder.FrameRecorder(path, size, opts.fps.value(), opts.mic.currentData(), parent=show)
    except Exception as exc:
        show.close()
        show_error(w, _t("Record Slide Show"), str(exc))
        return
    loop = recorder.CaptureLoop(rec, show, opts.fps.value(), opts.cam.currentData(), show_clicks=False, parent=show)
    errors: list[str] = []
    rec.error.connect(errors.append)

    def finished():
        loop.stop()
        QTimer.singleShot(1200, lambda: _report(w, path, errors))
    show.finished.connect(finished)
    w._show, w._recorder = show, loop
    loop.start()


def _report(w, path, errors):
    from app.shell.dialogs import show_error, show_info

    if errors or not os.path.exists(path) or os.path.getsize(path) < 1000:
        show_error(w, _t("Record Slide Show"), _t("The video could not be written.") + "\n" + "\n".join(errors[:3]))
        return
    show_info(w, _t("Record Slide Show"), _t("The recording was saved:\n{0}").format(path))


# ============================================================================ ink
def keep_ink(w, ink) -> None:
    """Put the pen/highlighter drawings of the show on their slides as a transparent picture."""
    W, H = w.pres.size_px
    added = 0
    for idx, strokes in ink.strokes.items():
        if not strokes or idx >= len(w.pres.slides):
            continue
        img = ink.ink_image(idx, int(W * 2), int(H * 2))
        if img is None:
            continue
        w.pres.slides[idx].shapes.append(Shape("image", 0, 0, w.pres.width, w.pres.height, image=_png(img),
                                               image_ext="png", name=_t("Ink")))
        added += 1
    if added:
        w.commit(_t("Keep Ink"))
        w.rebuild_all()


# ============================================================================ countdown
class CountdownDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Countdown Timer"))
        form = QFormLayout(self)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 180)
        self.minutes.setValue(5)
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 59)
        row = QHBoxLayout()
        row.addWidget(self.minutes)
        row.addWidget(QLabel(_t("min")))
        row.addWidget(self.seconds)
        row.addWidget(QLabel(_t("s")))
        form.addRow(_t("Duration:"), row)
        self.color = QPushButton("#1a73e8")
        self.color.clicked.connect(self._pick)
        form.addRow(_t("Colour:"), self.color)
        info = QLabel(_t("The timer counts down while the slide is shown in the slide show."))
        info.setWordWrap(True)
        info.setStyleSheet("color:#5f6368")
        form.addRow(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color.text()), self)
        if c.isValid():
            self.color.setText(c.name())

    def total(self) -> int:
        return max(1, self.minutes.value() * 60 + self.seconds.value())


def insert_countdown(w) -> None:
    d = CountdownDialog(w)
    if d.exec() != QDialog.Accepted:
        return
    secs = d.total()
    W, H = w.pres.size_px
    r = QRectF(W * 0.3, H * 0.35, W * 0.4, H * 0.3)
    sh = Shape("roundrect", emu(r.x()), emu(r.y()), emu(r.width()), emu(r.height()), fill="#ffffff",
               line=d.color.text(), line_width=3, valign="middle", name=f"countdown:{secs}")
    sh.set_text(f"{secs // 60:02d}:{secs % 60:02d}", size=72, bold=True, color=d.color.text(), align="center")
    w._insert_shape(sh)


# ============================================================================ smart diagrams
DIAGRAMS = [("process", "Process (chevrons)"), ("steps", "Steps with arrows"), ("cycle", "Cycle"),
            ("hierarchy", "Hierarchy / organisation chart"), ("list", "Vertical list"), ("pyramid", "Pyramid"),
            ("matrix", "Matrix 2 \u00d7 2"), ("timeline", "Timeline"), ("venn", "Venn diagram")]


def _palette(accent: str, n: int, multi: bool) -> list[str]:
    base = QColor(accent)
    out = []
    for i in range(max(1, n)):
        if multi:
            c = QColor.fromHsv((base.hue() + i * int(360 / max(3, n))) % 360, max(120, base.saturation()),
                               min(230, max(150, base.value())))
        else:
            c = base.lighter(100 + int(45 * i / max(1, n - 1))) if n > 1 else base
        out.append(c.name())
    return out


def _box(kind, x, y, w, h, text, fill, size=16, color="#ffffff", bold=True, line=None) -> Shape:
    longest = max((len(word) for word in text.split()), default=1)
    inner = w * (0.62 if kind in ("ellipse", "chevron", "triangle") else 0.86)
    size = max(8.0, min(float(size), inner / (longest * 0.6 * 96 / 72)))   # longest word fits on one line
    sh = Shape(kind, emu(x), emu(y), emu(w), emu(h), fill=fill, line=line, line_width=1.5, valign="middle")
    sh.set_text(text, size=size, bold=bold, color=color, align="center")
    sh.name = "Diagram"
    return sh


def _line(x1, y1, x2, y2, color, arrow=True) -> Shape:
    sh = Shape("arrow_line" if arrow else "line", emu(x1), emu(y1), emu(x2 - x1), emu(y2 - y1), line=color,
               line_width=2)
    sh.name = "Diagram"
    return sh


def parse_outline(text: str) -> list[tuple[int, str]]:
    """Lines with their indent level (tab or 2 spaces per level)."""
    items = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        expanded = raw.replace("\t", "  ")
        level = (len(expanded) - len(expanded.lstrip(" "))) // 2
        items.append((level, raw.strip().lstrip("-*\u2022 ").strip()))
    return items


def build_diagram(kind: str, outline: list[tuple[int, str]], area: QRectF, accent: str, multi: bool) -> list[Shape]:
    x0, y0, W, H = area.x(), area.y(), area.width(), area.height()
    top = [t for lvl, t in outline if lvl == 0] or [t for _l, t in outline]
    n = len(top)
    cols = _palette(accent, n, multi)
    shapes: list[Shape] = []
    if not n:
        return shapes
    if kind == "process":
        w = W / n
        for i, t in enumerate(top):
            shapes.append(_box("chevron", x0 + i * w, y0 + H * 0.3, w * 1.05, H * 0.4, t, cols[i], 15))
    elif kind == "steps":
        gap = W * 0.06
        w = (W - gap * (n - 1)) / n
        for i, t in enumerate(top):
            shapes.append(_box("roundrect", x0 + i * (w + gap), y0 + H * 0.3, w, H * 0.4, t, cols[i], 15))
            if i < n - 1:
                ax = x0 + i * (w + gap) + w + gap * 0.15
                shapes.append(Shape("arrow_right", emu(ax), emu(y0 + H * 0.45), emu(gap * 0.7), emu(H * 0.1),
                                    fill="#9aa0a6", name="Diagram"))
    elif kind == "cycle":
        cx, cy = x0 + W / 2, y0 + H / 2
        rad = min(W, H) * 0.36
        bw, bh = min(W, H) * 0.26, min(W, H) * 0.18
        pts = []
        for i, t in enumerate(top):
            a = -math.pi / 2 + 2 * math.pi * i / n
            px_, py_ = cx + rad * math.cos(a), cy + rad * math.sin(a)
            pts.append((px_, py_))
            shapes.append(_box("ellipse", px_ - bw / 2, py_ - bh / 2, bw, bh, t, cols[i], 14))
        for i in range(n):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
            dx, dy = bx - ax, by - ay
            dist = math.hypot(dx, dy) or 1
            sx, sy = ax + dx / dist * bw * 0.55, ay + dy / dist * bh * 0.6
            ex, ey = bx - dx / dist * bw * 0.55, by - dy / dist * bh * 0.6
            shapes.insert(0, _line(sx, sy, ex, ey, "#9aa0a6"))
    elif kind == "hierarchy":
        tree: list[tuple[str, list]] = []
        for lvl, t in outline:
            if lvl == 0 or not tree:
                tree.append((t, []))
            else:
                tree[-1][1].append(t)
        if len(tree) == 1:  # one root with children
            root, kids = tree[0]
            rw, rh = W * 0.28, H * 0.2
            shapes.append(_box("roundrect", x0 + (W - rw) / 2, y0, rw, rh, root, cols[0], 16))
            k = len(kids)
            if k:
                kw = min(W * 0.26, (W - (k - 1) * W * 0.03) / k)
                total = k * kw + (k - 1) * W * 0.03
                kcols = _palette(accent, k, multi)
                for i, t in enumerate(kids):
                    kx = x0 + (W - total) / 2 + i * (kw + W * 0.03)
                    shapes.append(_box("roundrect", kx, y0 + H * 0.55, kw, rh * 0.9, t,
                                       kcols[i] if multi else QColor(accent).lighter(130).name(), 14))
                    shapes.insert(0, _line(x0 + W / 2, y0 + rh, kx + kw / 2, y0 + H * 0.55, "#9aa0a6", False))
        else:  # several top-level items: a row of branches
            bw = (W - (len(tree) - 1) * W * 0.03) / len(tree)
            for i, (t, kids) in enumerate(tree):
                bx = x0 + i * (bw + W * 0.03)
                shapes.append(_box("roundrect", bx, y0, bw, H * 0.18, t, cols[i], 15))
                for j, kt in enumerate(kids):
                    shapes.append(_box("rect", bx + bw * 0.1, y0 + H * (0.26 + j * 0.16), bw * 0.8, H * 0.13, kt,
                                       QColor(cols[i]).lighter(160).name(), 12, color="#202124", bold=False))
    elif kind == "list":
        h = min(H / n * 0.85, H * 0.22)
        for i, t in enumerate(top):
            y = y0 + i * (H / n)
            shapes.append(Shape("rect", emu(x0), emu(y), emu(W * 0.04), emu(h), fill=cols[i], name="Diagram"))
            shapes.append(_box("rect", x0 + W * 0.05, y, W * 0.95, h, t, QColor(cols[i]).lighter(175).name(), 16,
                               color="#202124", bold=False))
    elif kind == "pyramid":
        h = H / n
        for i, t in enumerate(top):
            frac = (i + 1) / n
            w = W * 0.25 + W * 0.75 * frac
            shapes.append(_box("rect" if i else "triangle", x0 + (W - w) / 2, y0 + i * h, w, h * 0.94, t, cols[i],
                               15))
    elif kind == "matrix":
        items = (top + ["", "", "", ""])[:4]
        cw, ch = W * 0.48, H * 0.47
        for i, t in enumerate(items):
            shapes.append(_box("roundrect", x0 + (i % 2) * (cw + W * 0.04), y0 + (i // 2) * (ch + H * 0.06), cw, ch,
                               t, cols[i % len(cols)], 16))
    elif kind == "timeline":
        y = y0 + H / 2
        shapes.append(Shape("rect", emu(x0), emu(y - 3), emu(W), emu(6), fill="#9aa0a6", name="Diagram"))
        step = W / n
        for i, t in enumerate(top):
            cx = x0 + step * (i + 0.5)
            shapes.append(Shape("ellipse", emu(cx - 12), emu(y - 12), emu(24), emu(24), fill=cols[i],
                                line="#ffffff", line_width=2, name="Diagram"))
            above = i % 2 == 0
            lab = _box("text", cx - step * 0.48, y - H * 0.38 if above else y + H * 0.08, step * 0.96, H * 0.3, t,
                       None, 14, color="#202124", bold=False)
            lab.valign = "bottom" if above else "top"
            shapes.append(lab)
    elif kind == "venn":
        items = top[:3]
        k = len(items)
        d = min(W, H) * (0.62 if k > 1 else 0.8)
        centers = [(0.5, 0.5)] if k == 1 else [(0.38, 0.5), (0.62, 0.5)] if k == 2 else \
            [(0.4, 0.4), (0.6, 0.4), (0.5, 0.62)]
        for i, (t, (fx, fy)) in enumerate(zip(items, centers)):
            sh = _box("ellipse", x0 + W * fx - d / 2, y0 + H * fy - d / 2, d, d, t, cols[i], 16)
            sh.opacity = 0.62
            shapes.append(sh)
    return shapes


class DiagramDialog(QDialog):
    def __init__(self, parent, accent: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Smart Diagram"))
        self.resize(760, 480)
        self.accent = accent
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.kind = QListWidget()
        for key, label in DIAGRAMS:
            it = QListWidgetItem(_t(label))
            it.setData(Qt.UserRole, key)
            self.kind.addItem(it)
        self.kind.setCurrentRow(0)
        left.addWidget(QLabel(_t("Layout:")))
        left.addWidget(self.kind, 1)
        left.addWidget(QLabel(_t("Items (one per line; indent sub-items for a hierarchy):")))
        self.text = QPlainTextEdit(_t("Plan\nDesign\nBuild\nTest\nLaunch"))
        left.addWidget(self.text, 1)
        opts = QHBoxLayout()
        self.multi = QCheckBox(_t("Colourful"))
        self.multi.setChecked(True)
        self.color_btn = QPushButton(_t("Colour\u2026"))
        self.color_btn.clicked.connect(self._pick)
        opts.addWidget(self.multi)
        opts.addWidget(self.color_btn)
        opts.addStretch(1)
        left.addLayout(opts)
        h.addLayout(left, 1)
        right = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(360, 203)
        self.preview.setStyleSheet("border:1px solid #dadce0;background:#ffffff")
        right.addWidget(QLabel(_t("Preview:")))
        right.addWidget(self.preview)
        right.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_t("Insert"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        h.addLayout(right)
        for sig in (self.kind.currentRowChanged, self.text.textChanged, self.multi.toggled):
            sig.connect(self._render)
        self._render()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.accent), self)
        if c.isValid():
            self.accent = c.name()
            self._render()

    def shapes(self, area: QRectF) -> list[Shape]:
        return build_diagram(self.kind.currentItem().data(Qt.UserRole), parse_outline(self.text.toPlainText()), area,
                             self.accent, self.multi.isChecked())

    def _render(self):
        from app.slide.canvas import render_slide
        from app.slide.model import Presentation, Slide

        pres = Presentation()
        W, H = pres.size_px
        sl = Slide(layout="blank", shapes=self.shapes(QRectF(W * 0.06, H * 0.08, W * 0.88, H * 0.84)))
        self.preview.setPixmap(QPixmap.fromImage(render_slide(pres, sl, 360)))


def insert_diagram(w) -> None:
    d = DiagramDialog(w, w.pres.theme.accent)
    if d.exec() != QDialog.Accepted:
        return
    W, H = w.pres.size_px
    has_title = any(s.placeholder == "title" for s in w.slide.shapes)
    area = QRectF(W * 0.07, H * (0.27 if has_title else 0.1), W * 0.86, H * (0.65 if has_title else 0.8))
    w.slide.shapes.extend(d.shapes(area))
    w.commit(_t("Insert Diagram"))
    w.canvas.show_slide(w.slide)


# ============================================================================ icons
_ICON_ZIP = None


def icon_names() -> list[str]:
    global _ICON_ZIP
    if _ICON_ZIP is None:
        path = resource_path("icon_library.zip")
        _ICON_ZIP = zipfile.ZipFile(path) if path.exists() else False
    if not _ICON_ZIP:
        return []
    return sorted(os.path.splitext(os.path.basename(n))[0] for n in _ICON_ZIP.namelist() if n.endswith(".svg"))


def icon_svg(name: str) -> bytes:
    for n in _ICON_ZIP.namelist():
        if n.endswith("/" + name + ".svg") or n == name + ".svg":
            return _ICON_ZIP.read(n)
    raise KeyError(name)


def render_icon(name: str, color: str, size: int = 512, stroke: float | None = None) -> QImage:
    from PySide6.QtSvg import QSvgRenderer

    svg = icon_svg(name).decode("utf-8").replace("currentColor", color)
    if stroke:
        import re

        svg = re.sub(r'stroke-width="[0-9.]+"', f'stroke-width="{stroke}"', svg)
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return img


class IconDialog(QDialog):
    def __init__(self, parent, color: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Icons"))
        self.resize(760, 560)
        self.color = color
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search icons (e.g. user, chart, phone, arrow, heart)\u2026"))
        self.search.textChanged.connect(self._filter)
        self.color_btn = QPushButton(_t("Colour\u2026"))
        self.color_btn.clicked.connect(self._pick)
        row.addWidget(self.search, 1)
        row.addWidget(self.color_btn)
        v.addLayout(row)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(40, 40))
        self.list.setGridSize(QSize(84, 76))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setUniformItemSizes(True)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        v.addWidget(self.list, 1)
        self.count = QLabel("")
        self.count.setStyleSheet("color:#5f6368")
        v.addWidget(self.count)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_t("Insert"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.names = icon_names()
        self._filter()

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.color = c.name()
            self._filter()

    def _filter(self):
        q = self.search.text().strip().lower()
        names = [n for n in self.names if q in n] if q else self.names
        self.list.clear()
        for n in names[:400]:
            it = QListWidgetItem(QIcon(QPixmap.fromImage(render_icon(n, self.color, 64))), n)
            self.list.addItem(it)
        self.count.setText(_t("{0} icons").format(len(names)) + ("" if len(names) <= 400 else
                                                                 _t(" - showing the first 400, search to narrow")))

    def chosen(self) -> list[str]:
        return [it.text() for it in self.list.selectedItems()]


def insert_icon(w) -> None:
    if not icon_names():
        from app.shell.dialogs import show_error

        show_error(w, _t("Icons"), _t("The icon library is missing from this installation."))
        return
    d = IconDialog(w, w.pres.theme.accent)
    d.list.setSelectionMode(QListWidget.ExtendedSelection)
    if d.exec() != QDialog.Accepted or not d.chosen():
        return
    W, H = w.pres.size_px
    size = H * 0.18
    for i, name in enumerate(d.chosen()[:12]):
        x = W / 2 - size / 2 + (i - len(d.chosen()[:12]) / 2 + 0.5) * size * 1.2
        w.slide.shapes.append(Shape("image", emu(x), emu(H / 2 - size / 2), emu(size), emu(size),
                                    image=_png(render_icon(name, d.color)), image_ext="png", name=f"Icon {name}"))
    w.commit(_t("Insert Icon"))
    w.canvas.show_slide(w.slide)


# ============================================================================ WordArt
# name -> (colour or None, gradient or None, outline colour, outline width pt, shadow, font)
WORDART = {
    "Blue Gradient": (None, "#4facfe,#1a237e", None, 0, True, "Arial Black"),
    "Sunset": (None, "#ffb347,#ff3d00", "#7f1d00", 1.0, True, "Impact"),
    "Gold": (None, "#fff59d,#c79100", "#5d4037", 1.0, True, "Georgia"),
    "Outline Only": ("#ffffff", None, "#1a73e8", 2.0, False, "Arial Black"),
    "Neon Green": (None, "#b9f6ca,#00c853", "#004d1a", 1.25, True, "Verdana"),
    "Classic Shadow": ("#202124", None, None, 0, True, "Times New Roman"),
    "Royal Purple": (None, "#e1bee7,#6a1b9a", "#ffffff", 1.0, True, "Arial Black"),
    "Fire": (None, "#ffea00,#d50000", "#3e0000", 1.5, True, "Impact"),
}


def wordart_runs(text: str, name: str, size: float = 54) -> list[Paragraph]:
    color, gradient, outline, ow, _shadow, font = WORDART[name]
    return [Paragraph([Run(line, bold=True, size=size, color=color or (gradient or "#000000").split(",")[0],
                           font=font, outline=outline, outline_width=ow, gradient=gradient)], "center")
            for line in text.split("\n")]


class WordArtDialog(QDialog):
    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle(_t("WordArt"))
        self.resize(720, 420)
        v = QVBoxLayout(self)
        self.text = QLineEdit(text or _t("Your Text Here"))
        v.addWidget(self.text)
        grid = QGridLayout()
        self.choice = next(iter(WORDART))
        self.buttons = {}
        for i, name in enumerate(WORDART):
            b = QToolButton()
            b.setCheckable(True)
            b.setIconSize(QSize(150, 60))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setText(_t(name))
            b.clicked.connect(lambda _=False, n=name: self._choose(n))
            grid.addWidget(b, i // 4, i % 4)
            self.buttons[name] = b
        v.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.text.textChanged.connect(self._previews)
        self._previews()
        self._choose(self.choice)

    def _choose(self, name):
        self.choice = name
        for n, b in self.buttons.items():
            b.setChecked(n == name)

    def _previews(self):
        from app.slide.canvas import render_slide
        from app.slide.model import Presentation, Slide

        pres = Presentation()
        W, H = pres.size_px
        for name, b in self.buttons.items():
            sh = Shape("text", 0, emu(H * 0.2), pres.width, emu(H * 0.6), valign="middle",
                       paragraphs=wordart_runs(self.text.text()[:18] or "Abc", name, 120), shadow=WORDART[name][4])
            img = render_slide(pres, Slide(layout="blank", shapes=[sh]), 300)
            b.setIcon(QIcon(QPixmap.fromImage(img)))


def insert_wordart(w) -> None:
    sel = [it for it in w.canvas.selected_items() if it.model.kind in ("text", "rect", "roundrect")]
    d = WordArtDialog(w, sel[0].model.text if sel else "")
    if d.exec() != QDialog.Accepted:
        return
    if sel:
        sh = sel[0].model
        sizes = [r.size for p in sh.paragraphs for r in p.runs if r.size]
        sh.paragraphs = wordart_runs(d.text.text(), d.choice, max(sizes) if sizes else 54)
        sh.shadow = WORDART[d.choice][4]
        w.commit(_t("WordArt"))
        w.canvas.show_slide(w.slide)
        return
    W, H = w.pres.size_px
    sh = Shape("text", emu(W * 0.1), emu(H * 0.35), emu(W * 0.8), emu(H * 0.3), valign="middle",
               paragraphs=wordart_runs(d.text.text(), d.choice), shadow=WORDART[d.choice][4], name="WordArt")
    w._insert_shape(sh)


# ============================================================================ design ideas
def styled_slide(slide, theme, variant: int, pres_w: int, pres_h: int):
    """A copy of ``slide`` in ``theme`` colours/fonts with an optional accent decoration."""
    s = copy.deepcopy(slide)
    s.shapes = [sh for sh in s.shapes if sh.name != "Design accent"]
    s.background = theme.background
    s.background_image = None
    for sh in s.shapes:
        title = sh.placeholder in ("title", "subtitle")
        for p in sh.paragraphs:
            for r in p.runs:
                if r.gradient:
                    continue
                r.color = theme.title_color if sh.placeholder == "title" else theme.text_color
                r.font = theme.heading_font if title else theme.body_font
        if sh.kind not in ("text", "image", "table", "chart", "line", "arrow_line") and sh.fill:
            sh.fill = theme.accent
    if variant == 1:  # bar on the left
        s.shapes.insert(0, Shape("rect", 0, 0, int(pres_w * 0.025), pres_h, fill=theme.accent, name="Design accent"))
    elif variant == 2:  # band behind the title
        s.shapes.insert(0, Shape("rect", 0, 0, pres_w, int(pres_h * 0.2), fill=theme.accent, opacity=0.18,
                                 name="Design accent"))
    elif variant == 3:  # corner circle
        s.shapes.insert(0, Shape("ellipse", int(pres_w * 0.82), int(-pres_h * 0.12), int(pres_w * 0.3),
                                 int(pres_w * 0.3), fill=theme.accent, opacity=0.25, name="Design accent"))
    return s


class DesignIdeasDialog(QDialog):
    def __init__(self, w):
        super().__init__(w)
        from app.slide.canvas import render_slide

        self.setWindowTitle(_t("Design Ideas"))
        self.resize(820, 560)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(_t("Click a design to apply it to the current slide.")))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(240, 135))
        self.list.setGridSize(QSize(256, 165))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        v.addWidget(self.list, 1)
        self.variants = []
        pres = copy.copy(w.pres)
        for i, (tname, theme) in enumerate(THEMES.items()):
            for variant in ((i % 3) + 1, 0):
                s = styled_slide(w.slide, theme, variant, w.pres.width, w.pres.height)
                pres.theme = theme
                img = render_slide(pres, s, 240)
                self.variants.append(s)
                self.list.addItem(QListWidgetItem(QIcon(QPixmap.fromImage(img)), _t(tname)))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_t("Apply"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


def design_ideas(w) -> None:
    w.canvas.commit_text_edits()
    w._sync_all()
    d = DesignIdeasDialog(w)
    if d.exec() != QDialog.Accepted or d.list.currentRow() < 0:
        return
    new = d.variants[d.list.currentRow()]
    new.id = w.slide.id
    w.pres.slides[w.current] = new
    w.commit(_t("Design Ideas"))
    w.rebuild_all()


# ============================================================================ animation painter
class AnimationPainter:
    """Copy the selected shape's animation, then click other shapes to give them the same animation."""

    def __init__(self, w):
        self.w = w
        self.anim: Animation | None = None

    def toggle(self, on: bool):
        w = self.w
        if on:
            sel = w.canvas.selected_items()
            if len(sel) != 1 or sel[0].model.animation.effect == "none":
                w.a_anim_painter.setChecked(False)
                w.statusChanged.emit(_t("Select one animated shape first."))
                return
            self.anim = copy.deepcopy(sel[0].model.animation)
            self.source = sel[0].model.id
            w.canvas.selectionChangedSig.connect(self._apply)
            w.statusChanged.emit(_t("Animation Painter: click the shapes that should get this animation. Click the "
                                    "button again to stop."))
        else:
            self.stop()

    def _apply(self):
        w = self.w
        changed = 0
        for it in w.canvas.selected_items():
            if it.model.id != self.source and self.anim is not None:
                a = copy.deepcopy(self.anim)
                a.order = max([s.animation.order for s in w.slide.shapes] + [0]) + 1
                it.model.animation = a
                changed += 1
        if changed:
            w.commit(_t("Animation Painter"))
            w._refresh_anim_list()

    def stop(self):
        try:
            self.w.canvas.selectionChangedSig.disconnect(self._apply)
        except (RuntimeError, TypeError):
            pass
        self.anim = None
        self.w.a_anim_painter.setChecked(False)


# ============================================================================ MyOffice AI
def ai_slides(w) -> None:
    from app.shell.ai_dialogs import AISlidesDialog, ensure_ai

    if not ensure_ai(w):
        return

    def build(title: str, subtitle: str, outlines):
        w.canvas.commit_text_edits()
        w._sync_all()
        at = w.current + 1
        first = make_slide(w.pres, "title", title, subtitle)
        new = [first]
        for o in outlines:
            s = make_slide(w.pres, "title_content", o.title, "\n".join(o.bullets))
            s.notes = o.notes
            new.append(s)
        # an untouched first slide of a new deck (empty or only "Click to add..." hints) is replaced
        from app.slide.canvas import PLACEHOLDER_HINT

        hints = {h for v in PLACEHOLDER_HINT.values() for h in (v, _t(v))}
        if len(w.pres.slides) == 1 and all(not sh.text.strip() or sh.text.strip() in hints
                                           for sh in w.pres.slides[0].shapes):
            w.pres.slides[:] = new
            w.current = 0
        else:
            w.pres.slides[at:at] = new
            w.current = at
        w.commit(_t("AI Slides"))
        w.rebuild_all()
        w.statusChanged.emit(_t("{0} slides created.").format(len(new)))
    AISlidesDialog(w, build).exec()


def _text_shapes(w):
    sel = [it.model for it in w.canvas.selected_items() if it.model.paragraphs]
    return sel or [s for s in w.slide.shapes if s.paragraphs and s.text.strip()]


def ai_translate(w) -> None:
    from app.shell.ai_dialogs import TranslateDialog

    w.canvas.commit_text_edits()
    w._sync_all()
    shapes = _text_shapes(w)
    paras = [(sh, p) for sh in shapes for p in sh.paragraphs if p.text.strip()]
    if not paras:
        w.statusChanged.emit(_t("The slide has no text to translate."))
        return
    rtl_langs = ("Urdu", "Arabic", "Persian", "Punjabi")

    def use(translated: list[str], mode: str):
        target = getattr(d, "target", None)
        rtl = bool(target and target.currentData() in rtl_langs)
        for (sh, p), t in zip(paras, translated):
            if not t.strip():
                continue
            if mode == "replace":
                first = p.runs[0] if p.runs else Run("")
                p.runs = [copy.deepcopy(first)]
                p.runs[0].text = t
                p.rtl = rtl
            else:
                idx = sh.paragraphs.index(p)
                np_ = copy.deepcopy(p)
                np_.runs = [copy.deepcopy(p.runs[0]) if p.runs else Run("")]
                np_.runs[0].text = t
                np_.runs[0].italic = True
                np_.rtl = rtl
                sh.paragraphs.insert(idx + 1, np_)
        w.commit(_t("Parallel Translate"))
        w.canvas.show_slide(w.slide)
    d = TranslateDialog(w, [p.text for _s, p in paras], use)
    d.exec()


def ai_spell(w) -> None:
    from app.shell.ai_dialogs import GrammarDialog
    from app.services.ai import apply_corrections

    w.canvas.commit_text_edits()
    w._sync_all()
    shapes = _text_shapes(w)
    if not shapes:
        return
    blocks = [(sh, p) for sh in shapes for p in sh.paragraphs]
    text = "\n".join(p.text for _s, p in blocks)

    def apply(chosen):
        new = apply_corrections(text, chosen).split("\n")
        for (sh, p), t in zip(blocks, new):
            if t != p.text:
                first = p.runs[0] if p.runs else Run("")
                p.runs = [copy.deepcopy(first)]
                p.runs[0].text = t
        w.commit(_t("Spelling"))
        w.canvas.show_slide(w.slide)
    GrammarDialog(w, text, apply).exec()
