"""Sheet grid view: Excel-like navigation, selection frame + fill handle, freeze panes, autofilter buttons."""
from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from app.sheet.formula.tokenizer import MAX_COLS, MAX_ROWS


class _Overlay(QTableView):
    """A frozen pane: shares model, selection and delegate with the main view."""

    def __init__(self, main: "SheetView", show_hheader: bool, show_vheader: bool):
        super().__init__(main)
        self.main = main
        self.setModel(main.model())
        self.setSelectionModel(main.selectionModel())
        self.setItemDelegate(main.itemDelegate())
        self.setShowGrid(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QTableView.NoFrame)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(main.selectionMode())
        self.horizontalHeader().setVisible(show_hheader)
        self.verticalHeader().setVisible(show_vheader)
        for h in (self.horizontalHeader(), self.verticalHeader()):
            h.setSectionResizeMode(QHeaderView.Fixed)
            h.setHighlightSections(True)
        self.horizontalHeader().setDefaultSectionSize(main.horizontalHeader().defaultSectionSize())
        self.verticalHeader().setDefaultSectionSize(main.verticalHeader().defaultSectionSize())
        self.setStyleSheet("QTableView { border: none; background: white; }")
        self.clicked.connect(lambda idx: main.setCurrentIndex(idx))
        self.doubleClicked.connect(lambda idx: (main.setCurrentIndex(idx), main.edit(idx)))

    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid():
            self.main.setFocus()
            mods = e.modifiers()
            if mods & Qt.ShiftModifier:
                self.main._extend_to(idx)
                return
            self.main.setCurrentIndex(idx)
            self.main._overlay_drag = self
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        self.main.wheelEvent(e)


