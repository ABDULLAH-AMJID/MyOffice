"""Compile formula ASTs into Python closures ``f(ctx) -> value``."""
from __future__ import annotations

import math
import operator

from app.sheet.formula.functions import FUNCTIONS
from app.sheet.formula.parser import (ArrayLit, Bin, Bool, Err, Func, MissingArg, NameRef, Node, Num, Pct, Ref, Str,
                                      Unary, parse)
from app.sheet.formula.tokenizer import FormulaSyntaxError
from app.sheet.formula.values import (DIV0, MISSING, NAME, NULL, NUM, REF, VALUE, Array, RangeRef, XLError, compare,
                                      deref, to_number, to_text)

VOLATILE = {name for name, spec in FUNCTIONS.items() if spec.volatile}


class EvalContext:
    __slots__ = ("book", "sid", "row", "col", "cells", "depth")

    def __init__(self, book, sid=0, row=1, col=1):
        self.book, self.sid, self.row, self.col = book, sid, row, col
        self.cells = None
        self.depth = 0


# ----------------------------------------------------------------------------- operators
def _check(x: float) -> float:
    if x != x or x == math.inf or x == -math.inf:
        raise NUM
    return x


def _div(a, b):
    if b == 0:
        raise DIV0
    return a / b


def _pow(a, b):
    if a == 0 and b == 0:
        raise NUM
    if a == 0 and b < 0:
        raise DIV0
    try:
        r = a ** b
    except OverflowError:
        raise NUM
    if isinstance(r, complex):
        raise NUM
    return r


_ARITH = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": _div, "^": _pow}
_CMP = {"=": lambda c: c == 0, "<>": lambda c: c != 0, "<": lambda c: c < 0, ">": lambda c: c > 0,
        "<=": lambda c: c <= 0, ">=": lambda c: c >= 0}


def _num_operand(v):
    if isinstance(v, XLError):
        raise v
    if v is None:
        return 0.0
    if v.__class__ is float:
        return v
    return to_number(v)


def _scalar_binop(op, a, b):
    if op in _ARITH:
        return _check(_ARITH[op](_num_operand(a), _num_operand(b)))
    if op == "&":
        if isinstance(a, XLError):
            raise a
        if isinstance(b, XLError):
            raise b
        return to_text(a) + to_text(b)
    return _CMP[op](compare(a, b))


def _broadcast(op, a, b):
    """Element-wise operation when an operand is an array; errors become array elements."""
    ra = a.rows if isinstance(a, Array) else [[a]]
    rb = b.rows if isinstance(b, Array) else [[b]]
    nr = max(len(ra), len(rb))
    nc = max(len(ra[0]) if ra else 0, len(rb[0]) if rb else 0)
    out = []
    for i in range(nr):
        row = []
        for j in range(nc):
            try:
                x = ra[i if len(ra) > 1 else 0][j if len(ra[0]) > 1 else 0]
                y = rb[i if len(rb) > 1 else 0][j if len(rb[0]) > 1 else 0]
            except IndexError:
                row.append(XLError("#N/A"))
                continue
            try:
                row.append(_scalar_binop(op, x, y))
            except XLError as e:
                row.append(e)
        out.append(row)
    return Array(out)


def _binop(op, a, b):
    if isinstance(a, Array) or isinstance(b, Array):
        return _broadcast(op, a, b)
    return _scalar_binop(op, a, b)


def _map_array(fn, v):
    if isinstance(v, Array):
        out = []
        for row in v.rows:
            r2 = []
            for x in row:
                try:
                    r2.append(fn(x))
                except XLError as e:
                    r2.append(e)
            out.append(r2)
        return Array(out)
    return fn(v)


