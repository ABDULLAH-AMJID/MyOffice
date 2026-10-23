"""Value types and Excel coercion rules used by the formula engine."""
from __future__ import annotations

import datetime as _dt
import math


class XLError(Exception):
    """An Excel error value (#DIV/0!, #N/A ...).  Instances are used as values *and* raised."""
    __slots__ = ("code",)
    _cache: dict = {}

    def __new__(cls, code: str):
        obj = cls._cache.get(code)
        if obj is None:
            obj = super().__new__(cls)
            obj.code = code
            cls._cache[code] = obj
        return obj

    def __init__(self, code: str):
        super().__init__(code)

    def __repr__(self):
        return self.code

    def __str__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, XLError) and other.code == self.code

    def __hash__(self):
        return hash(self.code)

    def __reduce__(self):
        return (XLError, (self.code,))


DIV0 = XLError("#DIV/0!")
NA = XLError("#N/A")
VALUE = XLError("#VALUE!")
REF = XLError("#REF!")
NAME = XLError("#NAME?")
NUM = XLError("#NUM!")
NULL = XLError("#NULL!")
CIRC = XLError("#CIRC!")
CALC = XLError("#CALC!")
ERROR_TYPES = {"#NULL!": 1, "#DIV/0!": 2, "#VALUE!": 3, "#REF!": 4, "#NAME?": 5, "#NUM!": 6, "#N/A": 7,
               "#GETTING_DATA": 8, "#SPILL!": 9, "#CALC!": 14, "#CIRC!": 15}

EPOCH = _dt.datetime(1899, 12, 30)


class Missing:
    """An omitted function argument, e.g. the middle one in IF(A1,,2)."""
    __slots__ = ()

    def __repr__(self):
        return "<missing>"


MISSING = Missing()


class Array:
    """A 2-D array value (list of rows)."""
    __slots__ = ("rows",)

    def __init__(self, rows: list[list]):
        self.rows = rows

    @property
    def nrows(self) -> int:
        return len(self.rows)

    @property
    def ncols(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def flat(self):
        for r in self.rows:
            yield from r

    def first(self):
        return self.rows[0][0] if self.rows and self.rows[0] else None

    def __repr__(self):
        return f"Array({self.rows!r})"


class RangeRef:
    """A reference to a rectangular block of cells (r1..r2, c1..c2, 1-based, inclusive)."""
    __slots__ = ("book", "sid", "r1", "c1", "r2", "c2")

    def __init__(self, book, sid, r1, c1, r2, c2):
        self.book, self.sid = book, sid
        self.r1, self.c1, self.r2, self.c2 = r1, c1, r2, c2

    @property
    def is_cell(self) -> bool:
        return self.r1 == self.r2 and self.c1 == self.c2

    @property
    def nrows(self) -> int:
        return self.r2 - self.r1 + 1

    @property
    def ncols(self) -> int:
        return self.c2 - self.c1 + 1

    def sheet(self):
        return self.book.sheet_by_id(self.sid)

    def value(self):
        """Value of a single cell (top-left)."""
        cell = self.sheet().cells.get((self.r1, self.c1))
        return None if cell is None else cell.value

    def clamp(self):
        sh = self.sheet()
        mr, mc = sh.max_row, sh.max_col
        return self.r1, self.c1, min(self.r2, max(mr, self.r1)), min(self.c2, max(mc, self.c1))

    def rows_values(self) -> list[list]:
        """2-D list of values, clamped to the used area (full size for small ranges)."""
        cells = self.sheet().cells
        r1, c1, r2, c2 = self.r1, self.c1, self.r2, self.c2
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 20000:
            r1, c1, r2, c2 = self.clamp()
        get = cells.get
        out = []
        for r in range(r1, r2 + 1):
            row = []
            for c in range(c1, c2 + 1):
                cell = get((r, c))
                row.append(None if cell is None else cell.value)
            out.append(row)
        return out

    def iter_values(self):
        """Yield the values of non-empty cells (fast path for large/sparse ranges)."""
        sh = self.sheet()
        cells = sh.cells
        r1, c1, r2, c2 = self.r1, self.c1, self.r2, self.c2
        area = (r2 - r1 + 1) * (c2 - c1 + 1)
        if area > 4 * max(1, len(cells)):
            for (r, c), cell in list(cells.items()):
                if r1 <= r <= r2 and c1 <= c <= c2 and cell.value is not None:
                    yield cell.value
        else:
            get = cells.get
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    cell = get((r, c))
                    if cell is not None and cell.value is not None:
                        yield cell.value

    def to_array(self) -> Array:
        return Array(self.rows_values())

    def offset(self, dr: int, dc: int, h: int | None = None, w: int | None = None) -> "RangeRef":
        r1, c1 = self.r1 + dr, self.c1 + dc
        h = self.nrows if h is None else h
        w = self.ncols if w is None else w
        if r1 < 1 or c1 < 1 or h < 1 or w < 1:
            raise REF
        return RangeRef(self.book, self.sid, r1, c1, r1 + h - 1, c1 + w - 1)

    def __repr__(self):
        return f"RangeRef({self.sid},{self.r1},{self.c1},{self.r2},{self.c2})"


# ----------------------------------------------------------------------------- coercion
def deref(v):
    """Turn references into values (single cell) or arrays (multi-cell)."""
    if isinstance(v, RangeRef):
        if v.is_cell:
            return v.value()
        return v.to_array()
    return v


def scalar(v):
    """A single value: references/arrays use their top-left element."""
    if isinstance(v, RangeRef):
        v = v.value() if v.is_cell else v.to_array().first()
    elif isinstance(v, Array):
        v = v.first()
    if v is MISSING:
        return None
    return v


def parse_number_text(s: str):
    t = s.strip().replace(",", "")
    if not t:
        return None
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    for sym in ("$", "Rs.", "Rs", "\u20a8", "\u20ac", "\u00a3", "\u20b9"):
        if t.startswith(sym):
            t = t[len(sym):].strip()
    try:
        v = float(t)
    except ValueError:
        return parse_date_text(s)
    return v / 100 if pct else v


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%d %B %Y",
                 "%B %d, %Y", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M")


def parse_date_text(s: str):
    t = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime_to_serial(_dt.datetime.strptime(t, fmt))
        except ValueError:
            continue
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            tm = _dt.datetime.strptime(t, fmt)
            return (tm.hour * 3600 + tm.minute * 60 + tm.second) / 86400.0
        except ValueError:
            continue
    return None


def to_number(v, strict_text: bool = False) -> float:
    v = scalar(v)
    if isinstance(v, XLError):
        raise v
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        if strict_text:
            raise VALUE
        n = parse_number_text(v)
        if n is None:
            raise VALUE
        return n
    if isinstance(v, (_dt.datetime, _dt.date)):
        return datetime_to_serial(v)
    raise VALUE


def to_int(v) -> int:
    return int(math.floor(to_number(v)))


def to_text(v) -> str:
    v = scalar(v)
    if isinstance(v, XLError):
        raise v
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return number_to_text(v)
    return str(v)


def number_to_text(v: float) -> str:
    if v != v or v in (float("inf"), float("-inf")):
        return "#NUM!"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = repr(round(v, 15 - max(0, len(str(int(abs(v)))))))
    if "e" in s or "E" in s:
        s = f"{v:.10G}"
    return s


def to_bool(v) -> bool:
    v = scalar(v)
    if isinstance(v, XLError):
        raise v
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        u = v.strip().upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
        raise VALUE
    raise VALUE


def type_rank(v) -> int:
    """Excel sort order: numbers < text < logical (errors handled separately)."""
    if isinstance(v, bool):
        return 2
    if isinstance(v, str):
        return 1
    return 0


def compare(a, b) -> int:
    """Excel comparison semantics: -1, 0, 1 (case-insensitive text; blank equals 0/""/FALSE)."""
    if isinstance(a, XLError):
        raise a
    if isinstance(b, XLError):
        raise b
    if a is None:
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0.0)
    if b is None:
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0.0)
    ra, rb = type_rank(a), type_rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if isinstance(a, str):
        a, b = a.lower(), b.lower()
    elif isinstance(a, bool):
        a, b = int(a), int(b)
    else:
        a, b = float(a), float(b)
    return 0 if a == b else (-1 if a < b else 1)


