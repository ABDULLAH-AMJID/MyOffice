"""Pratt parser for Excel formulas + reference utilities (translate / insert / delete)."""
from __future__ import annotations

import re

from app.sheet.formula.tokenizer import (MAX_COLS, MAX_ROWS, FormulaSyntaxError, Token, col_to_index, index_to_col,
                                         tokenize)


# ----------------------------------------------------------------------------- AST
class Node:
    __slots__ = ()


class Num(Node):
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class Str(Node):
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class Bool(Node):
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class Err(Node):
    __slots__ = ("code",)

    def __init__(self, code):
        self.code = code


class Ref(Node):
    """Cell or range reference.  Whole columns/rows use row 1..MAX_ROWS / col 1..MAX_COLS."""
    __slots__ = ("sheet", "r1", "c1", "r2", "c2")

    def __init__(self, sheet, r1, c1, r2, c2):
        self.sheet, self.r1, self.c1, self.r2, self.c2 = sheet, r1, c1, r2, c2

    @property
    def is_cell(self):
        return self.r1 == self.r2 and self.c1 == self.c2


class NameRef(Node):
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


class Func(Node):
    __slots__ = ("name", "args")

    def __init__(self, name, args):
        self.name, self.args = name, args


class Unary(Node):
    __slots__ = ("op", "x")

    def __init__(self, op, x):
        self.op, self.x = op, x


class Bin(Node):
    __slots__ = ("op", "a", "b")

    def __init__(self, op, a, b):
        self.op, self.a, self.b = op, a, b


class Pct(Node):
    __slots__ = ("x",)

    def __init__(self, x):
        self.x = x


class ArrayLit(Node):
    __slots__ = ("rows",)

    def __init__(self, rows):
        self.rows = rows


class MissingArg(Node):
    __slots__ = ()


# ----------------------------------------------------------------------------- references
_REF_PART = re.compile(r"(\$?)([A-Za-z]{1,3})?(\$?)([0-9]{1,7})?$")


def split_sheet(text: str) -> tuple[str | None, str]:
    if "!" in text:
        sheet, _, ref = text.rpartition("!")
        if sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
        return sheet, ref
    return None, text


def parse_ref_text(text: str) -> Ref:
    sheet, ref = split_sheet(text)
    parts = ref.split(":")
    if len(parts) == 1:
        m = _REF_PART.match(parts[0])
        c = col_to_index(m.group(2))
        r = int(m.group(4))
        return Ref(sheet, r, c, r, c)
    a, b = (_REF_PART.match(p) for p in parts)
    if a.group(2) and a.group(4):
        r1, c1 = int(a.group(4)), col_to_index(a.group(2))
        r2, c2 = int(b.group(4)), col_to_index(b.group(2))
    elif a.group(2):  # A:C
        r1, r2 = 1, MAX_ROWS
        c1, c2 = col_to_index(a.group(2)), col_to_index(b.group(2))
    else:  # 1:3
        c1, c2 = 1, MAX_COLS
        r1, r2 = int(a.group(4)), int(b.group(4))
    return Ref(sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))


