"""Spreadsheet features: Excel 97-2003 (.xls) read/write, Get External Data, Format as Table, VBA macro viewer,
JSON/Markdown export and the MyOffice AI commands (formula, data analysis)."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sqlite3
import struct
import urllib.request

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from app.services.i18n import _t
from app.sheet.formula.values import XLError
from app.sheet.model import Border, Cell, Style

log = logging.getLogger(__name__)

XLS_MAX_ROWS, XLS_MAX_COLS = 65536, 256


def col_letter(c: int) -> str:
    s = ""
    while c:
        c, rem = divmod(c - 1, 26)
        s = chr(65 + rem) + s
    return s


# ============================================================================ .xls write (xlwt)
def _rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


_XLS_ERRORS = {"#NULL!": 0x00, "#DIV/0!": 0x07, "#VALUE!": 0x0F, "#REF!": 0x17, "#NAME?": 0x1D, "#NUM!": 0x24,
               "#N/A": 0x2A}


def _cached_formula_cell(xlwt, cell, result):
    """xlwt writes every formula result as "empty", so readers that do not recalculate (xlrd, previews) see
    blanks.  This cell stores the value MyOffice calculated (BIFF8 FORMULA record) and asks Excel to recalculate
    on open."""
    from struct import pack

    class CachedFormulaCell(xlwt.Cell.FormulaCell):
        __slots__ = ["result"]

        def get_biff_data(self):
            r = self.result
            if isinstance(r, bool):
                raw = pack("<BBBBHH", 1, 0, int(r), 0, 0, 0xFFFF)
            elif isinstance(r, (int, float)):
                raw = pack("<d", float(r))
            elif isinstance(r, XLError) and r.code in _XLS_ERRORS:
                raw = pack("<BBBBHH", 2, 0, _XLS_ERRORS[r.code], 0, 0, 0xFFFF)
            else:  # text results would need an extra STRING record: leave them to the recalculation
                raw = pack("<Q", 0xFFFF000000000003)
            data = pack("<3H", self.rowx, self.colx, self.xf_idx) + raw + pack("<HL", 0x0002, 0) + self.frmla.rpn()
            return pack("<2H", 0x0006, len(data)) + data

    c = CachedFormulaCell(cell.rowx, cell.colx, cell.xf_idx, cell.frmla, 0x0002)
    c.result = result
    return c


def save_xls(path: str, book, progress=None) -> list[str]:
    """Write ``book`` as an Excel 97-2003 workbook.  Returns warnings (truncation, formulas kept as values)."""
    import xlwt

    wb = xlwt.Workbook(encoding="utf-8")
    warnings: list[str] = []
    palette: dict[str, int] = {}
    next_index = [8]

    def colour(hexcolor: str | None) -> int | None:
        if not hexcolor:
            return None
        key = hexcolor.lower()
        if key not in palette:
            if next_index[0] > 63:
                return 0x08  # palette full: black
            idx = next_index[0]
            next_index[0] += 1
            try:
                wb.set_colour_RGB(idx, *_rgb(key))
            except Exception:
                return None
            palette[key] = idx
        return palette[key]

    styles: dict[int, object] = {}

    def xf(sid: int):
        if sid in styles:
            return styles[sid]
        st: Style = book.styles.get(sid)
        x = xlwt.XFStyle()
        f = xlwt.Font()
        if st.font:
            f.name = st.font
        if st.size:
            f.height = int(st.size * 20)
        f.bold, f.italic, f.struck_out = st.bold, st.italic, st.strike
        f.underline = xlwt.Font.UNDERLINE_SINGLE if st.underline else xlwt.Font.UNDERLINE_NONE
        ci = colour(st.color)
        if ci is not None:
            f.colour_index = ci
        x.font = f
        fi = colour(st.fill)
        if fi is not None:
            p = xlwt.Pattern()
            p.pattern = xlwt.Pattern.SOLID_PATTERN
            p.pattern_fore_colour = fi
            x.pattern = p
        al = xlwt.Alignment()
        al.horz = {"left": al.HORZ_LEFT, "center": al.HORZ_CENTER, "right": al.HORZ_RIGHT,
                   "justify": al.HORZ_JUSTIFIED}.get(st.halign or "", al.HORZ_GENERAL)
        al.vert = {"top": al.VERT_TOP, "center": al.VERT_CENTER, "bottom": al.VERT_BOTTOM}.get(st.valign or "",
                                                                                         al.VERT_BOTTOM)
        al.wrap = 1 if st.wrap else 0
        x.alignment = al
        bd = xlwt.Borders()
        kinds = {"thin": bd.THIN, "medium": bd.MEDIUM, "thick": bd.THICK, "dashed": bd.DASHED, "dotted": bd.DOTTED,
                 "double": bd.DOUBLE}
        for side in ("top", "bottom", "left", "right"):
            b: Border | None = getattr(st, f"border_{side}")
            if b is not None:
                setattr(bd, side, kinds.get(b.style, bd.THIN))
                ci = colour(b.color)
                if ci is not None:
                    setattr(bd, f"{side}_colour", ci)
        x.borders = bd
        if st.numfmt and st.numfmt != "General":
            x.num_format_str = st.numfmt
        styles[sid] = x
        return x

    for sh in book.sheets:
        ws = wb.add_sheet(re.sub(r"[\[\]:*?/\\]", "_", sh.name)[:31] or "Sheet", cell_overwrite_ok=True)
        cut = False
        for (r, c), cell in sorted(sh.cells.items()):
            if r > XLS_MAX_ROWS or c > XLS_MAX_COLS:
                cut = True
                continue
            style = xf(cell.style)
            value = cell.value
            if cell.formula:
                try:
                    ws.write(r - 1, c - 1, xlwt.Formula(cell.formula.lstrip("=")), style)
                    cells = ws.row(r - 1)._Row__cells
                    cells[c - 1] = _cached_formula_cell(xlwt, cells[c - 1], value)
                    continue
                except Exception:
                    warnings.append(_t("{0}!{1}{2}: formula saved as its value (not supported by .xls)").format(
                        sh.name, col_letter(c), r))
            if isinstance(value, XLError):
                value = value.code
            ws.write(r - 1, c - 1, value if value is not None else "", style)
        if cut:
            warnings.append(_t("Sheet \"{0}\" is larger than an .xls sheet (65,536 rows, 256 columns); the rest "
                               "was not saved.").format(sh.name))
        for c, wpx in sh.col_widths.items():
            if c <= XLS_MAX_COLS:
                ws.col(c - 1).width = max(256, min(65535, int(wpx / 7.0 * 256)))
        for r, hpx in sh.row_heights.items():
            if r <= XLS_MAX_ROWS:
                row = ws.row(r - 1)
                row.height_mismatch = True
                row.height = int(hpx * 15)
        for (r1, c1, r2, c2) in sh.merges:
            if r2 <= XLS_MAX_ROWS and c2 <= XLS_MAX_COLS:
                try:
                    ws.merge(r1 - 1, r2 - 1, c1 - 1, c2 - 1)
                except Exception as exc:
                    log.debug("merge skipped: %s", exc)
        fr, fc = sh.freeze
        if fr or fc:
            ws.set_panes_frozen(True)
            ws.set_horz_split_pos(fr)
            ws.set_vert_split_pos(fc)
    tmp = path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, path)
    return warnings[:20]


# ============================================================================ .xls read with formatting (xlrd)
def load_xls_formatted(path: str, book) -> dict:
    """Values, number formats, fonts, fills, alignment, column widths and merged cells - without LibreOffice.
    (Formulas come in as their last calculated values: the .xls format stores them in a binary token form.)"""
    import xlrd

    try:
        wb = xlrd.open_workbook(path, formatting_info=True)
    except NotImplementedError:
        wb = xlrd.open_workbook(path)
    except xlrd.XLRDError as exc:
        from app.sheet.xlsx_io import SpreadsheetError

        raise SpreadsheetError(f"Not a valid .xls workbook: {exc}") from exc
    fmt_info = bool(getattr(wb, "xf_list", None))
    colour_map = getattr(wb, "colour_map", {}) or {}

    def hexcol(idx):
        rgb = colour_map.get(idx)
        if not rgb:
            return None
        return "#%02x%02x%02x" % rgb

    cache: dict[int, int] = {}

    def style_id(xf_index: int, date: bool) -> int:
        key = xf_index * 2 + int(date)
        if key in cache:
            return cache[key]
        kw = {}
        if fmt_info and 0 <= xf_index < len(wb.xf_list):
            xf = wb.xf_list[xf_index]
            font = wb.font_list[xf.font_index]
            kw.update(font=font.name or None, size=font.height / 20 if font.height else None, bold=font.bold,
                      italic=bool(font.italic), underline=bool(font.underline_type), strike=bool(font.struck_out))
            c = hexcol(font.colour_index)
            if c and c != "#000000":
                kw["color"] = c
            bg = xf.background
            if bg.fill_pattern == 1:
                fc = hexcol(bg.pattern_colour_index)
                if fc and fc != "#ffffff":
                    kw["fill"] = fc
            al = xf.alignment
            kw["halign"] = {1: "left", 2: "center", 3: "right", 5: "justify"}.get(al.hor_align)
            kw["valign"] = {0: "top", 1: "center", 2: "bottom"}.get(al.vert_align)
            kw["wrap"] = bool(al.text_wrapped)
            fmt = wb.format_map.get(xf.format_key)
            if fmt is not None and fmt.format_str and fmt.format_str != "General":
                kw["numfmt"] = fmt.format_str
            bd = xf.border
            for side, attr in (("top", "top_line_style"), ("bottom", "bottom_line_style"),
                               ("left", "left_line_style"), ("right", "right_line_style")):
                ls = getattr(bd, attr, 0)
                if ls:
                    kw[f"border_{side}"] = Border({2: "medium", 5: "thick", 3: "dashed", 4: "dotted",
                                                   6: "double"}.get(ls, "thin"))
        if date and "numfmt" not in kw:
            kw["numfmt"] = "yyyy-mm-dd"
        sid = book.styles.intern(Style(**{k: v for k, v in kw.items() if v is not None}))
        cache[key] = sid
        return sid

    for xs in wb.sheets():
        sh = book.add_sheet(xs.name[:31])
        for r in range(xs.nrows):
            for c in range(xs.ncols):
                cell = xs.cell(r, c)
                t = cell.ctype
                if t == xlrd.XL_CELL_EMPTY and not fmt_info:
                    continue
                sid = style_id(cell.xf_index, t == xlrd.XL_CELL_DATE) if cell.xf_index is not None else 0
                if t == xlrd.XL_CELL_EMPTY or t == xlrd.XL_CELL_BLANK:
                    if sid:
                        sh.cells[(r + 1, c + 1)] = Cell(None, None, sid)
                    continue
                if t == xlrd.XL_CELL_BOOLEAN:
                    v = bool(cell.value)
                elif t == xlrd.XL_CELL_ERROR:
                    v = XLError(xlrd.error_text_from_code.get(cell.value, "#VALUE!"))
                elif t in (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE):
                    v = float(cell.value)
                else:
                    v = str(cell.value)
                sh.cells[(r + 1, c + 1)] = Cell(v, None, sid)
        if fmt_info:
            for c, info in xs.colinfo_map.items():
                if info.width:
                    sh.col_widths[c + 1] = max(20, int(info.width / 256 * 7))
            for r, info in xs.rowinfo_map.items():
                if info.height and getattr(info, "has_default_height", 0) == 0:
                    sh.row_heights[r + 1] = max(12, int(info.height / 15))
            sh.merges = [(r1 + 1, c1 + 1, r2, c2) for (r1, r2, c1, c2) in xs.merged_cells if r2 - r1 > 1 or c2 - c1 > 1]
            if getattr(xs, "horz_split_pos", 0) or getattr(xs, "vert_split_pos", 0):
                if getattr(xs, "panes_are_frozen", 0):
                    sh.freeze = (int(xs.horz_split_pos or 0), int(xs.vert_split_pos or 0))
        sh.invalidate_bounds()
    if not book.sheets:
        book.add_sheet("Sheet1")
    return {"values_only": True, "formatted": fmt_info}


# ============================================================================ VBA (vbaProject.bin)
def _decompress(data: bytes) -> bytes:
    """MS-OVBA 2.4.1 decompression."""
    if not data or data[0] != 1:
        raise ValueError("not a compressed VBA container")
    out = bytearray()
    pos = 1
    while pos < len(data):
        header = struct.unpack("<H", data[pos:pos + 2])[0]
        size = (header & 0x0FFF) + 3
        compressed = header & 0x8000
        chunk_end = min(len(data), pos + size)
        pos += 2
        if not compressed:
            out += data[pos:pos + 4096]
            pos += 4096
            continue
        start = len(out)
        while pos < chunk_end:
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if pos >= chunk_end:
                    break
                if not flags & (1 << bit):
                    out.append(data[pos])
                    pos += 1
                else:
                    token = struct.unpack("<H", data[pos:pos + 2])[0]
                    pos += 2
                    diff = len(out) - start
                    bit_count = max((diff - 1).bit_length(), 4)
                    length_mask = 0xFFFF >> bit_count
                    offset = (token >> (16 - bit_count)) + 1
                    length = (token & length_mask) + 3
                    for _ in range(length):
                        out.append(out[-offset])
    return bytes(out)


def vba_modules(vba_bin: bytes) -> list[tuple[str, str]]:
    """(module name, source code) of every module in a vbaProject.bin."""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(vba_bin))
    root = None
    for entry in ole.listdir():
        if entry[-1].lower() == "dir" and len(entry) >= 2 and entry[-2].upper() == "VBA":
            root = entry[:-1]
            break
    if root is None:
        return []
    d = _decompress(ole.openstream(root + ["dir"]).read())
    modules, name, stream, offset = [], None, None, 0
    codepage = 1252
    pos = 0
    while pos + 6 <= len(d):
        rid, size = struct.unpack("<HI", d[pos:pos + 6])
        pos += 6
        if rid == 0x0009:  # PROJECTVERSION has a fixed size
            size = 6
        payload = d[pos:pos + size]
        pos += size
        if rid == 0x0003 and len(payload) >= 2:
            codepage = struct.unpack("<H", payload[:2])[0]
        elif rid == 0x0019:
            name = payload.decode("cp%d" % codepage if codepage != 65001 else "utf-8", "replace")
        elif rid == 0x001A:
            stream = payload.decode("cp%d" % codepage if codepage != 65001 else "utf-8", "replace")
        elif rid == 0x0031 and len(payload) >= 4:
            offset = struct.unpack("<I", payload[:4])[0]
        elif rid == 0x002B:  # module terminator
            if name and stream:
                try:
                    raw = ole.openstream(root + [stream]).read()
                    code = _decompress(raw[offset:]).decode("cp%d" % codepage, "replace")
                except Exception as exc:
                    code = f"' (module could not be read: {exc})"
                code = "\n".join(ln for ln in code.replace("\r\n", "\n").split("\n")
                                 if not ln.startswith("Attribute "))
                modules.append((name, code.strip("\n")))
            name, stream, offset = None, None, 0
    return modules


def vba_from_package(data: bytes) -> bytes | None:
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.lower().endswith("vbaproject.bin"):
                    return z.read(n)
    except zipfile.BadZipFile:
        return None
    return None


class VbaDialog(QDialog):
    def __init__(self, parent, modules: list[tuple[str, str]]):
        super().__init__(parent)
        self.setWindowTitle(_t("VBA Macros"))
        self.resize(900, 600)
        v = QVBoxLayout(self)
        info = QLabel(_t("This workbook contains VBA macros. MyOffice shows their code and keeps them when you save "
                         "as .xlsm; it does not run VBA (for automation in MyOffice use Python macros: Tools \u25b8 "
                         "Macros)."))
        info.setWordWrap(True)
        v.addWidget(info)
        split = QSplitter()
        self.list = QListWidget()
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setFont(QFont("Consolas", 10))
        split.addWidget(self.list)
        split.addWidget(self.code)
        split.setSizes([200, 700])
        v.addWidget(split, 1)
        self.modules = modules
        for name, _code in modules:
            self.list.addItem(name)
        self.list.currentRowChanged.connect(lambda i: self.code.setPlainText(self.modules[i][1] if i >= 0 else ""))
        if modules:
            self.list.setCurrentRow(0)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


# ============================================================================ external data
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def read_text_table(path: str, delimiter: str = "") -> list[list[str]]:
    with open(path, "rb") as f:
        text = _decode(f.read())
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (MyOffice Get Data)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def frame_rows(df) -> list[list]:
    import math as _m

    head = [str(c) for c in df.columns]
    rows = [head]
    for rec in df.itertuples(index=False):
        row = []
        for v in rec:
            if v is None or (isinstance(v, float) and _m.isnan(v)):
                row.append(None)
            elif hasattr(v, "isoformat"):
                row.append(v.isoformat(sep=" ") if hasattr(v, "hour") else v.isoformat())
            elif hasattr(v, "item"):
                row.append(v.item())
            else:
                row.append(v)
        rows.append(row)
    return rows


def web_tables(url: str) -> list:
    import pandas as pd

    html = _decode(_fetch(url))
    return pd.read_html(io.StringIO(html))


def json_rows(source: str) -> list[list]:
    import pandas as pd

    raw = _fetch(source) if re.match(r"^https?://", source) else open(source, "rb").read()
    data = json.loads(_decode(raw))
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if len(lists) == 1 else [data]
    return frame_rows(pd.json_normalize(data))


def xml_rows(source: str) -> list[list]:
    import pandas as pd

    raw = _fetch(source) if re.match(r"^https?://", source) else open(source, "rb").read()
    return frame_rows(pd.read_xml(io.BytesIO(raw)))


def sqlite_tables(path: str) -> list[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                                          "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        con.close()


def sqlite_rows(path: str, query: str) -> list[list]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.execute(query)
        head = [d[0] for d in cur.description or []]
        return [head] + [list(r) for r in cur.fetchmany(1_000_000)]
    finally:
        con.close()


def workbook_rows(path: str, sheet: str | int = 0) -> list[list]:
    import pandas as pd

    return frame_rows(pd.read_excel(path, sheet_name=sheet))


class ExternalDataDialog(QDialog):
    """Data > Get External Data: text/CSV, web page tables, JSON, XML, SQLite database, another workbook."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Get External Data"))
        self.resize(780, 560)
        self.rows: list[list] = []
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(_t("Source:")))
        self.kind = QComboBox()
        for key, label in (("text", "Text / CSV file"), ("web", "Web page (table)"), ("json", "JSON (file or URL)"),
                           ("xml", "XML (file or URL)"), ("sqlite", "SQLite database"),
                           ("excel", "Excel workbook")):
            self.kind.addItem(_t(label), key)
        top.addWidget(self.kind, 1)
        v.addLayout(top)
        row = QHBoxLayout()
        self.location = QLineEdit()
        self.location.setPlaceholderText(_t("File path or https:// address"))
        browse = QPushButton(_t("Browse\u2026"))
        browse.clicked.connect(self._browse)
        load = QPushButton(_t("Load"))
        load.setObjectName("Primary")
        load.clicked.connect(self.load)
        row.addWidget(self.location, 1)
        row.addWidget(browse)
        row.addWidget(load)
        v.addLayout(row)
        opt = QHBoxLayout()
        self.part_label = QLabel(_t("Table:"))
        self.part = QComboBox()
        self.part.setMinimumWidth(240)
        self.part.activated.connect(lambda *_: self._show_part())
        self.query = QLineEdit()
        self.query.setPlaceholderText(_t("optional SQL, e.g. SELECT * FROM orders WHERE total > 100"))
        opt.addWidget(self.part_label)
        opt.addWidget(self.part)
        opt.addWidget(self.query, 1)
        v.addLayout(opt)
        self.preview = QTableWidget()
        v.addWidget(self.preview, 1)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#5f6368")
        v.addWidget(self.status)
        bb = QDialogButtonBox()
        self.ok = bb.addButton(_t("Insert at Selected Cell"), QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.ok.setEnabled(False)
        self._parts: list = []
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self):
        k = self.kind.currentData()
        self.query.setVisible(k == "sqlite")
        self.part.setVisible(k in ("web", "sqlite", "excel"))
        self.part_label.setVisible(k in ("web", "sqlite", "excel"))

    def _browse(self):
        from app.shell.dialogs import get_open_files

        filters = {"text": "Text (*.csv *.tsv *.txt)", "json": "JSON (*.json)", "xml": "XML (*.xml)",
                   "sqlite": "SQLite (*.db *.sqlite *.sqlite3)", "excel": "Excel (*.xlsx *.xlsm *.xls)",
                   "web": "HTML (*.html *.htm)"}
        files = get_open_files(self, _t("Choose a file"), filters.get(self.kind.currentData(), "*"))
        if files:
            self.location.setText(files[0])

    def load(self):
        from app.shell.dialogs import show_error
        from app.shell.tasks import TaskError, run_task

        k, loc = self.kind.currentData(), self.location.text().strip()
        if not loc:
            return
        try:
            if k == "text":
                self.rows = run_task(self, _t("Loading\u2026"), read_text_table, loc)
                self._parts = []
            elif k == "web":
                if os.path.exists(loc):
                    import pandas as pd

                    frames = pd.read_html(loc)
                else:
                    frames = run_task(self, _t("Loading\u2026"), web_tables, loc)
                self._parts = [frame_rows(f) for f in frames]
                self.part.clear()
                for i, f in enumerate(frames):
                    self.part.addItem(_t("Table {0} ({1} rows \u00d7 {2} columns)").format(i + 1, len(f),
                                                                                          len(f.columns)))
                self.rows = self._parts[0] if self._parts else []
            elif k == "json":
                self.rows = run_task(self, _t("Loading\u2026"), json_rows, loc)
            elif k == "xml":
                self.rows = run_task(self, _t("Loading\u2026"), xml_rows, loc)
            elif k == "sqlite":
                q = self.query.text().strip()
                if not q:
                    names = sqlite_tables(loc)
                    self.part.clear()
                    self.part.addItems(names)
                    if not names:
                        raise ValueError(_t("The database has no tables."))
                    q = f'SELECT * FROM "{self.part.currentText()}"'
                self.rows = run_task(self, _t("Loading\u2026"), sqlite_rows, loc, q)
            elif k == "excel":
                import pandas as pd

                names = list(pd.ExcelFile(loc).sheet_names)
                self.part.clear()
                self.part.addItems(names)
                self.rows = run_task(self, _t("Loading\u2026"), workbook_rows, loc, names[0] if names else 0)
        except TaskError as exc:
            show_error(self, _t("Get External Data"), str(exc.original), exc.tb)
            return
        except Exception as exc:
            show_error(self, _t("Get External Data"), str(exc))
            return
        self._fill()

    def _show_part(self):
        k = self.kind.currentData()
        if k == "web" and self._parts:
            self.rows = self._parts[self.part.currentIndex()]
        elif k == "sqlite" and self.part.currentText():
            self.rows = sqlite_rows(self.location.text().strip(), f'SELECT * FROM "{self.part.currentText()}"')
        elif k == "excel" and self.part.currentText():
            self.rows = workbook_rows(self.location.text().strip(), self.part.currentText())
        self._fill()

    def _fill(self):
        rows = self.rows[:201]
        width = max((len(r) for r in rows), default=0)
        self.preview.clear()
        self.preview.setRowCount(max(0, len(rows) - 1))
        self.preview.setColumnCount(width)
        if rows:
            self.preview.setHorizontalHeaderLabels([str(h) for h in rows[0]] + [""] * (width - len(rows[0])))
        for r, row in enumerate(rows[1:]):
            for c, v in enumerate(row):
                self.preview.setItem(r, c, QTableWidgetItem("" if v is None else str(v)))
        self.status.setText(_t("{0} rows \u00d7 {1} columns").format(max(0, len(self.rows) - 1), width))
        self.ok.setEnabled(bool(self.rows))


