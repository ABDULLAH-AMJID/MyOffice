"""Slide editing canvas (QGraphicsView) and slide rendering."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextListFormat, QTextOption,
                           QTransform)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView

from app.slide.model import Paragraph, Presentation, Run, Shape, Slide, emu, px

PT_TO_PX = 96 / 72
TEXT_GRADIENT = QTextCharFormat.UserProperty + 60  # run gradient "#a,#b" (kept while editing)
HANDLE = 8
TEXT_KINDS = {"text", "rect", "roundrect", "ellipse", "triangle", "diamond", "pentagon", "hexagon", "arrow_right",
              "arrow_left", "star5", "chevron"}
PLACEHOLDER_HINT = {"title": "Click to add title", "subtitle": "Click to add subtitle", "body": "Click to add text"}


# ============================================================================= rich text <-> QTextDocument
def fill_document(doc: QTextDocument, paras: list, theme, scale: float = 1.0) -> None:
    doc.clear()
    doc.setDocumentMargin(0)
    cur = QTextCursor(doc)
    first = True
    for p in paras or [Paragraph([Run("")])]:
        bf = QTextBlockFormat()
        bf.setAlignment({"center": Qt.AlignHCenter, "right": Qt.AlignRight, "justify": Qt.AlignJustify}.get(p.align, Qt.AlignLeft))
        if p.rtl:
            bf.setLayoutDirection(Qt.RightToLeft)
            if p.align == "right":
                bf.setAlignment(Qt.AlignLeft)  # logical start in RTL
        bf.setBottomMargin(4 * scale)
        size0 = (p.runs[0].size if p.runs and p.runs[0].size else 18) * PT_TO_PX * scale
        cf0 = QTextCharFormat()
        f0 = QFont(theme.body_font)
        f0.setPixelSize(max(1, int(round(size0))))
        cf0.setFont(f0)
        if first:
            cur.setBlockFormat(bf)
            cur.setBlockCharFormat(cf0)
            first = False
        else:
            cur.insertBlock(bf, cf0)
        if p.bullet:
            lf = QTextListFormat()
            lf.setStyle([QTextListFormat.ListDisc, QTextListFormat.ListCircle, QTextListFormat.ListSquare][p.level % 3])
            lf.setIndent(p.level + 1)
            cur.createList(lf)
        elif p.level:
            bf.setIndent(p.level)
            cur.setBlockFormat(bf)
        for r in p.runs:
            cf = QTextCharFormat()
            f = QFont()
            fam = r.font or theme.body_font
            f.setFamilies([fam])  # Calibri etc. resolve through the installed substitutions; Urdu/Hindi glyphs
            # fall back per script to the bundled Noto fonts
            f.setPixelSize(max(1, int(round((r.size or 18) * PT_TO_PX * scale))))
            f.setBold(bool(r.bold))
            f.setItalic(bool(r.italic))
            f.setUnderline(bool(r.underline))
            cf.setFont(f)
            if r.gradient:
                from PySide6.QtGui import QBrush, QGradient, QLinearGradient

                c1, _, c2 = r.gradient.partition(",")
                grad = QLinearGradient(0, 0, 0, 1)
                grad.setCoordinateMode(QGradient.ObjectBoundingMode)
                grad.setColorAt(0, QColor(c1))
                grad.setColorAt(1, QColor(c2 or c1))
                cf.setForeground(QBrush(grad))
                cf.setProperty(TEXT_GRADIENT, r.gradient)
            else:
                cf.setForeground(QColor(r.color or theme.text_color))
            if r.outline and r.outline_width > 0:
                from PySide6.QtGui import QPen

                cf.setTextOutline(QPen(QColor(r.outline), max(0.5, r.outline_width * PT_TO_PX * scale)))
            cur.insertText(r.text, cf)


def read_document(doc: QTextDocument) -> list:
    paras = []
    block = doc.begin()
    while block.isValid():
        bf = block.blockFormat()
        al = bf.alignment()
        rtl = bf.layoutDirection() == Qt.RightToLeft or block.textDirection() == Qt.RightToLeft
        if al & Qt.AlignHCenter:
            align = "center"
        elif al & Qt.AlignJustify:
            align = "justify"
        elif al & Qt.AlignRight:
            align = "left" if rtl else "right"
        else:
            align = "right" if rtl else "left"
        lst = block.textList()
        bullet = lst is not None
        level = (lst.format().indent() - 1) if lst is not None else bf.indent()
        runs = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            it += 1
            if not frag.isValid():
                continue
            cf = frag.charFormat()
            f = cf.font()
            size = (f.pixelSize() / PT_TO_PX) if f.pixelSize() > 0 else (f.pointSizeF() if f.pointSizeF() > 0 else 18)
            fams = cf.fontFamilies() or [f.family()]
            color = cf.foreground().color().name() if cf.hasProperty(QTextCharFormat.ForegroundBrush) else None
            run = Run(frag.text(), f.bold() or None, f.italic() or None, f.underline() or None,
                      round(size * 2) / 2, color, fams[0] if fams else None)
            if cf.hasProperty(TEXT_GRADIENT):
                run.gradient = str(cf.property(TEXT_GRADIENT))
                run.color = run.gradient.split(",")[0]
            pen = cf.textOutline()
            if cf.hasProperty(QTextCharFormat.TextOutline) and pen.style() != Qt.NoPen:
                run.outline = pen.color().name()
                run.outline_width = round(pen.widthF() / PT_TO_PX, 2)
            runs.append(run)
        if not runs:
            cf = block.charFormat()
            f = cf.font()
            size = (f.pixelSize() / PT_TO_PX) if f.pixelSize() > 0 else 18
            runs = [Run("", size=round(size * 2) / 2)]
        paras.append(Paragraph(runs, align, max(0, level), bullet, rtl))
        block = block.next()
    return paras


# ============================================================================= shape geometry
def shape_path(kind: str, w: float, h: float) -> QPainterPath:
    path = QPainterPath()
    if kind in ("rect", "text", "image", "table", "chart"):
        path.addRect(0, 0, w, h)
    elif kind == "roundrect":
        r = min(w, h) * 0.18
        path.addRoundedRect(0, 0, w, h, r, r)
    elif kind == "ellipse":
        path.addEllipse(0, 0, w, h)
    elif kind == "triangle":
        path.addPolygon(QPolygonF([QPointF(w / 2, 0), QPointF(w, h), QPointF(0, h)]))
        path.closeSubpath()
    elif kind == "diamond":
        path.addPolygon(QPolygonF([QPointF(w / 2, 0), QPointF(w, h / 2), QPointF(w / 2, h), QPointF(0, h / 2)]))
        path.closeSubpath()
    elif kind in ("pentagon", "hexagon", "star5"):
        n = {"pentagon": 5, "hexagon": 6, "star5": 10}[kind]
        pts = []
        for i in range(n):
            a = -math.pi / 2 + 2 * math.pi * i / n + (math.pi / 6 if kind == "hexagon" else 0)
            rr = 1.0 if kind != "star5" or i % 2 == 0 else 0.42
            pts.append(QPointF(w / 2 + rr * w / 2 * math.cos(a), h / 2 + rr * h / 2 * math.sin(a)))
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
    elif kind == "arrow_right":
        t, head = h * 0.28, w * 0.35
        path.addPolygon(QPolygonF([QPointF(0, t), QPointF(w - head, t), QPointF(w - head, 0), QPointF(w, h / 2),
                                   QPointF(w - head, h), QPointF(w - head, h - t), QPointF(0, h - t)]))
        path.closeSubpath()
    elif kind == "chevron":
        d = min(w * 0.3, h * 0.5)
        path.addPolygon(QPolygonF([QPointF(0, 0), QPointF(w - d, 0), QPointF(w, h / 2), QPointF(w - d, h),
                                   QPointF(0, h), QPointF(d, h / 2)]))
        path.closeSubpath()
    elif kind == "arrow_left":
        t, head = h * 0.28, w * 0.35
        path.addPolygon(QPolygonF([QPointF(w, t), QPointF(head, t), QPointF(head, 0), QPointF(0, h / 2),
                                   QPointF(head, h), QPointF(head, h - t), QPointF(w, h - t)]))
        path.closeSubpath()
    else:
        path.addRect(0, 0, w, h)
    return path


# ============================================================================= items
class ShapeItem(QGraphicsObject):
    geometryEdited = Signal(object)
    textEdited = Signal(object)

    def __init__(self, shape: Shape, theme, editing: bool = True):
        super().__init__()
        self.model = shape
        self.theme = theme
        self.editing = editing
        self.reveal = 1.0           # 0..1 used by the slideshow wipe effect
        self.reveal_dir = "left"
        self._w, self._h = px(abs(shape.w)), px(abs(shape.h))
        self._pix = None
        self._chart_key = None
        self.text_item: QGraphicsTextItem | None = None
        self.setPos(px(shape.x) if shape.w >= 0 or shape.kind not in ("line", "arrow_line") else px(shape.x + shape.w),
                    px(shape.y) if shape.h >= 0 or shape.kind not in ("line", "arrow_line") else px(shape.y + shape.h))
        if shape.kind in ("line", "arrow_line"):
            self.setPos(px(min(shape.x, shape.x + shape.w)), px(min(shape.y, shape.y + shape.h)))
        self.setTransformOriginPoint(self._w / 2, self._h / 2)
        self.setRotation(shape.rotation)
        if editing:
            self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                          QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemIsFocusable)
            self.setAcceptHoverEvents(True)
        if shape.kind in TEXT_KINDS:
            self.text_item = QGraphicsTextItem(self)
            if shape.shadow and not shape.fill and shape.kind == "text":
                from PySide6.QtWidgets import QGraphicsDropShadowEffect

                fx = QGraphicsDropShadowEffect()
                fx.setBlurRadius(6)
                fx.setOffset(3, 3)
                fx.setColor(QColor(0, 0, 0, 120))
                self.text_item.setGraphicsEffect(fx)
            self.text_item.setTextInteractionFlags(Qt.NoTextInteraction)
            doc = self.text_item.document()
            opt = doc.defaultTextOption()
            opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            doc.setDefaultTextOption(opt)
            self.refresh_text()
            doc.contentsChanged.connect(self._layout_text)

    # -- text
    def refresh_text(self):
        if self.text_item is None:
            return
        doc = self.text_item.document()
        doc.blockSignals(True)
        fill_document(doc, self.model.paragraphs, self.theme)
        doc.blockSignals(False)
        self._layout_text()

    def _layout_text(self):
        ti = self.text_item
        if ti is None:
            return
        margin = 7.0
        avail_w = max(10.0, self._w - 2 * margin)
        ti.setScale(1.0)
        ti.setTextWidth(avail_w)
        doc_h = ti.document().size().height()
        avail_h = max(10.0, self._h - 2 * margin)
        scale = 1.0
        if self.model.autofit and doc_h > avail_h and ti.textInteractionFlags() == Qt.NoTextInteraction:
            for _ in range(12):
                scale *= 0.9
                ti.setScale(scale)
                ti.setTextWidth(avail_w / scale)
                if ti.document().size().height() * scale <= avail_h:
                    break
            doc_h = ti.document().size().height()
        h = doc_h * scale
        va = self.model.valign if self.model.kind == "text" else (self.model.valign or "middle")
        if self.model.kind != "text" and va == "top":
            va = "middle"
        y = margin if va == "top" else (self._h - h) / 2 if va == "middle" else self._h - margin - h
        ti.setPos(margin, max(0.0 if self.model.kind != "text" else margin, y))
        self.update()

    def start_text_edit(self):
        if self.text_item is None:
            return
        self.text_item.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.text_item.setFocus()
        c = self.text_item.textCursor()
        if not self.model.text.strip():
            c.select(QTextCursor.Document)
        else:
            c.movePosition(QTextCursor.End)
        self.text_item.setTextCursor(c)
        self.update()

    def end_text_edit(self):
        if self.text_item is None or self.text_item.textInteractionFlags() == Qt.NoTextInteraction:
            return False
        self.text_item.setTextInteractionFlags(Qt.NoTextInteraction)
        c = self.text_item.textCursor()
        c.clearSelection()
        self.text_item.setTextCursor(c)
        new = read_document(self.text_item.document())
        changed = [p.text for p in new] != [p.text for p in self.model.paragraphs] or new != self.model.paragraphs
        self.model.paragraphs = new
        self._layout_text()
        if changed:
            self.textEdited.emit(self)
        return changed

    def is_editing_text(self) -> bool:
        return self.text_item is not None and self.text_item.textInteractionFlags() != Qt.NoTextInteraction

    # -- geometry
    def size(self) -> QSizeF:
        return QSizeF(self._w, self._h)

    def set_geometry(self, rect: QRectF):
        self.prepareGeometryChange()
        self._w, self._h = max(4.0, rect.width()), max(4.0, rect.height())
        self.setPos(rect.topLeft())
        self.setTransformOriginPoint(self._w / 2, self._h / 2)
        self._pix = None
        self._layout_text()

    def sync_model(self):
        s = self.model
        p = self.pos()
        if s.kind in ("line", "arrow_line"):
            sx = 1 if s.w >= 0 else -1
            sy = 1 if s.h >= 0 else -1
            x0 = p.x() if sx > 0 else p.x() + self._w
            y0 = p.y() if sy > 0 else p.y() + self._h
            s.x, s.y, s.w, s.h = emu(x0), emu(y0), emu(self._w) * sx, emu(self._h) * sy
        else:
            s.x, s.y, s.w, s.h = emu(p.x()), emu(p.y()), emu(self._w), emu(self._h)
        s.rotation = round(self.rotation(), 2) % 360

    def boundingRect(self) -> QRectF:
        m = max(2.0, self.model.line_width * PT_TO_PX)
        return QRectF(-m, -m, self._w + 2 * m, self._h + 2 * m)

    def shapeRect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def shape(self) -> QPainterPath:  # noqa: D401 - Qt API (hit testing)
        p = QPainterPath()
        if self.model.kind in ("line", "arrow_line"):
            p.addRect(QRectF(-4, -4, self._w + 8, self._h + 8))
        else:
            p.addRect(self.shapeRect())
        return p

    # -- painting
    def paint(self, painter: QPainter, option, widget=None):
        s = self.model
        w, h = self._w, self._h
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if self.reveal < 1.0:
            r = {"left": QRectF(0, 0, w * self.reveal, h), "right": QRectF(w * (1 - self.reveal), 0, w * self.reveal, h),
                 "top": QRectF(0, 0, w, h * self.reveal), "bottom": QRectF(0, h * (1 - self.reveal), w, h * self.reveal)}
            painter.setClipRect(r.get(self.reveal_dir, r["left"]).adjusted(-4, -4, 4, 4))
        if s.kind in ("line", "arrow_line"):
            pen = QPen(QColor(s.line or "#404040"), max(1.0, s.line_width * PT_TO_PX))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            a = QPointF(0 if s.w >= 0 else w, 0 if s.h >= 0 else h)
            b = QPointF(w if s.w >= 0 else 0, h if s.h >= 0 else 0)
            painter.drawLine(a, b)
            if s.kind == "arrow_line":
                ang = math.atan2(b.y() - a.y(), b.x() - a.x())
                L = 8 + pen.widthF() * 2.5
                p1 = b - QPointF(L * math.cos(ang - 0.4), L * math.sin(ang - 0.4))
                p2 = b - QPointF(L * math.cos(ang + 0.4), L * math.sin(ang + 0.4))
                painter.setBrush(QColor(s.line or "#404040"))
                painter.drawPolygon(QPolygonF([b, p1, p2]))
        elif s.kind == "image":
            if self._pix is None and s.image:
                self._pix = QPixmap()
                self._pix.loadFromData(s.image)
            if self._pix is not None and not self._pix.isNull():
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                painter.drawPixmap(self.shapeRect(), self._pix, QRectF(self._pix.rect()))
            else:
                painter.fillRect(self.shapeRect(), QColor("#eeeeee"))
        elif s.kind == "table":
            self._paint_table(painter)
        elif s.kind == "chart":
            key = (int(w), int(h), repr(s.chart))
            if self._chart_key != key:
                from app.sheet.charts import render_chart_dict_image

                self._pix = QPixmap.fromImage(render_chart_dict_image(s.chart or {}, (max(80, int(w)), max(60, int(h)))))
                self._chart_key = key
            painter.drawPixmap(self.shapeRect(), self._pix, QRectF(self._pix.rect()))
        else:
            path = shape_path(s.kind, w, h)
            if s.shadow and (s.fill or s.kind != "text"):  # text without a fill: the letters cast the shadow
                painter.fillPath(path.translated(4, 5), QColor(0, 0, 0, 50))
            if s.fill:
                c = QColor(s.fill)
                c.setAlphaF(max(0.0, min(1.0, s.opacity)))
                painter.fillPath(path, c)
            if s.line:
                painter.setPen(QPen(QColor(s.line), max(0.5, s.line_width * PT_TO_PX)))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(path)
            elif self.editing and s.kind == "text" and (s.placeholder or not s.text.strip()):
                painter.setPen(QPen(QColor("#b0b4ba"), 1, Qt.DashLine))
                painter.drawRect(self.shapeRect().adjusted(0.5, 0.5, -0.5, -0.5))
        if self.editing and self.text_item is not None and not s.text.strip() and not self.is_editing_text():
            hint = PLACEHOLDER_HINT.get(s.placeholder or "", "")
            if hint:
                painter.setPen(QColor("#9aa0a6"))
                f = QFont(self.theme.body_font)
                size = (s.paragraphs[0].runs[0].size if s.paragraphs and s.paragraphs[0].runs and
                        s.paragraphs[0].runs[0].size else 24) * PT_TO_PX
                f.setPixelSize(int(min(size, h * 0.6)))
                painter.setFont(f)
                al = {"center": Qt.AlignHCenter, "right": Qt.AlignRight}.get(
                    s.paragraphs[0].align if s.paragraphs else "left", Qt.AlignLeft)
                painter.drawText(self.shapeRect().adjusted(8, 4, -8, -4), int(al | Qt.AlignVCenter | Qt.TextWordWrap), hint)
        painter.restore()

    def _paint_table(self, painter: QPainter):
        s = self.model
        rows = s.table or [[""]]
        nr, nc = len(rows), max(len(r) for r in rows)
        w, h = self._w, self._h
        cw, rh = w / nc, h / nr
        accent = QColor(s.table_style.get("color", self.theme.accent))
        f = QFont(self.theme.body_font)
        f.setPixelSize(max(8, int(min(rh * 0.42, 14 * PT_TO_PX))))
        for i, row in enumerate(rows):
            for j in range(nc):
                rect = QRectF(j * cw, i * rh, cw, rh)
                if i == 0 and s.table_style.get("header", True):
                    painter.fillRect(rect, accent)
                    painter.setPen(QColor("#ffffff"))
                    bf = QFont(f)
                    bf.setBold(True)
                    painter.setFont(bf)
                else:
                    if s.table_style.get("banded", True):
                        c = QColor(accent)
                        c.setAlpha(45 if i % 2 else 22)
                        painter.fillRect(rect, c)
                    painter.setPen(QColor(self.theme.text_color))
                    painter.setFont(f)
                text = str(row[j]) if j < len(row) else ""
                painter.drawText(rect.adjusted(6, 2, -6, -2), int(Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap), text)
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        for j in range(1, nc):
            painter.drawLine(QPointF(j * cw, 0), QPointF(j * cw, h))
        for i in range(1, nr):
            painter.drawLine(QPointF(0, i * rh), QPointF(w, i * rh))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene() is not None and self.editing:
            canvas = self.scene().views()[0] if self.scene().views() else None
            if canvas is not None and hasattr(canvas, "snap_position") and self.isSelected():
                return canvas.snap_position(self, value)
        return super().itemChange(change, value)


# ============================================================================= scene building / rendering
def build_scene(pres: Presentation, slide: Slide, editing: bool = False) -> tuple[QGraphicsScene, list]:
    W, H = pres.size_px
    scene = QGraphicsScene(0, 0, W, H)
    bg = QGraphicsRectItem(0, 0, W, H)
    bg.setBrush(QColor(slide.background or pres.theme.background))
    bg.setPen(Qt.NoPen)
    bg.setZValue(-1000)
    bg.setData(0, "background")
    scene.addItem(bg)
    if slide.background_image:
        from PySide6.QtWidgets import QGraphicsPixmapItem

        pm = QPixmap()
        pm.loadFromData(slide.background_image)
        if not pm.isNull():
            pi = QGraphicsPixmapItem(pm.scaled(int(W), int(H), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            pi.setZValue(-999)
            scene.addItem(pi)
    items = []
    for z, sh in enumerate(slide.shapes):
        it = ShapeItem(sh, pres.theme, editing)
        it.setZValue(z)
        scene.addItem(it)
        items.append(it)
    return scene, items


def render_slide(pres: Presentation, slide: Slide, width: int = 320) -> QImage:
    W, H = pres.size_px
    height = max(1, int(width * H / W))
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.white)
    scene, _items = build_scene(pres, slide, False)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    scene.render(p, QRectF(0, 0, width, height), QRectF(0, 0, W, H))
    p.end()
    return img


def export_pdf(pres: Presentation, path: str, title: str = "") -> int:
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

    W, H = pres.size_px
    writer = QPdfWriter(path)
    writer.setResolution(96)
    writer.setTitle(title)
    writer.setCreator("MyOffice Presentation")
    writer.setPageLayout(QPageLayout(QPageSize(QSizeF(W * 72 / 96, H * 72 / 96), QPageSize.Point), QPageLayout.Portrait,
                                     QMarginsF(0, 0, 0, 0)))
    p = QPainter(writer)
    count = 0
    try:
        for s in pres.slides:
            if s.hidden:
                continue
            if count:
                writer.newPage()
            scene, _ = build_scene(pres, s, False)
            target = writer.pageLayout().paintRectPixels(96)
            scene.render(p, QRectF(0, 0, target.width(), target.height()), QRectF(0, 0, W, H))
            count += 1
    finally:
        p.end()
    return count


# ============================================================================= editing canvas
class SlideCanvas(QGraphicsView):
    changed = Signal(str)               # description (for undo)
    selectionChangedSig = Signal()
    insertFinished = Signal()

    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.setObjectName("PageCanvas")
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setAlignment(Qt.AlignCenter)
        self.items_: list[ShapeItem] = []
        self.slide: Slide | None = None
        self.zoom = 1.0
        self._drag = None
        self._guides: list = []
        self.insert_kind: str | None = None
        self._insert_start = None
        self._press_state = None

    # -- loading
    def show_slide(self, slide: Slide):
        self.slide = slide
        scene, items = build_scene(self.doc.pres, slide, True)
        scene.setSceneRect(QRectF(-60, -40, scene.width() + 120, scene.height() + 80))
        old = self.scene()
        if old is not None:
            old.blockSignals(True)
        self.setScene(scene)
        if old is not None:
            old.deleteLater()
        self.items_ = items
        for it in items:
            it.textEdited.connect(lambda _it: self.changed.emit("Edit Text"))
        scene.selectionChanged.connect(self.selectionChangedSig.emit)
        self.fit()

    def fit(self):
        W, H = self.doc.pres.size_px
        vw, vh = self.viewport().width() - 40, self.viewport().height() - 40
        if vw <= 0 or vh <= 0:
            return
        self.set_zoom(min(vw / W, vh / H))

    def set_zoom(self, z: float):
        self.zoom = max(0.1, min(5.0, z))
        self.setTransform(QTransform.fromScale(self.zoom, self.zoom))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_auto_fit", True):
            self.fit()

    def selected_items(self) -> list[ShapeItem]:
        import shiboken6

        return [it for it in self.items_ if shiboken6.isValid(it) and it.isSelected()]

    def item_for(self, shape) -> ShapeItem | None:
        return next((it for it in self.items_ if it.model is shape), None)

    def commit_text_edits(self) -> bool:
        changed = False
        for it in self.items_:
            if it.is_editing_text():
                changed = it.end_text_edit() or changed
        return changed

    # -- background / overlay painting
    def drawForeground(self, p: QPainter, rect):
        p.save()
        for it in self.selected_items():
            r = it.shapeRect()
            poly = it.mapToScene(r)
            p.setPen(QPen(QColor("#1a73e8"), 1.2 / self.zoom, Qt.DashLine if it.is_editing_text() else Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(poly)
            if len(self.selected_items()) == 1 and not it.is_editing_text():
                hs = HANDLE / self.zoom
                p.setPen(QPen(QColor("#1a73e8"), 1 / self.zoom))
                p.setBrush(QColor("#ffffff"))
                for name, pt in self._handle_points(it).items():
                    if name == "rot":
                        p.setBrush(QColor("#34a853"))
                        p.drawEllipse(pt, hs / 1.6, hs / 1.6)
                        p.setBrush(QColor("#ffffff"))
                    else:
                        p.drawRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs))
        if self._guides:
            p.setPen(QPen(QColor("#e8590c"), 1 / self.zoom, Qt.DashLine))
            for (a, b) in self._guides:
                p.drawLine(a, b)
        if self._insert_start is not None and self._drag is not None and self._drag.get("rect"):
            p.setPen(QPen(QColor("#1a73e8"), 1 / self.zoom, Qt.DashLine))
            p.drawRect(self._drag["rect"])
        p.restore()

    def _handle_points(self, it: ShapeItem) -> dict:
        w, h = it._w, it._h
        pts = {"tl": QPointF(0, 0), "t": QPointF(w / 2, 0), "tr": QPointF(w, 0), "r": QPointF(w, h / 2),
               "br": QPointF(w, h), "b": QPointF(w / 2, h), "bl": QPointF(0, h), "l": QPointF(0, h / 2)}
        out = {k: it.mapToScene(v) for k, v in pts.items()}
        if it.model.kind not in ("line", "arrow_line"):
            out["rot"] = it.mapToScene(QPointF(w / 2, -22 / self.zoom))
        return out

    def _handle_at(self, scene_pt: QPointF):
        sel = self.selected_items()
        if len(sel) != 1 or sel[0].is_editing_text():
            return None, None
        it = sel[0]
        tol = (HANDLE + 3) / self.zoom
        for name, pt in self._handle_points(it).items():
            if abs(pt.x() - scene_pt.x()) <= tol and abs(pt.y() - scene_pt.y()) <= tol:
                return it, name
        return None, None

    # -- snapping
    def snap_position(self, item: ShapeItem, value: QPointF) -> QPointF:
        if self._drag is not None and self._drag.get("mode") not in (None, "move"):
            return value
        W, H = self.doc.pres.size_px
        tol = 6 / self.zoom
        w, h = item._w, item._h
        xs = [0, W / 2, W]
        ys = [0, H / 2, H]
        for other in self.items_:
            if other is item or other.isSelected():
                continue
            o = other.pos()
            xs += [o.x(), o.x() + other._w / 2, o.x() + other._w]
            ys += [o.y(), o.y() + other._h / 2, o.y() + other._h]
        x, y = value.x(), value.y()
        guides = []
        for cand_off in (0, w / 2, w):
            for gx in xs:
                if abs(x + cand_off - gx) < tol:
                    x = gx - cand_off
                    guides.append((QPointF(gx, -2000), QPointF(gx, 4000)))
                    break
            else:
                continue
            break
        for cand_off in (0, h / 2, h):
            for gy in ys:
                if abs(y + cand_off - gy) < tol:
                    y = gy - cand_off
                    guides.append((QPointF(-4000, gy), QPointF(8000, gy)))
                    break
            else:
                continue
            break
        self._guides = guides
        self.viewport().update()
        return QPointF(x, y)

    # -- mouse
    def mousePressEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        if e.button() == Qt.LeftButton and self.insert_kind:
            self._insert_start = sp
            self._drag = {"mode": "insert", "rect": QRectF(sp, sp)}
            return
        if e.button() == Qt.LeftButton:
            it, handle = self._handle_at(sp)
            if it is not None:
                self._drag = {"mode": handle, "item": it, "start": sp, "rect": QRectF(it.pos(), it.size()),
                              "rot": it.rotation()}
                return
            hit = self.itemAt(e.position().toPoint())
            target = hit
            while target is not None and not isinstance(target, ShapeItem):
                target = target.parentItem()
            for other in self.items_:
                if other is not target and other.is_editing_text():
                    if other.end_text_edit():
                        pass
            if isinstance(target, ShapeItem) and target.is_editing_text():
                super().mousePressEvent(e)
                return
            self._press_state = {it_.model.id: (it_.pos(), it_.size()) for it_ in self.items_}
            self._drag = {"mode": "move"}
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        d = self._drag
        if d and d["mode"] == "insert":
            d["rect"] = QRectF(self._insert_start, sp).normalized()
            self.viewport().update()
            return
        if d and d["mode"] not in ("move",):
            it: ShapeItem = d["item"]
            if d["mode"] == "rot":
                c = it.mapToScene(QPointF(it._w / 2, it._h / 2))
                ang = math.degrees(math.atan2(sp.y() - c.y(), sp.x() - c.x())) + 90
                if e.modifiers() & Qt.ShiftModifier:
                    ang = round(ang / 15) * 15
                it.setRotation(ang)
                self.viewport().update()
                return
            r = QRectF(d["rect"])
            local = sp - d["start"]
            m = d["mode"]
            if "l" in m:
                r.setLeft(min(r.right() - 4, r.left() + local.x()))
            if "r" in m and m != "rot":
                r.setRight(max(r.left() + 4, r.right() + local.x()))
            if m.startswith("t"):
                r.setTop(min(r.bottom() - 4, r.top() + local.y()))
            if m.startswith("b"):
                r.setBottom(max(r.top() + 4, r.bottom() + local.y()))
            if e.modifiers() & Qt.ShiftModifier and m in ("tl", "tr", "bl", "br") and d["rect"].height():
                ratio = d["rect"].width() / d["rect"].height()
                r.setHeight(r.width() / ratio)
            it.set_geometry(r)
            self.viewport().update()
            return
        super().mouseMoveEvent(e)
        if d and d["mode"] == "move":
            self.viewport().update()
        else:
            it, handle = self._handle_at(sp)
            cursors = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                       "bl": Qt.SizeBDiagCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "l": Qt.SizeHorCursor,
                       "r": Qt.SizeHorCursor, "rot": Qt.CrossCursor}
            self.viewport().setCursor(cursors[handle] if handle else (Qt.CrossCursor if self.insert_kind else Qt.ArrowCursor))

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        self._guides = []
        if d and d["mode"] == "insert":
            rect = d["rect"]
            kind = self.insert_kind
            self.insert_kind = None
            self._insert_start = None
            if rect.width() < 8 or rect.height() < 8:
                W, H = self.doc.pres.size_px
                dw, dh = (W * 0.3, H * 0.12) if kind == "text" else (W * 0.18, H * 0.25)
                if kind in ("line", "arrow_line"):
                    dw, dh = W * 0.2, 0
                rect = QRectF(rect.topLeft(), QSizeF(dw, dh))
            self.doc.add_shape_at(kind, rect)
            self.insertFinished.emit()
            self.viewport().update()
            return
        if d and d["mode"] != "move":
            d["item"].sync_model()
            self.changed.emit("Rotate" if d["mode"] == "rot" else "Resize")
            self.viewport().update()
            return
        super().mouseReleaseEvent(e)
        if d and d.get("mode") == "move" and self._press_state is not None:
            moved = False
            for it in self.items_:
                old = self._press_state.get(it.model.id)
                if old is not None and old[0] != it.pos():
                    it.sync_model()
                    moved = True
            self._press_state = None
            if moved:
                self.changed.emit("Move")
        self.viewport().update()

    def mouseDoubleClickEvent(self, e):
        hit = self.itemAt(e.position().toPoint())
        while hit is not None and not isinstance(hit, ShapeItem):
            hit = hit.parentItem()
        if isinstance(hit, ShapeItem):
            if hit.text_item is not None:
                self.scene().clearSelection()
                hit.setSelected(True)
                hit.start_text_edit()
                self.viewport().update()
                return
            if hit.model.kind == "chart":
                self.doc.edit_chart(hit)
                return
            if hit.model.kind == "table":
                self.doc.edit_table(hit)
                return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        editing = any(it.is_editing_text() for it in self.items_)
        if editing:
            if e.key() == Qt.Key_Escape:
                self.commit_text_edits()
                self.viewport().update()
                return
            super().keyPressEvent(e)
            return
        sel = self.selected_items()
        k = e.key()
        if k in (Qt.Key_Delete, Qt.Key_Backspace) and sel:
            self.doc.delete_selected()
            return
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and sel:
            step = 10 if e.modifiers() & Qt.ShiftModifier else 1
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0)
            for it in sel:
                it.setPos(it.pos() + QPointF(dx, dy))
                it.sync_model()
            self._guides = []
            self.changed.emit("Nudge")
            return
        if k == Qt.Key_Escape:
            self.insert_kind = None
            self.scene().clearSelection()
            return
        if k in (Qt.Key_F2, Qt.Key_Return) and len(sel) == 1 and sel[0].text_item is not None:
            sel[0].start_text_edit()
            self.viewport().update()
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self._auto_fit = False
            self.set_zoom(self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
            return
        super().wheelEvent(e)


__all__ = ["SlideCanvas", "ShapeItem", "build_scene", "render_slide", "export_pdf", "fill_document", "read_document",
           "QBrush"]
