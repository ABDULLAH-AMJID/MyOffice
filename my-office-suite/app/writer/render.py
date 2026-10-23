"""Rendering of Writer documents to PDF, printers and images (with headers/footers)."""
from __future__ import annotations

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import (QAbstractTextDocumentLayout, QColor, QFont, QImage, QPageLayout, QPageSize, QPainter,
                           QPalette, QPdfWriter, QTextCharFormat, QTextCursor, QTextDocument)

from app.writer.model import P_COMMENTS, P_DEL, P_INS, HeaderFooter, PageSetup
from app.writer.revisions import fragments


def prepare_clone(qdoc: QTextDocument, page: PageSetup, final: bool = True) -> QTextDocument:
    """Copy of the document laid out for the page, optionally with all revisions accepted."""
    clone = qdoc.clone()
    clone.setUseDesignMetrics(True)
    clone.setDocumentMargin(0)
    w, h = page.page_px()
    top, right, bottom, left = page.margins_px()
    clone.setPageSize(QSizeF(w, h))
    ff = clone.rootFrame().frameFormat()
    ff.setTopMargin(top)
    ff.setBottomMargin(bottom)
    ff.setLeftMargin(left)
    ff.setRightMargin(right)
    ff.setPadding(0)
    ff.setBorder(0)
    clone.rootFrame().setFrameFormat(ff)
    from app.writer import notes as _notes

    reserve = 0.0
    if _notes.has_footnotes(clone):
        reserve = _notes.reserve_px(clone, h, w - left - right, clone.defaultFont().family(),
                                    0.45 * (h - top - bottom))
        ff.setBottomMargin(bottom + reserve)
        clone.rootFrame().setFrameFormat(ff)
    clone.setProperty("notes_reserve", reserve)
    if final:
        c = QTextCursor(clone)
        c.beginEditBlock()
        for pos, length, fmt in reversed(list(fragments(clone))):
            if fmt.hasProperty(P_DEL) or fmt.hasProperty(P_INS) or fmt.hasProperty(P_COMMENTS):
                c.setPosition(pos)
                c.setPosition(pos + length, QTextCursor.KeepAnchor)
                if fmt.hasProperty(P_DEL):
                    c.removeSelectedText()
                else:
                    f = QTextCharFormat(fmt)
                    f.clearProperty(P_INS)
                    f.clearProperty(P_COMMENTS)
                    c.setCharFormat(f)
        c.endEditBlock()
    return clone


def draw_watermark(painter: QPainter, w: float, h: float, text: str, y0: float = 0.0) -> None:
    if not text:
        return
    painter.save()
    painter.translate(w / 2, y0 + h / 2)
    painter.rotate(-45)
    font = QFont()
    font.setPixelSize(max(12, int(min(w, h) / max(6, len(text) * 0.75))))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(160, 160, 160, 110))
    painter.drawText(QRectF(-w, -h / 4, 2 * w, h / 2), Qt.AlignCenter, text)
    painter.restore()


def _paint_page(painter: QPainter, clone: QTextDocument, page: PageSetup, hf: HeaderFooter, title: str,
                index: int, count: int, scale: float, watermark: str = "") -> None:
    w, h = page.page_px()
    top, right, bottom, left = page.margins_px()
    painter.save()
    painter.scale(scale, scale)
    draw_watermark(painter, w, h, watermark)
    painter.save()
    painter.translate(0, -index * h)
    ctx = QAbstractTextDocumentLayout.PaintContext()
    ctx.clip = QRectF(0, index * h, w, h)
    pal = QPalette()
    pal.setColor(QPalette.Text, QColor("#000000"))
    ctx.palette = pal
    painter.setClipRect(ctx.clip)
    clone.documentLayout().draw(painter, ctx)
    painter.restore()
    font = QFont(clone.defaultFont().family())
    font.setPixelSize(12)  # 9pt in the 96-dpi layout space (the painter is already scaled)
    painter.setFont(font)
    painter.setPen(QColor("#444444"))
    tw = w - left - right
    align = {"left": Qt.AlignLeft | Qt.AlignAbsolute, "right": Qt.AlignRight | Qt.AlignAbsolute}
    if hf.header:
        r = QRectF(left, page.header_mm * 96 / 25.4, tw, 20)
        painter.drawText(r, align.get(hf.header_align, Qt.AlignHCenter) | Qt.AlignTop,
                         hf.render(hf.header, index + 1, count, title))
    if hf.footer:
        r = QRectF(left, h - page.footer_mm * 96 / 25.4 - 20, tw, 20)
        painter.drawText(r, align.get(hf.footer_align, Qt.AlignHCenter) | Qt.AlignBottom,
                         hf.render(hf.footer, index + 1, count, title))
    reserve = float(clone.property("notes_reserve") or 0.0)
    if reserve:
        from app.writer.notes import draw_notes, notes_by_page

        page_notes = notes_by_page(clone, h).get(index)
        if page_notes:
            draw_notes(painter, page_notes, left, h - bottom - reserve, tw, clone.defaultFont().family())
    painter.restore()


def export_pdf(qdoc: QTextDocument, page: PageSetup, hf: HeaderFooter, path: str, title: str = "",
               author: str = "", watermark: str = "") -> int:
    clone = prepare_clone(qdoc, page)
    wmm, hmm = page.page_mm()
    writer = QPdfWriter(path)
    writer.setPageLayout(QPageLayout(QPageSize(QSizeF(wmm, hmm), QPageSize.Millimeter), QPageLayout.Portrait,
                                     QMarginsF(0, 0, 0, 0)))
    writer.setResolution(1200)
    writer.setTitle(title or "")
    writer.setCreator("MyOffice Writer")
    try:
        writer.setAuthor(author or "")
    except AttributeError:
        pass
    painter = QPainter()
    if not painter.begin(writer):
        raise OSError(f"Cannot write PDF file: {path}")
    count = clone.pageCount()
    scale = writer.resolution() / 96.0
    try:
        for i in range(count):
            if i:
                writer.newPage()
            _paint_page(painter, clone, page, hf, title, i, count, scale, watermark)
    finally:
        painter.end()
    return count


def print_document(qdoc: QTextDocument, page: PageSetup, hf: HeaderFooter, printer, title: str = "",
                   watermark: str = "") -> None:
    clone = prepare_clone(qdoc, page)
    wmm, hmm = page.page_mm()
    printer.setFullPage(True)
    printer.setPageLayout(QPageLayout(QPageSize(QSizeF(wmm, hmm), QPageSize.Millimeter), QPageLayout.Portrait,
                                      QMarginsF(0, 0, 0, 0)))
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("Cannot start printing")
    count = clone.pageCount()
    first, last = 1, count
    if printer.fromPage() > 0:
        first, last = max(1, printer.fromPage()), min(count, printer.toPage() or count)
    scale = printer.resolution() / 96.0
    try:
        for n, i in enumerate(range(first - 1, last)):
            if n:
                printer.newPage()
            _paint_page(painter, clone, page, hf, title, i, count, scale, watermark)
    finally:
        painter.end()


def page_image(qdoc: QTextDocument, page: PageSetup, hf: HeaderFooter, index: int = 0, width: int = 360,
               title: str = "", watermark: str = "") -> QImage:
    clone = prepare_clone(qdoc, page)
    w, h = page.page_px()
    scale = width / w
    img = QImage(int(w * scale), int(h * scale), QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    _paint_page(p, clone, page, hf, title, min(index, clone.pageCount() - 1), clone.pageCount(), scale, watermark)
    p.end()
    return img
