"""Full-screen slideshow with transitions and entrance animations."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPointF, QPropertyAnimation, QRectF,
                            QSequentialAnimationGroup, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QGraphicsView, QLabel, QMenu, QMessageBox, QWidget

from app.services.i18n import _t
from app.slide.canvas import build_scene


class _TransitionOverlay(QWidget):
    """Paints old/new slide pixmaps while a transition runs."""

    def __init__(self, parent, old: QPixmap, new: QPixmap, kind: str, direction: str):
        super().__init__(parent)
        self.old, self.new, self.kind, self.direction = old, new, kind, direction
        self.t = 0.0
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_t(self, t):
        self.t = float(t)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        w, h, t = self.width(), self.height(), self.t
        sign = -1 if self.direction in ("left", "top") else 1
        horiz = self.direction in ("left", "right")
        if self.kind == "fade":
            p.setOpacity(1 - t)
            p.drawPixmap(0, 0, self.old)
            p.setOpacity(t)
            p.drawPixmap(0, 0, self.new)
        elif self.kind == "push":
            if horiz:
                p.drawPixmap(int(sign * t * w), 0, self.old)
                p.drawPixmap(int(sign * t * w - sign * w), 0, self.new)
            else:
                p.drawPixmap(0, int(sign * t * h), self.old)
                p.drawPixmap(0, int(sign * t * h - sign * h), self.new)
        elif self.kind == "cover":
            p.drawPixmap(0, 0, self.old)
            if horiz:
                p.drawPixmap(int(-sign * (1 - t) * w), 0, self.new)
            else:
                p.drawPixmap(0, int(-sign * (1 - t) * h), self.new)
        elif self.kind == "wipe":
            p.drawPixmap(0, 0, self.old)
            if horiz:
                r = QRectF(0, 0, w * t, h) if sign > 0 else QRectF(w * (1 - t), 0, w * t, h)
            else:
                r = QRectF(0, 0, w, h * t) if sign > 0 else QRectF(0, h * (1 - t), w, h * t)
            p.drawPixmap(r, self.new, r)
        elif self.kind == "zoom":
            p.setOpacity(1 - t)
            p.drawPixmap(0, 0, self.old)
            p.setOpacity(t)
            s = 0.6 + 0.4 * t
            r = QRectF(w * (1 - s) / 2, h * (1 - s) / 2, w * s, h * s)
            p.drawPixmap(r, self.new, QRectF(self.new.rect()))
        elif self.kind == "split":
            p.drawPixmap(0, 0, self.old)
            r = QRectF(w / 2 * (1 - t), 0, w * t, h)
            p.drawPixmap(r, self.new, r)
        else:
            p.drawPixmap(0, 0, self.new)
        p.end()


# ============================================================================= ink (pen, highlighter, laser)
INK_TOOLS = {"pen": ("#e53935", 3.0, 255), "highlighter": ("#ffeb3b", 18.0, 110), "laser": ("#ff1744", 0, 255),
             "eraser": ("#000000", 0, 0)}


class InkOverlay(QWidget):
    """Draws on top of the slide during the show.  Strokes are stored per slide in slide coordinates (0..1), so
    they stay in place when the window size changes and can be kept as a picture afterwards."""

    def __init__(self, show: "SlideShow"):
        super().__init__(show)
        self.show_ = show
        self.tool: str | None = None
        self.color: str | None = None
        self.strokes: dict[int, list[dict]] = {}
        self.current: dict | None = None
        self.laser_pos: QPointF | None = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)

    def slide_rect(self) -> QRectF:
        W, H = self.show_.pres.size_px
        poly = self.show_.view.mapFromScene(QRectF(0, 0, W, H))
        r = poly.boundingRect()
        # view and overlay are siblings: go through their common parent (the show window)
        top_left = self.mapFrom(self.show_, self.show_.view.mapTo(self.show_, r.topLeft()))
        return QRectF(top_left, r.size())

    def _norm(self, pt: QPointF) -> tuple[float, float]:
        r = self.slide_rect()
        return ((pt.x() - r.x()) / max(1.0, r.width()), (pt.y() - r.y()) / max(1.0, r.height()))

    def _denorm(self, xy) -> QPointF:
        r = self.slide_rect()
        return QPointF(r.x() + xy[0] * r.width(), r.y() + xy[1] * r.height())

    def set_tool(self, tool: str | None, color: str | None = None):
        self.tool = tool
        if color:
            self.color = color
        self.setAttribute(Qt.WA_TransparentForMouseEvents, tool is None)
        cursor = {"pen": Qt.CrossCursor, "highlighter": Qt.CrossCursor, "eraser": Qt.PointingHandCursor,
                  "laser": Qt.BlankCursor}.get(tool or "", Qt.BlankCursor)
        self.setCursor(cursor)
        self.show_.setCursor(cursor if tool else Qt.BlankCursor)
        self.laser_pos = None
        self.update()

    def page(self) -> list[dict]:
        return self.strokes.setdefault(self.show_.slides[self.show_.pos], [])

    def clear_page(self):
        self.strokes.pop(self.show_.slides[self.show_.pos], None)
        self.update()

    def has_ink(self) -> bool:
        return any(v for v in self.strokes.values())

    def mousePressEvent(self, e):
        if self.tool in ("pen", "highlighter") and e.button() == Qt.LeftButton:
            color, width, alpha = INK_TOOLS[self.tool]
            self.current = {"tool": self.tool, "color": self.color if self.tool == "pen" and self.color else color,
                            "width": width, "alpha": alpha, "points": [self._norm(e.position())]}
            self.page().append(self.current)
        elif self.tool == "eraser" and e.button() == Qt.LeftButton:
            self._erase_at(e.position())
        elif e.button() == Qt.RightButton:
            self.show_.context_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self.tool == "laser":
            self.laser_pos = e.position()
            self.update()
        elif self.current is not None and e.buttons() & Qt.LeftButton:
            self.current["points"].append(self._norm(e.position()))
            self.update()
        elif self.tool == "eraser" and e.buttons() & Qt.LeftButton:
            self._erase_at(e.position())

    def mouseReleaseEvent(self, e):
        self.current = None

    def _erase_at(self, pos: QPointF):
        r = self.slide_rect()
        tol = 14 / max(1.0, r.width())
        x, y = self._norm(pos)
        page = self.page()
        keep = [s for s in page if not any(abs(px - x) < tol and abs(py - y) < tol * r.width() / max(1, r.height())
                                           for px, py in s["points"])]
        if len(keep) != len(page):
            page[:] = keep
            self.update()

    def paint_strokes(self, p: QPainter, strokes, rect: QRectF):
        p.setRenderHint(QPainter.Antialiasing)
        scale = rect.width() / 960.0
        for st in strokes:
            pts = st["points"]
            if not pts:
                continue
            col = QColor(st["color"])
            col.setAlpha(st["alpha"])
            pen = QPen(col, max(1.0, st["width"] * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(rect.x() + pts[0][0] * rect.width(), rect.y() + pts[0][1] * rect.height()))
            for x, y in pts[1:]:
                path.lineTo(rect.x() + x * rect.width(), rect.y() + y * rect.height())
            if len(pts) == 1:
                path.lineTo(rect.x() + pts[0][0] * rect.width() + 0.1, rect.y() + pts[0][1] * rect.height())
            p.drawPath(path)

    def paintEvent(self, _):
        p = QPainter(self)
        idx = self.show_.slides[self.show_.pos] if self.show_.slides else 0
        self.paint_strokes(p, self.strokes.get(idx, []), self.slide_rect())
        if self.tool == "laser" and self.laser_pos is not None:
            g = QRadialGradient(self.laser_pos, 14)
            g.setColorAt(0.0, QColor(255, 40, 40, 255))
            g.setColorAt(0.35, QColor(255, 23, 68, 210))
            g.setColorAt(1.0, QColor(255, 23, 68, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(self.laser_pos, 14, 14)
        p.end()

    def ink_image(self, slide_index: int, width: int, height: int) -> QImage | None:
        strokes = self.strokes.get(slide_index)
        if not strokes:
            return None
        img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        self.paint_strokes(p, strokes, QRectF(0, 0, width, height))
        p.end()
        return img


class SlideShow(QWidget):
    positionChanged = Signal(int, int)    # slide position (among shown slides), animation step
    finished = Signal()

    def __init__(self, doc, start: int = 0):
        super().__init__(None, Qt.Window)
        self.doc = doc
        self.pres = doc.pres
        self.setWindowTitle(_t("Slide Show"))
        self.setStyleSheet("background:black;")
        self.setCursor(Qt.BlankCursor)
        self.view = QGraphicsView(self)
        self.view.setStyleSheet("background:black;border:none;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.viewport().setAttribute(Qt.WA_TransparentForMouseEvents)
        self.slides = [i for i, s in enumerate(self.pres.slides) if not s.hidden] or [0]
        self.pos = max(0, min(len(self.slides) - 1, self.slides.index(start) if start in self.slides else 0))
        self.steps: list = []
        self.step = 0
        self.items = []
        self.anims: list = []
        self.overlay = None
        self.blank = None
        self.typed = ""
        self.counter = QLabel(self)
        self.counter.setStyleSheet("color:#bbb;background:rgba(0,0,0,120);padding:4px 10px;border-radius:6px;")
        self.counter.hide()
        self._counter_timer = QTimer(self, singleShot=True, interval=1500, timeout=self.counter.hide)
        self.ink = InkOverlay(self)
        self._countdowns: list = []
        self._tick = QTimer(self, interval=1000, timeout=self._tick_countdowns)
        self.load_slide(self.pos)

    # -- slide loading
    def _fit(self):
        W, H = self.pres.size_px
        self.view.setGeometry(self.rect())
        self.view.resetTransform()
        s = min(self.width() / W, self.height() / H) if W and H else 1
        self.view.scale(s, s)
        self.view.centerOn(W / 2, H / 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()
        self.ink.setGeometry(self.rect())
        self._place_countdowns()

    def load_slide(self, pos: int, show_all: bool = False):
        self._stop_anims()
        slide = self.pres.slides[self.slides[pos]]
        scene, items = build_scene(self.pres, slide, editing=False)
        old = self.view.scene()
        self.view.setScene(scene)
        if old is not None:
            old.deleteLater()
        self.items = items
        self._fit()
        by_id = {it.model.id: it for it in items}
        groups = []
        for sh in slide.animated_shapes():
            it = by_id.get(sh.id)
            if it is None:
                continue
            trig = sh.animation.trigger if groups else "click"
            if trig == "click":
                groups.append([])
            groups[-1].append((it, sh.animation, trig))
        self.steps = groups
        self.step = len(groups) if show_all else 0
        if not show_all:
            for g in groups:
                for it, _an, _trig in g:
                    it.setOpacity(0.0)
        self._show_counter()
        self._start_countdowns(slide)
        self.ink.raise_()
        self.ink.update()
        self.positionChanged.emit(self.pos, self.step)

    def _show_counter(self):
        self.counter.setText(f"{self.pos + 1} / {len(self.slides)}")
        self.counter.adjustSize()
        self.counter.move(self.width() - self.counter.width() - 20, self.height() - self.counter.height() - 16)
        self.counter.show()
        self.counter.raise_()
        self._counter_timer.start()

    def grab_frame(self) -> QPixmap:
        return self.view.grab(self.view.rect())

    # -- animations
    def _stop_anims(self):
        for a in self.anims:
            a.stop()
        self.anims = []
        if self.overlay is not None:
            self.overlay.deleteLater()
            self.overlay = None

    def _play_group(self, group):
        root = QParallelAnimationGroup(self)
        offset = 0.0
        last_end = 0.0
        for it, an, trig in group:
            if trig == "after":
                offset = last_end
            start = offset + an.delay
            seq = QSequentialAnimationGroup()
            if start > 0:
                seq.addPause(int(start * 1000))
            par = QParallelAnimationGroup()
            dur = max(50, int(an.duration * 1000))
            eff = an.effect
            if eff == "appear":
                it.setOpacity(1.0)
                last_end = max(last_end, start)
                continue
            op = QPropertyAnimation(it, b"opacity")
            op.setStartValue(0.0)
            op.setEndValue(1.0)
            op.setDuration(dur if eff in ("fade", "zoom", "float_in") else max(1, dur // 4))
            par.addAnimation(op)
            if eff == "fly_in":
                W, H = self.pres.size_px
                end = QPointF(it.pos())
                d = an.direction
                startp = QPointF(-it._w - 10 if d == "left" else W + 10 if d == "right" else end.x(),
                                 -it._h - 10 if d == "top" else H + 10 if d == "bottom" else end.y())
                mv = QPropertyAnimation(it, b"pos")
                mv.setStartValue(startp)
                mv.setEndValue(end)
                mv.setDuration(dur)
                mv.setEasingCurve(QEasingCurve.OutCubic)
                it.setPos(startp)
                par.addAnimation(mv)
            elif eff == "float_in":
                end = QPointF(it.pos())
                mv = QPropertyAnimation(it, b"pos")
                mv.setStartValue(end + QPointF(0, 40))
                mv.setEndValue(end)
                mv.setDuration(dur)
                mv.setEasingCurve(QEasingCurve.OutCubic)
                par.addAnimation(mv)
            elif eff == "zoom":
                sc = QPropertyAnimation(it, b"scale")
                it.setTransformOriginPoint(it._w / 2, it._h / 2)
                sc.setStartValue(0.2)
                sc.setEndValue(1.0)
                sc.setDuration(dur)
                sc.setEasingCurve(QEasingCurve.OutBack)
                par.addAnimation(sc)
            elif eff == "wipe":
                it.reveal_dir = {"left": "left", "right": "right", "top": "top", "bottom": "bottom"}.get(
                    {"bottom": "top", "top": "bottom", "left": "left", "right": "right"}.get(an.direction, "left"), "left")
                rv = QVariantAnimation()
                rv.setStartValue(0.0)
                rv.setEndValue(1.0)
                rv.setDuration(dur)
                rv.valueChanged.connect(lambda v, i=it: (setattr(i, "reveal", float(v)), i.update()))
                it.reveal = 0.0
                par.addAnimation(rv)
            seq.addAnimation(par)
            root.addAnimation(seq)
            last_end = max(last_end, start + an.duration)
        root.start()
        self.anims.append(root)

    def _finish_running(self):
        for a in self.anims:
            a.setCurrentTime(a.totalDuration())
            a.stop()
        self.anims = []
        for it in self.items:
            it.reveal = 1.0

    def _transition_to(self, new_pos: int):
        slide = self.pres.slides[self.slides[new_pos]]
        tr = slide.transition
        if tr.kind == "none" or not self.isVisible():
            self.pos = new_pos
            self.load_slide(new_pos)
            return
        old_pm = self.grab_frame()
        self.pos = new_pos
        self.load_slide(new_pos)
        new_pm = self.grab_frame()
        self.overlay = _TransitionOverlay(self, old_pm, new_pm, tr.kind, tr.direction)
        self.overlay.show()
        self.overlay.raise_()
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(max(100, int(tr.duration * 1000)))
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.valueChanged.connect(lambda v: self.overlay.set_t(v) if self.overlay is not None else None)

        def done():
            if self.overlay is not None:
                self.overlay.deleteLater()
                self.overlay = None
        anim.finished.connect(done)
        anim.start()
        self.anims.append(anim)

    # -- navigation
    def next_step(self):
        if self.blank is not None:
            self._unblank()
            return
        if any(a.state() == a.State.Running for a in self.anims):
            self._finish_running()
            return
        if self.step < len(self.steps):
            self._play_group(self.steps[self.step])
            self.step += 1
            self.positionChanged.emit(self.pos, self.step)
            return
        if self.pos + 1 < len(self.slides):
            self._transition_to(self.pos + 1)
        else:
            self._end_screen()

    def prev_step(self):
        if self.blank is not None:
            self._unblank()
            return
        if self.pos > 0:
            self.pos -= 1
            self.load_slide(self.pos, show_all=True)

    def goto(self, pos: int):
        if 0 <= pos < len(self.slides):
            self.pos = pos
            self.load_slide(pos)

    def _end_screen(self):
        if getattr(self, "_ended", False):
            self.close()
            return
        self._ended = True
        self._blank_with(QColor("black"), _t("End of slide show \u2014 click to exit."))

    def _blank_with(self, color: QColor, text: str = ""):
        self.blank = QLabel(text, self)
        self.blank.setAlignment(Qt.AlignCenter)
        self.blank.setStyleSheet(f"background:{color.name()};color:#9aa0a6;font-size:18px;")
        self.blank.setGeometry(self.rect())
        self.blank.show()
        self.blank.raise_()

    def _unblank(self):
        if getattr(self, "_ended", False):
            self.close()
            return
        self.blank.deleteLater()
        self.blank = None

    # -- countdown timers (shapes named "countdown:<seconds>")
    def _start_countdowns(self, slide):
        for lab, *_ in self._countdowns:
            lab.deleteLater()
        self._countdowns = []
        for sh in slide.shapes:
            if not sh.name.startswith("countdown:"):
                continue
            try:
                secs = max(1, int(float(sh.name.split(":", 1)[1])))
            except ValueError:
                continue
            lab = QLabel(self)
            run = next((r for p in sh.paragraphs for r in p.runs), None)
            color = (run.color if run and run.color else self.pres.theme.title_color)
            lab.setStyleSheet(f"color:{color}; background:transparent; font-weight:700;")
            lab.setAlignment(Qt.AlignCenter)
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._countdowns.append([lab, sh, secs])
        self._place_countdowns()
        self._tick_countdowns(start=True)
        if self._countdowns:
            self._tick.start()
        else:
            self._tick.stop()

    def _place_countdowns(self):
        from app.slide.model import px

        for lab, sh, _secs in self._countdowns:
            poly = self.view.mapFromScene(QRectF(px(sh.x), px(sh.y), px(sh.w), px(sh.h)))
            r = poly.boundingRect()
            lab.setGeometry(r)
            f = QFont(self.pres.theme.heading_font)
            f.setPixelSize(max(10, int(r.height() * 0.62)))
            f.setBold(True)
            lab.setFont(f)
            lab.raise_()
            lab.show()
        self.ink.raise_()

    def _tick_countdowns(self, start: bool = False):
        for item in self._countdowns:
            lab, sh, secs = item
            if not start:
                secs = max(0, secs - 1)
                item[2] = secs
            lab.setText(f"{secs // 60:02d}:{secs % 60:02d}")
            if secs == 0:
                lab.setStyleSheet("color:#d93025; background:transparent; font-weight:700;")

    # -- ink tools
    def set_ink_tool(self, tool: str | None, color: str | None = None):
        self.ink.set_tool(tool, color)
        self.ink.raise_()

    def context_menu(self, global_pos):
        self.setCursor(Qt.ArrowCursor)
        m = QMenu(self)
        m.addAction(_t("Next"), self.next_step)
        m.addAction(_t("Previous"), self.prev_step)
        go = m.addMenu(_t("Go to Slide"))
        for i, idx in enumerate(self.slides):
            sl = self.pres.slides[idx]
            title = next((s.text.split("\n")[0] for s in sl.shapes if s.placeholder == "title" and s.text.strip()), "")
            go.addAction(f"{i + 1}. {title[:40]}", lambda p=i: self.goto(p))
        m.addSeparator()
        po = m.addMenu(_t("Pointer Options"))
        for tool, label, key in ((None, _t("Arrow"), "Ctrl+A"), ("laser", _t("Laser Pointer"), "Ctrl+L"),
                                 ("pen", _t("Pen"), "Ctrl+P"), ("highlighter", _t("Highlighter"), "Ctrl+I"),
                                 ("eraser", _t("Eraser"), "Ctrl+E")):
            a = po.addAction(f"{label}\t{key}", lambda t=tool: self.set_ink_tool(t))
            a.setCheckable(True)
            a.setChecked(self.ink.tool == tool)
        ink = po.addMenu(_t("Ink Color"))
        for name, col in ((_t("Red"), "#e53935"), (_t("Blue"), "#1e88e5"), (_t("Green"), "#43a047"),
                          (_t("Black"), "#212121"), (_t("White"), "#ffffff"), (_t("Yellow"), "#fdd835")):
            ink.addAction(name, lambda c=col: self.set_ink_tool("pen", c))
        po.addAction(_t("Erase All Ink on Slide") + "\tE", self.ink.clear_page)
        m.addSeparator()
        m.addAction(_t("End Show"), self.close)
        m.exec(global_pos)
        self.ink.set_tool(self.ink.tool)

    def keyPressEvent(self, e):
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        if ctrl and k in (Qt.Key_P, Qt.Key_I, Qt.Key_L, Qt.Key_E, Qt.Key_A):
            self.set_ink_tool({Qt.Key_P: "pen", Qt.Key_I: "highlighter", Qt.Key_L: "laser", Qt.Key_E: "eraser",
                               Qt.Key_A: None}[k])
            return
        if k == Qt.Key_E and not ctrl:
            self.ink.clear_page()
            return
        if k == Qt.Key_Escape and self.ink.tool is not None:
            self.set_ink_tool(None)
            return
        if k in (Qt.Key_Escape,):
            self.close()
        elif k in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Space, Qt.Key_PageDown, Qt.Key_N) or (
                k in (Qt.Key_Return, Qt.Key_Enter) and not self.typed):
            self.next_step()
        elif k in (Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace, Qt.Key_P):
            self.prev_step()
        elif k == Qt.Key_Home:
            self.goto(0)
        elif k == Qt.Key_End:
            self.goto(len(self.slides) - 1)
        elif k in (Qt.Key_B, Qt.Key_Period):
            self._unblank() if self.blank is not None else self._blank_with(QColor("black"))
        elif k in (Qt.Key_W, Qt.Key_Comma):
            self._unblank() if self.blank is not None else self._blank_with(QColor("white"))
        elif Qt.Key_0 <= k <= Qt.Key_9:
            self.typed += chr(k)
        elif k in (Qt.Key_Return, Qt.Key_Enter) and self.typed:
            self.goto(int(self.typed) - 1)
            self.typed = ""
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.next_step()
        elif e.button() == Qt.RightButton:
            self.context_menu(e.globalPosition().toPoint())

    def wheelEvent(self, e):
        self.next_step() if e.angleDelta().y() < 0 else self.prev_step()

    def start(self):
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, e):
        self._stop_anims()
        self._tick.stop()
        if self.ink.has_ink() and hasattr(self.doc, "keep_ink"):
            self.setCursor(Qt.ArrowCursor)
            box = QMessageBox(QMessageBox.Question, _t("Slide Show"), _t("Do you want to keep your ink annotations?"),
                              QMessageBox.NoButton, self)
            keep = box.addButton(_t("Keep"), QMessageBox.AcceptRole)
            box.addButton(_t("Discard"), QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is keep:
                self.doc.keep_ink(self.ink)
        super().closeEvent(e)
        self.finished.emit()


def render_show_frame(pres, index: int, width: int = 640) -> QImage:
    """Static frame (used by tests / thumbnails of the show)."""
    from app.slide.canvas import render_slide

    return render_slide(pres, pres.slides[index], width)


__all__ = ["SlideShow", "render_show_frame", "QFont"]