def insert_rows(w, rows: list[list], label: str) -> None:
    """Put a table of values at the active cell (one undo step)."""
    sh, book = w.sheet, w.book
    r0, c0 = w.current_rc()
    height = len(rows)
    width = max((len(r) for r in rows), default=0)
    before = book.block_states(sh, r0, c0, r0 + height - 1, c0 + width - 1)
    after = {}
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            text = "" if v is None else ("TRUE" if v is True else "FALSE" if v is False else str(v))
            if isinstance(v, str) and text.startswith("="):
                text = "'" + text  # data, not formulas
            after[(r0 + i, c0 + j)] = book.state_for_input(sh, r0 + i, c0 + j, text)
    w.push_states(before, after, label)


def get_external_data(w) -> None:
    d = ExternalDataDialog(w)
    if d.exec() == QDialog.Accepted and d.rows:
        insert_rows(w, d.rows, _t("Get External Data"))


def export_data(w, kind: str) -> None:
    """Export the used range of the active sheet as JSON (records) or a Markdown table."""
    from app.shell.dialogs import get_save_file, show_info

    sh = w.sheet
    r1, c1, r2, c2 = sh.used_range()
    rows = [[sh.display(r, c)[0] for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]
    if not rows:
        return
    ext = "json" if kind == "json" else "md"
    path = get_save_file(w, _t("Export"), "JSON (*.json)" if kind == "json" else "Markdown (*.md)",
                         f"{sh.name}.{ext}", ext)
    if not path:
        return
    head = [h or col_letter(c1 + i) for i, h in enumerate(rows[0])]
    if kind == "json":
        recs = []
        for r in range(r1 + 1, r2 + 1):
            rec = {}
            for i, c in enumerate(range(c1, c2 + 1)):
                v = sh.value(r, c)
                rec[head[i]] = v.code if isinstance(v, XLError) else v
            recs.append(rec)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=1, default=str)
    else:
        esc = lambda s: str(s).replace("|", "\\|")
        lines = ["| " + " | ".join(esc(h) for h in head) + " |", "|" + "---|" * len(head)]
        lines += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows[1:]]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    show_info(w, _t("Export"), _t("Saved:\n{0}").format(path))


