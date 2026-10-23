"""The Spreadsheets document tab."""
from __future__ import annotations

import copy
import logging
import os
import re
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QApplication, QComboBox, QCompleter, QFontComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QTabBar, QToolButton, QVBoxLayout, QWidget)

from app.services.config import cfg
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_file, get_save_file, handle_exception, show_error, show_info
from app.shell.document import DocumentError, DocumentWidget, OpenCancelled
from app.shell.icons import icon
from app.shell.ribbon import Ribbon
from app.shell.widgets import ColorMenu, ZoomSlider
from app.sheet import dialogs as D
from app.sheet.charts import ChartDialog, ChartWidget
from app.sheet.formula.functions import FUNCTIONS, function_names_by_category
from app.sheet.formula.tokenizer import MAX_COLS, MAX_ROWS, index_to_col
from app.sheet.formula.values import XLError
from app.sheet import features as sheet_features
from app.sheet.model import (Book, Border, Cell, ChartSpec, CondFormat, FilterSpec, SheetImage, Style, a1,
                             edit_text, parse_input, range_a1)
from app.sheet.qtmodel import CellDelegate, SheetModel
from app.sheet.view import SheetView

log = logging.getLogger(__name__)
CLIP_MIME = "application/x-myoffice-cells"
_CLIPBOARD: dict = {}


# ============================================================================= undo commands
class StatesCommand(QUndoCommand):
    def __init__(self, doc, sid, before: dict, after: dict, text: str):
        super().__init__(text)
        self.doc, self.sid, self.before, self.after = doc, sid, before, after

    def redo(self):
        self.doc.apply_states(self.sid, self.after)

    def undo(self):
        self.doc.apply_states(self.sid, self.before)


class SnapshotCommand(QUndoCommand):
    """Whole-workbook snapshot (structural edits: rows/columns, sheets, merges, names...)."""

    def __init__(self, doc, before, after, text: str):
        super().__init__(text)
        self.doc, self.before, self.after = doc, before, after
        self._first = True

    def redo(self):
        if self._first:
            self._first = False
            return
        self.doc.restore_snapshot(self.after)

    def undo(self):
        self.doc.restore_snapshot(self.before)


