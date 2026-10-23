"""Print / PDF rendering of sheets (pagination of the used range, fit-to-width)."""
from __future__ import annotations

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen

from app.sheet.formula.values import XLError
from app.sheet.numfmt import format_value


def _sheet_extent(sh):
    r2, c2 = max(1, sh.max_row), max(1, sh.max_col)
    for ch in sh.charts:
        r2 = max(r2, ch.anchor[0] + int(ch.size[1] / max(1, sh.default_row_height)))
        c2 = max(c2, ch.anchor[1] + int(ch.size[0] / max(1, sh.default_col_width)))
    for (a, b, c, d) in sh.merges:
        r2, c2 = max(r2, c), max(c2, d)
    return r2, c2


def paginate(sh, page_w: float, page_h: float, fit_width: bool = True):
    """Split rows/cols into pages. Returns (scale, [ (row_start,row_end,col_start,col_end) ... ])."""
    r2, c2 = _sheet_extent(sh)
    cols = [c for c in range(1, c2 + 1) if c not in sh.hidden_cols]
    rows = [r for r in range(1, r2 + 1) if r not in sh.hidden_rows and r not in sh.filtered_rows]
    total_w = sum(sh.col_width(c) for c in cols)
    scale = min(1.0, page_w / total_w) if fit_width and total_w else 1.0
    scale = max(scale, 0.35)
    col_groups, cur, w = [], [], 0.0
    for c in cols:
        cw = sh.col_width(c) * scale
        if cur and w + cw > page_w:
            col_groups.append(cur)
            cur, w = [], 0.0
        cur.append(c)
        w += cw
    if cur:
        col_groups.append(cur)
    row_groups, cur, h = [], [], 0.0
    for r in rows:
        rh = sh.row_height(r) * scale
        if cur and h + rh > page_h:
            row_groups.append(cur)
            cur, h = [], 0.0
        cur.append(r)
        h += rh
    if cur:
        row_groups.append(cur)
    pages = [(rg, cg) for cg in col_groups for rg in row_groups]
    return scale, pages


def paint_page(p: QPainter, book, sh, rows, cols, scale: float, origin_x: float = 0, origin_y: float = 0,
               show_grid: bool = True, chart_cache: dict | None = None):
    styles = book.styles
    xs, x = {}, origin_x
    for c in cols:
        xs[c] = x
        x += sh.col_width(c) * scale
    ys, y = {}, origin_y
    for r in rows:
        ys[r] = y
        y += sh.row_height(r) * scale
    right, bottom = x, y
    base = QFont("DejaVu Sans")
    rowset, colset = set(rows), set(cols)
    merged_skip = set()
    merge_rects = {}
    for (a, b, c, d) in sh.merges:
        if a in rowset and b in colset:
            w = sum(sh.col_width(cc) * scale for cc in range(b, d + 1) if cc in colset)
            h = sum(sh.row_height(rr) * scale for rr in range(a, c + 1) if rr in rowset)
            merge_rects[(a, b)] = QRectF(xs[b], ys[a], w, h)
            for rr in range(a, c + 1):
                for cc in range(b, d + 1):
                    if (rr, cc) != (a, b):
                        merged_skip.add((rr, cc))
    if show_grid and sh.show_grid:
        p.setPen(QPen(QColor("#d0d4d9"), 0))
        for r in rows:
            p.drawLine(QRectF(origin_x, ys[r], right - origin_x, 0).topLeft(), QRectF(right, ys[r], 0, 0).topLeft())
        p.drawLine(origin_x, bottom, right, bottom)
        for c in cols:
            p.drawLine(xs[c], origin_y, xs[c], bottom)
        p.drawLine(right, origin_y, right, bottom)
    for r in rows:
        for c in cols:
            if (r, c) in merged_skip:
                continue
            cell = sh.cells.get((r, c))
            if cell is None:
                continue
            st = styles.get(cell.style)
            rect = merge_rects.get((r, c)) or QRectF(xs[c], ys[r], sh.col_width(c) * scale, sh.row_height(r) * scale)
            if st.fill:
                p.fillRect(rect, QColor(st.fill))
            if cell.value is not None:
                text, numcolor = format_value(cell.value, st.numfmt)
                f = QFont(base)
                if st.font:
                    f.setFamilies([st.font, "DejaVu Sans"])
                f.setPixelSize(max(4, int((st.size or 11) * 96 / 72 * scale)))
                f.setBold(st.bold)
                f.setItalic(st.italic)
                f.setUnderline(st.underline)
                f.setStrikeOut(st.strike)
                p.setFont(f)
                p.setPen(QColor(numcolor or st.color or ("#c00000" if isinstance(cell.value, XLError) else "#000000")))
                is_num = isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool)
                h = st.halign or ("right" if is_num else ("center" if isinstance(cell.value, (bool, XLError)) else "left"))
                flags = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight,
                         "justify": Qt.AlignJustify}[h]
                flags |= {"top": Qt.AlignTop, "center": Qt.AlignVCenter}.get(st.valign, Qt.AlignVCenter)
                if st.wrap:
                    flags |= Qt.TextWordWrap
                tr = rect.adjusted(3 * scale, 0, -3 * scale, 0)
                if not st.wrap and h == "left" and not is_num:
                    # overflow into empty neighbours
                    cc = c + 1
                    while cc in colset and sh.cells.get((r, cc)) is None and p.fontMetrics().horizontalAdvance(text) > tr.width():
                        tr.setRight(tr.right() + sh.col_width(cc) * scale)
                        cc += 1
                p.save()
                p.setClipRect(tr.adjusted(-1, 0, 1, 0))
                p.drawText(tr, int(flags), text)
                p.restore()
            for b, line in ((st.border_top, (rect.topLeft(), rect.topRight())),
                            (st.border_bottom, (rect.bottomLeft(), rect.bottomRight())),
                            (st.border_left, (rect.topLeft(), rect.bottomLeft())),
                            (st.border_right, (rect.topRight(), rect.bottomRight()))):
                if b is not None:
                    w = {"medium": 2, "thick": 3, "double": 2}.get(b.style, 1)
                    pen = QPen(QColor(b.color or "#000000"), w * scale)
                    if b.style == "dashed":
                        pen.setStyle(Qt.DashLine)
                    elif b.style == "dotted":
                        pen.setStyle(Qt.DotLine)
                    p.setPen(pen)
                    p.drawLine(*line)
    from app.sheet.charts import render_chart_image

    for ch in sh.charts:
        r, c = ch.anchor
        if r in rowset and c in colset:
            key = (ch.id, ch.size, ch.data, ch.kind, ch.title)
            img = (chart_cache or {}).get(key)
            if img is None:
                img = render_chart_image(book, ch)
                if chart_cache is not None:
                    chart_cache[key] = img
            p.drawImage(QRectF(xs[c], ys[r], ch.size[0] * scale, ch.size[1] * scale), img)
    for im in sh.images:
        r, c = im.anchor
        if r in rowset and c in colset:
            p.drawImage(QRectF(xs[c], ys[r], im.size[0] * scale, im.size[1] * scale), QImage.fromData(im.data))