def datetime_to_serial(d) -> float:
    if isinstance(d, _dt.datetime):
        delta = d - EPOCH
    elif isinstance(d, _dt.date):
        delta = _dt.datetime(d.year, d.month, d.day) - EPOCH
    elif isinstance(d, _dt.time):
        return (d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6) / 86400.0
    else:
        raise VALUE
    return delta.days + delta.seconds / 86400.0 + delta.microseconds / 86400e6


def serial_to_datetime(v: float) -> _dt.datetime:
    if v < 0 or v > 2958465:
        raise NUM
    days = int(math.floor(v))
    frac = v - days
    secs = int(round(frac * 86400))
    return EPOCH + _dt.timedelta(days=days, seconds=secs)


def flatten(args, skip_text_in_refs: bool = True, skip_bool_in_refs: bool = True, errors: bool = True):
    """Yield numbers following Excel's aggregation rules (SUM/AVERAGE...).

    Direct arguments: numbers, numeric text and booleans count.  Values in references/arrays:
    only numbers count (text and booleans are ignored).  Errors propagate."""
    for a in args:
        if a is MISSING:
            continue
        if isinstance(a, RangeRef):
            for v in a.iter_values():
                if isinstance(v, bool):
                    if not skip_bool_in_refs:
                        yield 1.0 if v else 0.0
                elif isinstance(v, (int, float)):
                    yield float(v)
                elif isinstance(v, XLError):
                    if errors:
                        raise v
                elif isinstance(v, str) and not skip_text_in_refs:
                    yield 0.0
        elif isinstance(a, Array):
            for v in a.flat():
                if isinstance(v, bool):
                    if not skip_bool_in_refs:
                        yield 1.0 if v else 0.0
                elif isinstance(v, (int, float)):
                    yield float(v)
                elif isinstance(v, XLError):
                    if errors:
                        raise v
        elif isinstance(a, XLError):
            raise a
        elif a is None:
            continue
        else:
            yield to_number(a)


def iter_all(arg):
    """All values (including blanks as None) of a reference/array/scalar."""
    if isinstance(arg, RangeRef):
        for row in arg.rows_values():
            yield from row
    elif isinstance(arg, Array):
        yield from arg.flat()
    else:
        yield arg


def as_rows(arg) -> list[list]:
    if isinstance(arg, RangeRef):
        return arg.rows_values()
    if isinstance(arg, Array):
        return arg.rows
    return [[arg]]