class SheetView(QTableView):
    filterButtonClicked = Signal(int, QPoint)       # column (1-based), global position
    fillRequested = Signal(tuple, tuple)            # source range, target range (1-based r1,c1,r2,c2)
    linkActivated = Signal(int, int)
    contextRequested = Signal(QPoint)

    def __init__(self, doc, parent=None):
        self._overlays: dict = {}
        self.freeze = (0, 0)
        self._fill_drag = None
        self._overlay_drag = None
        super().__init__(parent)
        self.doc = doc
        self.setObjectName("SheetGrid")
        self.setShowGrid(False)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                             QAbstractItemView.AnyKeyPressed)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setCornerButtonEnabled(True)
        self.setWordWrap(False)
        self.setTabKeyNavigation(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda p: self.contextRequested.emit(self.viewport().mapToGlobal(p)))
        hh, vh = self.horizontalHeader(), self.verticalHeader()
        hh.setDefaultSectionSize(90)
        vh.setDefaultSectionSize(24)
        hh.setMinimumSectionSize(4)
        vh.setMinimumSectionSize(4)
        vh.setDefaultAlignment(Qt.AlignCenter)
        hh.setHighlightSections(True)
        vh.setHighlightSections(True)
        vh.setFixedWidth(58)
        self.horizontalScrollBar().valueChanged.connect(self._sync_overlays)
        self.verticalScrollBar().valueChanged.connect(self._sync_overlays)
        hh.sectionResized.connect(self._section_resized_h)
        vh.sectionResized.connect(self._section_resized_v)

    # ------------------------------------------------------------------ geometry helpers
    def cell_rect(self, r: int, c: int) -> QRect:
        """Viewport rectangle of cell (1-based), even when it is not visible."""
        return QRect(self.columnViewportPosition(c - 1), self.rowViewportPosition(r - 1),
                     self.columnWidth(c - 1), self.rowHeight(r - 1))

    def selected_ranges(self) -> list[tuple[int, int, int, int]]:
        out = []
        for rng in self.selectionModel().selection():
            out.append((rng.top() + 1, rng.left() + 1, rng.bottom() + 1, rng.right() + 1))
        if not out:
            cur = self.currentIndex()
            if cur.isValid():
                out.append((cur.row() + 1, cur.column() + 1, cur.row() + 1, cur.column() + 1))
        return out

    def selection_bounds(self) -> tuple[int, int, int, int]:
        rs = self.selected_ranges()
        if not rs:
            return (1, 1, 1, 1)
        return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))

    def select_range(self, r1, c1, r2, c2, current: tuple | None = None):
        model = self.model()
        sel = QItemSelection(model.index(r1 - 1, c1 - 1), model.index(r2 - 1, c2 - 1))
        cur = current or (r1, c1)
        self.selectionModel().setCurrentIndex(model.index(cur[0] - 1, cur[1] - 1), QItemSelectionModel.NoUpdate)
        self.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
        self.scrollTo(model.index(cur[0] - 1, cur[1] - 1))
        self.viewport().update()

    def goto(self, r: int, c: int):
        r = max(1, min(MAX_ROWS, r))
        c = max(1, min(MAX_COLS, c))
        self.select_range(r, c, r, c)

    def _extend_to(self, idx):
        cur = self.currentIndex()
        anchor = self._anchor if getattr(self, "_anchor", None) is not None else cur
        model = self.model()
        sel = QItemSelection(model.index(min(anchor.row(), idx.row()), min(anchor.column(), idx.column())),
                             model.index(max(anchor.row(), idx.row()), max(anchor.column(), idx.column())))
        self.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    # ------------------------------------------------------------------ keyboard
    def _jump(self, dr: int, dc: int, extend: bool):
        """Ctrl+Arrow: move to the edge of the current data region."""
        sh = self.doc.sheet
        cur = self.currentIndex()
        r, c = cur.row() + 1, cur.column() + 1

        def filled(rr, cc):
            cell = sh.cells.get((rr, cc))
            return cell is not None and (cell.value is not None or cell.formula)

        limit_r = max(sh.max_row, 1) if dr > 0 else 1
        limit_c = max(sh.max_col, 1) if dc > 0 else 1
        nr, nc = r + dr, c + dc
        if not (1 <= nr <= MAX_ROWS and 1 <= nc <= MAX_COLS):
            return
        if filled(r, c) and filled(nr, nc):
            while 1 <= nr + dr <= MAX_ROWS and 1 <= nc + dc <= MAX_COLS and filled(nr + dr, nc + dc):
                nr, nc = nr + dr, nc + dc
        else:
            while not filled(nr, nc):
                if (dr > 0 and nr >= limit_r) or (dc > 0 and nc >= limit_c) or (dr < 0 and nr <= 1) or (dc < 0 and nc <= 1):
                    if dr > 0 and nr >= limit_r:
                        nr = MAX_ROWS if not filled(nr, nc) else nr
                    if dc > 0 and nc >= limit_c:
                        nc = MAX_COLS if not filled(nr, nc) else nc
                    break
                nr, nc = nr + dr, nc + dc
        idx = self.model().index(nr - 1, nc - 1)
        if extend:
            self._extend_to(idx)
            self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
        else:
            self.setCurrentIndex(idx)
        self.scrollTo(idx)

    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        cur = self.currentIndex()
        if self.state() == QAbstractItemView.EditingState:
            return super().keyPressEvent(e)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._move_within_selection(-1 if shift else 1, 0)
            return
        if key == Qt.Key_Tab or key == Qt.Key_Backtab:
            self._move_within_selection(0, -1 if (shift or key == Qt.Key_Backtab) else 1)
            return
        if key in (Qt.Key_Delete,):
            self.doc.clear_contents()
            return
        if key == Qt.Key_Backspace:
            self.doc.clear_contents(current_only=True)
            self.edit(cur)
            return
        if key == Qt.Key_F2:
            self.doc.pending_cursor_end = True
            self.edit(cur)
            return
        if ctrl and key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            d = {Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0), Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1)}[key]
            self._anchor = cur if shift and getattr(self, "_anchor", None) is None else getattr(self, "_anchor", None)
            self._jump(*d, extend=shift)
            return
        if ctrl and key == Qt.Key_Home:
            self.goto(1, 1)
            return
        if ctrl and key == Qt.Key_End:
            sh = self.doc.sheet
            self.goto(max(1, sh.max_row), max(1, sh.max_col))
            return
        if ctrl and key == Qt.Key_Space:
            c = cur.column() + 1
            self.select_range(1, c, MAX_ROWS, c, (cur.row() + 1, c))
            return
        if shift and key == Qt.Key_Space and not ctrl:
            r = cur.row() + 1
            self.select_range(r, 1, r, MAX_COLS, (r, cur.column() + 1))
            return
        if not shift:
            self._anchor = None
        elif getattr(self, "_anchor", None) is None:
            self._anchor = cur
        super().keyPressEvent(e)

    def _move_within_selection(self, dr: int, dc: int):
        cur = self.currentIndex()
        r, c = cur.row() + 1, cur.column() + 1
        r1, c1, r2, c2 = self.selection_bounds()
        multi = (r1, c1) != (r2, c2) and r2 - r1 < 5000 and c2 - c1 < 500
        nr, nc = r + dr, c + dc
        if multi:
            if nr > r2:
                nr, nc = r1, c + 1 if c + 1 <= c2 else c1
            elif nr < r1:
                nr, nc = r2, c - 1 if c - 1 >= c1 else c2
            if nc > c2:
                nc, nr = c1, r + 1 if r + 1 <= r2 else r1
            elif nc < c1:
                nc, nr = c2, r - 1 if r - 1 >= r1 else r2
            self.selectionModel().setCurrentIndex(self.model().index(nr - 1, nc - 1), QItemSelectionModel.NoUpdate)
            self.scrollTo(self.model().index(nr - 1, nc - 1))
            self.viewport().update()
        else:
            self.goto(max(1, nr), max(1, nc))

    def moveCursor(self, action, modifiers):
        return super().moveCursor(action, modifiers)

    # ------------------------------------------------------------------ mouse
    def _handle_rect(self) -> QRect | None:
        r1, c1, r2, c2 = self.selection_bounds()
        if r2 - r1 > 100000 or c2 - c1 > 10000:
            return None
        br = self.cell_rect(r2, c2)
        return QRect(br.right() - 3, br.bottom() - 3, 7, 7)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        hr = self._handle_rect()
        if e.button() == Qt.LeftButton and hr is not None and hr.adjusted(-2, -2, 2, 2).contains(pos):
            self._fill_drag = {"src": self.selection_bounds(), "target": None}
            return
        idx = self.indexAt(pos)
        if idx.isValid() and e.button() == Qt.LeftButton:
            r, c = idx.row() + 1, idx.column() + 1
            sh = self.doc.sheet
            af = sh.autofilter
            if af and r == af[0] and af[1] <= c <= af[3]:
                rect = self.visualRect(idx)
                if pos.x() >= rect.right() - 18:
                    self.filterButtonClicked.emit(c, self.viewport().mapToGlobal(QPoint(rect.right() - 16, rect.bottom())))
                    return
            if e.modifiers() & Qt.ControlModifier and (r, c) in sh.links:
                self.linkActivated.emit(r, c)
                return
            if e.modifiers() & Qt.ShiftModifier:
                if getattr(self, "_anchor", None) is None:
                    self._anchor = self.currentIndex()
            else:
                self._anchor = None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._fill_drag is not None:
            idx = self.indexAt(pos)
            if idx.isValid():
                r1, c1, r2, c2 = self._fill_drag["src"]
                r, c = idx.row() + 1, idx.column() + 1
                if abs(r - r2) >= abs(c - c2) * 0.9 or (c1 <= c <= c2):
                    tgt = (r1, c1, max(r2, r), c2) if r >= r1 else (r, c1, r2, c2)
                else:
                    tgt = (r1, c1, r2, max(c2, c)) if c >= c1 else (r1, c, r2, c2)
                self._fill_drag["target"] = tgt
                self.viewport().update()
            return
        hr = self._handle_rect()
        if hr is not None and hr.adjusted(-2, -2, 2, 2).contains(pos):
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._fill_drag is not None:
            drag, self._fill_drag = self._fill_drag, None
            if drag["target"] and drag["target"] != drag["src"]:
                self.fillRequested.emit(drag["src"], drag["target"])
            self.viewport().update()
            return
        super().mouseReleaseEvent(e)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self.viewport())
        r1, c1, r2, c2 = self.selection_bounds()
        if r2 - r1 < 200000 and c2 - c1 < 20000:
            tl = self.cell_rect(r1, c1)
            br = self.cell_rect(r2, c2)
            rect = QRect(tl.topLeft(), br.bottomRight())
            p.setPen(QPen(QColor("#1a73e8"), 2))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
            hr = self._handle_rect()
            if hr is not None:
                p.fillRect(hr, QColor("#1a73e8"))
                p.fillRect(hr.adjusted(-1, -1, 1, 1).adjusted(0, 0, 0, 0), Qt.NoBrush)
        if self._fill_drag and self._fill_drag["target"]:
            t = self._fill_drag["target"]
            rect = QRect(self.cell_rect(t[0], t[1]).topLeft(), self.cell_rect(t[2], t[3]).bottomRight())
            p.setPen(QPen(QColor("#5f6368"), 1, Qt.DashLine))
            p.drawRect(rect.adjusted(0, 0, -1, -1))
        cut = getattr(self.doc, "cut_range", None)
        if cut and cut[0] == self.doc.sheet.id:
            _sid, a, b, c, d = cut
            rect = QRect(self.cell_rect(a, b).topLeft(), self.cell_rect(c, d).bottomRight())
            p.setPen(QPen(QColor("#1a73e8"), 1, Qt.DashLine))
            p.drawRect(rect.adjusted(1, 1, -2, -2))
        p.end()

    # ------------------------------------------------------------------ freeze panes
    def set_freeze(self, rows: int, cols: int):
        for ov in self._overlays.values():
            ov.hide()
            ov.deleteLater()
        self._overlays = {}
        self.freeze = (rows, cols)
        if rows:
            self._overlays["top"] = _Overlay(self, False, True)
        if cols:
            self._overlays["left"] = _Overlay(self, True, False)
        if rows and cols:
            self._overlays["corner"] = _Overlay(self, True, True)
        for ov in self._overlays.values():
            self._copy_sections(ov)
            ov.show()
            ov.raise_()
        self._layout_overlays()

    def _copy_sections(self, ov):
        rows, cols = self.freeze
        for r in range(rows):
            ov.setRowHeight(r, self.rowHeight(r))
            ov.setRowHidden(r, self.isRowHidden(r))
        for c in range(cols):
            ov.setColumnWidth(c, self.columnWidth(c))
            ov.setColumnHidden(c, self.isColumnHidden(c))
        for c in range(cols, min(cols + 60, MAX_COLS)):
            ov.setColumnWidth(c, self.columnWidth(c))
        for r in range(rows, min(rows + 200, MAX_ROWS)):
            ov.setRowHeight(r, self.rowHeight(r))
        for (a, b, c, d) in self.doc.sheet.merges:
            if a - 1 < max(rows, 1) or b - 1 < max(cols, 1):
                ov.setSpan(a - 1, b - 1, c - a + 1, d - b + 1)

    def _layout_overlays(self):
        if not getattr(self, "_overlays", None):
            return
        rows, cols = self.freeze
        fw = self.frameWidth()
        hh_h = self.horizontalHeader().height()
        vh_w = self.verticalHeader().width()
        top_h = sum(self.rowHeight(r) for r in range(rows) if not self.isRowHidden(r))
        left_w = sum(self.columnWidth(c) for c in range(cols) if not self.isColumnHidden(c))
        vp = self.viewport().geometry()
        if "top" in self._overlays:
            ov = self._overlays["top"]
            ov.verticalHeader().setFixedWidth(vh_w)
            ov.setGeometry(fw, fw + hh_h, vh_w + vp.width(), top_h + 1)
        if "left" in self._overlays:
            ov = self._overlays["left"]
            ov.horizontalHeader().setFixedHeight(hh_h)
            ov.setGeometry(fw + vh_w, fw, left_w + 1, hh_h + vp.height())
        if "corner" in self._overlays:
            ov = self._overlays["corner"]
            ov.verticalHeader().setFixedWidth(vh_w)
            ov.horizontalHeader().setFixedHeight(hh_h)
            ov.setGeometry(fw, fw, vh_w + left_w + 1, hh_h + top_h + 1)
        self._sync_overlays()

    def _sync_overlays(self, *_):
        if not getattr(self, "_overlays", None):
            return
        h = self.horizontalScrollBar().value()
        v = self.verticalScrollBar().value()
        if "top" in self._overlays:
            ov = self._overlays["top"]
            ov.horizontalScrollBar().setValue(h)
            ov.verticalScrollBar().setValue(0)
        if "left" in self._overlays:
            ov = self._overlays["left"]
            ov.verticalScrollBar().setValue(v)
            ov.horizontalScrollBar().setValue(0)
        if "corner" in self._overlays:
            self._overlays["corner"].horizontalScrollBar().setValue(0)
            self._overlays["corner"].verticalScrollBar().setValue(0)
        for ov in self._overlays.values():
            ov.viewport().update()

    def _section_resized_h(self, idx, _old, new):
        for ov in self._overlays.values():
            ov.setColumnWidth(idx, new)
        self._layout_overlays()

    def _section_resized_v(self, idx, _old, new):
        for ov in self._overlays.values():
            ov.setRowHeight(idx, new)
        self._layout_overlays()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_overlays()

    def updateGeometries(self):
        super().updateGeometries()
        self._layout_overlays()

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        rows, cols = self.freeze
        if (rows or cols) and index.isValid():
            r, c = index.row(), index.column()
            vp = self.viewport()
            if rows and r >= rows:
                top_h = sum(self.rowHeight(i) for i in range(rows))
                y = self.rowViewportPosition(r)
                if y < top_h:
                    self.verticalScrollBar().setValue(max(0, self.verticalScrollBar().value() - (top_h - y)))
            if cols and c >= cols:
                left_w = sum(self.columnWidth(i) for i in range(cols))
                x = self.columnViewportPosition(c)
                if x < left_w:
                    self.horizontalScrollBar().setValue(max(0, self.horizontalScrollBar().value() - (left_w - x)))
            if r < rows and c < cols:
                return
            if r < rows:
                x = self.columnViewportPosition(c)
                if x + self.columnWidth(c) > vp.width():
                    self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + x + self.columnWidth(c) - vp.width())
                return
            if c < cols:
                y = self.rowViewportPosition(r)
                if y + self.rowHeight(r) > vp.height():
                    self.verticalScrollBar().setValue(self.verticalScrollBar().value() + y + self.rowHeight(r) - vp.height())
                return
        super().scrollTo(index, hint)

    def apply_sheet_layout(self, sh):
        """Push widths/heights/hidden/merges of a sheet into the view."""
        hh, vh = self.horizontalHeader(), self.verticalHeader()
        self.clearSpans()
        hh.setDefaultSectionSize(sh.default_col_width)
        vh.setDefaultSectionSize(sh.default_row_height)
        for c, w in sh.col_widths.items():
            if c <= MAX_COLS:
                self.setColumnWidth(c - 1, w)
        for r, h in sh.row_heights.items():
            if r <= MAX_ROWS:
                self.setRowHeight(r - 1, h)
        for c in sh.hidden_cols:
            self.setColumnHidden(c - 1, True)
        for r in sh.hidden_rows | sh.filtered_rows:
            self.setRowHidden(r - 1, True)
        for (r1, c1, r2, c2) in sh.merges:
            self.setSpan(r1 - 1, c1 - 1, r2 - r1 + 1, c2 - c1 + 1)
        self.set_freeze(*sh.freeze)

    def reset_layout(self, old_sh):
        """Undo per-section customisations of the previously shown sheet."""
        hh, vh = self.horizontalHeader(), self.verticalHeader()
        for c in old_sh.col_widths:
            if c <= MAX_COLS:
                self.setColumnWidth(c - 1, hh.defaultSectionSize())
        for r in old_sh.row_heights:
            if r <= MAX_ROWS:
                self.setRowHeight(r - 1, vh.defaultSectionSize())
        for c in old_sh.hidden_cols:
            self.setColumnHidden(c - 1, False)
        for r in old_sh.hidden_rows | old_sh.filtered_rows:
            self.setRowHidden(r - 1, False)
        self.clearSpans()


__all__ = ["SheetView", "QKeySequence"]