# ============================================================================ Format as Table
# Excel built-in style name -> (header fill, header font, band fill, border)
TABLE_STYLES = {
    "TableStyleMedium2": ("#4472c4", "#ffffff", "#d9e1f2", "#8ea9db"),
    "TableStyleMedium3": ("#ed7d31", "#ffffff", "#fce4d6", "#f4b084"),
    "TableStyleMedium4": ("#a5a5a5", "#ffffff", "#ededed", "#c9c9c9"),
    "TableStyleMedium5": ("#ffc000", "#ffffff", "#fff2cc", "#ffd966"),
    "TableStyleMedium6": ("#5b9bd5", "#ffffff", "#ddebf7", "#9bc2e6"),
    "TableStyleMedium7": ("#70ad47", "#ffffff", "#e2efda", "#a9d08e"),
    "TableStyleMedium9": ("#4472c4", "#ffffff", "#b4c6e7", "#ffffff"),
    "TableStyleLight9": ("#4472c4", "#ffffff", None, "#4472c4"),
    "TableStyleLight1": ("#ffffff", "#000000", "#d9d9d9", "#000000"),
    "TableStyleDark1": ("#000000", "#ffffff", "#595959", "#000000"),
}


def table_name_ok(book, name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_\\][A-Za-z0-9_.]{0,254}", name)) and not any(
        t.get("name", "").lower() == name.lower() for sh in book.sheets for t in getattr(sh, "tables", []))