# ============================================================================= image widget
class ImageWidget(ChartWidget):
    def refresh(self):
        from PySide6.QtGui import QPixmap

        pm = QPixmap()
        pm.loadFromData(self.spec.data)
        self._pix = pm.scaled(*self.spec.size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(self._pix)
        self.reposition()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction(_t("Copy"), self.copy_image)
        m.addAction(_t("Save as Image\u2026"), self.save_image)
        m.addSeparator()
        m.addAction(_t("Delete Picture"), lambda: self.deleteRequested.emit(self))
        m.exec(e.globalPos())

    def mouseDoubleClickEvent(self, e):
        pass


class _ViewportFilter(QObject):
    """Point mode: clicking cells while typing a formula inserts references."""

    def __init__(self, doc):
        super().__init__(doc)
        self.doc = doc

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            target = self.doc.formula_target()
            if target is not None:
                idx = self.doc.view.indexAt(ev.position().toPoint())
                if idx.isValid():
                    self.doc.insert_reference(target, idx.row() + 1, idx.column() + 1,
                                              bool(ev.modifiers() & Qt.ShiftModifier))
                    return True
        return False


# ============================================================================= document
class SheetDocument(DocumentWidget):
    kind = "sheet"
    default_ext = ".xlsx"
    writable_exts = (".xlsx", ".xlsm", ".xls", ".ett", ".csv", ".tsv")
    cellsEdited = Signal(int, object)     # sheet id, {(r,c): state} (local edits, for collaboration)

    def __init__(self, parent=None, book: Book | None = None):
        super().__init__(parent)
        self.book = book or Book()
        if not self.book.sheets:
            self.book.add_sheet()
        self.sheet = self.book.sheets[0]
        self.undo = QUndoStack(self)
        self.undo.cleanChanged.connect(lambda clean: self.set_modified(not clean) if not self._loading else None)
        self.show_formulas = False
        self.zoom = 1.0
        self.pending_editor_text = None
        self.pending_cursor_end = False
        self.cut_range = None
        self.chart_widgets: list[ChartWidget] = []
        self.collab_session = None
        self.recorder = None
        self._loading = False
        self._point_anchor = None
        self._last_ref_span = None
        self.find_dialog = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # formula bar
        fb = QWidget()
        fb.setObjectName("StatusStrip")
        fl = QHBoxLayout(fb)
        fl.setContentsMargins(6, 3, 6, 3)
        fl.setSpacing(4)
        self.name_box = QComboBox()
        self.name_box.setEditable(True)
        self.name_box.setFixedWidth(110)
        self.name_box.lineEdit().returnPressed.connect(self._name_box_go)
        self.name_box.activated.connect(lambda *_: self._name_box_go())
        fl.addWidget(self.name_box)
        fx = QToolButton()
        fx.setText("fx")
        fx.setToolTip(_t("Insert Function"))
        fx.clicked.connect(self.insert_function)
        fl.addWidget(fx)
        self.formula_bar = QLineEdit()
        self.formula_bar.setPlaceholderText(_t("Enter a value or a formula starting with ="))
        self.formula_bar.returnPressed.connect(self._formula_commit)
        self.formula_bar.textEdited.connect(self._formula_edited)
        self.formula_bar.installEventFilter(self)
        comp = QCompleter(sorted("=" + n + "(" for n in FUNCTIONS), self.formula_bar)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self._fn_completer = comp
        fl.addWidget(self.formula_bar, 1)
        lay.addWidget(fb)
        # grid
        self.view = SheetView(self)
        self.model = SheetModel(self, self.sheet)
        self.view.setModel(self.model)
        self.delegate = CellDelegate(self.view, self)
        self.view.setItemDelegate(self.delegate)
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        self.view.filterButtonClicked.connect(self._filter_popup)
        self.view.fillRequested.connect(self.fill_range)
        self.view.linkActivated.connect(self._open_link)
        self.view.contextRequested.connect(self._context_menu)
        self.view.horizontalHeader().sectionResized.connect(self._col_resized)
        self.view.verticalHeader().sectionResized.connect(self._row_resized)
        self.view.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.horizontalHeader().customContextMenuRequested.connect(lambda p: self._header_menu("col", p))
        self.view.verticalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.verticalHeader().customContextMenuRequested.connect(lambda p: self._header_menu("row", p))
        self.view.horizontalScrollBar().valueChanged.connect(self._reposition_floats)
        self.view.verticalScrollBar().valueChanged.connect(self._reposition_floats)
        self.view.verticalHeader().setFixedWidth(58)
        self._vp_filter = _ViewportFilter(self)
        self.view.viewport().installEventFilter(self._vp_filter)
        lay.addWidget(self.view, 1)
        # bottom: sheet tabs + stats + zoom
        bottom = QWidget()
        bottom.setObjectName("StatusStrip")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(4, 0, 8, 0)
        bl.setSpacing(4)
        add = QToolButton()
        add.setIcon(icon("plus"))
        add.setToolTip(_t("New sheet"))
        add.clicked.connect(lambda: self.add_sheet())
        bl.addWidget(add)
        self.tabs = QTabBar()
        self.tabs.setMovable(True)
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.setShape(QTabBar.RoundedSouth)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabMoved.connect(self._tab_moved)
        self.tabs.tabBarDoubleClicked.connect(lambda i: self.rename_sheet(i))
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_menu)
        bl.addWidget(self.tabs, 1)
        self.stats = QLabel("")
        self.stats.setObjectName("Muted")
        bl.addWidget(self.stats)
        self.zoom_slider = ZoomSlider()
        self.zoom_slider.zoomRequested.connect(self.set_zoom)
        bl.addWidget(self.zoom_slider)
        lay.addWidget(bottom)

        self._chart_timer = QTimer(self)
        self._chart_timer.setSingleShot(True)
        self._chart_timer.setInterval(500)
        self._chart_timer.timeout.connect(self._refresh_charts)
        self._build_actions()
        self.ribbon = self._build_ribbon()
        self._rebuild_tabs()
        self.view.apply_sheet_layout(self.sheet)
        self.view.goto(1, 1)

    # ================================================================== DocumentWidget API
    def untitled_label(self) -> str:
        return _t("Book")

    def ribbon_widget(self):
        return self.ribbon

    def save_filters(self) -> str:
        return ("Excel Workbook (*.xlsx);;Excel Macro-Enabled Workbook (*.xlsm);;Excel 97-2003 Workbook (*.xls);;"
                "Spreadsheet Template (*.ett);;OpenDocument Spreadsheet (*.ods);;"
                "CSV UTF-8 (*.csv);;Tab-separated (*.tsv);;Web Page (*.html)")

    @staticmethod
    def open_filter() -> str:
        return "Spreadsheets (*.xlsx *.xlsm *.xls *.ett *.csv *.tsv *.ods);;All files (*)"

    def load(self, path: str) -> None:
        from app.sheet.xlsx_io import LARGE_FILE_BYTES, SpreadsheetError, load_any
        from app.shell.tasks import TaskError, run_task

        ext = Path(path).suffix.lower()
        size = os.path.getsize(path)
        fast, max_rows = False, None
        if size > LARGE_FILE_BYTES and ext in (".xlsx", ".xlsm", ".ett", ".csv", ".tsv"):
            box, fast_b, full_b = self.large_file_box(path, size)
            box.exec()
            if box.clickedButton() is fast_b:
                fast, max_rows = True, 200_000
            elif box.clickedButton() is not full_b:
                raise OpenCancelled()
        book = Book()
        try:
            info = run_task(self, _t("Opening {0}\u2026").format(os.path.basename(path)), load_any, path, book,
                            fast=fast, max_rows=max_rows)
        except TaskError as exc:
            orig = exc.original
            if isinstance(orig, MemoryError):
                raise DocumentError(_t("Not enough memory to open this workbook. Try the Fast Preview mode.")) from exc
            if isinstance(orig, SpreadsheetError):
                raise DocumentError(str(orig)) from exc
            raise DocumentError(f"{orig.__class__.__name__}: {orig}") from exc
        if not book.sheets:
            book.add_sheet()
        self._loading = True
        self.set_book(book)
        self._loading = False
        self.path = Path(path)
        self.read_only = bool(info.get("truncated") or info.get("fast")) or ext not in self.writable_exts
        self.undo.clear()
        self.undo.setClean()
        super().set_modified(False)
        msgs = []
        if info.get("truncated"):
            msgs.append(_t("Fast preview: only the first {0:,} rows were loaded (read-only).").format(max_rows))
        if info.get("unsupported"):
            msgs.append(_t("Kept Excel's cached results for unsupported functions: {0}").format(
                ", ".join(sorted(info["unsupported"]))))
        if info.get("vba"):
            msgs.append(_t("This workbook contains VBA macros. They are kept when you save as .xlsm "
                           "(Tools \u25b8 View VBA Code shows them)."))
        if msgs:
            QTimer.singleShot(300, lambda: self.statusChanged.emit("  ".join(msgs)))
        self.titleChanged.emit()

    def large_file_box(self, path: str, size: int):
        box = QMessageBox(self.window() if self.window() is not self else None)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(_t("Large workbook"))
        box.setText(_t("\u201c{0}\u201d is {1:.0f} MB.").format(os.path.basename(path), size / 1048576))
        box.setInformativeText(_t("Opening it fully loads every cell with formatting and formulas, which can take "
                                  "several minutes and needs a lot of memory.\n\nA fast preview opens the values "
                                  "of the first 200,000 rows read-only."))
        fast_b = box.addButton(_t("Fast Preview"), QMessageBox.AcceptRole)
        full_b = box.addButton(_t("Open Fully"), QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        return box, fast_b, full_b

    def save_to(self, path: str) -> None:
        from app.sheet.xlsx_io import save_csv, save_xlsx
        from app.shell.tasks import TaskError, run_task

        ext = Path(path).suffix.lower()
        self._commit_open_editor()
        try:
            if ext in (".xlsx", ".ett", ".xlsm"):
                run_task(self, _t("Saving\u2026"), save_xlsx, path, self.book)
                if self.book.vba_source and ext != ".xlsm":
                    self.statusChanged.emit(_t("Saved without the VBA macros - only .xlsm workbooks can hold them."))
            elif ext == ".xls":
                warnings = run_task(self, _t("Saving\u2026"), sheet_features.save_xls, path, self.book)
                if warnings:
                    self.statusChanged.emit(warnings[0] + ("" if len(warnings) == 1 else
                                                            _t(" (and {0} more)").format(len(warnings) - 1)))
            elif ext in (".csv", ".tsv"):
                save_csv(path, self.book, self.sheet, "\t" if ext == ".tsv" else ",")
                if len(self.book.sheets) > 1:
                    self.statusChanged.emit(_t("Only the active sheet was saved (CSV holds one sheet)."))
            elif ext == ".ods":
                import tempfile

                from app.services.office_convert import convert

                tmpdir = tempfile.mkdtemp()
                tmp = os.path.join(tmpdir, Path(path).stem + ".xlsx")
                save_xlsx(tmp, self.book)
                out = run_task(self, _t("Converting\u2026"), convert, tmp, "ods", tmpdir)
                os.replace(out, path)
            elif ext in (".html", ".htm"):
                self._save_html(path)
            elif ext == ".pdf":
                self.export_pdf_to(path)
            else:
                raise DocumentError(_t("Cannot save as {0}").format(ext))
        except TaskError as exc:
            raise exc.original from None
        if ext in (".xlsx", ".ett", ".csv", ".tsv"):
            self.undo.setClean()

    def set_modified(self, value: bool = True) -> None:
        if not value and not self._loading:
            try:
                self.undo.setClean()
            except RuntimeError:  # undo stack already destroyed (application shutdown)
                return
        super().set_modified(value)

    def export_pdf_to(self, path: str) -> None:
        from app.sheet.printing import export_pdf

        export_pdf(self.book, path, title=self.display_name())

    def print_document(self) -> None:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        from app.sheet.printing import print_book

        printer = QPrinter(QPrinter.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec():
            try:
                print_book(self.book, printer, [self.sheet])
            except Exception as exc:
                handle_exception(self, _t("Print"), exc)

    def status_text(self) -> str:
        return self.stats.text()

    def recovery_ext(self) -> str:
        return ".xlsx"

    def write_recovery(self, path: str) -> None:
        from app.sheet.xlsx_io import save_xlsx

        save_xlsx(path, self.book)

    def _save_html(self, path: str):
        import html

        sh = self.sheet
        r2, c2 = max(1, sh.max_row), max(1, sh.max_col)
        rows = []
        for r in range(1, r2 + 1):
            tds = []
            for c in range(1, c2 + 1):
                st = sh.style_of(r, c)
                css = []
                if st.bold:
                    css.append("font-weight:bold")
                if st.italic:
                    css.append("font-style:italic")
                if st.fill:
                    css.append(f"background:{st.fill}")
                if st.color:
                    css.append(f"color:{st.color}")
                v = sh.value(r, c)
                if st.halign or isinstance(v, (int, float)) and not isinstance(v, bool):
                    css.append(f"text-align:{st.halign or 'right'}")
                tds.append(f"<td style=\"{';'.join(css)}\">{html.escape(sh.display(r, c)[0])}</td>")
            rows.append("<tr>" + "".join(tds) + "</tr>")
        doc = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(sh.name)}</title>"
               "<style>table{border-collapse:collapse;font-family:Calibri,Arial,sans-serif;font-size:11pt}"
               "td{border:1px solid #d0d4d9;padding:2px 6px}</style></head><body><table>"
               + "".join(rows) + "</table></body></html>")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)

    # ================================================================== book / sheets
    def set_book(self, book: Book):
        old = self.sheet
        self.book = book
        self.view.reset_layout(old)
        self.sheet = book.sheets[0]
        self.model.set_sheet(self.sheet)
        self._rebuild_tabs()
        self.view.apply_sheet_layout(self.sheet)
        self._rebuild_floats()
        self.view.goto(1, 1)

    def _rebuild_tabs(self):
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for sh in self.book.sheets:
            i = self.tabs.addTab(sh.name)
            if sh.tab_color:
                self.tabs.setTabTextColor(i, QColor(sh.tab_color))
        self.tabs.setCurrentIndex(self.book.sheets.index(self.sheet))
        self.tabs.blockSignals(False)

    def switch_sheet(self, sh):
        if sh is self.sheet:
            return
        self._commit_open_editor()
        self.view.reset_layout(self.sheet)
        self.sheet = sh
        self.model.set_sheet(sh)
        self.view.apply_sheet_layout(sh)
        self._apply_zoom_sizes()
        self._rebuild_floats()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(self.book.sheets.index(sh))
        self.tabs.blockSignals(False)
        self.view.goto(1, 1)

    def _tab_changed(self, i: int):
        if 0 <= i < len(self.book.sheets):
            self.switch_sheet(self.book.sheets[i])

    def _tab_moved(self, frm: int, to: int):
        before = self.snapshot()
        self.book.move_sheet(frm, to)
        self.undo.push(SnapshotCommand(self, before, self.snapshot(), _t("Move Sheet")))

    def add_sheet(self, name: str | None = None):
        before = self.snapshot()
        sh = self.book.add_sheet(name, self.book.sheets.index(self.sheet) + 1)
        self.undo.push(SnapshotCommand(self, before, self.snapshot(), _t("Insert Sheet")))
        self._rebuild_tabs()
        self.switch_sheet(sh)
        return sh

    def rename_sheet(self, i: int | None = None):
        i = self.tabs.currentIndex() if i is None or i < 0 else i
        sh = self.book.sheets[i]
        name, ok = QInputDialog.getText(self, _t("Rename Sheet"), _t("Sheet name:"), text=sh.name)
        if not ok or name == sh.name:
            return
        err = self.book.valid_sheet_name(name, sh)
        if err:
            show_error(self, _t("Rename Sheet"), _t(err))
            return
        before = self.snapshot()
        self.book.rename_sheet(sh, name)
        self.undo.push(SnapshotCommand(self, before, self.snapshot(), _t("Rename Sheet")))
        self._rebuild_tabs()
        self.model.cells_changed()

    def delete_sheet(self, i: int | None = None):
        if len(self.book.sheets) <= 1:
            show_info(self, _t("Delete Sheet"), _t("A workbook must contain at least one sheet."))
            return
        i = self.tabs.currentIndex() if i is None else i
        sh = self.book.sheets[i]
        if sh.cells and not confirm(self, _t("Delete Sheet"), _t("Delete the sheet \u201c{0}\u201d and its data?").format(sh.name)):
            return
        before = self.snapshot()
        self.book.remove_sheet(sh)
        self.undo.push(SnapshotCommand(self, before, self.snapshot(), _t("Delete Sheet")))
        new = self.book.sheets[max(0, i - 1)]
        self.view.reset_layout(sh)
        self.sheet = new
        self.model.set_sheet(new)
        self.view.apply_sheet_layout(new)
        self._rebuild_tabs()
        self._rebuild_floats()

    def duplicate_sheet(self, i: int | None = None):
        i = self.tabs.currentIndex() if i is None else i
        before = self.snapshot()
        new = self.book.copy_sheet(self.book.sheets[i])
        self.undo.push(SnapshotCommand(self, before, self.snapshot(), _t("Duplicate Sheet")))
        self._rebuild_tabs()
        self.switch_sheet(new)

    def set_tab_color(self, i: int, color: QColor):
        sh = self.book.sheets[i]
        sh.tab_color = color.name() if color.isValid() else None
        self.set_modified(True)
        self._rebuild_tabs()

    def _tab_menu(self, pos):
        i = self.tabs.tabAt(pos)
        if i < 0:
            return
        m = QMenu(self)
        m.addAction(icon("plus"), _t("Insert Sheet"), lambda: self.add_sheet())
        m.addAction(icon("edit"), _t("Rename"), lambda: self.rename_sheet(i))
        m.addAction(icon("duplicate"), _t("Duplicate"), lambda: self.duplicate_sheet(i))
        m.addAction(icon("delete"), _t("Delete"), lambda: self.delete_sheet(i))
        cm = ColorMenu(m, _t("No Color"))
        cm.setTitle(_t("Tab Color"))
        cm.colorPicked.connect(lambda c: self.set_tab_color(i, c))
        m.addMenu(cm)
        m.exec(self.tabs.mapToGlobal(pos))

    # ================================================================== snapshots (structural undo)
    def snapshot(self):
        sheets = []
        for sh in self.book.sheets:
            feats = {k: copy.deepcopy(getattr(sh, k)) for k in (
                "col_widths", "row_heights", "merges", "freeze", "hidden_rows", "hidden_cols", "filtered_rows",
                "comments", "links", "cond_formats", "validations", "charts", "images", "autofilter", "filters",
                "tab_color", "show_grid", "protected", "protect_password", "default_col_width", "default_row_height")}
            cells = {k: (c.value, c.formula, c.style) for k, c in sh.cells.items()}
            sheets.append((sh.id, sh.name, cells, feats))
        return {"sheets": sheets, "names": dict(self.book.names), "current": self.sheet.id,
                "next": self.book._next_sid}

    def restore_snapshot(self, snap):
        from app.sheet.model import SheetData

        old = self.sheet
        self.view.reset_layout(old)
        new_sheets = []
        for sid, name, cells, feats in snap["sheets"]:
            sh = SheetData(self.book, sid, name)
            for k, v in feats.items():
                setattr(sh, k, copy.deepcopy(v))
            for key, (value, formula, style) in cells.items():
                cell = Cell(value, None, style)
                cell.formula = formula
                sh.cells[key] = cell
            new_sheets.append(sh)
        self.book.sheets = new_sheets
        self.book.names = dict(snap["names"])
        self.book._next_sid = snap["next"]
        self.book.engine.rebuild()
        self.book.engine.recalc_all()
        self.sheet = self.book.sheet_by_id(snap["current"]) or self.book.sheets[0]
        self.model.set_sheet(self.sheet)
        self.view.apply_sheet_layout(self.sheet)
        self._apply_zoom_sizes()
        self._rebuild_tabs()
        self._rebuild_floats()
        self.set_modified(True)

    def structural(self, text: str, fn):
        """Run ``fn()`` (a structural change) with snapshot undo."""
        self._commit_open_editor()
        before = self.snapshot()
        self.view.reset_layout(self.sheet)
        try:
            fn()
        finally:
            self.model.set_sheet(self.sheet)
            self.view.apply_sheet_layout(self.sheet)
            self._apply_zoom_sizes()
            self._rebuild_floats()
        self.undo.push(SnapshotCommand(self, before, self.snapshot(), text))
        self.set_modified(True)

    # ================================================================== cell states
    def apply_states(self, sid: int, states: dict):
        sh = self.book.sheet_by_id(sid)
        if sh is None:
            return
        keys = self.book.apply_states(sh, states)
        self._after_change(keys)

    def _after_change(self, keys):
        cur = [(r, c) for (s, r, c) in keys if s == self.sheet.id]
        self.model.cells_changed(cur if len(cur) < 5000 else None)
        self.view.viewport().update()
        self._update_formula_bar()
        self._selection_changed()
        if self.sheet.charts:
            self._chart_timer.start()

    def push_states(self, before: dict, after: dict, text: str, sid: int | None = None):
        sid = self.sheet.id if sid is None else sid
        if before == after:
            return
        self.undo.push(StatesCommand(self, sid, before, after, text))
        if not self._loading:
            self.cellsEdited.emit(sid, after)

    def commit_input(self, r: int, c: int, text: str) -> bool:
        sh = self.sheet
        if self.read_only:
            self.statusChanged.emit(_t("This workbook is open read-only."))
            return False
        cell = sh.cells.get((r, c))
        if sh.protected and self.book.styles.get(cell.style if cell else 0).locked:
            show_info(self, _t("Protected sheet"), _t("The cell is locked. Unprotect the sheet to edit it."))
            return False
        value, formula, _fmt = parse_input(text)
        for v in sh.validations:
            if v.range[0] <= r <= v.range[2] and v.range[1] <= c <= v.range[3] and not formula:
                if not D.validate_value(v, value):
                    show_error(self, _t("Data Validation"), v.error_message or _t("The value you entered is not valid."))
                    return False
        if formula:
            from app.sheet.formula.engine import compile_formula

            comp, _ = compile_formula(formula)
            if comp.error and "argument" in comp.error:
                show_error(self, _t("Formula"), _t("There is a problem with this formula."), comp.error)
                return False
        before = {(r, c): self.book.snapshot(sh, r, c)}
        after = {(r, c): self.book.state_for_input(sh, r, c, text)}
        self.push_states(before, after, _t("Typing in {0}").format(a1(r, c)))
        if self.recorder is not None:
            self.recorder.record_input(sh.name, a1(r, c), text)
        return True

    def editor_text_changed(self, text: str):
        self.formula_bar.setText(text)

    def _commit_open_editor(self):
        if self.view.state() == QAbstractItemView.State.EditingState:
            w = QApplication.focusWidget()
            if w is not None:
                self.view.commitData(w)
                self.view.closeEditor(w, QAbstractItemDelegate.EndEditHint.NoHint)

    # ================================================================== formula bar & name box
    def _current_changed(self, cur, _prev):
        self._update_formula_bar()
        self.name_box.setEditText(a1(cur.row() + 1, cur.column() + 1) if cur.isValid() else "")
        if cur.isValid():
            st = self.current_style()
            for w, text in ((self.font_combo, st.font or "Calibri"), (self.size_combo, f"{st.size or 11:g}")):
                w.blockSignals(True)
                w.setEditText(text) if w is self.size_combo else (w.setCurrentFont(QFont(text)), w.setEditText(text))
                w.blockSignals(False)
            i = self.fmt_combo.findData(st.numfmt)
            self.fmt_combo.blockSignals(True)
            self.fmt_combo.setCurrentIndex(i if i >= 0 else 0)
            self.fmt_combo.blockSignals(False)

    def _update_formula_bar(self):
        cur = self.view.currentIndex()
        if not cur.isValid() or self.formula_bar.hasFocus():
            return
        r, c = cur.row() + 1, cur.column() + 1
        cell = self.sheet.cells.get((r, c))
        self.formula_bar.setText(edit_text(cell, self.book.styles.get(cell.style if cell else 0)))

    def _formula_edited(self, text: str):
        self._fb_active = True
        if text.startswith("=") and re.search(r"[A-Za-z]{2,}$", text):
            self.formula_bar.setCompleter(self._fn_completer)
        else:
            self.formula_bar.setCompleter(None)

    def _formula_commit(self):
        cur = self.view.currentIndex()
        if not cur.isValid():
            return
        text = self.formula_bar.text()
        self._fb_active = False
        self._point_anchor = None
        if self.commit_input(cur.row() + 1, cur.column() + 1, text):
            self.view.setFocus()
            self.view.goto(cur.row() + 2, cur.column() + 1)

    def eventFilter(self, obj, ev):
        if obj is self.formula_bar:
            if ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
                self._fb_active = False
                self._point_anchor = None
                self.formula_bar.clearFocus()
                self._update_formula_bar()
                self.view.setFocus()
                return True
            if ev.type() == QEvent.FocusIn:
                self._fb_active = True
        return super().eventFilter(obj, ev)

    def formula_target(self):
        """Line edit currently receiving a formula (for point mode), or None."""
        if getattr(self, "_fb_active", False) and self.formula_bar.text().startswith("="):
            return self.formula_bar
        if self.view.state() == QAbstractItemView.State.EditingState:
            w = QApplication.focusWidget()
            if isinstance(w, QLineEdit) and w.text().startswith("=") and w is not self.formula_bar:
                txt = w.text()
                if txt and txt[-1] in "=(,+-*/^&<>:; ":
                    return w
        return None

    def insert_reference(self, edit: QLineEdit, r: int, c: int, extend: bool):
        ref = a1(r, c)
        if extend and self._point_anchor is not None:
            ar, ac = self._point_anchor
            ref = range_a1(min(ar, r), min(ac, c), max(ar, r), max(ac, c))
        else:
            self._point_anchor = (r, c)
        text = edit.text()
        pos = edit.cursorPosition()
        if self._last_ref_span and self._last_ref_span[0] is edit and self._last_ref_span[2] == pos:
            start = self._last_ref_span[1]
            text = text[:start] + text[pos:]
            pos = start
        new = text[:pos] + ref + text[pos:]
        edit.setText(new)
        edit.setCursorPosition(pos + len(ref))
        self._last_ref_span = (edit, pos, pos + len(ref))
        edit.setFocus()
        if edit is not self.formula_bar:
            self.formula_bar.setText(new)

    def _name_box_go(self):
        text = self.name_box.currentText().strip()
        if not text:
            return
        nm = self.book.names.get(text.upper())
        if nm:
            text = nm.lstrip("=")
        parsed = D.parse_a1_range(text)
        if parsed is None:
            show_error(self, _t("Go To"), _t("Invalid reference: {0}").format(text))
            return
        sheet_name, (r1, c1, r2, c2) = parsed
        if sheet_name:
            sh = self.book.sheet_by_name(sheet_name)
            if sh is not None:
                self.switch_sheet(sh)
        self.view.select_range(r1, c1, min(r2, MAX_ROWS), min(c2, MAX_COLS))
        self.view.setFocus()

    def _refresh_name_box_items(self):
        cur = self.name_box.currentText()
        self.name_box.blockSignals(True)
        self.name_box.clear()
        self.name_box.addItems(sorted(self.book.names))
        self.name_box.setEditText(cur)
        self.name_box.blockSignals(False)

    # ================================================================== selection helpers
    def sel_bounds(self, clamp: bool = True) -> tuple[int, int, int, int]:
        r1, c1, r2, c2 = self.view.selection_bounds()
        if clamp:
            sh = self.sheet
            if r2 - r1 > 5000 and r2 > sh.max_row:
                r2 = max(r1, sh.max_row)
            if c2 - c1 > 500 and c2 > sh.max_col:
                c2 = max(c1, sh.max_col)
        return r1, c1, r2, c2

    def sel_cells(self):
        out = []
        sh = self.sheet
        for (r1, c1, r2, c2) in self.view.selected_ranges():
            if (r2 - r1 + 1) * (c2 - c1 + 1) > 200_000:
                r2 = min(r2, max(r1, sh.max_row))
                c2 = min(c2, max(c1, sh.max_col))
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    out.append((r, c))
        return out

    def current_rc(self):
        cur = self.view.currentIndex()
        return (cur.row() + 1, cur.column() + 1) if cur.isValid() else (1, 1)

    def _selection_changed(self):
        sh = self.sheet
        total = 0.0
        count = nums = 0
        mn, mx = None, None
        for (r1, c1, r2, c2) in self.view.selected_ranges():
            area = (r2 - r1 + 1) * (c2 - c1 + 1)
            if area > 5000:
                items = [((r, c), cell) for (r, c), cell in sh.cells.items() if r1 <= r <= r2 and c1 <= c <= c2]
            else:
                items = [((r, c), sh.cells.get((r, c))) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]
            for _k, cell in items:
                if cell is None or cell.value is None:
                    continue
                count += 1
                v = cell.value
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    nums += 1
                    total += v
                    mn = v if mn is None else min(mn, v)
                    mx = v if mx is None else max(mx, v)
        if count > 1:
            from app.sheet.numfmt import general

            parts = [_t("Count: {0}").format(count)]
            if nums:
                parts = [_t("Average: {0}").format(general(total / nums)), _t("Count: {0}").format(count),
                         _t("Sum: {0}").format(general(total)), _t("Min: {0}").format(general(mn)),
                         _t("Max: {0}").format(general(mx))]
            self.stats.setText("    ".join(parts))
        else:
            self.stats.setText(_t("Read-only") if self.read_only else "")

    # ================================================================== editing commands
    def clear_contents(self, current_only: bool = False, what: str = "contents"):
        sh = self.sheet
        cells = [self.current_rc()] if current_only else self.sel_cells()
        before, after = {}, {}
        for r, c in cells:
            cell = sh.cells.get((r, c))
            if cell is None:
                if what in ("comments", "all"):
                    pass
                continue
            before[(r, c)] = (cell.value, cell.formula, cell.style)
            if what == "contents":
                after[(r, c)] = (None, None, cell.style) if cell.style else None
            elif what == "formats":
                after[(r, c)] = (cell.value, cell.formula, 0) if (cell.value is not None or cell.formula) else None
            else:
                after[(r, c)] = None
        if what in ("comments", "all"):
            for r, c in cells:
                sh.comments.pop((r, c), None)
            self.view.viewport().update()
        self.push_states(before, after, _t("Clear"))
        if self.recorder is not None and not current_only:
            self.recorder.record(f"sheet.clear({range_a1(*self.sel_bounds())!r})")

    def _style_cells(self, fn, text: str, record: str | None = None):
        if self.read_only:
            return
        cells = self.sel_cells()
        if len(cells) > 500_000:
            show_info(self, _t("Format"), _t("The selection is too large to format cell by cell."))
            return
        states = self.book.styled_states(self.sheet, cells, fn)
        before = {k: self.book.snapshot(self.sheet, *k) for k in states}
        self.push_states(before, states, text)
        if self.recorder is not None and record:
            self.recorder.record(f"sheet.format({range_a1(*self.sel_bounds())!r}, {record})")

    def current_style(self) -> Style:
        r, c = self.current_rc()
        return self.sheet.style_of(r, c)

    def toggle_style(self, attr: str):
        new = not getattr(self.current_style(), attr)
        self._style_cells(lambda s: s.with_(**{attr: new}), _t("Format"), f"{attr}={new}")

    def set_style_attr(self, **kw):
        rec = ", ".join(f"{k}={v!r}" for k, v in kw.items())
        self._style_cells(lambda s: s.with_(**kw), _t("Format"), rec)

    def set_font_size(self, text):
        try:
            size = float(str(text).replace(",", "."))
        except ValueError:
            return
        self.set_style_attr(size=None if size == 11 else size)

    def step_font(self, d: int):
        cur = self.current_style().size or 11
        sizes = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72]
        nxt = next((s for s in sizes if s > cur), cur + 4) if d > 0 else next((s for s in reversed(sizes) if s < cur), max(1, cur - 1))
        self.set_style_attr(size=None if nxt == 11 else float(nxt))

    def set_borders(self, mode: str):
        r1, c1, r2, c2 = self.sel_bounds()
        thin, thick = Border("thin"), Border("medium")

        def fn_for(r, c):
            def fn(s: Style):
                if mode == "none":
                    return s.with_(border_top=None, border_bottom=None, border_left=None, border_right=None)
                if mode == "all":
                    return s.with_(border_top=thin, border_bottom=thin, border_left=thin, border_right=thin)
                b = thick if mode == "thick_outside" else thin
                kw = {}
                if mode in ("outside", "thick_outside"):
                    if r == r1:
                        kw["border_top"] = b
                    if r == r2:
                        kw["border_bottom"] = b
                    if c == c1:
                        kw["border_left"] = b
                    if c == c2:
                        kw["border_right"] = b
                elif mode == "bottom" and r == r2:
                    kw["border_bottom"] = thin
                elif mode == "top" and r == r1:
                    kw["border_top"] = thin
                elif mode == "left" and c == c1:
                    kw["border_left"] = thin
                elif mode == "right" and c == c2:
                    kw["border_right"] = thin
                elif mode == "double_bottom" and r == r2:
                    kw["border_bottom"] = Border("double")
                return s.with_(**kw)
            return fn
        states = {}
        sh = self.sheet
        for r, c in self.sel_cells():
            states.update(self.book.styled_states(sh, [(r, c)], fn_for(r, c)))
        before = {k: self.book.snapshot(sh, *k) for k in states}
        self.push_states(before, states, _t("Borders"))

    def number_format(self, fmt: str):
        self.set_style_attr(numfmt=fmt)

    def change_decimals(self, d: int):
        st = self.current_style()
        fmt = st.numfmt
        if fmt == "General":
            r, c = self.current_rc()
            v = self.sheet.value(r, c)
            dec = 0
            if isinstance(v, float) and v != int(v):
                dec = min(10, len(f"{v:.10f}".rstrip("0").split(".")[1]))
            fmt = "0" + ("." + "0" * dec if dec else "")
        m = re.search(r"0(\.0+)?", fmt)
        if not m:
            return
        cur = len(m.group(1)) - 1 if m.group(1) else 0
        new = max(0, cur + d)
        rep = "0" + ("." + "0" * new if new else "")
        self.number_format(fmt[:m.start()] + rep + fmt[m.end():])

    def format_cells_dialog(self, tab: int = 0):
        r, c = self.current_rc()
        v = self.sheet.value(r, c)
        d = D.FormatCellsDialog(self, self.current_style(), v if isinstance(v, (int, float)) and not isinstance(v, bool) else 1234.5678, tab)
        if d.exec():
            new = d.result_style()
            base = self.current_style()
            changed = {f: getattr(new, f) for f in new.__dataclass_fields__ if getattr(new, f) != getattr(base, f)}
            if changed:
                self._style_cells(lambda s: s.with_(**changed), _t("Format Cells"))

    def merge(self, mode: str = "center"):
        r1, c1, r2, c2 = self.sel_bounds()
        sh = self.sheet
        if mode == "unmerge":
            def fn():
                sh.merges[:] = [m for m in sh.merges if not (m[0] <= r2 and m[2] >= r1 and m[1] <= c2 and m[3] >= c1)]
            self.structural(_t("Unmerge Cells"), fn)
            return
        if (r1, c1) == (r2, c2):
            return
        lost = [(r, c) for (r, c), cell in sh.cells.items() if r1 <= r <= r2 and c1 <= c <= c2 and (r, c) != (r1, c1)
                and cell.value is not None]
        if lost and not confirm(self, _t("Merge Cells"), _t("Merging keeps only the upper-left value. Continue?")):
            return

        def fn():
            rows = [(r, r) for r in range(r1, r2 + 1)] if mode == "across" else [(r1, r2)]
            for (a, b) in rows:
                sh.merges[:] = [m for m in sh.merges if not (m[0] <= b and m[2] >= a and m[1] <= c2 and m[3] >= c1)]
                if (a, c1) != (b, c2):
                    sh.merges.append((a, c1, b, c2))
            for (r, c) in lost:
                if mode != "across" or c != c1:
                    sh.cells.pop((r, c), None)
            if mode == "center":
                cell = sh.cells.get((r1, c1)) or Cell()
                sh.cells[(r1, c1)] = cell
                cell.style = self.book.styles.intern(self.book.styles.get(cell.style).with_(halign="center", valign="center"))
            self.book.engine.rebuild()
            self.book.engine.recalc_all()
        self.structural(_t("Merge Cells"), fn)

    def insert_rows(self, above: bool = True):
        r1, c1, r2, c2 = self.sel_bounds()
        n = r2 - r1 + 1 if r2 - r1 < 1000 else 1
        at = r1 if above else r2 + 1
        self.structural(_t("Insert Rows"), lambda: self.book.insert_rows(self.sheet, at, n))

    def insert_cols(self, left: bool = True):
        r1, c1, r2, c2 = self.sel_bounds()
        n = c2 - c1 + 1 if c2 - c1 < 200 else 1
        at = c1 if left else c2 + 1
        self.structural(_t("Insert Columns"), lambda: self.book.insert_cols(self.sheet, at, n))

    def delete_rows(self):
        r1, _c1, r2, _c2 = self.view.selection_bounds()
        r2 = min(r2, max(r1, self.sheet.max_row))
        self.structural(_t("Delete Rows"), lambda: self.book.delete_rows(self.sheet, r1, r2 - r1 + 1))

    def delete_cols(self):
        _r1, c1, _r2, c2 = self.view.selection_bounds()
        c2 = min(c2, max(c1, self.sheet.max_col))
        self.structural(_t("Delete Columns"), lambda: self.book.delete_cols(self.sheet, c1, c2 - c1 + 1))

    def set_row_height(self):
        r1, _c1, r2, _c2 = self.sel_bounds()
        h, ok = QInputDialog.getInt(self, _t("Row Height"), _t("Height (pixels):"), self.sheet.row_height(r1), 4, 800)
        if ok:
            self.structural(_t("Row Height"), lambda: self.sheet.row_heights.update({r: h for r in range(r1, r2 + 1)}))

    def set_col_width(self):
        _r1, c1, _r2, c2 = self.sel_bounds()
        w, ok = QInputDialog.getInt(self, _t("Column Width"), _t("Width (pixels):"), self.sheet.col_width(c1), 4, 2000)
        if ok:
            self.structural(_t("Column Width"), lambda: self.sheet.col_widths.update({c: w for c in range(c1, c2 + 1)}))

    def autofit_cols(self):
        _r1, c1, _r2, c2 = self.sel_bounds()
        from PySide6.QtGui import QFontMetrics

        sh = self.sheet

        def fn():
            for c in range(c1, c2 + 1):
                best = 30
                for (r, cc), cell in sh.cells.items():
                    if cc == c and cell.value is not None and sh.merge_at(r, c) is None:
                        st = self.book.styles.get(cell.style)
                        fm = QFontMetrics(self.model.font_for(st, self.view.font()))
                        best = max(best, fm.horizontalAdvance(sh.display(r, c)[0]) + 12)
                sh.col_widths[c] = min(600, int(best / self.zoom))
        self.structural(_t("AutoFit Column Width"), fn)

    def hide_rows(self, hide: bool = True):
        r1, _c1, r2, _c2 = self.view.selection_bounds()
        sh = self.sheet

        def fn():
            for r in range(r1, min(r2, r1 + 100000) + 1):
                (sh.hidden_rows.add if hide else sh.hidden_rows.discard)(r)
            if not hide and r1 > 1:
                sh.hidden_rows.discard(r1 - 1)
        self.structural(_t("Hide Rows") if hide else _t("Unhide Rows"), fn)

    def hide_cols(self, hide: bool = True):
        _r1, c1, _r2, c2 = self.view.selection_bounds()
        sh = self.sheet

        def fn():
            for c in range(c1, min(c2, c1 + 2000) + 1):
                (sh.hidden_cols.add if hide else sh.hidden_cols.discard)(c)
            if not hide and c1 > 1:
                sh.hidden_cols.discard(c1 - 1)
        self.structural(_t("Hide Columns") if hide else _t("Unhide Columns"), fn)

    def _col_resized(self, idx, _old, new):
        if self._loading or self.view.signalsBlocked():
            return
        w = int(round(new / self.zoom))
        if self.sheet.col_widths.get(idx + 1, self.sheet.default_col_width) != w:
            self.sheet.col_widths[idx + 1] = w
            self.model.invalidate()
            self._reposition_floats()
            super().set_modified(True)

    def _row_resized(self, idx, _old, new):
        if self._loading or self.view.signalsBlocked():
            return
        h = int(round(new / self.zoom))
        if self.sheet.row_heights.get(idx + 1, self.sheet.default_row_height) != h:
            self.sheet.row_heights[idx + 1] = h
            self._reposition_floats()
            super().set_modified(True)

    def _header_menu(self, axis: str, pos):
        m = QMenu(self)
        if axis == "col":
            m.addAction(_t("Insert Columns Left"), lambda: self.insert_cols(True))
            m.addAction(_t("Insert Columns Right"), lambda: self.insert_cols(False))
            m.addAction(_t("Delete Columns"), self.delete_cols)
            m.addSeparator()
            m.addAction(_t("Column Width\u2026"), self.set_col_width)
            m.addAction(_t("AutoFit Width"), self.autofit_cols)
            m.addAction(_t("Hide"), lambda: self.hide_cols(True))
            m.addAction(_t("Unhide"), lambda: self.hide_cols(False))
            hdr = self.view.horizontalHeader()
        else:
            m.addAction(_t("Insert Rows Above"), lambda: self.insert_rows(True))
            m.addAction(_t("Insert Rows Below"), lambda: self.insert_rows(False))
            m.addAction(_t("Delete Rows"), self.delete_rows)
            m.addSeparator()
            m.addAction(_t("Row Height\u2026"), self.set_row_height)
            m.addAction(_t("Hide"), lambda: self.hide_rows(True))
            m.addAction(_t("Unhide"), lambda: self.hide_rows(False))
            hdr = self.view.verticalHeader()
        m.addSeparator()
        m.addAction(self.a_copy)
        m.addAction(self.a_cut)
        m.addAction(self.a_paste)
        m.addAction(_t("Clear Contents"), self.clear_contents)
        m.exec(hdr.mapToGlobal(pos))

    # ================================================================== clipboard
    def copy(self, cut: bool = False):
        r1, c1, r2, c2 = self.sel_bounds()
        sh = self.sheet
        states = self.book.block_states(sh, r1, c1, r2, c2)
        token = uuid.uuid4().hex
        _CLIPBOARD.clear()
        _CLIPBOARD[token] = {"book": id(self.book), "sid": sh.id, "rng": (r1, c1, r2, c2), "states": states,
                             "cut": cut, "comments": {k: v for k, v in sh.comments.items()
                                                      if r1 <= k[0] <= r2 and c1 <= k[1] <= c2}}
        import html as _html

        lines, rows_html = [], []
        for r in range(r1, r2 + 1):
            vals = [sh.display(r, c)[0] for c in range(c1, c2 + 1)]
            lines.append("\t".join(vals))
            rows_html.append("<tr>" + "".join(f"<td>{_html.escape(v)}</td>" for v in vals) + "</tr>")
        md = QMimeData()
        md.setData(CLIP_MIME, token.encode())
        md.setText("\n".join(lines))
        md.setHtml("<table>" + "".join(rows_html) + "</table>")
        QApplication.clipboard().setMimeData(md)
        self.cut_range = (sh.id, r1, c1, r2, c2) if cut else None
        self.view.viewport().update()
        self.statusChanged.emit(_t("Copied {0}").format(range_a1(r1, c1, r2, c2)))

    def cut(self):
        self.copy(cut=True)

    def paste(self, mode: str = "all", transpose: bool = False, skip_blanks: bool = False):
        if self.read_only:
            return
        md = QApplication.clipboard().mimeData()
        tr, tc = self.sel_bounds()[:2]
        sh = self.sheet
        before, after = {}, {}
        token = bytes(md.data(CLIP_MIME)).decode() if md.hasFormat(CLIP_MIME) else ""
        payload = _CLIPBOARD.get(token)
        if payload is not None:
            r1, c1, r2, c2 = payload["rng"]
            src_states = payload["states"]
            src_sheet = self.book.sheet_by_id(payload["sid"]) if payload["book"] == id(self.book) else None
            moved = {}
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    st = src_states.get((r, c))
                    dr, dc = (c - c1, r - r1) if transpose else (r - r1, c - c1)
                    nr, nc = tr + dr, tc + dc
                    if nr > MAX_ROWS or nc > MAX_COLS:
                        continue
                    if st is None and skip_blanks:
                        continue
                    if st is not None:
                        value, formula, style = st
                        if formula and not payload["cut"]:
                            from app.sheet.formula.parser import translate_formula

                            formula = translate_formula(formula, nr - r, nc - c)
                        cur = sh.cells.get((nr, nc))
                        if mode == "values":
                            if formula and src_sheet is not None:
                                src = src_sheet.cells.get((r, c))
                                value = src.value if src else value
                            st = (value, None, cur.style if cur else 0)
                        elif mode == "formats":
                            st = ((cur.value if cur else None), (cur.formula if cur else None), style)
                        elif mode == "formulas":
                            st = (value, formula, cur.style if cur else 0)
                        else:
                            st = (value, formula, style)
                        if st[0] is None and st[1] is None and not st[2]:
                            st = None
                    moved[(nr, nc)] = st
            if payload["cut"] and src_sheet is sh and mode == "all":
                for (r, c) in src_states:
                    if (r, c) not in moved:
                        before[(r, c)] = self.book.snapshot(sh, r, c)
                        after[(r, c)] = None
            for k, st in moved.items():
                before[k] = self.book.snapshot(sh, *k)
                after[k] = st
            if payload["cut"]:
                if src_sheet is not None and src_sheet is not sh and mode == "all":
                    sb = {k: self.book.snapshot(src_sheet, *k) for k in src_states}
                    self.undo.beginMacro(_t("Move"))
                    self.push_states(sb, {k: None for k in src_states}, _t("Cut"), src_sheet.id)
                    self.push_states(before, after, _t("Paste"))
                    self.undo.endMacro()
                    self.cut_range = None
                    _CLIPBOARD.clear()
                    return
                self.cut_range = None
                _CLIPBOARD.clear()
            if mode == "all":
                for (r, c), text in payload.get("comments", {}).items():
                    sh.comments[(tr + r - r1, tc + c - c1)] = text
        elif md.hasImage():
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice

            img = md.imageData()
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            self.add_image(bytes(ba.data()), "png", (img.width(), img.height()))
            return
        elif md.hasText():
            text = md.text().rstrip("\n")
            rows = [line.split("\t") for line in text.split("\n")]
            if transpose:
                width = max(len(r) for r in rows)
                rows = [[rows[i][j] if j < len(rows[i]) else "" for i in range(len(rows))] for j in range(width)]
            for i, row in enumerate(rows):
                for j, val in enumerate(row):
                    r, c = tr + i, tc + j
                    if val == "" and skip_blanks:
                        continue
                    before[(r, c)] = self.book.snapshot(sh, r, c)
                    after[(r, c)] = self.book.state_for_input(sh, r, c, val.strip("\r"))
        else:
            return
        self.push_states(before, after, _t("Paste"))
        if after:
            ks = list(after)
            self.view.select_range(min(k[0] for k in ks), min(k[1] for k in ks), max(k[0] for k in ks),
                                   max(k[1] for k in ks), (tr, tc))

    def paste_special(self):
        d = D.PasteSpecialDialog(self)
        if d.exec():
            self.paste(d.mode(), d.transpose.isChecked(), d.skip_blanks.isChecked())

    # ================================================================== fill / autosum / series
    def fill_range(self, src: tuple, dst: tuple):
        sh = self.sheet
        states = self.book.fill_states(sh, src, dst)
        before = {k: self.book.snapshot(sh, *k) for k in states}
        self.push_states(before, states, _t("Fill"))
        self.view.select_range(*dst)

    def fill_direction(self, direction: str):
        r1, c1, r2, c2 = self.sel_bounds()
        if direction == "down" and r2 > r1:
            src = (r1, c1, r1, c2)
        elif direction == "right" and c2 > c1:
            src = (r1, c1, r2, c1)
        elif direction == "up" and r2 > r1:
            src = (r2, c1, r2, c2)
        elif direction == "left" and c2 > c1:
            src = (r1, c2, r2, c2)
        else:
            if direction == "down" and r1 > 1:
                src, r1 = (r1 - 1, c1, r1 - 1, c2), r1 - 1
            elif direction == "right" and c1 > 1:
                src, c1 = (r1, c1 - 1, r2, c1 - 1), c1 - 1
            else:
                return
        sh = self.sheet
        states = {}
        if direction in ("down", "up"):
            for r in range(r1, r2 + 1):
                if r == src[0]:
                    continue
                for c in range(c1, c2 + 1):
                    s = self.book.copy_states({(src[0], c): self.book.snapshot(sh, src[0], c)}, r - src[0], 0)
                    states.update(s)
        else:
            for c in range(c1, c2 + 1):
                if c == src[1]:
                    continue
                for r in range(r1, r2 + 1):
                    s = self.book.copy_states({(r, src[1]): self.book.snapshot(sh, r, src[1])}, 0, c - src[1])
                    states.update(s)
        before = {k: self.book.snapshot(sh, *k) for k in states}
        self.push_states(before, states, _t("Fill"))

    def fill_series(self):
        d = D.SeriesDialog(self)
        if not d.exec():
            return
        r, c = self.current_rc()
        vals = d.values()
        sh = self.sheet
        after = {}
        for i, v in enumerate(vals):
            k = (r + i, c) if d.dir.currentIndex() == 0 else (r, c + i)
            cell = sh.cells.get(k)
            after[k] = (float(v), None, cell.style if cell else 0)
        before = {k: self.book.snapshot(sh, *k) for k in after}
        self.push_states(before, after, _t("Fill Series"))

    def autosum(self, func: str = "SUM"):
        sh = self.sheet
        r1, c1, r2, c2 = self.sel_bounds()

        def numeric(r, c):
            v = sh.value(r, c)
            cell = sh.cells.get((r, c))
            return cell is not None and isinstance(v, (int, float)) and not isinstance(v, bool)

        if (r1, c1) == (r2, c2):
            r, c = r1, c1
            top = r - 1
            while top >= 1 and numeric(top, c):
                top -= 1
            if top < r - 1:
                formula = f"={func}({range_a1(top + 1, c, r - 1, c)})"
            else:
                left = c - 1
                while left >= 1 and numeric(r, left):
                    left -= 1
                formula = f"={func}({range_a1(r, left + 1, r, c - 1)})" if left < c - 1 else f"={func}()"
            self.pending_editor_text = formula
            self.pending_cursor_end = True
            self.view.edit(self.view.currentIndex())
            return
        after = {}
        for c in range(c1, c2 + 1):
            after[(r2 + 1, c)] = self.book.state_for_input(sh, r2 + 1, c, f"={func}({range_a1(r1, c, r2, c)})")
        before = {k: self.book.snapshot(sh, *k) for k in after}
        self.push_states(before, after, _t("AutoSum"))

    # ================================================================== sort & filter
    def _data_region(self):
        """Selection, or the contiguous block around the current cell when one cell is selected."""
        r1, c1, r2, c2 = self.sel_bounds()
        if (r1, c1) != (r2, c2):
            return r1, c1, r2, c2
        sh = self.sheet

        def filled(r, c):
            cell = sh.cells.get((r, c))
            return cell is not None and cell.value is not None

        changed = True
        while changed:
            changed = False
            if r1 > 1 and any(filled(r1 - 1, c) for c in range(c1, c2 + 1)):
                r1 -= 1
                changed = True
            if r2 < MAX_ROWS and any(filled(r2 + 1, c) for c in range(c1, c2 + 1)):
                r2 += 1
                changed = True
            if c1 > 1 and any(filled(r, c1 - 1) for r in range(r1, r2 + 1)):
                c1 -= 1
                changed = True
            if c2 < MAX_COLS and any(filled(r, c2 + 1) for r in range(r1, r2 + 1)):
                c2 += 1
                changed = True
        return r1, c1, r2, c2

    def _has_header(self, rng) -> bool:
        r1, c1, r2, c2 = rng
        sh = self.sheet
        first = [sh.value(r1, c) for c in range(c1, c2 + 1)]
        second = [sh.value(r1 + 1, c) for c in range(c1, c2 + 1)] if r2 > r1 else []
        return all(isinstance(v, str) for v in first if v is not None) and any(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in second) or (
            bool(self.sheet.autofilter) and self.sheet.autofilter[0] == r1)

    def sort(self, ascending: bool = True, rng=None, keys=None, header=None):
        rng = rng or self._data_region()
        r1, c1, r2, c2 = rng
        if header is None:
            header = self._has_header(rng)
        if keys is None:
            keys = [(self.current_rc()[1] if c1 <= self.current_rc()[1] <= c2 else c1, ascending)]
        sh = self.sheet
        states = self.book.sort_states(sh, rng, keys, header)
        before = {k: self.book.snapshot(sh, *k) for k in states}
        self.push_states(before, states, _t("Sort"))

    def sort_dialog(self):
        rng = self._data_region()
        header = self._has_header(rng)
        headers = {c: self.sheet.display(rng[0], c)[0] for c in range(rng[1], rng[3] + 1)} if header else {}
        d = D.SortDialog(self, rng, header, headers)
        if d.exec() and d.keys():
            self.sort(rng=rng, keys=d.keys(), header=d.header.isChecked())

    def toggle_filter(self):
        sh = self.sheet

        def fn():
            if sh.autofilter:
                sh.autofilter = None
                sh.filters.clear()
                sh.filtered_rows.clear()
            else:
                sh.autofilter = self._data_region()
        self.structural(_t("Filter"), fn)

    def clear_filters(self):
        sh = self.sheet

        def fn():
            sh.filters.clear()
            sh.filtered_rows.clear()
        self.structural(_t("Clear Filter"), fn)

    def _filter_popup(self, col: int, gpos):
        sh = self.sheet
        af = sh.autofilter
        if not af:
            return
        r1, c1, r2, c2 = af
        r2 = max(r2, sh.max_row) if r2 >= sh.max_row - 1 else r2
        values = sorted({sh.display(r, col)[0] for r in range(r1 + 1, r2 + 1)}, key=lambda s: (s == "", s.lower()))
        spec = sh.filters.get(col)

        def apply(selected, condition):
            def fn():
                if selected is None and not condition:
                    sh.filters.pop(col, None)
                else:
                    sh.filters[col] = FilterSpec(selected, condition)
                self._recompute_filter()
            self.structural(_t("Filter"), fn)

        def do_sort(asc):
            self.sort(asc, rng=(r1, c1, r2, c2), keys=[(col, asc)], header=True)

        FilterPopup = D.FilterPopup
        menu = FilterPopup(self, values[:5000], spec.values if spec else None, spec.condition if spec else "", apply, do_sort)
        menu.exec(gpos)

    def _recompute_filter(self):
        from app.sheet.formula.functions import make_criteria

        sh = self.sheet
        sh.filtered_rows.clear()
        if not sh.autofilter:
            return
        r1, c1, r2, c2 = sh.autofilter
        r2 = max(r2, sh.max_row)
        preds = {c: (spec, make_criteria(spec.condition) if spec.condition else None) for c, spec in sh.filters.items()}
        for r in range(r1 + 1, r2 + 1):
            for c, (spec, pred) in preds.items():
                text = sh.display(r, c)[0]
                ok = spec.values is None or text in spec.values
                if ok and pred is not None:
                    ok = pred(sh.value(r, c))
                if not ok:
                    sh.filtered_rows.add(r)
                    break

    # ================================================================== find / replace / goto
    def show_find(self, replace: bool = False):
        if self.find_dialog is None:
            self.find_dialog = D.FindReplaceSheetDialog(self, replace)
        self.find_dialog.set_mode(replace)
        self.find_dialog.show()
        self.find_dialog.raise_()
        self.find_dialog.find.setFocus()

    def _matches(self, sh, text, case, whole, formulas):
        needle = text if case else text.lower()
        out = []
        for (r, c), cell in sh.cells.items():
            hay = cell.formula if (formulas and cell.formula) else sh.display(r, c)[0]
            if hay is None:
                continue
            h = hay if case else hay.lower()
            if (whole and h == needle) or (not whole and needle in h):
                out.append((r, c))
        out.sort()
        return out

    def find_next(self, text, case=False, whole=False, formulas=False, all_sheets=False) -> bool:
        if not text:
            return False
        sheets = self.book.sheets if all_sheets else [self.sheet]
        start = self.current_rc()
        order = sheets[sheets.index(self.sheet):] + sheets[:sheets.index(self.sheet)] if self.sheet in sheets else sheets
        for i, sh in enumerate(order):
            ms = self._matches(sh, text, case, whole, formulas)
            if i == 0:
                ms = [m for m in ms if m > start] or ([] if len(order) > 1 else ms)
            if ms:
                if sh is not self.sheet:
                    self.switch_sheet(sh)
                self.view.goto(*ms[0])
                return True
        return False

    def replace_matches(self, new, text, case=False, whole=False, formulas=False, all_sheets=False,
                        only_current=False) -> int:
        if not text:
            return 0
        sheets = self.book.sheets if all_sheets and not only_current else [self.sheet]
        count = 0
        self.undo.beginMacro(_t("Replace"))
        for sh in sheets:
            targets = [self.current_rc()] if only_current else self._matches(sh, text, case, whole, formulas)
            before, after = {}, {}
            for r, c in targets:
                cell = sh.cells.get((r, c))
                if cell is None:
                    continue
                src = cell.formula if cell.formula else edit_text(cell, self.book.styles.get(cell.style))
                if whole:
                    hit = (src == text) if case else (src.lower() == text.lower())
                    res = new if hit else src
                else:
                    res = re.sub(re.escape(text), lambda m: new, src, flags=0 if case else re.I)
                if res != src:
                    before[(r, c)] = self.book.snapshot(sh, r, c)
                    after[(r, c)] = self.book.state_for_input(sh, r, c, res)
                    count += 1
            self.push_states(before, after, _t("Replace"), sh.id)
        self.undo.endMacro()
        return count

    def goto_dialog(self):
        d = D.GoToDialog(self, sorted(self.book.names))
        if d.exec():
            self.name_box.setEditText(d.ref.currentText())
            self._name_box_go()

    # ================================================================== comments / links
    def edit_comment(self):
        r, c = self.current_rc()
        sh = self.sheet
        text, ok = QInputDialog.getMultiLineText(self, _t("Note"), _t("Note for {0}:").format(a1(r, c)),
                                                 sh.comments.get((r, c), ""))
        if ok:
            def fn():
                if text.strip():
                    sh.comments[(r, c)] = text
                else:
                    sh.comments.pop((r, c), None)
            self.structural(_t("Edit Note"), fn)

    def delete_comment(self):
        cells = self.sel_cells()
        sh = self.sheet
        self.structural(_t("Delete Note"), lambda: [sh.comments.pop(k, None) for k in cells])

    def insert_link(self):
        r, c = self.current_rc()
        sh = self.sheet
        url, ok = QInputDialog.getText(self, _t("Hyperlink"), _t("Address (URL, or #Sheet!A1):"),
                                       text=sh.links.get((r, c), "https://"))
        if not ok:
            return

        def fn():
            if url.strip() and url.strip() != "https://":
                sh.links[(r, c)] = url.strip()
                if sh.value(r, c) is None:
                    sh.cells[(r, c)] = Cell(url.strip(), None, sh.cells[(r, c)].style if (r, c) in sh.cells else 0)
            else:
                sh.links.pop((r, c), None)
            self.book.engine.rebuild()
        self.structural(_t("Hyperlink"), fn)

    def _open_link(self, r: int, c: int):
        url = self.sheet.links.get((r, c), "")
        if url.startswith("#"):
            self.name_box.setEditText(url[1:])
            self._name_box_go()
        elif url:
            QDesktopServices.openUrl(QUrl(url))

    # ================================================================== formulas
    def insert_function(self, name: str | None = None):
        if not name:
            d = D.InsertFunctionDialog(self)
            if not d.exec() or not d.chosen():
                return
            name = d.chosen()
        self.pending_editor_text = f"={name}("
        self.pending_cursor_end = True
        self.view.edit(self.view.currentIndex())

    def name_manager(self):
        r1, c1, r2, c2 = self.sel_bounds()
        from app.sheet.formula.parser import quote_sheet

        default = f"={quote_sheet(self.sheet.name)}!${index_to_col(c1)}${r1}" + (
            f":${index_to_col(c2)}${r2}" if (r1, c1) != (r2, c2) else "")
        d = D.NameManagerDialog(self, self.book.names, default)
        if d.exec():
            changed = set(self.book.names) ^ set(d.names) | {k for k in d.names if self.book.names.get(k) != d.names[k]}
            self.structural(_t("Name Manager"), lambda: (self.book.names.clear(), self.book.names.update(d.names)))
            if changed:
                self.book.engine.rebuild()
                keys = self.book.engine.recalc_all()
                self._after_change({k for k in keys})
            self._refresh_name_box_items()

    def toggle_formulas(self, checked: bool):
        self.show_formulas = checked
        self.model.cells_changed()
        self.view.viewport().update()

    def calculate_now(self):
        keys = self.book.engine.recalc_all()
        self._after_change(keys)
        self.statusChanged.emit(_t("Recalculated {0} formula(s)").format(len(keys)))

    def set_auto_calc(self, auto: bool):
        self.book.calc_auto = auto
        if auto:
            self.calculate_now()

    def trace_precedents(self):
        r, c = self.current_rc()
        prec = self.book.engine.precedent_cells((self.sheet.id, r, c))
        if not prec:
            self.statusChanged.emit(_t("The cell has no precedents."))
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel

        sel = QItemSelection()
        model = self.model
        for p in prec:
            if p[0] != self.sheet.id:
                continue
            if len(p) == 3:
                sel.select(model.index(p[1] - 1, p[2] - 1), model.index(p[1] - 1, p[2] - 1))
            else:
                sel.select(model.index(p[1] - 1, p[2] - 1), model.index(min(p[3], MAX_ROWS) - 1, min(p[4], MAX_COLS) - 1))
        self.view.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    # ================================================================== data tools
    def text_to_columns(self):
        r1, c1, r2, _c2 = self.sel_bounds()
        sh = self.sheet
        sample = [sh.display(r, c1)[0] for r in range(r1, min(r2, r1 + 8) + 1)]
        d = D.TextToColumnsDialog(self, sample)
        if not d.exec():
            return
        delim = d.delimiter()
        after = {}
        for r in range(r1, r2 + 1):
            v = sh.value(r, c1)
            if not isinstance(v, str):
                continue
            for j, part in enumerate(v.split(delim)):
                after[(r, c1 + j)] = self.book.state_for_input(sh, r, c1 + j, part.strip())
        before = {k: self.book.snapshot(sh, *k) for k in after}
        self.push_states(before, after, _t("Text to Columns"))

    def remove_duplicates(self):
        rng = self._data_region()
        r1, c1, r2, c2 = rng
        sh = self.sheet
        header = self._has_header(rng)
        cols = [(c, sh.display(r1, c)[0] if header else "") for c in range(c1, c2 + 1)]
        d = D.RemoveDuplicatesDialog(self, cols, header)
        if not d.exec() or not d.columns():
            return
        start = r1 + (1 if d.header.isChecked() else 0)
        seen = set()
        keep_rows = []
        for r in range(start, r2 + 1):
            key = tuple((str(sh.value(r, c)).lower() if isinstance(sh.value(r, c), str) else sh.value(r, c))
                        for c in d.columns())
            if key in seen:
                continue
            seen.add(key)
            keep_rows.append(r)
        removed = (r2 - start + 1) - len(keep_rows)
        after = {}
        for i, src in enumerate(keep_rows):
            for c in range(c1, c2 + 1):
                after[(start + i, c)] = self.book.snapshot(sh, src, c)
        for r in range(start + len(keep_rows), r2 + 1):
            for c in range(c1, c2 + 1):
                after[(r, c)] = None
        before = {k: self.book.snapshot(sh, *k) for k in after}
        self.push_states(before, after, _t("Remove Duplicates"))
        show_info(self, _t("Remove Duplicates"), _t("{0} duplicate row(s) removed; {1} unique row(s) remain.").format(
            removed, len(keep_rows)))

    def validation_dialog(self):
        rng = self.sel_bounds()
        sh = self.sheet
        cur = next((v for v in sh.validations if v.range == rng), None)
        d = D.ValidationDialog(self, range_a1(*rng), cur)
        if not d.exec():
            return
        parsed = D.parse_a1_range(d.range.text())
        if parsed is None:
            return
        new = d.result(parsed[1])

        def fn():
            sh.validations[:] = [v for v in sh.validations if v.range != parsed[1]]
            if new is not None:
                sh.validations.append(new)
        self.structural(_t("Data Validation"), fn)

    def cond_format_dialog(self, preset: str | None = None):
        rng = self.sel_bounds()
        sh = self.sheet
        if preset:
            presets = {
                "gt": CondFormat(rng, "cell", ">"), "lt": CondFormat(rng, "cell", "<"),
                "between": CondFormat(rng, "cell", "between"), "eq": CondFormat(rng, "cell", "="),
                "text": CondFormat(rng, "text"), "dup": CondFormat(rng, "duplicate"),
                "top10": CondFormat(rng, "top", value1="10", fill="#c6efce", color="#006100"),
                "bottom10": CondFormat(rng, "bottom", value1="10"),
                "bar": CondFormat(rng, "databar", fill="#638ec6"), "scale3": CondFormat(rng, "scale3"),
                "scale2": CondFormat(rng, "scale2", colors=("#ffffff", "#63be7b")),
            }
            cf = presets[preset]
            if cf.kind in ("cell", "text"):
                v1, ok = QInputDialog.getText(self, _t("Conditional Formatting"), _t("Value:"))
                if not ok:
                    return
                cf.value1 = v1
                if cf.op == "between":
                    v2, ok = QInputDialog.getText(self, _t("Conditional Formatting"), _t("And:"))
                    if not ok:
                        return
                    cf.value2 = v2
            self.structural(_t("Conditional Formatting"), lambda: sh.cond_formats.append(cf))
            return
        d = D.CondFormatDialog(self, range_a1(*rng), sh.cond_formats)
        if d.exec():
            new = d.new_rule(lambda t: (D.parse_a1_range(t) or (None, None))[1])

            def fn():
                sh.cond_formats[:] = d.existing + ([new] if new else [])
            self.structural(_t("Conditional Formatting"), fn)

    def clear_cond_formats(self):
        rng = self.sel_bounds()
        sh = self.sheet
        self.structural(_t("Clear Rules"), lambda: sh.cond_formats.__setitem__(
            slice(None), [cf for cf in sh.cond_formats if not (cf.range[0] <= rng[2] and cf.range[2] >= rng[0]
                                                               and cf.range[1] <= rng[3] and cf.range[3] >= rng[1])]))

    def show_vba(self):
        data = self.book.vba_source
        if not data:
            show_info(self, _t("VBA Macros"), _t("This workbook has no VBA macros."))
            return
        try:
            mods = sheet_features.vba_modules(sheet_features.vba_from_package(data) or b"")
        except Exception as exc:
            show_error(self, _t("VBA Macros"), _t("The macro project could not be read: {0}").format(exc))
            return
        sheet_features.VbaDialog(self, mods).exec()

    def _ai_settings(self):
        from app.shell.ai_dialogs import AISettingsDialog

        AISettingsDialog(self).exec()

    def format_as_table(self):
        """Home > Format as Table: choice of Excel table styles; saved as a real Excel table."""
        sheet_features.format_as_table(self)

    def cell_style(self, name: str):
        presets = {"Good": dict(fill="#c6efce", color="#006100"), "Bad": dict(fill="#ffc7ce", color="#9c0006"),
                   "Neutral": dict(fill="#ffeb9c", color="#9c5700"), "Title": dict(size=18.0, bold=True, color="#1f3864"),
                   "Heading 1": dict(size=15.0, bold=True, color="#1f3864", border_bottom=Border("thick", "#4472c4")),
                   "Heading 2": dict(size=13.0, bold=True, color="#1f3864", border_bottom=Border("medium", "#a9c4eb")),
                   "Total": dict(bold=True, border_top=Border("thin", "#4472c4"), border_bottom=Border("double", "#4472c4")),
                   "Note": dict(fill="#ffffcc", border_top=Border("thin", "#b2b2b2"), border_bottom=Border("thin", "#b2b2b2"),
                                border_left=Border("thin", "#b2b2b2"), border_right=Border("thin", "#b2b2b2")),
                   "Normal": dict(fill=None, color=None, bold=False, italic=False, size=None, border_top=None,
                                  border_bottom=None, border_left=None, border_right=None)}
        self.set_style_attr(**presets[name])

    def pivot_dialog(self):
        from app.sheet.pivot import PivotDialog

        rng = self._data_region()
        from app.sheet.formula.parser import range_name

        d = PivotDialog(self, self.sheet, range_name(*rng, self.sheet.name), D.parse_a1_range)
        if d.exec():
            self.write_table(d.result, _t("Pivot"), header_rows=1)

    def write_table(self, rows: list[list], base_name: str, header_rows: int = 1, chart: ChartSpec | None = None):
        """Write a 2-D list to a new sheet (used by pivot / analysis)."""
        def fn():
            name = base_name
            n = 1
            while self.book.sheet_by_name(name):
                n += 1
                name = f"{base_name} {n}"
            sh = self.book.add_sheet(name, self.book.sheets.index(self.sheet) + 1)
            hdr = self.book.styles.intern(Style(bold=True, fill="#dde7f5", border_bottom=Border("thin", "#8eaadb")))
            num = self.book.styles.intern(Style(numfmt="#,##0.00"))
            for i, row in enumerate(rows, start=1):
                for j, v in enumerate(row, start=1):
                    if v is None or v == "":
                        if i <= header_rows:
                            sh.cells[(i, j)] = Cell(None, None, hdr)
                        continue
                    if isinstance(v, int) and not isinstance(v, bool):
                        v = float(v)
                    style = hdr if i <= header_rows or (j == 1 and isinstance(v, str)) else (
                        num if isinstance(v, float) and v != int(v) else 0)
                    sh.cells[(i, j)] = Cell(v, None, style)
            sh.col_widths[1] = 160
            if chart is not None:
                chart.data = chart.data.replace("{sheet}", name)
                sh.charts.append(chart)
            sh.invalidate_bounds()
            self.book.engine.rebuild()
            self.sheet_to_show = sh
        self.sheet_to_show = None
        self.structural(base_name, fn)
        self._rebuild_tabs()
        if self.sheet_to_show is not None:
            self.switch_sheet(self.sheet_to_show)

    def analysis_dialog(self, tool: str | None = None):
        from app.sheet.analysis import AnalysisDialog, run_tool
        from app.sheet.formula.parser import range_name

        rng = self._data_region()
        d = AnalysisDialog(self, range_name(*rng, self.sheet.name), tool)
        if not d.exec():
            return
        try:
            parsed = D.parse_a1_range(d.input.text())
            if parsed is None:
                raise ValueError(_t("Invalid input range."))
            sh = self.book.sheet_by_name(parsed[0]) if parsed[0] else self.sheet
            extra = D.parse_a1_range(d.extra.text())[1] if d.extra.text().strip() else None
            title, rows = run_tool(d.tool(), sh, parsed[1], d.labels.isChecked(), extra, d.interval.value())
        except Exception as exc:
            show_error(self, _t("Data Analysis"), str(exc))
            return
        chart = None
        if d.tool() == "histogram" and d.chart.isChecked():
            chart = ChartSpec("column", f"{{sheet}}!A1:B{len(rows)}", _t("Histogram"), anchor=(2, 5), legend=False)
        self.write_table(rows, title, chart=chart)

    def goal_seek(self):
        from app.sheet.analysis import GoalSeekDialog, goal_seek

        r, c = self.current_rc()
        d = GoalSeekDialog(self, a1(r, c))
        if not d.exec():
            return
        s = D.parse_a1_range(d.set_cell.text())
        ch = D.parse_a1_range(d.change.text())
        try:
            target = float(d.target.text())
        except ValueError:
            show_error(self, _t("Goal Seek"), _t("The target must be a number."))
            return
        if s is None or ch is None:
            show_error(self, _t("Goal Seek"), _t("Invalid cell reference."))
            return
        sh = self.sheet
        set_rc, change_rc = s[1][:2], ch[1][:2]
        cell = sh.cells.get(set_rc)
        if cell is None or not cell.formula:
            show_error(self, _t("Goal Seek"), _t("The 'Set cell' must contain a formula."))
            return
        before = {change_rc: self.book.snapshot(sh, *change_rc)}
        ok, x, achieved = goal_seek(self.book, sh, set_rc, target, change_rc)
        self.book.apply_states(sh, before)
        after = {change_rc: (float(x), None, before[change_rc][2] if before[change_rc] else 0)}
        self.push_states(before, after, _t("Goal Seek"))
        if ok:
            show_info(self, _t("Goal Seek"), _t("Found a solution: {0} = {1:.10g} (result {2:.10g}).").format(
                a1(*change_rc), x, achieved))
        else:
            show_error(self, _t("Goal Seek"), _t("Goal Seek could not find a solution."))

    # ================================================================== protection / view
    def toggle_protect(self, checked: bool):
        sh = self.sheet
        if checked:
            pw, ok = QInputDialog.getText(self, _t("Protect Sheet"), _t("Password (optional):"), QLineEdit.Password)
            if not ok:
                self.a_protect.setChecked(False)
                return
            sh.protected, sh.protect_password = True, pw
        else:
            if sh.protect_password:
                pw, ok = QInputDialog.getText(self, _t("Unprotect Sheet"), _t("Password:"), QLineEdit.Password)
                if not ok or pw != sh.protect_password:
                    show_error(self, _t("Unprotect Sheet"), _t("The password is not correct."))
                    self.a_protect.setChecked(True)
                    return
            sh.protected, sh.protect_password = False, ""
        self.set_modified(True)
        self.model.layoutChanged.emit()

    def freeze(self, mode: str):
        sh = self.sheet
        r, c = self.current_rc()
        value = {"panes": (r - 1, c - 1), "row": (1, 0), "col": (0, 1), "none": (0, 0)}[mode]

        def fn():
            sh.freeze = value
        self.structural(_t("Freeze Panes"), fn)

    def toggle_grid(self, checked: bool):
        self.sheet.show_grid = checked
        self.set_modified(True)
        self.view.viewport().update()

    def toggle_headers(self, checked: bool):
        self.view.horizontalHeader().setVisible(checked)
        self.view.verticalHeader().setVisible(checked)

    def set_zoom(self, z: float):
        self.zoom = max(0.25, min(4.0, z))
        self._apply_zoom_sizes()
        self.zoom_slider.set_zoom(self.zoom)

    def _apply_zoom_sizes(self):
        z = self.zoom
        sh = self.sheet
        f = QFont(self.view.font())
        f.setPointSizeF(9 * z)
        self.view.horizontalHeader().setFont(f)
        self.view.verticalHeader().setFont(f)
        self.model._fonts.clear()
        hh, vh = self.view.horizontalHeader(), self.view.verticalHeader()
        self.view.blockSignals(True)
        hh.blockSignals(True)
        vh.blockSignals(True)
        hh.setDefaultSectionSize(int(sh.default_col_width * z))
        vh.setDefaultSectionSize(int(sh.default_row_height * z))
        for c, w in sh.col_widths.items():
            self.view.setColumnWidth(c - 1, int(w * z))
        for r, h in sh.row_heights.items():
            self.view.setRowHeight(r - 1, int(h * z))
        hh.blockSignals(False)
        vh.blockSignals(False)
        self.view.blockSignals(False)
        self.model.invalidate()
        self.view.viewport().update()
        self._reposition_floats()

    # ================================================================== charts / images
    def _rebuild_floats(self):
        for w in self.chart_widgets:
            w.hide()
            w.deleteLater()
        self.chart_widgets = []
        for spec in self.sheet.charts:
            self._add_chart_widget(spec)
        for img in self.sheet.images:
            w = ImageWidget(self.view, self, img)
            w.changed.connect(lambda: self.set_modified(True))
            w.deleteRequested.connect(self._delete_float)
            w.show()
            self.chart_widgets.append(w)

    def _add_chart_widget(self, spec):
        w = ChartWidget(self.view, self, spec)
        w.changed.connect(lambda: self.set_modified(True))
        w.editRequested.connect(self._edit_chart)
        w.deleteRequested.connect(self._delete_float)
        w.show()
        self.chart_widgets.append(w)
        return w

    def _reposition_floats(self, *_):
        for w in self.chart_widgets:
            w.reposition()

    def _refresh_charts(self):
        for w in self.chart_widgets:
            if isinstance(w.spec, ChartSpec):
                w.refresh()

    def insert_chart(self, kind: str = "column"):
        from app.sheet.formula.parser import range_name

        rng = self._data_region()
        r1, c1, r2, c2 = rng
        spec = ChartSpec(kind, range_name(r1, c1, r2, c2, self.sheet.name), "", has_header=self._has_header(rng),
                         anchor=(r1, min(MAX_COLS, c2 + 2)))
        d = ChartDialog(self, self.book, spec)
        if d.exec():
            new = d.result_spec()
            new.id = uuid.uuid4().hex[:8]
            self.structural(_t("Insert Chart"), lambda: self.sheet.charts.append(new))

    def _edit_chart(self, w):
        d = ChartDialog(self, self.book, w.spec)
        if d.exec():
            new = d.result_spec()
            sh = self.sheet

            def fn():
                for i, s in enumerate(sh.charts):
                    if s.id == w.spec.id:
                        sh.charts[i] = new
            self.structural(_t("Edit Chart"), fn)

    def _delete_float(self, w):
        sh = self.sheet

        def fn():
            sh.charts[:] = [s for s in sh.charts if s.id != w.spec.id]
            sh.images[:] = [s for s in sh.images if s.id != w.spec.id]
        self.structural(_t("Delete"), fn)

    def add_image(self, data: bytes, ext: str, size=(320, 220)):
        r, c = self.current_rc()
        w, h = size
        if w > 640:
            h = int(h * 640 / w)
            w = 640
        img = SheetImage(data, ext, (r, c), (w, h), uuid.uuid4().hex[:8])
        self.structural(_t("Insert Picture"), lambda: self.sheet.images.append(img))

    def insert_picture(self):
        path = get_open_file(self, _t("Insert Picture"), "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if path:
            from PySide6.QtGui import QImage

            img = QImage(path)
            if img.isNull():
                show_error(self, _t("Insert Picture"), _t("The image could not be read."))
                return
            with open(path, "rb") as fh:
                self.add_image(fh.read(), Path(path).suffix.lstrip(".").lower(), (img.width(), img.height()))

    def insert_image(self, qimage):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        qimage.save(buf, "PNG")
        self.add_image(bytes(ba.data()), "png", (qimage.width(), qimage.height()))

    def insert_screenshot(self):
        from app.services.capture import capture_dialog

        img = capture_dialog(self.window())
        if img is not None:
            self.insert_image(img)

    # ================================================================== macros / collaboration / forms
    def macros(self):
        from app.macros.manager import MacroDialog

        MacroDialog(self.window()).exec()

    def record_macro(self, checked: bool):
        from app.macros.recorder import MacroRecorder

        if checked:
            self.recorder = MacroRecorder()
            self.statusChanged.emit(_t("Recording macro\u2026 click Stop Recording when done."))
        else:
            rec, self.recorder = self.recorder, None
            if rec is not None and rec.lines:
                rec.save_dialog(self)

    def collaborate(self):
        from app.collab.dialog import CollabDialog

        CollabDialog(self).exec()

    def forms(self):
        from app.forms.designer import FormsDialog

        FormsDialog(self.window(), self).exec()

    # ================================================================== macro / plugin API
    def apply_remote_inputs(self, sheet_name: str, changes: dict):
        """Apply cell inputs from collaborators (no undo entry, no echo)."""
        sh = self.book.sheet_by_name(sheet_name)
        if sh is None:
            sh = self.book.add_sheet(sheet_name)
            self._rebuild_tabs()
        states = {k: self.book.state_for_input(sh, k[0], k[1], v) for k, v in changes.items()}
        self._loading = True
        try:
            keys = self.book.apply_states(sh, states)
        finally:
            self._loading = False
        if sh is self.sheet:
            self._after_change(keys)
        super().set_modified(True)

    # ================================================================== context menu
    def _context_menu(self, gpos):
        m = QMenu(self)
        m.addAction(self.a_cut)
        m.addAction(self.a_copy)
        m.addAction(self.a_paste)
        m.addAction(_t("Paste Special\u2026"), self.paste_special)
        m.addSeparator()
        ins = m.addMenu(_t("Insert"))
        ins.addAction(_t("Rows Above"), lambda: self.insert_rows(True))
        ins.addAction(_t("Columns Left"), lambda: self.insert_cols(True))
        dele = m.addMenu(_t("Delete"))
        dele.addAction(_t("Rows"), self.delete_rows)
        dele.addAction(_t("Columns"), self.delete_cols)
        m.addAction(_t("Clear Contents"), self.clear_contents)
        m.addSeparator()
        m.addAction(icon("sort_asc"), _t("Sort A \u2192 Z"), lambda: self.sort(True))
        m.addAction(icon("sort_desc"), _t("Sort Z \u2192 A"), lambda: self.sort(False))
        m.addAction(icon("filter"), _t("Filter"), self.toggle_filter)
        m.addSeparator()
        m.addAction(icon("note"), _t("Insert/Edit Note"), self.edit_comment)
        if self.current_rc() in self.sheet.comments:
            m.addAction(_t("Delete Note"), self.delete_comment)
        m.addAction(icon("link"), _t("Hyperlink\u2026"), self.insert_link)
        m.addSeparator()
        m.addAction(icon("settings"), _t("Format Cells\u2026"), self.format_cells_dialog)
        m.addAction(_t("Define Name\u2026"), self.name_manager)
        m.exec(gpos)

    # ================================================================== actions & ribbon
    def _act(self, text, icon_name=None, slot=None, shortcut=None, checkable=False, tip=None) -> QAction:
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        a.setCheckable(checkable)
        a.setToolTip((tip or text.replace("&", "")) + (f" ({QKeySequence(shortcut).toString(QKeySequence.NativeText)})" if shortcut else ""))
        if slot is not None:
            a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_undo = self.undo.createUndoAction(self, _t("Undo"))
        self.a_undo.setIcon(icon("undo"))
        self.a_undo.setShortcut(QKeySequence.Undo)
        self.a_undo.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.addAction(self.a_undo)
        self.a_redo = self.undo.createRedoAction(self, _t("Redo"))
        self.a_redo.setIcon(icon("redo"))
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence.Redo])
        self.a_redo.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.addAction(self.a_redo)
        self.a_cut = A(_t("Cut"), "cut", self.cut, "Ctrl+X")
        self.a_copy = A(_t("Copy"), "copy", lambda: self.copy(False), "Ctrl+C")
        self.a_paste = A(_t("Paste"), "paste", lambda: self.paste(), "Ctrl+V")
        pm = QMenu(self)
        pm.addAction(_t("Paste"), lambda: self.paste())
        pm.addAction(_t("Paste Values"), lambda: self.paste("values"))
        pm.addAction(_t("Paste Formats"), lambda: self.paste("formats"))
        pm.addAction(_t("Transpose"), lambda: self.paste("all", True))
        pm.addAction(_t("Paste Special\u2026"), self.paste_special)
        self.a_paste.setMenu(pm)
        self.a_painter = A(_t("Format Painter"), "edit", self.format_painter)
        self.a_bold = A(_t("Bold"), "bold", lambda: self.toggle_style("bold"), "Ctrl+B")
        self.a_italic = A(_t("Italic"), "italic", lambda: self.toggle_style("italic"), "Ctrl+I")
        self.a_underline = A(_t("Underline"), "underline", lambda: self.toggle_style("underline"), "Ctrl+U")
        self.a_strike = A(_t("Strikethrough"), "strike", lambda: self.toggle_style("strike"), "Ctrl+5")
        self.a_grow = A(_t("Grow Font"), "plus", lambda: self.step_font(1))
        self.a_shrink = A(_t("Shrink Font"), "minus", lambda: self.step_font(-1))
        self.a_color = A(_t("Font Color"), "font_color", lambda: self.set_style_attr(color=self._last_color))
        self._last_color = "#c00000"
        cm = ColorMenu(self, _t("Automatic"))
        cm.colorPicked.connect(lambda c: (setattr(self, "_last_color", c.name()) if c.isValid() else None,
                                          self.set_style_attr(color=c.name() if c.isValid() else None)))
        self.a_color.setMenu(cm)
        self.a_fill = A(_t("Fill Color"), "fill_color", lambda: self.set_style_attr(fill=self._last_fill))
        self._last_fill = "#ffff00"
        fm = ColorMenu(self, _t("No Fill"))
        fm.colorPicked.connect(lambda c: (setattr(self, "_last_fill", c.name()) if c.isValid() else None,
                                          self.set_style_attr(fill=c.name() if c.isValid() else None)))
        self.a_fill.setMenu(fm)
        self.a_borders = A(_t("Borders"), "border", lambda: self.set_borders("all"))
        bm = QMenu(self)
        for label, mode in ((_t("All Borders"), "all"), (_t("Outside Borders"), "outside"),
                            (_t("Thick Outside Borders"), "thick_outside"), (_t("Bottom Border"), "bottom"),
                            (_t("Top Border"), "top"), (_t("Left Border"), "left"), (_t("Right Border"), "right"),
                            (_t("Double Bottom Border"), "double_bottom"), (_t("No Border"), "none")):
            bm.addAction(label, lambda m=mode: self.set_borders(m))
        self.a_borders.setMenu(bm)
        self.a_left = A(_t("Align Left"), "align_left", lambda: self.set_style_attr(halign="left"))
        self.a_center = A(_t("Center"), "align_center", lambda: self.set_style_attr(halign="center"))
        self.a_right = A(_t("Align Right"), "align_right", lambda: self.set_style_attr(halign="right"))
        self.a_top = A(_t("Top Align"), "up", lambda: self.set_style_attr(valign="top"))
        self.a_middle = A(_t("Middle Align"), "rows", lambda: self.set_style_attr(valign="center"))
        self.a_bottom = A(_t("Bottom Align"), "down", lambda: self.set_style_attr(valign=None))
        self.a_wrap = A(_t("Wrap Text"), "wrap", lambda: self.toggle_style("wrap"))
        self.a_merge = A(_t("Merge && Center"), "merge_cells", lambda: self.merge("center"))
        mm = QMenu(self)
        mm.addAction(_t("Merge && Center"), lambda: self.merge("center"))
        mm.addAction(_t("Merge Across"), lambda: self.merge("across"))
        mm.addAction(_t("Merge Cells"), lambda: self.merge("merge"))
        mm.addAction(_t("Unmerge Cells"), lambda: self.merge("unmerge"))
        self.a_merge.setMenu(mm)
        self.a_indent_more = A(_t("Increase Indent"), "indent_more",
                               lambda: self.set_style_attr(indent=min(15, self.current_style().indent + 1)))
        self.a_indent_less = A(_t("Decrease Indent"), "indent_less",
                               lambda: self.set_style_attr(indent=max(0, self.current_style().indent - 1)))
        self.a_currency = A(_t("Currency"), "currency", lambda: self.number_format('"Rs" #,##0.00'))
        cym = QMenu(self)
        for label, fmt in ((_t("Rs Pakistani Rupee"), '"Rs" #,##0.00'), ("$ Dollar", "$#,##0.00"),
                           ("\u20ac Euro", '"\u20ac"#,##0.00'), ("\u00a3 Pound", '"\u00a3"#,##0.00'),
                           ("\u20b9 Indian Rupee", '"\u20b9"#,##0.00')):
            cym.addAction(label, lambda f=fmt: self.number_format(f))
        self.a_currency.setMenu(cym)
        self.a_percent = A(_t("Percent Style"), "percent", lambda: self.number_format("0%"), "Ctrl+Shift+5")
        self.a_comma = A(_t("Comma Style"), "number", lambda: self.number_format("#,##0.00"))
        self.a_dec_more = A(_t("Increase Decimal"), "plus", lambda: self.change_decimals(1))
        self.a_dec_less = A(_t("Decrease Decimal"), "minus", lambda: self.change_decimals(-1))
        self.a_format_cells = A(_t("Format Cells"), "settings", self.format_cells_dialog, "Ctrl+1")
        self.a_cond = A(_t("Conditional Formatting"), "highlight", lambda: self.cond_format_dialog())
        cfm = QMenu(self)
        hl = cfm.addMenu(_t("Highlight Cells Rules"))
        for label, key in ((_t("Greater Than\u2026"), "gt"), (_t("Less Than\u2026"), "lt"), (_t("Between\u2026"), "between"),
                           (_t("Equal To\u2026"), "eq"), (_t("Text that Contains\u2026"), "text"),
                           (_t("Duplicate Values"), "dup")):
            hl.addAction(label, lambda k=key: self.cond_format_dialog(k))
        tb = cfm.addMenu(_t("Top/Bottom Rules"))
        tb.addAction(_t("Top 10 Items"), lambda: self.cond_format_dialog("top10"))
        tb.addAction(_t("Bottom 10 Items"), lambda: self.cond_format_dialog("bottom10"))
        cfm.addAction(_t("Data Bars"), lambda: self.cond_format_dialog("bar"))
        cs = cfm.addMenu(_t("Color Scales"))
        cs.addAction(_t("Red - Yellow - Green"), lambda: self.cond_format_dialog("scale3"))
        cs.addAction(_t("White - Green"), lambda: self.cond_format_dialog("scale2"))
        cfm.addSeparator()
        cfm.addAction(_t("Manage Rules\u2026"), lambda: self.cond_format_dialog())
        cfm.addAction(_t("Clear Rules from Selected Cells"), self.clear_cond_formats)
        self.a_cond.setMenu(cfm)
        self.a_table_style = A(_t("Format as Table"), "table", self.format_as_table)
        self.a_cell_styles = A(_t("Cell Styles"), "theme", lambda: self.cell_style("Good"))
        csm = QMenu(self)
        for name in ("Normal", "Good", "Bad", "Neutral", "Title", "Heading 1", "Heading 2", "Total", "Note"):
            csm.addAction(_t(name), lambda n=name: self.cell_style(n))
        self.a_cell_styles.setMenu(csm)
        self.a_insert = A(_t("Insert"), "plus", lambda: self.insert_rows(True))
        im = QMenu(self)
        im.addAction(_t("Insert Rows Above"), lambda: self.insert_rows(True))
        im.addAction(_t("Insert Rows Below"), lambda: self.insert_rows(False))
        im.addAction(_t("Insert Columns Left"), lambda: self.insert_cols(True))
        im.addAction(_t("Insert Columns Right"), lambda: self.insert_cols(False))
        im.addAction(_t("Insert Sheet"), lambda: self.add_sheet())
        self.a_insert.setMenu(im)
        self.a_delete = A(_t("Delete"), "delete", self.delete_rows)
        dm = QMenu(self)
        dm.addAction(_t("Delete Rows"), self.delete_rows)
        dm.addAction(_t("Delete Columns"), self.delete_cols)
        dm.addAction(_t("Delete Sheet"), lambda: self.delete_sheet())
        self.a_delete.setMenu(dm)
        self.a_format = A(_t("Format"), "settings", self.set_col_width)
        fmm = QMenu(self)
        fmm.addAction(_t("Row Height\u2026"), self.set_row_height)
        fmm.addAction(_t("Column Width\u2026"), self.set_col_width)
        fmm.addAction(_t("AutoFit Column Width"), self.autofit_cols)
        fmm.addSeparator()
        fmm.addAction(_t("Hide Rows"), lambda: self.hide_rows(True))
        fmm.addAction(_t("Unhide Rows"), lambda: self.hide_rows(False))
        fmm.addAction(_t("Hide Columns"), lambda: self.hide_cols(True))
        fmm.addAction(_t("Unhide Columns"), lambda: self.hide_cols(False))
        fmm.addSeparator()
        fmm.addAction(_t("Rename Sheet"), lambda: self.rename_sheet())
        fmm.addAction(_t("Format Cells\u2026"), self.format_cells_dialog)
        self.a_format.setMenu(fmm)
        self.a_autosum = A(_t("AutoSum"), "sigma", lambda: self.autosum("SUM"), "Alt+=")
        asm = QMenu(self)
        for fn_ in ("SUM", "AVERAGE", "COUNT", "MAX", "MIN"):
            asm.addAction(fn_.capitalize(), lambda f=fn_: self.autosum(f))
        asm.addAction(_t("More Functions\u2026"), lambda: self.insert_function())
        self.a_autosum.setMenu(asm)
        self.a_fill_down = A(_t("Fill Down"), "down", lambda: self.fill_direction("down"), "Ctrl+D")
        self.a_fill_right = A(_t("Fill Right"), "next", lambda: self.fill_direction("right"), "Ctrl+R")
        self.a_fill = A(_t("Fill"), "down", lambda: self.fill_direction("down"))
        flm = QMenu(self)
        flm.addAction(self.a_fill_down)
        flm.addAction(self.a_fill_right)
        flm.addAction(_t("Up"), lambda: self.fill_direction("up"))
        flm.addAction(_t("Left"), lambda: self.fill_direction("left"))
        flm.addAction(_t("Series\u2026"), self.fill_series)
        self.a_fill.setMenu(flm)
        self.a_clear = A(_t("Clear"), "close", lambda: self.clear_contents(what="all"))
        clm = QMenu(self)
        clm.addAction(_t("Clear All"), lambda: self.clear_contents(what="all"))
        clm.addAction(_t("Clear Formats"), lambda: self.clear_contents(what="formats"))
        clm.addAction(_t("Clear Contents"), lambda: self.clear_contents(what="contents"))
        clm.addAction(_t("Clear Notes"), lambda: self.clear_contents(what="comments"))
        self.a_clear.setMenu(clm)
        self.a_sort_asc = A(_t("Sort A to Z"), "sort_asc", lambda: self.sort(True))
        self.a_sort_desc = A(_t("Sort Z to A"), "sort_desc", lambda: self.sort(False))
        self.a_sort = A(_t("Sort\u2026"), "sort_asc", self.sort_dialog)
        self.a_filter = A(_t("Filter"), "filter", self.toggle_filter, "Ctrl+Shift+L")
        self.a_clear_filter = A(_t("Clear Filter"), "close", self.clear_filters)
        self.a_find = A(_t("Find"), "find", lambda: self.show_find(False), "Ctrl+F")
        self.a_replace = A(_t("Replace"), "replace", lambda: self.show_find(True), "Ctrl+H")
        self.a_goto = A(_t("Go To"), "next", self.goto_dialog, "Ctrl+G")
        # insert
        self.a_pivot = A(_t("PivotTable"), "pivot", self.pivot_dialog)
        self.a_chart = A(_t("Chart"), "chart_bar", lambda: self.insert_chart("column"))
        chm = QMenu(self)
        for k, label, ic in (("column", _t("Column"), "chart_bar"), ("bar", _t("Bar"), "chart_bar"),
                             ("line", _t("Line"), "chart_line"), ("area", _t("Area"), "chart_line"),
                             ("pie", _t("Pie"), "chart_pie"), ("doughnut", _t("Doughnut"), "chart_pie"),
                             ("scatter", _t("Scatter"), "chart_scatter")):
            chm.addAction(icon(ic), label, lambda kk=k: self.insert_chart(kk))
        self.a_chart.setMenu(chm)
        self.a_picture = A(_t("Picture"), "image", self.insert_picture)
        self.a_screenshot = A(_t("Screenshot"), "capture", self.insert_screenshot)
        self.a_link = A(_t("Hyperlink"), "link", self.insert_link, "Ctrl+K")
        self.a_note = A(_t("Note"), "note", self.edit_comment, "Shift+F2")
        self.a_del_note = A(_t("Delete Note"), "delete", self.delete_comment)
        self.a_function = A(_t("Insert Function"), "function", lambda: self.insert_function(), "Shift+F3")
        # formulas
        self.a_names = A(_t("Name Manager"), "book", self.name_manager, "Ctrl+F3")
        self.a_show_formulas = A(_t("Show Formulas"), "function", self.toggle_formulas, "Ctrl+`", checkable=True)
        self.a_calc = A(_t("Calculate Now"), "refresh", self.calculate_now, "F9")
        self.a_auto_calc = A(_t("Automatic Calculation"), "refresh", self.set_auto_calc, checkable=True)
        self.a_auto_calc.setChecked(True)
        self.a_trace = A(_t("Trace Precedents"), "arrow", self.trace_precedents)
        # data
        self.a_text_cols = A(_t("Text to Columns"), "columns", self.text_to_columns)
        self.a_dedupe = A(_t("Remove Duplicates"), "dedupe", self.remove_duplicates)
        self.a_validation = A(_t("Data Validation"), "check", self.validation_dialog)
        self.a_analysis = A(_t("Data Analysis"), "analysis", lambda: self.analysis_dialog())
        self.a_goal = A(_t("Goal Seek"), "goal", self.goal_seek)
        # review / view
        self.a_protect = A(_t("Protect Sheet"), "lock", self.toggle_protect, checkable=True)
        self.a_freeze = A(_t("Freeze Panes"), "freeze", lambda: self.freeze("panes"))
        frm = QMenu(self)
        frm.addAction(_t("Freeze Panes (at current cell)"), lambda: self.freeze("panes"))
        frm.addAction(_t("Freeze Top Row"), lambda: self.freeze("row"))
        frm.addAction(_t("Freeze First Column"), lambda: self.freeze("col"))
        frm.addAction(_t("Unfreeze Panes"), lambda: self.freeze("none"))
        self.a_freeze.setMenu(frm)
        self.a_grid = A(_t("Gridlines"), "table", self.toggle_grid, checkable=True)
        self.a_grid.setChecked(True)
        self.a_headers = A(_t("Headings"), "layout", self.toggle_headers, checkable=True)
        self.a_headers.setChecked(True)
        self.a_zoom_in = A(_t("Zoom In"), "zoom_in", lambda: self.set_zoom(self.zoom + 0.1), "Ctrl++")
        self.a_zoom_out = A(_t("Zoom Out"), "zoom_out", lambda: self.set_zoom(self.zoom - 0.1), "Ctrl+-")
        self.a_zoom_100 = A(_t("100%"), "zoom_in", lambda: self.set_zoom(1.0), "Ctrl+0")
        # tools
        self.a_macros = A(_t("Macros"), "macro", self.macros, "Alt+F8")
        self.a_vba = A(_t("View VBA Code"), "code", self.show_vba)
        self.a_get_data = A(_t("Get External Data"), "download", lambda: sheet_features.get_external_data(self))
        self.a_export_json = A(_t("Export JSON"), "export_pdf", lambda: sheet_features.export_data(self, "json"))
        self.a_export_md = A(_t("Export Markdown"), "export_pdf", lambda: sheet_features.export_data(self, "md"))
        self.a_ai_formula = A(_t("AI Formula"), "ai", lambda: sheet_features.ai_formula(self), "Ctrl+J")
        self.a_ai_analyze = A(_t("Analyze Data"), "analysis", lambda: sheet_features.ai_analyze(self))
        self.a_ai_settings = A(_t("AI Settings"), "settings", self._ai_settings)
        self.a_record = A(_t("Record Macro"), "record", self.record_macro, checkable=True)
        self.a_forms = A(_t("Forms"), "forms", self.forms)
        self.a_collab = A(_t("Collaborate"), "collab", self.collaborate)
        self.a_export_pdf = A(_t("Export PDF"), "export_pdf", self.export_pdf)
        self.a_save_as = A(_t("Save As / Export"), "save_as", self.save_as)
        # shortcuts without buttons
        A(_t("Insert Date"), None, lambda: self._insert_now("date"), "Ctrl+;")
        A(_t("Insert Time"), None, lambda: self._insert_now("time"), "Ctrl+Shift+;")
        A(_t("Next Sheet"), None, lambda: self._cycle_sheet(1), "Ctrl+PgDown")
        A(_t("Previous Sheet"), None, lambda: self._cycle_sheet(-1), "Ctrl+PgUp")

    def _insert_now(self, what: str):
        import datetime

        r, c = self.current_rc()
        now = datetime.datetime.now()
        self.commit_input(r, c, now.strftime("%Y-%m-%d") if what == "date" else now.strftime("%H:%M"))

    def _cycle_sheet(self, d: int):
        i = (self.book.sheets.index(self.sheet) + d) % len(self.book.sheets)
        self.switch_sheet(self.book.sheets[i])

    def format_painter(self):
        st = self.current_style()
        sid = self.book.styles.intern(st)
        self._painter_style = sid
        self.statusChanged.emit(_t("Format Painter: select the cells to format"))
        conn = []

        def apply(*_):
            if QApplication.mouseButtons() != Qt.NoButton:
                return
            self.view.selectionModel().selectionChanged.disconnect(conn[0])
            states = self.book.styled_states(self.sheet, self.sel_cells(), lambda s: st)
            before = {k: self.book.snapshot(self.sheet, *k) for k in states}
            self.push_states(before, states, _t("Format Painter"))
        conn.append(self.view.selectionModel().selectionChanged.connect(apply))

    def _build_ribbon(self) -> Ribbon:
        rb = Ribbon(self)
        home = rb.page(_t("Home"))
        g = home.group(_t("Clipboard"))
        g.large(self.a_paste, popup=True)
        g.small(self.a_cut)
        g.small(self.a_copy)
        g.small(self.a_painter)
        g = home.group(_t("Font"))
        self.font_combo = QFontComboBox()
        self.font_combo.setMaximumWidth(160)
        self.font_combo.activated.connect(lambda *_: self.set_style_attr(font=self.font_combo.currentFont().family()))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setMaximumWidth(60)
        for s in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72):
            self.size_combo.addItem(str(s))
        self.size_combo.setCurrentText("11")
        self.size_combo.activated.connect(lambda *_: self.set_font_size(self.size_combo.currentText()))
        self.size_combo.lineEdit().returnPressed.connect(lambda: self.set_font_size(self.size_combo.currentText()))
        g.row(self.font_combo, self.size_combo, self.a_grow, self.a_shrink)
        g.row(self.a_bold, self.a_italic, self.a_underline, self.a_strike, self.a_borders)
        g.row(self.a_fill, self.a_color)
        g = home.group(_t("Alignment"))
        g.row(self.a_top, self.a_middle, self.a_bottom, self.a_wrap)
        g.row(self.a_left, self.a_center, self.a_right, self.a_indent_less, self.a_indent_more)
        g.row(self.a_merge)
        g = home.group(_t("Number"))
        self.fmt_combo = QComboBox()
        from app.sheet.numfmt import BUILTIN_FORMATS

        for name, code in BUILTIN_FORMATS.items():
            self.fmt_combo.addItem(_t(name), code)
        self.fmt_combo.setMaximumWidth(140)
        self.fmt_combo.activated.connect(lambda i: self.number_format(self.fmt_combo.itemData(i)))
        g.row(self.fmt_combo)
        g.row(self.a_currency, self.a_percent, self.a_comma, self.a_dec_more, self.a_dec_less)
        g.row(self.a_format_cells)
        g = home.group(_t("Styles"))
        g.small(self.a_cond)
        g.small(self.a_table_style)
        g.small(self.a_cell_styles)
        g = home.group(_t("Cells"))
        g.small(self.a_insert)
        g.small(self.a_delete)
        g.small(self.a_format)
        g = home.group(_t("Editing"))
        g.row(self.a_autosum, self.a_fill, self.a_clear)
        g.row(self.a_sort_asc, self.a_sort_desc, self.a_filter)
        g.row(self.a_find, self.a_replace, self.a_goto)
        ins = rb.page(_t("Insert"))
        g = ins.group(_t("Tables"))
        g.large(self.a_pivot)
        g.large(self.a_table_style)
        g = ins.group(_t("Charts"))
        g.large(self.a_chart, popup=True)
        g = ins.group(_t("Illustrations"))
        g.large(self.a_picture)
        g.large(self.a_screenshot)
        g = ins.group(_t("Links && Notes"))
        g.small(self.a_link)
        g.small(self.a_note)
        g.small(self.a_function)
        fo = rb.page(_t("Formulas"))
        g = fo.group(_t("Function Library"))
        g.large(self.a_function)
        g.large(self.a_autosum, popup=True)
        cats = function_names_by_category()
        row = []
        for cat in ("Math", "Statistical", "Logical", "Lookup", "Text", "Date", "Financial", "Info"):
            b = QToolButton()
            b.setText(_t(cat))
            b.setPopupMode(QToolButton.InstantPopup)
            m = QMenu(b)
            for name in cats.get(cat, []):
                m.addAction(name, lambda n=name: self.insert_function(n))
            b.setMenu(m)
            row.append(b)
        g.row(*row[:4])
        g.row(*row[4:])
        g = fo.group(_t("Defined Names"))
        g.large(self.a_names)
        g = fo.group(_t("Formula Auditing"))
        g.small(self.a_show_formulas)
        g.small(self.a_trace)
        g = fo.group(_t("Calculation"))
        g.large(self.a_calc)
        g.small(self.a_auto_calc)
        da = rb.page(_t("Data"))
        g = da.group(_t("Sort && Filter"))
        g.large(self.a_sort_asc)
        g.large(self.a_sort_desc)
        g.large(self.a_sort)
        g.large(self.a_filter)
        g.small(self.a_clear_filter)
        g = da.group(_t("Data Tools"))
        g.small(self.a_text_cols)
        g.small(self.a_dedupe)
        g.small(self.a_validation)
        g = da.group(_t("Get && Export Data"))
        g.large(self.a_get_data)
        g.small(self.a_export_json)
        g.small(self.a_export_md)
        g = da.group(_t("Analysis"))
        g.large(self.a_pivot)
        g.large(self.a_analysis)
        g.large(self.a_goal)
        rv = rb.page(_t("Review"))
        g = rv.group(_t("Notes"))
        g.large(self.a_note)
        g.small(self.a_del_note)
        g = rv.group(_t("Protect"))
        g.large(self.a_protect)
        vw = rb.page(_t("View"))
        g = vw.group(_t("Window"))
        g.large(self.a_freeze, popup=True)
        g = vw.group(_t("Show"))
        g.small(self.a_grid)
        g.small(self.a_headers)
        g.small(self.a_show_formulas)
        g = vw.group(_t("Zoom"))
        g.large(self.a_zoom_100)
        g.small(self.a_zoom_in)
        g.small(self.a_zoom_out)
        tl = rb.page(_t("Tools"))
        g = tl.group(_t("Macros"))
        g.large(self.a_macros)
        g.large(self.a_record)
        g.small(self.a_vba)
        g = tl.group(_t("Forms"))
        g.large(self.a_forms)
        g = tl.group(_t("Share"))
        g.large(self.a_collab)
        g = tl.group(_t("Export"))
        g.large(self.a_export_pdf)
        g.large(self.a_save_as)
        aip = rb.page(_t("AI"))
        g = aip.group(_t("Formulas"))
        g.large(self.a_ai_formula)
        g = aip.group(_t("Insights"))
        g.large(self.a_ai_analyze)
        g = aip.group(_t("Setup"))
        g.large(self.a_ai_settings)
        return rb


__all__ = ["SheetDocument", "a1", "XLError", "QPushButton", "cfg", "get_save_file"]