def quote_sheet(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and not re.fullmatch(r"[A-Za-z]{1,3}[0-9]+", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def cell_name(row: int, col: int, rabs: bool = False, cabs: bool = False) -> str:
    return f"{'$' if cabs else ''}{index_to_col(col)}{'$' if rabs else ''}{row}"


def range_name(r1, c1, r2, c2, sheet: str | None = None) -> str:
    s = cell_name(r1, c1) if (r1, c1) == (r2, c2) else f"{cell_name(r1, c1)}:{cell_name(r2, c2)}"
    return f"{quote_sheet(sheet)}!{s}" if sheet else s


# ----------------------------------------------------------------------------- parser
_BINARY = {"=": 20, "<>": 20, "<": 20, ">": 20, "<=": 20, ">=": 20, "&": 30, "+": 40, "-": 40, "*": 50, "/": 50,
           "^": 60, ":": 90, " ": 85}
_PREFIX_BP = 70
_POSTFIX_PCT = 65


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> Token:
        return self.toks[self.i]

    def next(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind: str) -> Token:
        t = self.next()
        if t.kind != kind:
            raise FormulaSyntaxError(f"Expected {kind} but found {t.text or 'end of formula'!r} at {t.pos + 1}")
        return t

    def parse(self) -> Node:
        node = self.expr(0)
        if self.peek().kind != "EOF":
            t = self.peek()
            raise FormulaSyntaxError(f"Unexpected {t.text!r} at {t.pos + 1}")
        return node

    def expr(self, min_bp: int) -> Node:
        left = self.prefix()
        while True:
            t = self.peek()
            if t.kind == "OP" and t.text == "%":
                if _POSTFIX_PCT < min_bp:
                    break
                self.next()
                left = Pct(left)
                continue
            if t.kind != "OP" or t.text not in _BINARY:
                break
            bp = _BINARY[t.text]
            if bp < min_bp or (bp == min_bp):
                break
            self.next()
            right = self.expr(bp)
            left = Bin(t.text, left, right)
        return left

    def prefix(self) -> Node:
        t = self.next()
        k = t.kind
        if k == "NUM":
            return Num(t.value)
        if k == "STR":
            return Str(t.value)
        if k == "BOOL":
            return Bool(t.value)
        if k == "ERR":
            return Err(t.text)
        if k == "REF":
            return parse_ref_text(t.text)
        if k == "NAME":
            return NameRef(t.text)
        if k == "FUNC":
            self.expect("LP")
            args = []
            if self.peek().kind == "RP":
                self.next()
                return Func(t.value, args)
            while True:
                if self.peek().kind in ("SEP", "ROWSEP", "RP"):
                    args.append(MissingArg())
                else:
                    args.append(self.expr(0))
                nk = self.next()
                if nk.kind == "RP":
                    break
                if nk.kind not in ("SEP", "ROWSEP"):
                    raise FormulaSyntaxError(f"Expected ',' or ')' at {nk.pos + 1}")
                if self.peek().kind == "RP":
                    args.append(MissingArg())
            return Func(t.value, args)
        if k == "LP":
            node = self.expr(0)
            self.expect("RP")
            return node
        if k == "OP" and t.text in ("-", "+"):
            x = self.expr(_PREFIX_BP)
            return Unary(t.text, x)
        if k == "LBRACE":
            rows, row = [], []
            while True:
                tk = self.next()
                neg = False
                if tk.kind == "OP" and tk.text == "-":
                    neg, tk = True, self.next()
                if tk.kind == "NUM":
                    row.append(-tk.value if neg else tk.value)
                elif tk.kind == "STR":
                    row.append(tk.value)
                elif tk.kind == "BOOL":
                    row.append(tk.value)
                elif tk.kind == "ERR":
                    from app.sheet.formula.values import XLError

                    row.append(XLError(tk.text))
                else:
                    raise FormulaSyntaxError(f"Invalid array constant at {tk.pos + 1}")
                sep = self.next()
                if sep.kind == "SEP":
                    continue
                if sep.kind == "ROWSEP":
                    rows.append(row)
                    row = []
                    continue
                if sep.kind == "RBRACE":
                    rows.append(row)
                    break
                raise FormulaSyntaxError(f"Invalid array constant at {sep.pos + 1}")
            if len({len(r) for r in rows}) != 1:
                raise FormulaSyntaxError("Array constant rows must have the same length")
            return ArrayLit(rows)
        if k == "EOF":
            raise FormulaSyntaxError("The formula is incomplete")
        raise FormulaSyntaxError(f"Unexpected {t.text!r} at {t.pos + 1}")


def parse(formula: str) -> Node:
    text = formula[1:] if formula.startswith("=") else formula
    if not text.strip():
        raise FormulaSyntaxError("Empty formula")
    return Parser(tokenize(text)).parse()


# ----------------------------------------------------------------------------- analysis
def walk(node: Node):
    yield node
    if isinstance(node, Func):
        for a in node.args:
            yield from walk(a)
    elif isinstance(node, Bin):
        yield from walk(node.a)
        yield from walk(node.b)
    elif isinstance(node, (Unary, Pct)):
        yield from walk(node.x)


def references(node: Node) -> list[Ref]:
    return [n for n in walk(node) if isinstance(n, Ref)]


def function_names(node: Node) -> set[str]:
    return {n.name for n in walk(node) if isinstance(n, Func)}


def names_used(node: Node) -> set[str]:
    return {n.name.upper() for n in walk(node) if isinstance(n, NameRef)}


# ----------------------------------------------------------------------------- token-level rewriting
def _rewrite(formula: str, fn) -> str:
    """Apply ``fn(ref_token_text) -> new_text`` to every reference token, keeping all other text."""
    if not formula.startswith("="):
        return formula
    body = formula[1:]
    try:
        toks = tokenize(body)
    except FormulaSyntaxError:
        return formula
    out, last = [], 0
    for t in toks:
        if t.kind == "REF":
            out.append(body[last:t.pos])
            out.append(fn(t.text))
            last = t.pos + len(t.text)
    out.append(body[last:])
    return "=" + "".join(out)


_PART_RE = re.compile(r"(\$?)([A-Za-z]{1,3})?(\$?)([0-9]{1,7})?")


def _shift_part(part: str, drow: int, dcol: int) -> str:
    m = _PART_RE.fullmatch(part)
    if not m:
        return part
    cabs, col, rabs, row = m.groups()
    out = ""
    if col:
        c = col_to_index(col)
        if not cabs:
            c += dcol
        if c < 1 or c > MAX_COLS:
            return "#REF!"
        out += cabs + index_to_col(c)
    if row:
        r = int(row)
        if not rabs:
            r += drow
        if r < 1 or r > MAX_ROWS:
            return "#REF!"
        out += rabs + str(r)
    return out


def translate_formula(formula: str, drow: int, dcol: int) -> str:
    """Shift relative references (copy / fill semantics)."""
    def fn(text):
        sheet_prefix = ""
        if "!" in text:
            sheet_prefix, _, text = text.rpartition("!")
            sheet_prefix += "!"
        parts = [_shift_part(p, drow, dcol) for p in text.split(":")]
        if "#REF!" in parts:
            return "#REF!"
        return sheet_prefix + ":".join(parts)
    return _rewrite(formula, fn)


def adjust_for_structure(formula: str, formula_sheet: str, target_sheet: str, axis: str, at: int, count: int) -> str:
    """Update references after inserting (count>0) or deleting (count<0) rows/columns.

    ``axis`` is "row" or "col"; ``at`` is the first inserted/deleted index (1-based)."""
    def adj(idx: int, is_end: bool):
        if count > 0:
            return idx + count if idx >= at else idx
        first, last = at, at - count - 1
        if idx < first:
            return idx
        if idx > last:
            return idx + count
        return None if not is_end else first - 1

    def fn(text):
        sheet = None
        prefix = ""
        if "!" in text:
            prefix, _, rest = text.rpartition("!")
            sheet = prefix[1:-1].replace("''", "'") if prefix.startswith("'") else prefix
            prefix += "!"
        else:
            rest = text
        if (sheet or formula_sheet) != target_sheet:
            return text
        parts = rest.split(":")
        ms = [_PART_RE.fullmatch(p) for p in parts]
        if not all(ms):
            return text
        new_parts = []
        for i, m in enumerate(ms):
            cabs, col, rabs, row = m.groups()
            is_end = i == len(ms) - 1 and len(ms) == 2
            if axis == "row" and row:
                r = int(row)
                nr = adj(r, is_end)
                if nr is None and len(ms) == 2 and i == 0:
                    # range start deleted: move to first surviving row
                    nr = at
                if nr is None or nr < 1:
                    return "#REF!"
                row = str(nr)
            if axis == "col" and col:
                c = col_to_index(col)
                nc = adj(c, is_end)
                if nc is None and len(ms) == 2 and i == 0:
                    nc = at
                if nc is None or nc < 1:
                    return "#REF!"
                col = index_to_col(nc)
            new_parts.append(f"{cabs}{col or ''}{rabs}{row or ''}")
        if len(new_parts) == 2:
            a, b = (_PART_RE.fullmatch(p) for p in new_parts)
            ra, rb = a.group(4), b.group(4)
            ca, cb = a.group(2), b.group(2)
            if (ra and rb and int(rb) < int(ra)) or (ca and cb and col_to_index(cb) < col_to_index(ca)):
                return "#REF!"
        return prefix + ":".join(new_parts)
    return _rewrite(formula, fn)


def rename_sheet_in_formula(formula: str, old: str, new: str) -> str:
    def fn(text):
        if "!" not in text:
            return text
        prefix, _, rest = text.rpartition("!")
        sheet = prefix[1:-1].replace("''", "'") if prefix.startswith("'") else prefix
        if sheet == old:
            return f"{quote_sheet(new)}!{rest}"
        return text
    return _rewrite(formula, fn)