def apply_table(w, r1: int, c1: int, r2: int, c2: int, style: str, header: bool, name: str) -> None:
    """Style the range (header, banded rows, borders) as one undo step and register it as an Excel table, which is
    saved as a real table (``xl/tables/tableN.xml``) with the same built-in style."""
    sh, book = w.sheet, w.book
    head_fill, head_font, band, border = TABLE_STYLES[style]
    bd = Border("thin", border) if border else None
    before = book.block_states(sh, r1, c1, r2, c2)
    after: dict = {}
    seen: set[str] = set()
    for c in range(c1, c2 + 1):
        if header:  # Excel needs unique text headers
            text = sh.display(r1, c)[0].strip() or f"Column{c - c1 + 1}"
            base, n = text, 2
            while text.lower() in seen:
                text = f"{base}{n}"
                n += 1
            seen.add(text.lower())
            cell = sh.cells.get((r1, c))
            st = book.styles.get(cell.style if cell else 0).with_(bold=True, fill=head_fill, color=head_font,
                                                                    border_bottom=bd, border_top=bd)
            after[(r1, c)] = (text, None, book.styles.intern(st))
        for r in range(r1 + (1 if header else 0), r2 + 1):
            cell = sh.cells.get((r, c))
            k = r - r1 - (1 if header else 0)
            st = book.styles.get(cell.style if cell else 0).with_(
                fill=band if band and k % 2 == 0 else None, border_bottom=bd)
            after[(r, c)] = ((cell.value, cell.formula, book.styles.intern(st)) if cell else
                             (None, None, book.styles.intern(st)))
    w.push_states(before, after, _t("Format as Table"))
    sh.tables = [t for t in getattr(sh, "tables", []) if not _overlap(t["ref"], (r1, c1, r2, c2))]
    sh.tables.append({"name": name, "ref": f"{col_letter(c1)}{r1}:{col_letter(c2)}{r2}", "style": style,
                      "header": header})
    w.set_modified(True)


