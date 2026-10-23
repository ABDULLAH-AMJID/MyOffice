"""Spreadsheet data model (pure Python, no Qt): cells, styles, sheets, workbook operations."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field, fields, replace

from app.sheet.formula.engine import Engine
from app.sheet.formula.parser import adjust_for_structure, rename_sheet_in_formula, translate_formula
from app.sheet.formula.tokenizer import MAX_COLS, MAX_ROWS, index_to_col
from app.sheet.formula.values import REF, XLError, parse_date_text, serial_to_datetime
from app.sheet.numfmt import format_value, is_date_format, is_percent_format

DEFAULT_COL_WIDTH = 90
DEFAULT_ROW_HEIGHT = 24


# ============================================================================= cells & styles
class Cell:
    __slots__ = ("value", "formula", "fn", "style")

    def __init__(self, value=None, formula=None, style=0):
        self.value = value
        self.formula = formula
        self.fn = None
        self.style = style

    def is_empty(self) -> bool:
        return self.value is None and not self.formula and self.style == 0

    def __repr__(self):
        return f"Cell({self.value!r}, {self.formula!r}, s{self.style})"


@dataclass(frozen=True)
class Border:
    style: str = "thin"     # thin, medium, thick, dashed, dotted, double
    color: str = "#000000"


@dataclass(frozen=True)
class Style:
    font: str | None = None
    size: float | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    color: str | None = None
    fill: str | None = None
    halign: str | None = None      # left, center, right, justify
    valign: str | None = None      # top, center, bottom
    wrap: bool = False
    numfmt: str = "General"
    indent: int = 0
    rotation: int = 0
    border_top: Border | None = None
    border_bottom: Border | None = None
    border_left: Border | None = None
    border_right: Border | None = None
    locked: bool = True

    def with_(self, **kw) -> "Style":
        return replace(self, **kw)

    def to_dict(self) -> dict:
        d = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if v != f.default:
                d[f.name] = (v.__dict__ if isinstance(v, Border) else v)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Style":
        kw = {}
        for k, v in d.items():
            if k.startswith("border_") and isinstance(v, dict):
                v = Border(**v)
            kw[k] = v
        return Style(**kw)


class StyleTable:
    def __init__(self):
        self._styles: list[Style] = [Style()]
        self._index: dict[Style, int] = {Style(): 0}

    def get(self, sid: int) -> Style:
        try:
            return self._styles[sid]
        except IndexError:
            return self._styles[0]

    def intern(self, style: Style) -> int:
        i = self._index.get(style)
        if i is None:
            i = len(self._styles)
            self._styles.append(style)
            self._index[style] = i
        return i

    def __len__(self):
        return len(self._styles)


# ============================================================================= sheet features
@dataclass
class CondFormat:
    range: tuple                 # (r1, c1, r2, c2)
    kind: str                    # cell, text, top, bottom, duplicate, unique, scale2, scale3, databar, formula
    op: str = ">"                # for kind == cell: >, <, >=, <=, =, <>, between
    value1: str = ""
    value2: str = ""
    fill: str | None = "#ffc7ce"
    color: str | None = "#9c0006"
    bold: bool = False
    colors: tuple = ("#f8696b", "#ffeb84", "#63be7b")


@dataclass
class Validation:
    range: tuple
    kind: str = "list"           # list, whole, decimal, date, textlength
    op: str = "between"
    value1: str = ""
    value2: str = ""
    allow_blank: bool = True
    error_message: str = ""
    prompt: str = ""

    def list_items(self) -> list[str]:
        return [x.strip() for x in self.value1.split(",") if x.strip()] if self.kind == "list" else []


@dataclass
class ChartSpec:
    kind: str = "column"         # column, bar, line, pie, scatter, area, doughnut
    data: str = ""               # range text e.g. Sheet1!A1:C5
    title: str = ""
    series_in_rows: bool = False
    has_header: bool = True
    x_title: str = ""
    y_title: str = ""
    legend: bool = True
    anchor: tuple = (1, 1)       # (row, col) top-left cell
    size: tuple = (480, 300)     # pixels
    style: str = "default"
    id: str = ""


@dataclass
class SheetImage:
    data: bytes
    ext: str
    anchor: tuple = (1, 1)
    size: tuple = (240, 160)
    id: str = ""


@dataclass
class FilterSpec:
    values: set | None = None    # allowed display values (None = all)
    condition: str = ""          # e.g. ">10" (COUNTIF criteria syntax)


class SheetData:
    def __init__(self, book: "Book", sid: int, name: str):
        self.book = book
        self.id = sid
        self.name = name
        self.cells: dict[tuple[int, int], Cell] = {}
        self.col_widths: dict[int, int] = {}
        self.row_heights: dict[int, int] = {}
        self.merges: list[tuple[int, int, int, int]] = []
        self.freeze: tuple[int, int] = (0, 0)
        self.hidden_rows: set[int] = set()
        self.hidden_cols: set[int] = set()
        self.filtered_rows: set[int] = set()
        self.comments: dict[tuple[int, int], str] = {}
        self.links: dict[tuple[int, int], str] = {}
        self.cond_formats: list[CondFormat] = []
        self.validations: list[Validation] = []
        self.charts: list[ChartSpec] = []
        self.images: list[SheetImage] = []
        self.tables: list[dict] = []          # Excel tables {name, ref, style, header}
        self.autofilter: tuple | None = None
        self.filters: dict[int, FilterSpec] = {}
        self.tab_color: str | None = None
        self.show_grid = True
        self.protected = False
        self.protect_password = ""
        self.default_col_width = DEFAULT_COL_WIDTH
        self.default_row_height = DEFAULT_ROW_HEIGHT
        self._bounds: tuple[int, int] | None = None
        self._colcache: dict = {}
        self._colmiss: dict = {}

    # -- numeric column cache (fast SUM/AVERAGE/COUNT/MIN/MAX over large ranges)
    def numeric_column(self, c: int):
        ent = self._colcache.get(c)
        if ent is not None:
            return ent
        miss = self._colmiss.get(c, 0) + 1
        self._colmiss[c] = miss
        if miss > 24:  # column keeps changing during this recalculation: let callers loop instead
            return None
        import numpy as np

        arr = np.full(self.max_row + 2, np.nan)
        errs = []
        for (r, cc), cell in self.cells.items():
            if cc == c:
                v = cell.value
                if v.__class__ is float or (isinstance(v, (int, float)) and not isinstance(v, bool)):
                    if r >= len(arr):
                        arr = np.concatenate([arr, np.full(r - len(arr) + 1, np.nan)])
                    arr[r] = v
                elif isinstance(v, XLError):
                    errs.append((r, v))
        errs.sort(key=lambda e: e[0])
        ent = (arr, errs)
        self._colcache[c] = ent
        return ent

    def invalidate_columns(self, cols=None) -> None:
        if cols is None:
            self._colcache.clear()
        else:
            for c in cols:
                self._colcache.pop(c, None)

    # -- bounds
    def invalidate_bounds(self):
        self._bounds = None

    def _compute_bounds(self):
        mr = mc = 0
        for (r, c), cell in self.cells.items():
            if cell.value is not None or cell.formula:
                if r > mr:
                    mr = r
                if c > mc:
                    mc = c
        for (r1, c1, r2, c2) in self.merges:
            mr, mc = max(mr, r2), max(mc, c2)
        self._bounds = (mr, mc)

    @property
    def max_row(self) -> int:
        if self._bounds is None:
            self._compute_bounds()
        return self._bounds[0]

    @property
    def max_col(self) -> int:
        if self._bounds is None:
            self._compute_bounds()
        return self._bounds[1]

    # -- access
    def cell(self, r: int, c: int) -> Cell | None:
        return self.cells.get((r, c))

    def value(self, r: int, c: int):
        cell = self.cells.get((r, c))
        return None if cell is None else cell.value

    def style_of(self, r: int, c: int) -> Style:
        cell = self.cells.get((r, c))
        return self.book.styles.get(cell.style if cell else 0)

    def display(self, r: int, c: int) -> tuple[str, str | None]:
        cell = self.cells.get((r, c))
        if cell is None:
            return "", None
        return format_value(cell.value, self.book.styles.get(cell.style).numfmt)

    def merge_at(self, r: int, c: int):
        for m in self.merges:
            if m[0] <= r <= m[2] and m[1] <= c <= m[3]:
                return m
        return None

    def col_width(self, c: int) -> int:
        return self.col_widths.get(c, self.default_col_width)

    def row_height(self, r: int) -> int:
        return self.row_heights.get(r, self.default_row_height)

    def used_range(self) -> tuple[int, int, int, int]:
        return (1, 1, max(1, self.max_row), max(1, self.max_col))


# ============================================================================= input parsing
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_CURRENCY = (("$", '$#,##0'), ("Rs.", '"Rs" #,##0'), ("Rs", '"Rs" #,##0'), ("\u20a8", '"\u20a8" #,##0'),
             ("\u20ac", '"\u20ac"#,##0'), ("\u00a3", '"\u00a3"#,##0'), ("\u20b9", '"\u20b9"#,##0'))


def parse_input(text: str):
    """Interpret typed text like Excel.  Returns (value, formula, numfmt_suggestion)."""
    if text is None:
        return None, None, None
    s = str(text)
    if s == "":
        return None, None, None
    if s.startswith("=") and len(s) > 1:
        return None, s, None
    if s.startswith("'"):
        return s[1:], None, None
    t = s.strip()
    up = t.upper()
    if up in ("TRUE", "FALSE"):
        return up == "TRUE", None, None
    if up in ("#N/A", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#NULL!"):
        return XLError(up), None, None
    if _NUM_RE.match(t):
        return float(t), None, None
    neg = False
    core = t
    if core.startswith("(") and core.endswith(")"):
        neg, core = True, core[1:-1].strip()
    if core.startswith("-"):
        neg, core = not neg, core[1:].strip()
    if core.endswith("%"):
        num = core[:-1].strip().replace(",", "")
        if _NUM_RE.match(num):
            v = float(num) / 100
            dec = len(num.split(".")[1]) if "." in num else 0
            return (-v if neg else v), None, ("0%" if dec == 0 else "0." + "0" * dec + "%")
    for sym, fmt in _CURRENCY:
        if core.startswith(sym):
            num = core[len(sym):].strip()
            if _NUM_RE.match(num.replace(",", "")) and (num.count(",") == 0 or _THOUSANDS_RE.match(num)):
                v = float(num.replace(",", ""))
                dec = len(num.split(".")[1]) if "." in num else 0
                return (-v if neg else v), None, fmt + ("." + "0" * dec if dec else "")
    if _THOUSANDS_RE.match(core):
        v = float(core.replace(",", ""))
        dec = len(core.split(".")[1]) if "." in core else 0
        return (-v if neg else v), None, "#,##0" + ("." + "0" * dec if dec else "")
    if neg and _NUM_RE.match(core):
        return -float(core), None, None
    if re.match(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}( \d{1,2}:\d{2}(:\d{2})?)?$", t) or \
            re.match(r"^\d{1,2}:\d{2}(:\d{2})?( ?[AaPp][Mm])?$", t) or \
            re.match(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$", t) or re.match(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$", t):
        v = parse_date_text(t)
        if v is not None:
            if ":" in t and not re.search(r"[-/.]", t.split(" ")[0]):
                return v, None, "h:mm" if t.count(":") == 1 else "h:mm:ss"
            if ":" in t:
                return v, None, "yyyy-mm-dd hh:mm"
            if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", t):
                return v, None, "dd/mm/yyyy"
            return v, None, "yyyy-mm-dd"
    return s, None, None


def edit_text(cell: Cell | None, style: Style) -> str:
    """Text shown in the editor / formula bar for a cell."""
    if cell is None:
        return ""
    if cell.formula:
        return cell.formula
    v = cell.value
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, XLError):
        return v.code
    if isinstance(v, (int, float)):
        fmt = style.numfmt
        if is_date_format(fmt):
            try:
                d = serial_to_datetime(float(v))
            except XLError:
                return f"{v:.15g}"
            if float(v) == int(v):
                return d.strftime("%Y-%m-%d")
            if float(v) < 1:
                return d.strftime("%H:%M:%S")
            return d.strftime("%Y-%m-%d %H:%M:%S")
        if is_percent_format(fmt):
            return f"{float(v) * 100:.13g}%"
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{float(v):.15g}"
    s = str(v)
    if parse_input(s)[0] != s and not s.startswith("="):
        return "'" + s
    return s


# ============================================================================= workbook
class Book:
    def __init__(self):
        self.sheets: list[SheetData] = []
        self.styles = StyleTable()
        self.names: dict[str, str] = {}
        self.name_cache: dict = {}
        self._next_sid = 1
        self.engine = Engine(self)
        self.calc_auto = True
        self.properties: dict = {}
        self.vba_source: bytes | None = None   # original .xlsm package when it holds VBA macros

    # -- sheets
    def add_sheet(self, name: str | None = None, index: int | None = None) -> SheetData:
        if not name:
            n = len(self.sheets) + 1
            while self.sheet_by_name(f"Sheet{n}"):
                n += 1
            name = f"Sheet{n}"
        sh = SheetData(self, self._next_sid, name)
        self._next_sid += 1
        if index is None:
            self.sheets.append(sh)
        else:
            self.sheets.insert(index, sh)
        return sh

    def sheet_by_id(self, sid: int) -> SheetData | None:
        for s in self.sheets:
            if s.id == sid:
                return s
        return None

    def sheet_by_name(self, name: str) -> SheetData | None:
        low = name.lower()
        for s in self.sheets:
            if s.name.lower() == low:
                return s
        return None

    def sheet_id(self, name: str) -> int:
        s = self.sheet_by_name(name)
        if s is None:
            raise REF
        return s.id

    def sheet_index(self, sid: int) -> int:
        for i, s in enumerate(self.sheets):
            if s.id == sid:
                return i
        return -1

    def valid_sheet_name(self, name: str, exclude: SheetData | None = None) -> str | None:
        """Return an error message, or None when ``name`` is acceptable."""
        if not name or len(name) > 31:
            return "Sheet names must be 1-31 characters long."
        if re.search(r"[\\/?*\[\]:]", name) or name.startswith("'") or name.endswith("'"):
            return "Sheet names cannot contain \\ / ? * [ ] : or start/end with an apostrophe."
        other = self.sheet_by_name(name)
        if other is not None and other is not exclude:
            return "A sheet with this name already exists."
        return None

    def rename_sheet(self, sh: SheetData, new: str) -> None:
        old = sh.name
        sh.name = new
        for s in self.sheets:
            for cell in s.cells.values():
                if cell.formula and old.lower() in cell.formula.lower():
                    cell.formula = rename_sheet_in_formula(cell.formula, old, new)
        for k, v in list(self.names.items()):
            self.names[k] = rename_sheet_in_formula(v if v.startswith("=") else "=" + v, old, new)
        for s in self.sheets:
            for ch in s.charts:
                if ch.data.lower().startswith((old.lower() + "!", f"'{old.lower()}'!")):
                    ch.data = rename_sheet_in_formula("=" + ch.data, old, new)[1:]
        self.engine.rebuild()

    def remove_sheet(self, sh: SheetData) -> None:
        self.sheets.remove(sh)
        pat = re.compile(r"(?:'" + re.escape(sh.name.replace("'", "''")) + r"'|" + re.escape(sh.name) +
                         r")!\$?[A-Za-z]{0,3}\$?\d*(?::\$?[A-Za-z]{0,3}\$?\d*)?", re.I)
        for s in self.sheets:
            for cell in s.cells.values():
                if cell.formula and sh.name.lower() in cell.formula.lower():
                    cell.formula = pat.sub("#REF!", cell.formula)
        self.engine.rebuild()
        self.engine.recalc_all()

    def move_sheet(self, frm: int, to: int) -> None:
        sh = self.sheets.pop(frm)
        self.sheets.insert(to, sh)

    def copy_sheet(self, sh: SheetData, name: str | None = None) -> SheetData:
        base = name or f"{sh.name} (2)"
        n = 2
        while self.sheet_by_name(base):
            n += 1
            base = f"{sh.name} ({n})"
        new = self.add_sheet(base, self.sheets.index(sh) + 1)
        for (r, c), cell in sh.cells.items():
            new.cells[(r, c)] = Cell(cell.value, cell.formula, cell.style)
        for attr in ("col_widths", "row_heights", "merges", "freeze", "hidden_rows", "hidden_cols", "comments", "links",
                     "cond_formats", "validations", "charts", "images", "tab_color", "show_grid"):
            setattr(new, attr, copy.deepcopy(getattr(sh, attr)))
        self.engine.rebuild()
        self.engine.recalc_all()
        return new

    # -- cell state snapshots (undo/redo)
    @staticmethod
    def snapshot(sh: SheetData, r: int, c: int):
        cell = sh.cells.get((r, c))
        if cell is None:
            return None
        return (cell.value, cell.formula, cell.style)

    def apply_states(self, sh: SheetData, states: dict, recalc: bool = True) -> set:
        """Set cells to snapshot states {(r,c): (value, formula, style) | None}; returns recalculated keys."""
        eng = self.engine
        changed = []
        for (r, c), st in states.items():
            key = (sh.id, r, c)
            old = sh.cells.get((r, c))
            if old is not None and old.formula:
                eng.clear_formula(sh.id, r, c)
            if st is None:
                sh.cells.pop((r, c), None)
            else:
                value, formula, style = st
                cell = Cell(value, None, style)
                sh.cells[(r, c)] = cell
                if formula:
                    eng.set_formula(sh.id, r, c, formula, cell)
            changed.append(key)
        sh.invalidate_bounds()
        sh.invalidate_columns({c for (_r, c) in states})
        if recalc and self.calc_auto:
            return eng.recalc(changed) | set(changed)
        return set(changed)

    def state_for_input(self, sh: SheetData, r: int, c: int, text: str):
        """Snapshot state that results from typing ``text`` into a cell."""
        value, formula, fmt = parse_input(text)
        cell = sh.cells.get((r, c))
        style_id = cell.style if cell else 0
        if fmt:
            st = self.styles.get(style_id)
            if st.numfmt == "General" or (is_date_format(fmt) and not is_date_format(st.numfmt)):
                style_id = self.styles.intern(st.with_(numfmt=fmt))
        if value is None and formula is None and style_id == 0:
            return None
        return (value, formula, style_id)

    def set_input(self, sh: SheetData, r: int, c: int, text: str) -> set:
        return self.apply_states(sh, {(r, c): self.state_for_input(sh, r, c, text)})

    def set_value(self, sh: SheetData, r: int, c: int, value) -> set:
        """Programmatic setter (macros, imports): formulas start with '='."""
        if isinstance(value, str) and value.startswith("="):
            return self.set_input(sh, r, c, value)
        cell = sh.cells.get((r, c))
        style = cell.style if cell else 0
        if isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        return self.apply_states(sh, {(r, c): (value, None, style) if value is not None or style else None})

    def recalc_all(self) -> set:
        return self.engine.recalc_all()

    # -- styles
    def styled_states(self, sh: SheetData, cells, fn) -> dict:
        """States for cells after applying ``fn(Style) -> Style``."""
        out = {}
        for r, c in cells:
            cell = sh.cells.get((r, c))
            st = self.styles.get(cell.style if cell else 0)
            new_id = self.styles.intern(fn(st))
            if cell is None:
                if new_id == 0:
                    continue
                out[(r, c)] = (None, None, new_id)
            else:
                out[(r, c)] = (cell.value, cell.formula, new_id)
        return out

    # -- structure
    def _rewrite_all_formulas(self, fn) -> None:
        for s in self.sheets:
            for cell in s.cells.values():
                if cell.formula:
                    cell.formula = fn(cell.formula, s.name)
        for k, v in list(self.names.items()):
            self.names[k] = fn(v if v.startswith("=") else "=" + v, None)

    def insert_rows(self, sh: SheetData, at: int, count: int) -> None:
        self._shift(sh, "row", at, count)

    def delete_rows(self, sh: SheetData, at: int, count: int) -> None:
        self._shift(sh, "row", at, -count)

    def insert_cols(self, sh: SheetData, at: int, count: int) -> None:
        self._shift(sh, "col", at, count)

    def delete_cols(self, sh: SheetData, at: int, count: int) -> None:
        self._shift(sh, "col", at, -count)

    def _shift(self, sh: SheetData, axis: str, at: int, count: int) -> None:
        self._rewrite_all_formulas(lambda f, sname: adjust_for_structure(f, sname or "", sh.name, axis, at, count))
        last_deleted = at - count - 1 if count < 0 else None

        def move(idx):
            if count > 0:
                return idx + count if idx >= at else idx
            if idx < at:
                return idx
            if idx <= last_deleted:
                return None
            return idx + count

        def mv_key(r, c):
            if axis == "row":
                nr = move(r)
                return None if nr is None else (nr, c)
            nc = move(c)
            return None if nc is None else (r, nc)

        sh.cells = {nk: cell for (r, c), cell in sh.cells.items() if (nk := mv_key(r, c)) is not None}
        sh.comments = {nk: v for (r, c), v in sh.comments.items() if (nk := mv_key(r, c)) is not None}
        sh.links = {nk: v for (r, c), v in sh.links.items() if (nk := mv_key(r, c)) is not None}
        sizes = sh.row_heights if axis == "row" else sh.col_widths
        new_sizes = {}
        for i, v in sizes.items():
            ni = move(i)
            if ni is not None:
                new_sizes[ni] = v
        if axis == "row":
            sh.row_heights = new_sizes
            sh.hidden_rows = {m for i in sh.hidden_rows if (m := move(i)) is not None}
        else:
            sh.col_widths = new_sizes
            sh.hidden_cols = {m for i in sh.hidden_cols if (m := move(i)) is not None}

        def mv_range(rng):
            r1, c1, r2, c2 = rng
            if axis == "row":
                a, b = move(r1), move(r2)
                if a is None and b is None:
                    return None
                a = a if a is not None else at
                b = b if b is not None else at - 1
                return (a, c1, b, c2) if b >= a else None
            a, b = move(c1), move(c2)
            if a is None and b is None:
                return None
            a = a if a is not None else at
            b = b if b is not None else at - 1
            return (r1, a, r2, b) if b >= a else None

        sh.merges = [m for m in (mv_range(x) for x in sh.merges) if m is not None and m[:2] != m[2:]]
        for cf in sh.cond_formats:
            cf.range = mv_range(cf.range) or cf.range
        for v in sh.validations:
            v.range = mv_range(v.range) or v.range
        for ch in sh.charts:
            r, c = ch.anchor
            ch.anchor = mv_key(r, c) or ch.anchor
            if ch.data:
                ch.data = adjust_for_structure("=" + ch.data, sh.name, sh.name, axis, at, count)[1:]
        sh.invalidate_bounds()
        sh.invalidate_columns()
        self.engine.rebuild()
        self.engine.recalc_all()

    # -- block operations
    def block_states(self, sh: SheetData, r1: int, c1: int, r2: int, c2: int) -> dict:
        out = {}
        for (r, c), cell in sh.cells.items():
            if r1 <= r <= r2 and c1 <= c <= c2:
                out[(r, c)] = (cell.value, cell.formula, cell.style)
        return out

    def copy_states(self, src: dict, dr: int, dc: int, sh_dst: SheetData | None = None) -> dict:
        """Translate a block of states by (dr, dc), adjusting relative references."""
        out = {}
        for (r, c), st in src.items():
            nr, nc = r + dr, c + dc
            if not (1 <= nr <= MAX_ROWS and 1 <= nc <= MAX_COLS):
                continue
            if st is None:
                out[(nr, nc)] = None
                continue
            value, formula, style = st
            if formula:
                formula = translate_formula(formula, dr, dc)
            out[(nr, nc)] = (value, formula, style)
        return out

    def fill_states(self, sh: SheetData, src: tuple, dst: tuple) -> dict:
        """Fill/extend a source block into a destination block (series detection for numbers/dates)."""
        sr1, sc1, sr2, sc2 = src
        dr1, dc1, dr2, dc2 = dst
        out: dict = {}
        vertical = (dc1, dc2) == (sc1, sc2)
        if vertical:
            for c in range(sc1, sc2 + 1):
                seq = [sh.cells.get((r, c)) for r in range(sr1, sr2 + 1)]
                self._fill_line(out, seq, [(r, c) for r in range(dr1, dr2 + 1)],
                                [(r, c) for r in range(sr1, sr2 + 1)], "row")
        else:
            for r in range(sr1, sr2 + 1):
                seq = [sh.cells.get((r, c)) for c in range(sc1, sc2 + 1)]
                self._fill_line(out, seq, [(r, c) for c in range(dc1, dc2 + 1)],
                                [(r, c) for c in range(sc1, sc2 + 1)], "col")
        return out

    def _fill_line(self, out, seq, targets, sources, axis):
        n = len(seq)
        src_pos = {p: i for i, p in enumerate(sources)}
        nums = [cell.value for cell in seq if cell is not None and not cell.formula
                and isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool)]
        series = n >= 2 and len(nums) == n
        step = (nums[-1] - nums[0]) / (n - 1) if series else (1.0 if n == 1 and nums else 0.0)
        text_series = None
        if n == 1 and seq[0] is not None and isinstance(seq[0].value, str):
            m = re.match(r"^(.*?)(\d+)$", seq[0].value)
            if m:
                text_series = (m.group(1), int(m.group(2)), len(m.group(2)))
        first = sources[0]
        for k, pos in enumerate(targets):
            if pos in src_pos:
                continue
            offset = (pos[0] - first[0]) if axis == "row" else (pos[1] - first[1])
            i = offset % n
            cell = seq[i]
            if cell is None:
                out[pos] = None
                continue
            if cell.formula:
                dr = pos[0] - sources[i][0]
                dc = pos[1] - sources[i][1]
                out[pos] = (None, translate_formula(cell.formula, dr, dc), cell.style)
            elif series or (n == 1 and nums):
                out[pos] = (float(nums[0] + step * offset), None, cell.style)
            elif text_series:
                pre, num, width = text_series
                out[pos] = (f"{pre}{str(num + offset).zfill(width)}", None, cell.style)
            else:
                out[pos] = (cell.value, None, cell.style)

    def sort_states(self, sh: SheetData, rng: tuple, keys: list[tuple[int, bool]], header: bool) -> dict:
        """States after sorting rows of ``rng`` by [(column, ascending)]."""
        from functools import cmp_to_key

        r1, c1, r2, c2 = rng
        start = r1 + 1 if header else r1
        rows = []
        for r in range(start, r2 + 1):
            rows.append((r, [sh.cells.get((r, c)) for c in range(c1, c2 + 1)]))

        def cmp(a, b):
            for col, asc in keys:
                va = a[1][col - c1].value if a[1][col - c1] is not None else None
                vb = b[1][col - c1].value if b[1][col - c1] is not None else None
                if va is None and vb is None:
                    continue
                if va is None:
                    return 1  # blanks always last
                if vb is None:
                    return -1
                ea, eb = isinstance(va, XLError), isinstance(vb, XLError)
                if ea or eb:
                    res = (ea > eb) - (ea < eb)
                else:
                    from app.sheet.formula.values import compare

                    res = compare(va, vb)
                if res:
                    return res if asc else -res
            return 0

        rows.sort(key=cmp_to_key(cmp))
        out = {}
        for i, (orig_r, cells) in enumerate(rows):
            new_r = start + i
            for j, cell in enumerate(cells):
                c = c1 + j
                if cell is None:
                    out[(new_r, c)] = None
                else:
                    f = translate_formula(cell.formula, new_r - orig_r, 0) if cell.formula else None
                    out[(new_r, c)] = (cell.value, f, cell.style)
        return out


# ============================================================================= helpers
def a1(r: int, c: int) -> str:
    return f"{index_to_col(c)}{r}"


def range_a1(r1, c1, r2, c2) -> str:
    return a1(r1, c1) if (r1, c1) == (r2, c2) else f"{a1(r1, c1)}:{a1(r2, c2)}"


def to_float_or_none(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if not (isinstance(v, float) and math.isnan(v)) else None
    return None


__all__ = ["Book", "SheetData", "Cell", "Style", "Border", "StyleTable", "CondFormat", "Validation", "ChartSpec",
           "SheetImage", "FilterSpec", "parse_input", "edit_text", "a1", "range_a1", "field"]
