"""Macro API (Python macros instead of VBA) and the macro runner.

Example macro::

    s = app.sheet                     # active spreadsheet
    s["A1"] = "Total"
    s["B1"] = "=SUM(B2:B100)"
    s.format("A1:B1", bold=True, fill="#ddebf7")
    for r, row in enumerate(s.range("A2:C10").values, start=2):
        ...
    app.message("Done!")
"""
from __future__ import annotations

import datetime
import io
import math
import re
import sys
import time
import traceback
from contextlib import redirect_stdout

from app.services.i18n import _t


class MacroError(Exception):
    pass


class MacroTimeout(MacroError):
    pass


def _rc(ref: str):
    from app.sheet.formula.parser import parse_ref_text

    node = parse_ref_text(ref.replace("$", ""))
    return node


class RangeAPI:
    def __init__(self, sheet_api: "SheetAPI", r1, c1, r2, c2):
        self.s = sheet_api
        self.r1, self.c1, self.r2, self.c2 = r1, c1, r2, c2

    @property
    def values(self) -> list[list]:
        sh = self.s.sh
        return [[sh.value(r, c) for c in range(self.c1, self.c2 + 1)] for r in range(self.r1, self.r2 + 1)]

    @values.setter
    def values(self, rows):
        self.set_values(rows)

    def set_values(self, rows):
        before, after = {}, {}
        book, sh = self.s.doc.book, self.s.sh
        for i, row in enumerate(rows):
            for j, v in enumerate(row if isinstance(row, (list, tuple)) else [row]):
                r, c = self.r1 + i, self.c1 + j
                before[(r, c)] = book.snapshot(sh, r, c)
                after[(r, c)] = book.state_for_input(sh, r, c, "" if v is None else (v if isinstance(v, str) else _num_text(v)))
        self.s.doc.push_states(before, after, _t("Macro"), sh.id)

    def cells(self):
        for r in range(self.r1, self.r2 + 1):
            for c in range(self.c1, self.c2 + 1):
                yield r, c, self.s.sh.value(r, c)

    def __iter__(self):
        return iter(self.values)


def _num_text(v):
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


class SheetAPI:
    """Access to a sheet of the active workbook."""

    def __init__(self, doc, sheet=None):
        self.doc = doc
        self._sheet = sheet

    @property
    def sh(self):
        return self._sheet or self.doc.sheet

    @property
    def name(self) -> str:
        return self.sh.name

    def __getitem__(self, ref: str):
        n = _rc(ref)
        if n.is_cell:
            return self.sh.value(n.r1, n.c1)
        return RangeAPI(self, n.r1, n.c1, n.r2, n.c2).values

    def __setitem__(self, ref: str, value):
        n = _rc(ref)
        if not n.is_cell:
            rows = value if isinstance(value, (list, tuple)) else [[value] * (n.c2 - n.c1 + 1)] * (n.r2 - n.r1 + 1)
            RangeAPI(self, n.r1, n.c1, n.r2, n.c2).set_values(rows)
            return
        text = "" if value is None else (value if isinstance(value, str) else _num_text(value))
        book, sh = self.doc.book, self.sh
        self.doc.push_states({(n.r1, n.c1): book.snapshot(sh, n.r1, n.c1)},
                             {(n.r1, n.c1): book.state_for_input(sh, n.r1, n.c1, text)}, _t("Macro"), sh.id)

    def cell(self, row: int, col: int):
        return self.sh.value(row, col)

    def set_cell(self, row: int, col: int, value):
        from app.sheet.model import a1

        self[a1(row, col)] = value

    def range(self, ref: str) -> RangeAPI:
        n = _rc(ref)
        return RangeAPI(self, n.r1, n.c1, min(n.r2, max(self.sh.max_row, n.r1)), min(n.c2, max(self.sh.max_col, n.c1)))

    @property
    def used_range(self) -> str:
        from app.sheet.model import range_a1

        return range_a1(*self.sh.used_range())

    @property
    def max_row(self) -> int:
        return self.sh.max_row

    @property
    def max_col(self) -> int:
        return self.sh.max_col

    def format(self, ref: str, **style):
        n = _rc(ref)
        cells = [(r, c) for r in range(n.r1, n.r2 + 1) for c in range(n.c1, n.c2 + 1)]
        book, sh = self.doc.book, self.sh
        allowed = {k: v for k, v in style.items() if k in ("bold", "italic", "underline", "strike", "color", "fill",
                                                            "halign", "valign", "wrap", "numfmt", "size", "font", "indent")}
        states = book.styled_states(sh, cells, lambda s: s.with_(**allowed))
        before = {k: book.snapshot(sh, *k) for k in states}
        self.doc.push_states(before, states, _t("Macro"), sh.id)

    def clear(self, ref: str):
        n = _rc(ref)
        book, sh = self.doc.book, self.sh
        before = {(r, c): book.snapshot(sh, r, c) for r in range(n.r1, n.r2 + 1) for c in range(n.c1, n.c2 + 1)
                  if (r, c) in sh.cells}
        self.doc.push_states(before, {k: None for k in before}, _t("Macro"), sh.id)

    def select(self, ref: str):
        n = _rc(ref)
        if self.sh is self.doc.sheet:
            self.doc.view.select_range(n.r1, n.c1, n.r2, n.c2)

    def sort(self, ref: str, column: str, ascending: bool = True, header: bool = True):
        from app.sheet.formula.tokenizer import col_to_index

        n = _rc(ref)
        self.doc.sort(ascending, rng=(n.r1, n.c1, n.r2, n.c2), keys=[(col_to_index(column), ascending)], header=header)

    def recalc(self):
        self.doc.calculate_now()