def _ref_bounds(ref: str) -> tuple[int, int, int, int]:
    from app.sheet.formula.parser import parse_ref_text

    n = parse_ref_text(ref)
    return n.r1, n.c1, n.r2, n.c2


def _overlap(ref: str, b: tuple[int, int, int, int]) -> bool:
    try:
        r1, c1, r2, c2 = _ref_bounds(ref)
    except Exception:
        return False
    return not (r2 < b[0] or r1 > b[2] or c2 < b[1] or c1 > b[3])


class FormatTableDialog(QDialog):
    def __init__(self, parent, ref: str, name: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Format as Table"))
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.ref = QLineEdit(ref)
        form.addRow(_t("Range:"), self.ref)
        self.style = QComboBox()
        for key, (hf, _f, band, _b) in TABLE_STYLES.items():
            self.style.addItem(key.replace("TableStyle", ""), key)
            self.style.setItemData(self.style.count() - 1, QColor(hf), Qt.DecorationRole)
        self.style.setCurrentIndex(0)
        form.addRow(_t("Style:"), self.style)
        self.header = QCheckBox(_t("My table has headers"))
        self.header.setChecked(True)
        form.addRow("", self.header)
        self.name = QLineEdit(name)
        form.addRow(_t("Table name:"), self.name)
        self.err = QLabel("")
        self.err.setStyleSheet("color:#c5221f")
        form.addRow(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


def current_region(sh, r: int, c: int) -> tuple[int, int, int, int]:
    """The block of non-empty cells around (r, c) - like Ctrl+A in Excel."""
    def filled(rr, cc):
        cell = sh.cells.get((rr, cc))
        return cell is not None and (cell.value not in (None, "") or cell.formula)
    r1 = r2 = r
    c1 = c2 = c
    changed = True
    while changed:
        changed = False
        if r1 > 1 and any(filled(r1 - 1, cc) for cc in range(c1, c2 + 1)):
            r1 -= 1
            changed = True
        if any(filled(r2 + 1, cc) for cc in range(c1, c2 + 1)):
            r2 += 1
            changed = True
        if c1 > 1 and any(filled(rr, c1 - 1) for rr in range(r1, r2 + 1)):
            c1 -= 1
            changed = True
        if any(filled(rr, c2 + 1) for rr in range(r1, r2 + 1)):
            c2 += 1
            changed = True
    return r1, c1, r2, c2


def format_as_table(w) -> None:
    sh = w.sheet
    r1, c1, r2, c2 = w.sel_bounds()
    if r1 == r2 and c1 == c2:
        r1, c1, r2, c2 = current_region(sh, r1, c1)
    n = 1
    while not table_name_ok(w.book, f"Table{n}"):
        n += 1
    d = FormatTableDialog(w, f"{col_letter(c1)}{r1}:{col_letter(c2)}{r2}", f"Table{n}")
    while d.exec() == QDialog.Accepted:
        try:
            rr1, cc1, rr2, cc2 = _ref_bounds(d.ref.text().strip().replace("$", ""))
        except Exception:
            d.err.setText(_t("The range is not valid."))
            continue
        name = d.name.text().strip()
        if not table_name_ok(w.book, name):
            d.err.setText(_t("Use a unique name with letters, digits and underscores."))
            continue
        if rr2 - rr1 < (1 if d.header.isChecked() else 0):
            d.err.setText(_t("The table needs at least one data row."))
            continue
        apply_table(w, rr1, cc1, rr2, cc2, d.style.currentData(), d.header.isChecked(), name)
        return


# ============================================================================ MyOffice AI
def sheet_context(w, max_rows: int = 6) -> str:
    sh = w.sheet
    r1, c1, r2, c2 = sh.used_range()
    if r2 < r1:
        return "(empty sheet)"
    lines = [f"Sheet \"{sh.name}\", used range {col_letter(c1)}{r1}:{col_letter(c2)}{r2}."]
    for r in range(r1, min(r2, r1 + max_rows - 1) + 1):
        cells = [f"{col_letter(c)}{r}={sh.display(r, c)[0]!r}" for c in range(c1, min(c2, c1 + 15) + 1)
                 if sh.display(r, c)[0] != ""]
        if cells:
            lines.append(", ".join(cells))
    sr1, sc1, sr2, sc2 = w.sel_bounds()
    lines.append(f"Selected: {col_letter(sc1)}{sr1}:{col_letter(sc2)}{sr2}.")
    return "\n".join(lines)


def ai_formula(w) -> None:
    from app.shell.ai_dialogs import FormulaDialog, ensure_ai

    if not ensure_ai(w):
        return
    r, c = w.current_rc()
    FormulaDialog(w, sheet_context(w), f"{col_letter(c)}{r}", lambda f: w.commit_input(r, c, f)).exec()


def selection_summary(w, max_cells: int = 200000) -> str:
    import pandas as pd

    sh = w.sheet
    r1, c1, r2, c2 = w.sel_bounds()
    if r1 == r2 and c1 == c2:
        r1, c1, r2, c2 = current_region(sh, r1, c1)
    if (r2 - r1 + 1) * (c2 - c1 + 1) > max_cells:
        r2 = r1 + max_cells // max(1, c2 - c1 + 1)
    head = [sh.display(r1, c)[0] or col_letter(c) for c in range(c1, c2 + 1)]
    data = []
    for r in range(r1 + 1, r2 + 1):
        row = []
        for c in range(c1, c2 + 1):
            v = sh.value(r, c)
            row.append(None if isinstance(v, XLError) else v)
        data.append(row)
    df = pd.DataFrame(data, columns=[str(h) for h in head])
    out = [f"Range {col_letter(c1)}{r1}:{col_letter(c2)}{r2}: {len(df)} rows, {len(df.columns)} columns."]
    num = df.apply(pd.to_numeric, errors="coerce")
    numeric = [col for col in df.columns if num[col].notna().sum() >= max(1, len(df) // 2)]
    if numeric:
        out.append("Numeric columns (count, mean, std, min, 25%, 50%, 75%, max):")
        out.append(num[numeric].describe().T.round(4).to_string())
        if len(numeric) > 1:
            out.append("Correlations:")
            out.append(num[numeric].corr().round(3).to_string())
    text_cols = [col for col in df.columns if col not in numeric]
    for col in text_cols[:6]:
        top = df[col].dropna().astype(str).value_counts().head(5)
        if len(top):
            out.append(f"Most frequent in '{col}': " + ", ".join(f"{k} ({v})" for k, v in top.items()))
    out.append("First rows:")
    out.append(df.head(8).to_string(index=False))
    return "\n".join(out)


def ai_analyze(w) -> None:
    from app.shell.ai_dialogs import AnalyzeDialog, ensure_ai

    if not ensure_ai(w):
        return
    AnalyzeDialog(w, selection_summary(w)).exec()