# ----------------------------------------------------------------------------- compiler
class Compiler:
    def __init__(self):
        self.volatile = False
        self.uses_names = set()

    # -- values (operands of operators)
    def value(self, node: Node):
        if isinstance(node, Ref) and node.is_cell:
            return self._cell_value(node)
        f = self.ref(node)
        if isinstance(node, (Ref, NameRef, Func)):
            return lambda ctx: deref(f(ctx))
        return f

    def _cell_value(self, node: Ref):
        r, c, sheet = node.r1, node.c1, node.sheet
        if sheet is None:
            def f(ctx):
                cell = ctx.cells.get((r, c))
                return None if cell is None else cell.value
        else:
            def f(ctx):
                sh = ctx.book.sheet_by_name(sheet)
                if sh is None:
                    raise REF
                cell = sh.cells.get((r, c))
                return None if cell is None else cell.value
        return f

    # -- references (function arguments)
    def ref(self, node: Node):
        if isinstance(node, Num):
            v = float(node.v)
            return lambda ctx: v
        if isinstance(node, (Str, Bool)):
            v = node.v
            return lambda ctx: v
        if isinstance(node, Err):
            err = XLError(node.code)

            def f(ctx):
                raise err
            return f
        if isinstance(node, MissingArg):
            return lambda ctx: MISSING
        if isinstance(node, Ref):
            r1, c1, r2, c2, sheet = node.r1, node.c1, node.r2, node.c2, node.sheet
            if sheet is None:
                return lambda ctx: RangeRef(ctx.book, ctx.sid, r1, c1, r2, c2)

            def f(ctx):
                sid = ctx.book.sheet_id(sheet)
                return RangeRef(ctx.book, sid, r1, c1, r2, c2)
            return f
        if isinstance(node, NameRef):
            return self._name(node.name)
        if isinstance(node, ArrayLit):
            arr = Array([[float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v for v in row]
                         for row in node.rows])
            return lambda ctx: arr
        if isinstance(node, Unary):
            x = self.value(node.x)
            if node.op == "-":
                return lambda ctx: _map_array(lambda v: -_num_operand(v), x(ctx))
            return x
        if isinstance(node, Pct):
            x = self.value(node.x)
            return lambda ctx: _map_array(lambda v: _num_operand(v) / 100.0, x(ctx))
        if isinstance(node, Bin):
            return self._bin(node)
        if isinstance(node, Func):
            return self._func(node)
        raise FormulaSyntaxError(f"Unsupported expression {node!r}")

    def _bin(self, node: Bin):
        op = node.op
        if op in (":", " "):
            fa, fb = self.ref(node.a), self.ref(node.b)

            def f(ctx):
                a, b = fa(ctx), fb(ctx)
                if not isinstance(a, RangeRef) or not isinstance(b, RangeRef) or a.sid != b.sid:
                    raise VALUE
                if op == ":":
                    return RangeRef(a.book, a.sid, min(a.r1, b.r1), min(a.c1, b.c1), max(a.r2, b.r2), max(a.c2, b.c2))
                r1, c1, r2, c2 = max(a.r1, b.r1), max(a.c1, b.c1), min(a.r2, b.r2), min(a.c2, b.c2)
                if r1 > r2 or c1 > c2:
                    raise NULL
                return RangeRef(a.book, a.sid, r1, c1, r2, c2)
            return f
        fa, fb = self.value(node.a), self.value(node.b)
        if op in _ARITH:
            arith = _ARITH[op]

            def f(ctx):
                a, b = fa(ctx), fb(ctx)
                if a.__class__ is float and b.__class__ is float:
                    return _check(arith(a, b))
                return _binop(op, a, b)
            return f
        return lambda ctx: _binop(op, fa(ctx), fb(ctx))

    def _name(self, name: str):
        key = name.upper()
        self.uses_names.add(key)

        def f(ctx):
            book = ctx.book
            text = book.names.get(key)
            if text is None:
                raise NAME
            cached = book.name_cache.get(key)
            if cached is None or cached[0] != text:
                try:
                    comp = Compiler()
                    fn_ = comp.ref(parse(text if text.startswith("=") else "=" + text))
                except FormulaSyntaxError:
                    raise NAME
                cached = (text, fn_)
                book.name_cache[key] = cached
            if ctx.depth > 32:
                raise NAME
            ctx.depth += 1
            try:
                return cached[1](ctx)
            finally:
                ctx.depth -= 1
        return f

    def _func(self, node: Func):
        spec = FUNCTIONS.get(node.name)
        if spec is None:
            def unknown(ctx):
                raise NAME
            return unknown
        n = len(node.args)
        if n < spec.min_args or (spec.max_args is not None and n > spec.max_args):
            raise FormulaSyntaxError(f"{node.name} expects "
                                     f"{spec.min_args if spec.max_args == spec.min_args else str(spec.min_args) + '-' + (str(spec.max_args) if spec.max_args is not None else 'many')}"
                                     f" argument(s), got {n}")
        if spec.volatile:
            self.volatile = True
        args = [self.ref(a) for a in node.args]
        impl = spec.impl
        if spec.lazy:
            return lambda ctx: impl(ctx, *args)
        if spec.ctx:
            return lambda ctx: impl(ctx, *[a(ctx) for a in args])
        if n == 0:
            return lambda ctx: impl()
        if n == 1:
            a0 = args[0]
            return lambda ctx: impl(a0(ctx))
        if n == 2:
            a0, a1 = args
            return lambda ctx: impl(a0(ctx), a1(ctx))
        return lambda ctx: impl(*[a(ctx) for a in args])


def compile_node(node: Node):
    """Return (closure, volatile, names_used)."""
    comp = Compiler()
    f = comp.ref(node)
    return f, comp.volatile, comp.uses_names


def finalize(v):
    """Convert an evaluation result into a storable cell value."""
    if isinstance(v, RangeRef):
        v = deref(v)
    if isinstance(v, Array):
        v = v.first()
    if v is None or v is MISSING:
        return 0.0
    if isinstance(v, bool) or isinstance(v, str) or isinstance(v, XLError):
        return v
    if isinstance(v, int):
        return float(v)
    if isinstance(v, float):
        if v != v or v in (math.inf, -math.inf):
            return NUM
        return v
    return v
