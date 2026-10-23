"""Qt adapter for a sheet: table model (XFD x 1,048,576) and a cell delegate that paints Excel-style cells."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QComboBox, QLineEdit, QStyle, QStyledItemDelegate

from app.sheet.formula.tokenizer import MAX_COLS, MAX_ROWS, index_to_col
from app.sheet.formula.values import XLError, compare
from app.sheet.model import SheetData, Style, edit_text
from app.sheet.numfmt import format_value

ROLE_STYLE = Qt.UserRole + 1
ROLE_RAW = Qt.UserRole + 2


class SheetModel(QAbstractTableModel):
    def __init__(self, doc, sheet: SheetData):
        super().__init__()
        self.doc = doc
        self.sheet = sheet
        self._fonts: dict = {}
        self._cf_cache: dict | None = None
        self._overflow: dict = {}

    # -- dimensions
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else MAX_ROWS

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else MAX_COLS

    def set_sheet(self, sheet: SheetData):
        self.beginResetModel()
        self.sheet = sheet
        self.invalidate()
        self.endResetModel()

    def invalidate(self):
        self._cf_cache = None
        self._overflow.clear()

    def cells_changed(self, keys=None):
        """Notify views that cell contents changed (keys: iterable of (row, col) or None for all)."""
        self.invalidate()
        if not keys:
            self.dataChanged.emit(self.index(0, 0), self.index(min(MAX_ROWS - 1, self.sheet.max_row + 50),
                                                               min(MAX_COLS - 1, self.sheet.max_col + 30)))
            return
        rows = [k[0] for k in keys]
        cols = [k[1] for k in keys]
        self.dataChanged.emit(self.index(min(rows) - 1, 0), self.index(max(rows) - 1, min(MAX_COLS - 1, max(cols) + 20)))

    # -- styles
    def font_for(self, st: Style, base: QFont, extra_bold: bool = False) -> QFont:
        zoom = getattr(self.doc, "zoom", 1.0)
        key = (st.font, st.size, st.bold or extra_bold, st.italic, st.underline, st.strike, base.family(), zoom)
        f = self._fonts.get(key)
        if f is None:
            f = QFont(base)
            f.setFamilies([st.font or "Calibri", base.family()])
            f.setPointSizeF((st.size or 11) * zoom)
            f.setBold(st.bold or extra_bold)
            f.setItalic(st.italic)
            f.setUnderline(st.underline)
            f.setStrikeOut(st.strike)
            self._fonts[key] = f
        return f

    # -- conditional formatting
    def _cf_stats(self):
        if self._cf_cache is not None:
            return self._cf_cache
        cache = {}
        sh = self.sheet
        for i, cf in enumerate(sh.cond_formats):
            r1, c1, r2, c2 = cf.range
            r2, c2 = min(r2, max(sh.max_row, r1)), min(c2, max(sh.max_col, c1))
            vals = []
            for (r, c), cell in sh.cells.items():
                if r1 <= r <= r2 and c1 <= c <= c2 and cell.value is not None:
                    vals.append(cell.value)
            nums = sorted(float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
            st = {"min": nums[0] if nums else 0.0, "max": nums[-1] if nums else 0.0}
            if cf.kind in ("top", "bottom") and nums:
                k = max(1, int(float(cf.value1 or 10)))
                st["threshold"] = nums[-k] if cf.kind == "top" and k <= len(nums) else (
                    nums[k - 1] if cf.kind == "bottom" and k <= len(nums) else (nums[0] if cf.kind == "top" else nums[-1]))
            if cf.kind in ("duplicate", "unique"):
                counts: dict = {}
                for v in vals:
                    key = v.lower() if isinstance(v, str) else v
                    counts[key] = counts.get(key, 0) + 1
                st["counts"] = counts
            if nums:
                st["mid"] = nums[len(nums) // 2] if len(nums) % 2 else (nums[len(nums) // 2 - 1] + nums[len(nums) // 2]) / 2
            cache[i] = st
        self._cf_cache = cache
        return cache

    def cond_format(self, r: int, c: int, value):
        """Return dict(fill, color, bold, bar) for conditional formats applying to the cell, or None."""
        sh = self.sheet
        if not sh.cond_formats:
            return None
        out = None
        stats = None
        for i, cf in enumerate(sh.cond_formats):
            r1, c1, r2, c2 = cf.range
            if not (r1 <= r <= r2 and c1 <= c <= c2):
                continue
            if stats is None:
                stats = self._cf_stats()
            st = stats.get(i, {})
            hit = False
            res = {}
            num = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            try:
                if cf.kind == "cell" and value is not None:
                    a = _cf_operand(cf.value1, sh)
                    b = _cf_operand(cf.value2, sh)
                    if cf.op == "between":
                        hit = compare(value, a) >= 0 and compare(value, b) <= 0
                    else:
                        cmpv = compare(value, a)
                        hit = {">": cmpv > 0, "<": cmpv < 0, ">=": cmpv >= 0, "<=": cmpv <= 0, "=": cmpv == 0,
                               "<>": cmpv != 0}[cf.op]
                elif cf.kind == "text" and value is not None:
                    hit = cf.value1.lower() in str(value).lower()
                elif cf.kind == "top" and num is not None and "threshold" in st:
                    hit = num >= st["threshold"]
                elif cf.kind == "bottom" and num is not None and "threshold" in st:
                    hit = num <= st["threshold"]
                elif cf.kind in ("duplicate", "unique") and value is not None:
                    key = value.lower() if isinstance(value, str) else value
                    n = st.get("counts", {}).get(key, 0)
                    hit = n > 1 if cf.kind == "duplicate" else n == 1
                elif cf.kind in ("scale2", "scale3") and num is not None:
                    lo, hi = st["min"], st["max"]
                    t = 0.0 if hi == lo else (num - lo) / (hi - lo)
                    cols = cf.colors
                    if cf.kind == "scale2" or len(cols) < 3:
                        res["fill"] = _mix(cols[0], cols[-1], t)
                    else:
                        mid = st.get("mid", (lo + hi) / 2)
                        if num <= mid:
                            tt = 0.0 if mid == lo else (num - lo) / (mid - lo)
                            res["fill"] = _mix(cols[0], cols[1], tt)
                        else:
                            tt = 0.0 if hi == mid else (num - mid) / (hi - mid)
                            res["fill"] = _mix(cols[1], cols[2], tt)
                    hit = True
                elif cf.kind == "databar" and num is not None:
                    lo, hi = min(0.0, st["min"]), st["max"]
                    res["bar"] = (0.0 if hi == lo else (num - lo) / (hi - lo), cf.fill or "#638ec6")
                    hit = True
                elif cf.kind == "formula" and cf.value1:
                    hit = self._formula_rule(cf, r, c)
            except XLError:
                hit = False
            if hit:
                if cf.kind not in ("scale2", "scale3", "databar"):
                    res = {"fill": cf.fill, "color": cf.color, "bold": cf.bold}
                out = dict(out or {}, **{k: v for k, v in res.items() if v is not None})
        return out

    def _formula_rule(self, cf, r, c) -> bool:
        from app.sheet.formula.engine import compile_formula
        from app.sheet.formula.parser import translate_formula
        from app.sheet.formula.values import to_bool

        text = cf.value1 if cf.value1.startswith("=") else "=" + cf.value1
        text = translate_formula(text, r - cf.range[0], c - cf.range[1])
        comp, _node = compile_formula(text)
        ctx = self.doc.book.engine.ctx
        saved = (ctx.sid, ctx.row, ctx.col, ctx.cells)
        ctx.sid, ctx.row, ctx.col, ctx.cells = self.sheet.id, r, c, self.sheet.cells
        try:
            return to_bool(comp.fn(ctx))
        except XLError:
            return False
        finally:
            ctx.sid, ctx.row, ctx.col, ctx.cells = saved

    # -- data
    def data(self, index, role=Qt.DisplayRole):
        r, c = index.row() + 1, index.column() + 1
        sh = self.sheet
        cell = sh.cells.get((r, c))
        if role == Qt.DisplayRole:
            if cell is None:
                return None
            if self.doc.show_formulas and cell.formula:
                return cell.formula
            return format_value(cell.value, self.doc.book.styles.get(cell.style).numfmt)[0]
        if role == Qt.EditRole:
            return edit_text(cell, self.doc.book.styles.get(cell.style if cell else 0))
        if role == Qt.ToolTipRole:
            tips = []
            if (r, c) in sh.comments:
                tips.append(sh.comments[(r, c)])
            if (r, c) in sh.links:
                tips.append(sh.links[(r, c)] + "\n(Ctrl+click to open)")
            if cell is not None and isinstance(cell.value, XLError):
                tips.append(_ERROR_HELP.get(cell.value.code, cell.value.code))
                if cell.fn is not None and cell.fn.error:
                    tips.append(cell.fn.error)
            return "\n".join(tips) if tips else None
        if role == ROLE_RAW:
            return None if cell is None else cell.value
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return index_to_col(section + 1) if orientation == Qt.Horizontal else str(section + 1)
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter)
        return None

    def flags(self, index):
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if self.sheet.protected:
            cell = self.sheet.cells.get((index.row() + 1, index.column() + 1))
            st = self.doc.book.styles.get(cell.style if cell else 0)
            if st.locked:
                return f
        return f | Qt.ItemIsEditable

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole:
            return False
        return self.doc.commit_input(index.row() + 1, index.column() + 1, "" if value is None else str(value))

    # -- overflow (text spilling into empty neighbours, like Excel)
    def overflow_span(self, r: int, c: int, view) -> int:
        key = (r, c)
        if key in self._overflow:
            return self._overflow[key]
        span = 0
        sh = self.sheet
        cell = sh.cells.get(key)
        if cell is not None and isinstance(cell.value, str) and cell.value and not self.doc.show_formulas:
            st = self.doc.book.styles.get(cell.style)
            if not st.wrap and st.halign in (None, "left") and st.rotation == 0 and sh.merge_at(r, c) is None:
                fm = QFontMetrics(self.font_for(st, view.font()))
                need = fm.horizontalAdvance(format_value(cell.value, st.numfmt)[0]) + 6 + st.indent * 10
                have = view.columnWidth(c - 1)
                cc = c
                while need > have and cc < MAX_COLS and span < 30:
                    nxt = sh.cells.get((r, cc + 1))
                    if nxt is not None and (nxt.value is not None or nxt.formula):
                        break
                    cc += 1
                    span += 1
                    have += view.columnWidth(cc - 1)
        self._overflow[key] = span
        return span

    def covering_cell(self, r: int, c: int, view):
        """If an empty cell is covered by overflowing text from the left, return (source_col, span)."""
        sh = self.sheet
        for k in range(1, 31):
            cc = c - k
            if cc < 1:
                return None
            cell = sh.cells.get((r, cc))
            if cell is not None and (cell.value is not None or cell.formula):
                span = self.overflow_span(r, cc, view)
                return (cc, span) if cc + span >= c else None
        return None


def _cf_operand(text: str, sh):
    if text is None or text == "":
        return None
    from app.sheet.model import parse_input

    v, f, _ = parse_input(text)
    if f:
        from app.sheet.formula.engine import compile_formula

        comp, _ = compile_formula(f)
        ctx = sh.book.engine.ctx
        saved = (ctx.sid, ctx.cells)
        ctx.sid, ctx.cells = sh.id, sh.cells
        try:
            from app.sheet.formula.compiler import finalize

            return finalize(comp.fn(ctx))
        finally:
            ctx.sid, ctx.cells = saved
    return v


def _mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t), int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


_ERROR_HELP = {
    "#DIV/0!": "Division by zero.", "#N/A": "A value is not available (lookup did not find a match).",
    "#VALUE!": "Wrong type of argument or operand.", "#REF!": "Invalid cell reference.",
    "#NAME?": "Unknown function or name.", "#NUM!": "Invalid numeric value.", "#NULL!": "Ranges do not intersect.",
    "#CIRC!": "Circular reference: the formula depends on its own result.", "#CALC!": "Calculation error (empty result).",
}

_PEN_STYLES = {"thin": (1, Qt.SolidLine), "medium": (2, Qt.SolidLine), "thick": (3, Qt.SolidLine),
               "dashed": (1, Qt.DashLine), "dotted": (1, Qt.DotLine), "double": (3, Qt.SolidLine)}


class CellDelegate(QStyledItemDelegate):
    def __init__(self, view, doc):
        super().__init__(view)
        self.view = view
        self.doc = doc
        self.grid_color = QColor("#e1e3e6")
        self.sel_color = QColor(26, 115, 232, 40)

    # -- editing
    def createEditor(self, parent, option, index):
        sh = self.doc.sheet
        r, c = index.row() + 1, index.column() + 1
        for v in sh.validations:
            if v.kind == "list" and v.range[0] <= r <= v.range[2] and v.range[1] <= c <= v.range[3] and v.list_items():
                cb = QComboBox(parent)
                cb.setEditable(True)
                cb.addItems(v.list_items())
                return cb
        ed = QLineEdit(parent)
        ed.setFrame(False)
        ed.setStyleSheet("QLineEdit{border:2px solid #1a73e8; padding:0 2px; background:white; color:black;}")
        ed.textEdited.connect(self.doc.editor_text_changed)
        return ed

    def setEditorData(self, editor, index):
        text = index.data(Qt.EditRole) or ""
        pending = self.doc.pending_editor_text
        if pending is not None:
            text = pending
            self.doc.pending_editor_text = None
        if isinstance(editor, QComboBox):
            editor.setCurrentText(text)
        else:
            editor.setText(text)
            if self.doc.pending_cursor_end:
                editor.end(False)

    def setModelData(self, editor, model, index):
        text = editor.currentText() if isinstance(editor, QComboBox) else editor.text()
        model.setData(index, text, Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        r = option.rect
        editor.setGeometry(QRect(r.x(), r.y(), max(r.width(), 60), r.height()))

    # -- painting
    def paint(self, p: QPainter, option, index):
        model: SheetModel = index.model()
        sh = model.sheet
        r, c = index.row() + 1, index.column() + 1
        rect = option.rect
        cell = sh.cells.get((r, c))
        styles = self.doc.book.styles
        st = styles.get(cell.style if cell else 0)
        value = cell.value if cell else None
        cf = model.cond_format(r, c, value) if sh.cond_formats else None
        p.save()
        # background
        fill = (cf or {}).get("fill") or st.fill
        if fill:
            p.fillRect(rect, QColor(fill))
        if cf and cf.get("bar"):
            frac, color = cf["bar"]
            bar = QRect(rect.x() + 1, rect.y() + 3, int((rect.width() - 2) * max(0.0, min(1.0, frac))), rect.height() - 6)
            p.fillRect(bar, QColor(color).lighter(125))
        # grid
        covered = None
        span = 0
        if cell is None or (cell.value is None and not cell.formula):
            covered = model.covering_cell(r, c, self.view) if self.doc.sheet.show_grid or True else None
        else:
            span = model.overflow_span(r, c, self.view)
        if sh.show_grid and not fill:
            p.setPen(QPen(self.grid_color, 1))
            p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            draw_right = True
            if span > 0:
                draw_right = False
            elif covered is not None and covered[0] + covered[1] > c:
                draw_right = False
            if draw_right:
                p.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        elif sh.show_grid and fill:
            p.setPen(QPen(QColor(fill).darker(108), 1))
            p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            p.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        # text
        if cell is not None and (cell.value is not None or cell.formula):
            show_f = self.doc.show_formulas and cell.formula
            text, numcolor = (cell.formula, None) if show_f else format_value(value, st.numfmt)
            font = model.font_for(st, self.view.font(), bool(cf and cf.get("bold")))
            if (r, c) in sh.links and not st.color:
                font = QFont(font)
                font.setUnderline(True)
            p.setFont(font)
            color = (cf or {}).get("color") or numcolor or st.color or (
                "#0563c1" if (r, c) in sh.links else ("#c00000" if isinstance(value, XLError) else "#000000"))
            p.setPen(QColor(color))
            is_num = isinstance(value, (int, float)) and not isinstance(value, bool) and not show_f
            h = st.halign
            if h is None:
                h = "right" if is_num else ("center" if isinstance(value, (bool, XLError)) else "left")
            if self.view.isRightToLeft() and st.halign is None:
                h = {"left": "right", "right": "left"}.get(h, h)
            flags = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight,
                     "justify": Qt.AlignJustify}.get(h, Qt.AlignLeft)
            flags |= {"top": Qt.AlignTop, "center": Qt.AlignVCenter}.get(st.valign, Qt.AlignVCenter if
                                                                      rect.height() <= 30 else Qt.AlignBottom)
            text_rect = rect.adjusted(3 + st.indent * 10 if h == "left" else 3, 1,
                                      -3 - (st.indent * 10 if h == "right" else 0), -1)
            if span > 0:
                extra = sum(self.view.columnWidth(c - 1 + k) for k in range(1, span + 1))
                text_rect.setRight(text_rect.right() + extra)
            fm = QFontMetrics(font)
            if st.wrap:
                flags |= Qt.TextWordWrap
            elif is_num and fm.horizontalAdvance(text) > text_rect.width():
                text = "#" * max(1, text_rect.width() // max(1, fm.horizontalAdvance("#")))
            if st.rotation:
                p.translate(rect.center())
                ang = -st.rotation if st.rotation <= 90 else st.rotation - 90
                p.rotate(ang)
                p.translate(-rect.center())
            p.setClipRect(text_rect.adjusted(-2, 0, 2, 0))
            p.drawText(text_rect, int(flags), text)
            p.setClipping(False)
            if st.rotation:
                p.resetTransform()
        p.restore()
        # borders
        self._borders(p, rect, st)
        # comment marker
        if (r, c) in sh.comments:
            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#d93025"))
            p.drawPolygon(QPolygon([rect.topRight() + _pt(-6, 0), rect.topRight(), rect.topRight() + _pt(0, 6)]))
            p.restore()
        # autofilter button
        af = sh.autofilter
        if af and r == af[0] and af[1] <= c <= af[3]:
            p.save()
            b = QRect(rect.right() - 16, rect.top() + (rect.height() - 15) // 2, 15, 15)
            active = c in sh.filters
            p.setPen(QColor("#9aa0a6"))
            p.setBrush(QColor("#e8f0fe") if active else QColor("#ffffff"))
            p.drawRect(b)
            p.setBrush(QColor("#1a73e8") if active else QColor("#5f6368"))
            p.setPen(Qt.NoPen)
            cx, cy = b.center().x(), b.center().y()
            p.drawPolygon(QPolygon([_pt(cx - 4, cy - 2), _pt(cx + 4, cy - 2), _pt(cx, cy + 3)]))
            p.restore()
        # selection overlay
        if option.state & QStyle.State_Selected:
            cur = self.view.currentIndex()
            if not (cur.row() == index.row() and cur.column() == index.column()):
                p.fillRect(rect, self.sel_color)

    def _borders(self, p: QPainter, rect: QRect, st: Style):
        if not (st.border_top or st.border_bottom or st.border_left or st.border_right):
            return
        p.save()
        for b, line in ((st.border_top, (rect.topLeft(), rect.topRight())),
                        (st.border_bottom, (rect.bottomLeft(), rect.bottomRight())),
                        (st.border_left, (rect.topLeft(), rect.bottomLeft())),
                        (st.border_right, (rect.topRight(), rect.bottomRight()))):
            if b is None:
                continue
            w, style = _PEN_STYLES.get(b.style, (1, Qt.SolidLine))
            pen = QPen(QColor(b.color or "#000000"), w, style)
            p.setPen(pen)
            if b.style == "double":
                p.setPen(QPen(QColor(b.color or "#000000"), 1))
                a, z = line
                if a.y() == z.y():
                    p.drawLine(a.x(), a.y() - 1, z.x(), z.y() - 1)
                    p.drawLine(a.x(), a.y() + 1, z.x(), z.y() + 1)
                else:
                    p.drawLine(a.x() - 1, a.y(), z.x() - 1, z.y())
                    p.drawLine(a.x() + 1, a.y(), z.x() + 1, z.y())
            else:
                p.drawLine(*line)
        p.restore()


def _pt(x, y):
    from PySide6.QtCore import QPoint

    return QPoint(x, y)


__all__ = ["SheetModel", "CellDelegate", "QBrush", "QRectF"]