def export_pdf(book, path: str, sheets=None, landscape: bool | None = None, title: str = "") -> int:
    sheets = sheets or [s for s in book.sheets if s.cells or s.charts]
    if not sheets:
        sheets = book.sheets[:1]
    writer = QPdfWriter(path)
    writer.setResolution(96)
    writer.setTitle(title)
    writer.setCreator("MyOffice Spreadsheets")
    first_wide = sum(sheets[0].col_width(c) for c in range(1, max(1, sheets[0].max_col) + 1)) > 760
    orient = QPageLayout.Landscape if (landscape if landscape is not None else first_wide) else QPageLayout.Portrait
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), orient, QMarginsF(12, 12, 12, 12), QPageLayout.Millimeter))
    p = QPainter(writer)
    count = 0
    cache: dict = {}
    try:
        page_rect = writer.pageLayout().paintRectPixels(96)
        pw, ph = page_rect.width(), page_rect.height() - 24
        for si, sh in enumerate(sheets):
            scale, pages = paginate(sh, pw, ph)
            for i, (rows, cols) in enumerate(pages):
                if count:
                    writer.newPage()
                paint_page(p, book, sh, rows, cols, scale, 0, 0, True, cache)
                p.setPen(QColor("#80868b"))
                f = QFont("DejaVu Sans")
                f.setPixelSize(10)
                p.setFont(f)
                p.drawText(QRectF(0, ph + 6, pw, 16), Qt.AlignCenter,
                           f"{sh.name} \u2014 {i + 1}/{len(pages)}")
                count += 1
    finally:
        p.end()
    return count


def print_book(book, printer, sheets=None):
    sheets = sheets or [s for s in book.sheets if s.cells or s.charts] or book.sheets[:1]
    p = QPainter(printer)
    try:
        res = printer.resolution()
        page = printer.pageLayout().paintRectPixels(res)
        sc = res / 96.0
        pw, ph = page.width() / sc, page.height() / sc - 24
        first = True
        cache: dict = {}
        for sh in sheets:
            scale, pages = paginate(sh, pw, ph)
            for rows, cols in pages:
                if not first:
                    printer.newPage()
                first = False
                p.save()
                p.scale(sc, sc)
                paint_page(p, book, sh, rows, cols, scale, 0, 0, True, cache)
                p.restore()
    finally:
        p.end()


__all__ = ["export_pdf", "print_book", "paginate", "paint_page", "QSizeF"]