class WriterAPI:
    def __init__(self, doc):
        self.doc = doc

    @property
    def text(self) -> str:
        return self.doc.text()

    def insert(self, text: str):
        self.doc.insert_text(text)

    def replace_all(self, old: str, new: str, case: bool = False) -> int:
        return self.doc.replace_all(old, new, case)

    def paragraphs(self) -> list[str]:
        return self.doc.text().split("\n")

    def word_count(self) -> int:
        from app.writer.model import count_words

        return count_words(self.doc.text())

    def set_style(self, name: str):
        self.doc.set_style(name)

    def bold(self):
        self.doc.a_bold.setChecked(not self.doc.a_bold.isChecked())
        self.doc.toggle_bold()


class AppAPI:
    def __init__(self, main):
        self._main = main

    @property
    def active(self):
        return self._main.current_document() if self._main is not None else None

    @property
    def sheet(self) -> SheetAPI:
        d = self._main.current_of_kind("sheet") if self._main is not None else None
        if d is None:
            raise MacroError(_t("Open a spreadsheet first (app.new_sheet() creates one)."))
        return SheetAPI(d)

    @property
    def writer(self) -> WriterAPI:
        d = self._main.current_of_kind("writer") if self._main is not None else None
        if d is None:
            raise MacroError(_t("Open a Writer document first."))
        return WriterAPI(d)

    def sheet_named(self, name: str) -> SheetAPI:
        api = self.sheet
        sh = api.doc.book.sheet_by_name(name)
        if sh is None:
            raise MacroError(f"No sheet named {name!r}")
        return SheetAPI(api.doc, sh)

    def add_sheet(self, name: str | None = None) -> SheetAPI:
        api = self.sheet
        sh = api.doc.add_sheet(name)
        return SheetAPI(api.doc, sh)

    def new_sheet(self) -> SheetAPI:
        return SheetAPI(self._main.new_sheet())

    def new_document(self) -> WriterAPI:
        return WriterAPI(self._main.new_writer())

    def open(self, path: str):
        return self._main.open_path(path)

    def message(self, text, title: str = "Macro"):
        from app.shell.dialogs import show_info

        show_info(self._main, title, str(text))

    def input(self, prompt: str, default: str = "") -> str:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self._main, "Macro", prompt, text=default)
        return text if ok else ""

    def status(self, text: str):
        self._main.statusBar().showMessage(str(text), 8000)


SAFE_BUILTINS = {k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
                 for k in ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "format", "int", "isinstance",
                           "len", "list", "map", "max", "min", "print", "range", "repr", "reversed", "round", "set",
                           "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError", "KeyError", "TypeError",
                           "IndexError", "ZeroDivisionError", "True", "False", "None")
                 if (k in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, k))}


ALLOWED_IMPORTS = {"math", "statistics", "datetime", "re", "random", "json", "string", "itertools", "collections",
                   "decimal", "fractions", "calendar", "textwrap", "functools", "operator"}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"Macros may only import: {', '.join(sorted(ALLOWED_IMPORTS))}")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS["__import__"] = _safe_import


def run_macro(code: str, main, timeout: float = 30.0, name: str = "macro") -> str:
    """Execute macro ``code``; returns captured print() output.  Spreadsheet edits form one undo step."""
    app = AppAPI(main)
    ns = {"__builtins__": SAFE_BUILTINS, "app": app, "math": math, "datetime": datetime, "re": re}
    out = io.StringIO()
    deadline = time.monotonic() + timeout

    def tracer(frame, event, arg):
        if time.monotonic() > deadline:
            raise MacroTimeout(_t("The macro ran longer than {0} seconds and was stopped.").format(int(timeout)))
        return tracer

    sheet_doc = main.current_of_kind("sheet") if main is not None else None
    if sheet_doc is not None:
        sheet_doc.undo.beginMacro(_t("Macro: {0}").format(name))
    old_trace = sys.gettrace()
    try:
        compiled = compile(code, f"<{name}>", "exec")
        sys.settrace(tracer)
        with redirect_stdout(out):
            exec(compiled, ns)
    except MacroError:
        raise
    except SyntaxError as exc:
        raise MacroError(f"Syntax error in line {exc.lineno}: {exc.msg}") from exc
    except Exception as exc:
        tb = traceback.extract_tb(exc.__traceback__)
        line = next((f.lineno for f in reversed(tb) if f.filename == f"<{name}>"), "?")
        raise MacroError(f"Line {line}: {exc.__class__.__name__}: {exc}") from exc
    finally:
        sys.settrace(old_trace)
        if sheet_doc is not None:
            sheet_doc.undo.endMacro()
    return out.getvalue()
