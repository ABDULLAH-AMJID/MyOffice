"""Excel-compatible worksheet function library."""
from __future__ import annotations

import datetime as _dt
import math
import random
import re
import statistics
from dataclasses import dataclass

from app.sheet.formula.values import (DIV0, MISSING, NA, NAME, NUM, REF, VALUE, Array, RangeRef, XLError, as_rows,
                                      compare, datetime_to_serial, deref, flatten, iter_all, parse_date_text,
                                      scalar, serial_to_datetime, to_bool, to_int, to_number, to_text)


@dataclass
class FuncSpec:
    impl: object
    min_args: int = 0
    max_args: int | None = None
    lazy: bool = False      # receives (ctx, *arg_closures)
    ctx: bool = False       # receives ctx as first argument
    volatile: bool = False
    category: str = "Other"
    syntax: str = ""
    help: str = ""


FUNCTIONS: dict[str, FuncSpec] = {}


def fn(names, min_args=0, max_args=None, *, lazy=False, ctx=False, volatile=False, cat="Other", syntax="", help=""):
    def deco(f):
        for n in (names if isinstance(names, (tuple, list)) else (names,)):
            FUNCTIONS[n] = FuncSpec(f, min_args, max_args, lazy, ctx, volatile, cat, syntax or f"{n}()", help)
        return f
    return deco


def _num(v):
    r = to_number(v)
    if r != r or r in (math.inf, -math.inf):
        raise NUM
    return r


def _opt(v, default):
    return default if v is MISSING or v is None else v


def _check(x: float) -> float:
    if x != x or x in (math.inf, -math.inf):
        raise NUM
    return x


# ============================================================================= criteria
_CRIT_RE = re.compile(r"^(<=|>=|<>|<|>|=)?(.*)$", re.S)


def wildcard_regex(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern):
            out += re.escape(pattern[i + 1])
            i += 2
            continue
        out += ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
        i += 1
    return re.compile(f"^{out}$", re.I | re.S)


def make_criteria(crit):
    """Return a predicate implementing COUNTIF-style criteria."""
    crit = scalar(crit)
    if isinstance(crit, XLError):
        return lambda v: isinstance(v, XLError) and v == crit
    if isinstance(crit, bool):
        return lambda v: isinstance(v, bool) and v == crit
    if isinstance(crit, (int, float)):
        c = float(crit)
        return lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) == c) or \
            (isinstance(v, str) and _textnum(v) == c)
    s = "" if crit is None else str(crit)
    m = _CRIT_RE.match(s)
    op, operand = m.group(1) or "=", m.group(2)
    num = None
    try:
        num = float(operand.replace(",", "")) if operand.strip() != "" else None
    except ValueError:
        num = parse_date_text(operand) if operand.strip() else None
    up = operand.upper()
    if up in ("TRUE", "FALSE") and num is None:
        b = up == "TRUE"
        if op == "=":
            return lambda v: isinstance(v, bool) and v == b
        if op == "<>":
            return lambda v: not (isinstance(v, bool) and v == b)
    if op in ("=", "<>"):
        if operand == "":
            pred = (lambda v: v is None or v == "") if op == "=" else (lambda v: not (v is None or v == ""))
            return pred
        if num is not None:
            eq = lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) == num) or \
                (isinstance(v, str) and _textnum(v) == num)
        elif any(ch in operand for ch in "*?~"):
            rx = wildcard_regex(operand)
            eq = lambda v: isinstance(v, str) and bool(rx.match(v))
        else:
            low = operand.lower()
            eq = lambda v: isinstance(v, str) and v.lower() == low
        return eq if op == "=" else (lambda v: not eq(v))
    if num is not None:
        cmp = {"<": lambda a: a < num, ">": lambda a: a > num, "<=": lambda a: a <= num, ">=": lambda a: a >= num}[op]
        return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and cmp(float(v))
    low = operand.lower()
    cmp = {"<": lambda a: a < low, ">": lambda a: a > low, "<=": lambda a: a <= low, ">=": lambda a: a >= low}[op]
    return lambda v: isinstance(v, str) and cmp(v.lower())


def _textnum(v: str):
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def _pairs_filter(args):
    """For *IFS functions: return a list of booleans for the cells matching all criteria pairs."""
    if len(args) % 2:
        raise VALUE
    mask = None
    shape = None
    for i in range(0, len(args), 2):
        rng, crit = args[i], args[i + 1]
        vals = [v for v in iter_all(rng)]
        if shape is None:
            shape = len(vals)
        elif len(vals) != shape:
            raise VALUE
        pred = make_criteria(crit)
        m = [pred(v) for v in vals]
        mask = m if mask is None else [a and b for a, b in zip(mask, m)]
    return mask or []


def _sum_range_values(rng, n: int):
    vals = list(iter_all(rng))
    if isinstance(rng, RangeRef) and len(vals) < n:
        # Excel resizes the sum range to the criteria range size
        r = rng.offset(0, 0, None, None)
        vals = list(iter_all(RangeRef(r.book, r.sid, r.r1, r.c1, r.r1 + max(1, n // max(1, r.ncols)) - 1, r.c2)))
    return vals


# ============================================================================= fast range statistics
_FAST_AREA = 48


def _range_stats(rr: RangeRef):
    """(sum, count, min, max) of the numbers in a large range via the column cache, or None."""
    if rr.nrows * rr.ncols < _FAST_AREA:
        return None
    sh = rr.sheet()
    if not hasattr(sh, "numeric_column"):
        return None
    r1, c1, r2, c2 = rr.clamp()
    tot, cnt, mn, mx = 0.0, 0, math.inf, -math.inf
    for c in range(c1, c2 + 1):
        ent = sh.numeric_column(c)
        if ent is None:
            return None
        arr, errs = ent
        for r, e in errs:
            if r1 <= r <= r2:
                raise e
        hi = min(r2, len(arr) - 1)
        if hi < r1:
            continue
        seg = arr[r1:hi + 1]
        vals = seg[seg == seg]  # drop NaN
        k = len(vals)
        if k:
            tot += float(vals.sum())
            cnt += k
            mn = min(mn, float(vals.min()))
            mx = max(mx, float(vals.max()))
    return tot, cnt, mn, mx


def _agg(args):
    """Combine fast stats for big ranges with the generic path for everything else."""
    tot, cnt, mn, mx = 0.0, 0, math.inf, -math.inf
    rest = []
    for a in args:
        st = _range_stats(a) if isinstance(a, RangeRef) else None
        if st is None:
            rest.append(a)
            continue
        tot += st[0]
        cnt += st[1]
        mn, mx = min(mn, st[2]), max(mx, st[3])
    if rest:
        vals = list(flatten(rest))
        if vals:
            tot += math.fsum(vals)
            cnt += len(vals)
            mn, mx = min(mn, min(vals)), max(mx, max(vals))
    return tot, cnt, mn, mx


# ============================================================================= math
@fn("SUM", cat="Math", syntax="SUM(number1, [number2], ...)", help="Adds all the numbers.")
def f_sum(*args):
    if any(isinstance(a, RangeRef) for a in args):
        return _agg(args)[0]
    return math.fsum(flatten(args))


@fn("PRODUCT", cat="Math", syntax="PRODUCT(number1, ...)")
def f_product(*args):
    p = 1.0
    seen = False
    for v in flatten(args):
        p *= v
        seen = True
    return _check(p) if seen else 0.0


@fn("SUMSQ", cat="Math")
def f_sumsq(*args):
    return math.fsum(v * v for v in flatten(args))


@fn("SUMIF", 2, 3, cat="Math", syntax="SUMIF(range, criteria, [sum_range])", help="Adds cells that meet a condition.")
def f_sumif(rng, crit, sum_range=MISSING):
    pred = make_criteria(crit)
    vals = list(iter_all(rng))
    sums = vals if sum_range is MISSING else _sum_range_values(sum_range, len(vals))
    total = 0.0
    for v, s in zip(vals, sums):
        if pred(v) and isinstance(s, (int, float)) and not isinstance(s, bool):
            total += s
        elif pred(v) and isinstance(s, XLError):
            raise s
    return total


@fn("SUMIFS", 3, None, cat="Math", syntax="SUMIFS(sum_range, criteria_range1, criteria1, ...)")
def f_sumifs(sum_range, *pairs):
    mask = _pairs_filter(pairs)
    vals = list(iter_all(sum_range))
    if len(vals) != len(mask):
        raise VALUE
    total = 0.0
    for v, ok in zip(vals, mask):
        if ok:
            if isinstance(v, XLError):
                raise v
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += v
    return total


@fn("SUMPRODUCT", 1, None, cat="Math", syntax="SUMPRODUCT(array1, [array2], ...)")
def f_sumproduct(*arrays):
    mats = [as_rows(deref(a)) if isinstance(a, RangeRef) else as_rows(a) for a in arrays]
    shape = (len(mats[0]), len(mats[0][0]) if mats[0] else 0)
    for m in mats:
        if (len(m), len(m[0]) if m else 0) != shape:
            raise VALUE
    total = 0.0
    for i in range(shape[0]):
        for j in range(shape[1]):
            p = 1.0
            for m in mats:
                v = m[i][j]
                if isinstance(v, XLError):
                    raise v
                p *= float(v) if isinstance(v, (int, float)) else 0.0
            total += p
    return total


@fn("ABS", 1, 1, cat="Math")
def f_abs(x):
    return abs(_num(x))


@fn("SIGN", 1, 1, cat="Math")
def f_sign(x):
    v = _num(x)
    return float((v > 0) - (v < 0))


@fn("INT", 1, 1, cat="Math")
def f_int(x):
    return float(math.floor(_num(x)))


@fn("TRUNC", 1, 2, cat="Math")
def f_trunc(x, digits=MISSING):
    d = int(_num(_opt(digits, 0)))
    f = 10.0 ** d
    return math.trunc(_num(x) * f) / f


def _round_half_up(v: float, d: int) -> float:
    from app.sheet.numfmt import xl_round

    return xl_round(v, d)


@fn("ROUND", 2, 2, cat="Math", syntax="ROUND(number, num_digits)")
def f_round(x, d):
    return _round_half_up(_num(x), int(_num(d)))


@fn("ROUNDUP", 2, 2, cat="Math")
def f_roundup(x, d):
    v, n = _num(x), int(_num(d))
    f = 10.0 ** n
    r = math.ceil(abs(v) * f - 1e-9) / f
    return math.copysign(r, v) if v else 0.0


@fn("ROUNDDOWN", 2, 2, cat="Math")
def f_rounddown(x, d):
    v, n = _num(x), int(_num(d))
    f = 10.0 ** n
    r = math.floor(abs(v) * f + 1e-9) / f
    return math.copysign(r, v) if v else 0.0


@fn("MROUND", 2, 2, cat="Math")
def f_mround(x, m):
    v, mult = _num(x), _num(m)
    if mult == 0:
        return 0.0
    if (v > 0) != (mult > 0) and v != 0:
        raise NUM
    return _round_half_up(v / mult, 0) * mult


@fn(("CEILING", "CEILING.PRECISE", "ISO.CEILING"), 1, 2, cat="Math")
def f_ceiling(x, sig=MISSING):
    v, s = _num(x), _num(_opt(sig, 1))
    if s == 0:
        return 0.0
    return math.ceil(v / abs(s) - 1e-12) * abs(s)


@fn("CEILING.MATH", 1, 3, cat="Math")
def f_ceiling_math(x, sig=MISSING, mode=MISSING):
    v, s = _num(x), abs(_num(_opt(sig, 1))) or 1
    if v < 0 and _num(_opt(mode, 0)):
        return -math.ceil(-v / s - 1e-12) * s
    return math.ceil(v / s - 1e-12) * s


@fn(("FLOOR", "FLOOR.PRECISE"), 1, 2, cat="Math")
def f_floor(x, sig=MISSING):
    v, s = _num(x), _num(_opt(sig, 1))
    if s == 0:
        raise DIV0
    return math.floor(v / abs(s) + 1e-12) * abs(s)


@fn("FLOOR.MATH", 1, 3, cat="Math")
def f_floor_math(x, sig=MISSING, mode=MISSING):
    v, s = _num(x), abs(_num(_opt(sig, 1))) or 1
    if v < 0 and _num(_opt(mode, 0)):
        return -math.floor(-v / s + 1e-12) * s
    return math.floor(v / s + 1e-12) * s


@fn("EVEN", 1, 1, cat="Math")
def f_even(x):
    v = _num(x)
    r = math.ceil(abs(v) / 2) * 2
    return math.copysign(r, v) if v else 0.0


@fn("ODD", 1, 1, cat="Math")
def f_odd(x):
    v = _num(x)
    r = math.ceil(abs(v))
    if r % 2 == 0:
        r += 1
    return math.copysign(r, v) if v else 1.0


@fn("MOD", 2, 2, cat="Math", syntax="MOD(number, divisor)")
def f_mod(x, d):
    a, b = _num(x), _num(d)
    if b == 0:
        raise DIV0
    return a - b * math.floor(a / b)


@fn("QUOTIENT", 2, 2, cat="Math")
def f_quotient(x, d):
    a, b = _num(x), _num(d)
    if b == 0:
        raise DIV0
    return float(math.trunc(a / b))


@fn("POWER", 2, 2, cat="Math")
def f_power(x, y):
    return _pow(_num(x), _num(y))


def _pow(a, b):
    if a == 0 and b == 0:
        raise NUM
    if a == 0 and b < 0:
        raise DIV0
    try:
        r = a ** b
    except (OverflowError, ZeroDivisionError):
        raise NUM
    if isinstance(r, complex):
        raise NUM
    return _check(float(r))


@fn("SQRT", 1, 1, cat="Math")
def f_sqrt(x):
    v = _num(x)
    if v < 0:
        raise NUM
    return math.sqrt(v)


@fn("SQRTPI", 1, 1, cat="Math")
def f_sqrtpi(x):
    v = _num(x)
    if v < 0:
        raise NUM
    return math.sqrt(v * math.pi)


@fn("EXP", 1, 1, cat="Math")
def f_exp(x):
    try:
        return math.exp(_num(x))
    except OverflowError:
        raise NUM


@fn("LN", 1, 1, cat="Math")
def f_ln(x):
    v = _num(x)
    if v <= 0:
        raise NUM
    return math.log(v)


@fn("LOG", 1, 2, cat="Math")
def f_log(x, base=MISSING):
    v, b = _num(x), _num(_opt(base, 10))
    if v <= 0 or b <= 0:
        raise NUM
    if b == 1:
        raise DIV0
    return math.log(v) / math.log(b)


@fn("LOG10", 1, 1, cat="Math")
def f_log10(x):
    v = _num(x)
    if v <= 0:
        raise NUM
    return math.log10(v)


@fn("PI", 0, 0, cat="Math")
def f_pi():
    return math.pi


@fn("FACT", 1, 1, cat="Math")
def f_fact(x):
    v = int(_num(x))
    if v < 0:
        raise NUM
    if v > 170:
        raise NUM
    return float(math.factorial(v))


@fn("FACTDOUBLE", 1, 1, cat="Math")
def f_factdouble(x):
    v = int(_num(x))
    if v < -1:
        raise NUM
    r = 1
    while v > 1:
        r *= v
        v -= 2
    return float(r)


@fn("COMBIN", 2, 2, cat="Math")
def f_combin(n, k):
    a, b = int(_num(n)), int(_num(k))
    if a < 0 or b < 0 or b > a:
        raise NUM
    return float(math.comb(a, b))


@fn("PERMUT", 2, 2, cat="Math")
def f_permut(n, k):
    a, b = int(_num(n)), int(_num(k))
    if a < 0 or b < 0 or b > a:
        raise NUM
    return float(math.perm(a, b))


@fn("GCD", 1, None, cat="Math")
def f_gcd(*args):
    vals = [int(v) for v in flatten(args)]
    if any(v < 0 for v in vals):
        raise NUM
    r = 0
    for v in vals:
        r = math.gcd(r, v)
    return float(r)


@fn("LCM", 1, None, cat="Math")
def f_lcm(*args):
    vals = [int(v) for v in flatten(args)]
    if any(v < 0 for v in vals):
        raise NUM
    r = 1
    for v in vals:
        if v == 0:
            return 0.0
        r = r * v // math.gcd(r, v)
    return float(r)


@fn("RAND", 0, 0, volatile=True, cat="Math")
def f_rand():
    return random.random()


@fn("RANDBETWEEN", 2, 2, volatile=True, cat="Math")
def f_randbetween(a, b):
    lo, hi = math.ceil(_num(a)), math.floor(_num(b))
    if lo > hi:
        raise NUM
    return float(random.randint(lo, hi))


@fn("DEGREES", 1, 1, cat="Math")
def f_degrees(x):
    return math.degrees(_num(x))


@fn("RADIANS", 1, 1, cat="Math")
def f_radians(x):
    return math.radians(_num(x))


def _trig(name, f, domain=None):
    def impl(x):
        v = _num(x)
        if domain and not domain(v):
            raise NUM
        try:
            return _check(f(v))
        except (ValueError, OverflowError):
            raise NUM
    FUNCTIONS[name] = FuncSpec(impl, 1, 1, category="Math", syntax=f"{name}(number)")


for _n, _f, _d in (("SIN", math.sin, None), ("COS", math.cos, None), ("TAN", math.tan, None),
                   ("ASIN", math.asin, lambda v: -1 <= v <= 1), ("ACOS", math.acos, lambda v: -1 <= v <= 1),
                   ("ATAN", math.atan, None), ("SINH", math.sinh, None), ("COSH", math.cosh, None),
                   ("TANH", math.tanh, None), ("ASINH", math.asinh, None), ("ACOSH", math.acosh, lambda v: v >= 1),
                   ("ATANH", math.atanh, lambda v: -1 < v < 1)):
    _trig(_n, _f, _d)


@fn("ATAN2", 2, 2, cat="Math", syntax="ATAN2(x_num, y_num)")
def f_atan2(x, y):
    a, b = _num(x), _num(y)
    if a == 0 and b == 0:
        raise DIV0
    return math.atan2(b, a)


_SUBTOTAL = {1: "AVERAGE", 2: "COUNT", 3: "COUNTA", 4: "MAX", 5: "MIN", 6: "PRODUCT", 7: "STDEV.S", 8: "STDEV.P",
             9: "SUM", 10: "VAR.S", 11: "VAR.P"}


@fn("SUBTOTAL", 2, None, cat="Math", syntax="SUBTOTAL(function_num, ref1, ...)")
def f_subtotal(num, *refs):
    n = int(_num(num))
    name = _SUBTOTAL.get(n if n < 100 else n - 100)
    if name is None:
        raise VALUE
    return FUNCTIONS[name].impl(*refs)


@fn("AGGREGATE", 3, None, cat="Math", syntax="AGGREGATE(function_num, options, ref1, ...)")
def f_aggregate(num, options, *refs):
    n = int(_num(num))
    names = dict(_SUBTOTAL, **{12: "MEDIAN", 13: "MODE.SNGL", 14: "LARGE", 15: "SMALL", 16: "PERCENTILE.INC",
                               17: "QUARTILE.INC"})
    name = names.get(n)
    if name is None:
        raise VALUE
    opt = int(_num(options))
    if opt in (2, 3, 6, 7) and n <= 13:  # ignore error values
        vals = [v for v in iter_all(refs[0]) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return FUNCTIONS[name].impl(Array([vals]) if vals else Array([[0.0]]), *refs[1:])
    return FUNCTIONS[name].impl(*refs)


# ============================================================================= statistics
def _nums(args):
    return list(flatten(args))


@fn("AVERAGE", 1, None, cat="Statistical", syntax="AVERAGE(number1, ...)", help="Arithmetic mean.")
def f_average(*args):
    tot, cnt, _mn, _mx = _agg(args)
    if not cnt:
        raise DIV0
    return tot / cnt


@fn("AVERAGEA", 1, None, cat="Statistical")
def f_averagea(*args):
    vals = list(flatten(args, skip_text_in_refs=False, skip_bool_in_refs=False))
    if not vals:
        raise DIV0
    return math.fsum(vals) / len(vals)


@fn("AVERAGEIF", 2, 3, cat="Statistical")
def f_averageif(rng, crit, avg_range=MISSING):
    pred = make_criteria(crit)
    vals = list(iter_all(rng))
    src = vals if avg_range is MISSING else _sum_range_values(avg_range, len(vals))
    sel = [s for v, s in zip(vals, src) if pred(v) and isinstance(s, (int, float)) and not isinstance(s, bool)]
    if not sel:
        raise DIV0
    return math.fsum(sel) / len(sel)


@fn("AVERAGEIFS", 3, None, cat="Statistical")
def f_averageifs(avg_range, *pairs):
    mask = _pairs_filter(pairs)
    vals = list(iter_all(avg_range))
    if len(vals) != len(mask):
        raise VALUE
    sel = [v for v, ok in zip(vals, mask) if ok and isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not sel:
        raise DIV0
    return math.fsum(sel) / len(sel)


@fn("COUNT", 0, None, cat="Statistical", syntax="COUNT(value1, ...)")
def f_count(*args):
    n = 0
    for a in args:
        if isinstance(a, RangeRef):
            st = _range_stats(a)
            if st is not None:
                n += st[1]
                continue
        if isinstance(a, (RangeRef, Array)):
            for v in (a.iter_values() if isinstance(a, RangeRef) else a.flat()):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    n += 1
        elif a is MISSING or a is None:
            continue
        elif isinstance(a, bool) or isinstance(a, (int, float)):
            n += 1
        elif isinstance(a, str):
            try:
                to_number(a)
                n += 1
            except XLError:
                pass
    return float(n)


@fn("COUNTA", 0, None, cat="Statistical")
def f_counta(*args):
    n = 0
    for a in args:
        if isinstance(a, RangeRef):
            n += sum(1 for v in a.iter_values() if v is not None)
        elif isinstance(a, Array):
            n += sum(1 for v in a.flat() if v is not None)
        elif a is not MISSING:
            n += 1
    return float(n)


@fn("COUNTBLANK", 1, 1, cat="Statistical")
def f_countblank(rng):
    return float(sum(1 for v in iter_all(rng) if v is None or v == ""))


@fn("COUNTIF", 2, 2, cat="Statistical", syntax="COUNTIF(range, criteria)")
def f_countif(rng, crit):
    pred = make_criteria(crit)
    return float(sum(1 for v in iter_all(rng) if pred(v)))


@fn("COUNTIFS", 2, None, cat="Statistical")
def f_countifs(*pairs):
    return float(sum(1 for ok in _pairs_filter(pairs) if ok))


@fn("MAX", 1, None, cat="Statistical")
def f_max(*args):
    _t, cnt, _mn, mx = _agg(args)
    return mx if cnt else 0.0


@fn("MIN", 1, None, cat="Statistical")
def f_min(*args):
    _t, cnt, mn, _mx = _agg(args)
    return mn if cnt else 0.0


@fn("MAXA", 1, None, cat="Statistical")
def f_maxa(*args):
    vals = list(flatten(args, False, False))
    return max(vals) if vals else 0.0


@fn("MINA", 1, None, cat="Statistical")
def f_mina(*args):
    vals = list(flatten(args, False, False))
    return min(vals) if vals else 0.0


def _ifs_values(target, pairs):
    mask = _pairs_filter(pairs)
    vals = list(iter_all(target))
    if len(vals) != len(mask):
        raise VALUE
    return [v for v, ok in zip(vals, mask) if ok and isinstance(v, (int, float)) and not isinstance(v, bool)]


@fn("MAXIFS", 3, None, cat="Statistical")
def f_maxifs(target, *pairs):
    vals = _ifs_values(target, pairs)
    return max(vals) if vals else 0.0


@fn("MINIFS", 3, None, cat="Statistical")
def f_minifs(target, *pairs):
    vals = _ifs_values(target, pairs)
    return min(vals) if vals else 0.0


@fn("MEDIAN", 1, None, cat="Statistical")
def f_median(*args):
    vals = _nums(args)
    if not vals:
        raise NUM
    return float(statistics.median(vals))


@fn(("MODE", "MODE.SNGL"), 1, None, cat="Statistical")
def f_mode(*args):
    vals = _nums(args)
    counts: dict[float, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    if not counts or max(counts.values()) < 2:
        raise NA
    best = max(counts.values())
    for v in vals:
        if counts[v] == best:
            return v


def _var(vals, sample: bool):
    n = len(vals)
    if n < (2 if sample else 1):
        raise DIV0
    m = math.fsum(vals) / n
    return math.fsum((v - m) ** 2 for v in vals) / (n - 1 if sample else n)


@fn(("STDEV", "STDEV.S"), 1, None, cat="Statistical")
def f_stdev(*args):
    return math.sqrt(_var(_nums(args), True))


@fn(("STDEVP", "STDEV.P"), 1, None, cat="Statistical")
def f_stdevp(*args):
    return math.sqrt(_var(_nums(args), False))


@fn(("VAR", "VAR.S"), 1, None, cat="Statistical")
def f_var(*args):
    return _var(_nums(args), True)


@fn(("VARP", "VAR.P"), 1, None, cat="Statistical")
def f_varp(*args):
    return _var(_nums(args), False)


@fn("STDEVA", 1, None, cat="Statistical")
def f_stdeva(*args):
    return math.sqrt(_var(list(flatten(args, False, False)), True))


@fn("LARGE", 2, 2, cat="Statistical")
def f_large(arr, k):
    vals = sorted(_nums([arr]), reverse=True)
    i = math.ceil(_num(k))
    if i < 1 or i > len(vals):
        raise NUM
    return vals[i - 1]


@fn("SMALL", 2, 2, cat="Statistical")
def f_small(arr, k):
    vals = sorted(_nums([arr]))
    i = math.ceil(_num(k))
    if i < 1 or i > len(vals):
        raise NUM
    return vals[i - 1]


@fn(("RANK", "RANK.EQ"), 2, 3, cat="Statistical", syntax="RANK(number, ref, [order])")
def f_rank(number, ref, order=MISSING):
    x = _num(number)
    vals = _nums([ref])
    if x not in vals:
        raise NA
    if _num(_opt(order, 0)):
        return float(sum(1 for v in vals if v < x) + 1)
    return float(sum(1 for v in vals if v > x) + 1)


@fn("RANK.AVG", 2, 3, cat="Statistical")
def f_rank_avg(number, ref, order=MISSING):
    x = _num(number)
    vals = _nums([ref])
    if x not in vals:
        raise NA
    asc = bool(_num(_opt(order, 0)))
    better = sum(1 for v in vals if (v < x if asc else v > x))
    ties = vals.count(x)
    return better + (ties + 1) / 2


def _percentile(vals, p, exclusive=False):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        raise NUM
    if exclusive:
        if p <= 0 or p >= 1:
            raise NUM
        pos = p * (n + 1) - 1
        if pos < 0 or pos > n - 1:
            raise NUM
    else:
        if p < 0 or p > 1:
            raise NUM
        pos = p * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


@fn(("PERCENTILE", "PERCENTILE.INC"), 2, 2, cat="Statistical")
def f_percentile(arr, k):
    return _percentile(_nums([arr]), _num(k))


@fn("PERCENTILE.EXC", 2, 2, cat="Statistical")
def f_percentile_exc(arr, k):
    return _percentile(_nums([arr]), _num(k), True)


@fn(("QUARTILE", "QUARTILE.INC"), 2, 2, cat="Statistical")
def f_quartile(arr, q):
    qi = int(_num(q))
    if qi < 0 or qi > 4:
        raise NUM
    return _percentile(_nums([arr]), qi / 4)


@fn("QUARTILE.EXC", 2, 2, cat="Statistical")
def f_quartile_exc(arr, q):
    qi = int(_num(q))
    if qi < 1 or qi > 3:
        raise NUM
    return _percentile(_nums([arr]), qi / 4, True)


@fn(("PERCENTRANK", "PERCENTRANK.INC"), 2, 3, cat="Statistical")
def f_percentrank(arr, x, sig=MISSING):
    vals = sorted(_nums([arr]))
    v = _num(x)
    if not vals or v < vals[0] or v > vals[-1]:
        raise NA
    below = sum(1 for a in vals if a < v)
    if v in vals:
        r = below / (len(vals) - 1) if len(vals) > 1 else 1.0
    else:
        hi = next(a for a in vals if a > v)
        lo = max(a for a in vals if a < v)
        r0 = (below - 1) / (len(vals) - 1)
        r = r0 + (v - lo) / (hi - lo) / (len(vals) - 1)
    d = int(_num(_opt(sig, 3)))
    return math.floor(r * 10 ** d) / 10 ** d


def _paired(a, b):
    xa, xb = list(iter_all(a)), list(iter_all(b))
    if len(xa) != len(xb):
        raise NA
    pairs = [(float(x), float(y)) for x, y in zip(xa, xb)
             if isinstance(x, (int, float)) and not isinstance(x, bool) and isinstance(y, (int, float)) and not isinstance(y, bool)]
    return pairs


@fn(("CORREL", "PEARSON"), 2, 2, cat="Statistical")
def f_correl(a, b):
    p = _paired(a, b)
    if len(p) < 2:
        raise DIV0
    xs, ys = zip(*p)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in p)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        raise DIV0
    return sxy / math.sqrt(sxx * syy)


@fn("RSQ", 2, 2, cat="Statistical")
def f_rsq(ys, xs):
    return f_correl(ys, xs) ** 2


@fn(("COVAR", "COVARIANCE.P"), 2, 2, cat="Statistical")
def f_covar(a, b):
    p = _paired(a, b)
    if not p:
        raise DIV0
    mx = sum(x for x, _ in p) / len(p)
    my = sum(y for _, y in p) / len(p)
    return sum((x - mx) * (y - my) for x, y in p) / len(p)


@fn("COVARIANCE.S", 2, 2, cat="Statistical")
def f_covars(a, b):
    p = _paired(a, b)
    if len(p) < 2:
        raise DIV0
    mx = sum(x for x, _ in p) / len(p)
    my = sum(y for _, y in p) / len(p)
    return sum((x - mx) * (y - my) for x, y in p) / (len(p) - 1)


def _linfit(known_y, known_x):
    p = _paired(known_x, known_y)
    if len(p) < 2:
        raise DIV0
    xs = [x for x, _ in p]
    ys = [y for _, y in p]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise DIV0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx, xs, ys


@fn("SLOPE", 2, 2, cat="Statistical", syntax="SLOPE(known_y's, known_x's)")
def f_slope(ys, xs):
    return _linfit(ys, xs)[0]


@fn("INTERCEPT", 2, 2, cat="Statistical")
def f_intercept(ys, xs):
    return _linfit(ys, xs)[1]


@fn(("FORECAST", "FORECAST.LINEAR"), 3, 3, cat="Statistical", syntax="FORECAST(x, known_y's, known_x's)")
def f_forecast(x, ys, xs):
    slope, icpt, _x, _y = _linfit(ys, xs)
    return icpt + slope * _num(x)


@fn("STEYX", 2, 2, cat="Statistical")
def f_steyx(ys, xs):
    slope, icpt, xv, yv = _linfit(ys, xs)
    n = len(xv)
    if n < 3:
        raise DIV0
    return math.sqrt(sum((y - (icpt + slope * x)) ** 2 for x, y in zip(xv, yv)) / (n - 2))


@fn("GEOMEAN", 1, None, cat="Statistical")
def f_geomean(*args):
    vals = _nums(args)
    if not vals or any(v <= 0 for v in vals):
        raise NUM
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


@fn("HARMEAN", 1, None, cat="Statistical")
def f_harmean(*args):
    vals = _nums(args)
    if not vals or any(v <= 0 for v in vals):
        raise NUM
    return len(vals) / sum(1 / v for v in vals)


@fn("AVEDEV", 1, None, cat="Statistical")
def f_avedev(*args):
    vals = _nums(args)
    if not vals:
        raise NUM
    m = sum(vals) / len(vals)
    return sum(abs(v - m) for v in vals) / len(vals)


@fn("DEVSQ", 1, None, cat="Statistical")
def f_devsq(*args):
    vals = _nums(args)
    if not vals:
        raise NUM
    m = sum(vals) / len(vals)
    return sum((v - m) ** 2 for v in vals)


@fn("SKEW", 1, None, cat="Statistical")
def f_skew(*args):
    vals = _nums(args)
    n = len(vals)
    if n < 3:
        raise DIV0
    m = sum(vals) / n
    s = math.sqrt(_var(vals, True))
    if s == 0:
        raise DIV0
    return n / ((n - 1) * (n - 2)) * sum(((v - m) / s) ** 3 for v in vals)


@fn("KURT", 1, None, cat="Statistical")
def f_kurt(*args):
    vals = _nums(args)
    n = len(vals)
    if n < 4:
        raise DIV0
    m = sum(vals) / n
    s = math.sqrt(_var(vals, True))
    if s == 0:
        raise DIV0
    a = n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * sum(((v - m) / s) ** 4 for v in vals)
    return a - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))


@fn("TRIMMEAN", 2, 2, cat="Statistical")
def f_trimmean(arr, pct):
    vals = sorted(_nums([arr]))
    p = _num(pct)
    if p < 0 or p >= 1 or not vals:
        raise NUM
    k = int(math.floor(len(vals) * p / 2))
    sel = vals[k:len(vals) - k]
    return sum(sel) / len(sel)


@fn("STANDARDIZE", 3, 3, cat="Statistical")
def f_standardize(x, mean, sd):
    s = _num(sd)
    if s <= 0:
        raise NUM
    return (_num(x) - _num(mean)) / s


@fn("NORM.DIST", 4, 4, cat="Statistical")
def f_normdist(x, mean, sd, cumulative):
    s = _num(sd)
    if s <= 0:
        raise NUM
    nd = statistics.NormalDist(_num(mean), s)
    return nd.cdf(_num(x)) if to_bool(cumulative) else nd.pdf(_num(x))


FUNCTIONS["NORMDIST"] = FUNCTIONS["NORM.DIST"]


@fn(("NORM.S.DIST", "NORMSDIST"), 1, 2, cat="Statistical")
def f_normsdist(z, cumulative=MISSING):
    nd = statistics.NormalDist()
    return nd.cdf(_num(z)) if to_bool(_opt(cumulative, True)) else nd.pdf(_num(z))


@fn(("NORM.INV", "NORMINV"), 3, 3, cat="Statistical")
def f_norminv(p, mean, sd):
    pv, s = _num(p), _num(sd)
    if not 0 < pv < 1 or s <= 0:
        raise NUM
    return statistics.NormalDist(_num(mean), s).inv_cdf(pv)


@fn(("NORM.S.INV", "NORMSINV"), 1, 1, cat="Statistical")
def f_normsinv(p):
    pv = _num(p)
    if not 0 < pv < 1:
        raise NUM
    return statistics.NormalDist().inv_cdf(pv)


@fn("FREQUENCY", 2, 2, cat="Statistical", syntax="FREQUENCY(data_array, bins_array)")
def f_frequency(data, bins):
    vals = _nums([data])
    bs = sorted(_nums([bins]))
    counts = [0] * (len(bs) + 1)
    for v in vals:
        for i, b in enumerate(bs):
            if v <= b:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    return Array([[float(c)] for c in counts])


# ============================================================================= logical
@fn("IF", 1, 3, lazy=True, cat="Logical", syntax="IF(logical_test, [value_if_true], [value_if_false])",
    help="Returns one value if a condition is true and another if it is false.")
def f_if(ctx, cond, a=None, b=None):
    c = to_bool(cond(ctx))
    if c:
        if a is None:
            return True
        v = a(ctx)
        return 0.0 if v is MISSING else v
    if b is None:
        return False
    v = b(ctx)
    return 0.0 if v is MISSING else v


@fn("IFS", 2, None, lazy=True, cat="Logical", syntax="IFS(test1, value1, [test2, value2], ...)")
def f_ifs(ctx, *args):
    if len(args) % 2:
        raise VALUE
    for i in range(0, len(args), 2):
        if to_bool(args[i](ctx)):
            return args[i + 1](ctx)
    raise NA


@fn("IFERROR", 2, 2, lazy=True, cat="Logical", syntax="IFERROR(value, value_if_error)")
def f_iferror(ctx, a, b):
    try:
        v = a(ctx)
        sv = scalar(v) if isinstance(v, RangeRef) and v.is_cell else v
        if isinstance(sv, XLError):
            return b(ctx)
        return v
    except XLError:
        return b(ctx)


@fn("IFNA", 2, 2, lazy=True, cat="Logical")
def f_ifna(ctx, a, b):
    try:
        v = a(ctx)
        sv = scalar(v) if isinstance(v, RangeRef) and v.is_cell else v
        if sv == NA:
            return b(ctx)
        return v
    except XLError as e:
        if e == NA:
            return b(ctx)
        raise


def _bools(args):
    for a in args:
        if isinstance(a, (RangeRef, Array)):
            for v in (a.iter_values() if isinstance(a, RangeRef) else a.flat()):
                if isinstance(v, XLError):
                    raise v
                if isinstance(v, bool):
                    yield v
                elif isinstance(v, (int, float)):
                    yield v != 0
        elif a is MISSING:
            continue
        else:
            yield to_bool(a)


@fn("AND", 1, None, cat="Logical")
def f_and(*args):
    vals = list(_bools(args))
    if not vals:
        raise VALUE
    return all(vals)


@fn("OR", 1, None, cat="Logical")
def f_or(*args):
    vals = list(_bools(args))
    if not vals:
        raise VALUE
    return any(vals)


@fn("XOR", 1, None, cat="Logical")
def f_xor(*args):
    vals = list(_bools(args))
    if not vals:
        raise VALUE
    return sum(vals) % 2 == 1


@fn("NOT", 1, 1, cat="Logical")
def f_not(x):
    return not to_bool(x)


@fn("TRUE", 0, 0, cat="Logical")
def f_true():
    return True


@fn("FALSE", 0, 0, cat="Logical")
def f_false():
    return False


@fn("SWITCH", 3, None, lazy=True, cat="Logical", syntax="SWITCH(expression, value1, result1, [default])")
def f_switch(ctx, expr, *rest):
    v = scalar(expr(ctx))
    pairs = len(rest) // 2
    for i in range(pairs):
        if compare(v, scalar(rest[2 * i](ctx))) == 0:
            return rest[2 * i + 1](ctx)
    if len(rest) % 2:
        return rest[-1](ctx)
    raise NA


@fn("CHOOSE", 2, None, lazy=True, cat="Lookup", syntax="CHOOSE(index_num, value1, [value2], ...)")
def f_choose(ctx, idx, *vals):
    i = int(_num(idx(ctx)))
    if i < 1 or i > len(vals):
        raise VALUE
    return vals[i - 1](ctx)


# ============================================================================= lookup & reference
def _match_value(a, b) -> bool:
    try:
        return compare(a, b) == 0
    except XLError:
        return False


def _exact_pred(lookup):
    if isinstance(lookup, str) and any(ch in lookup for ch in "*?~"):
        rx = wildcard_regex(lookup)
        return lambda v: isinstance(v, str) and bool(rx.match(v))
    return lambda v: v is not None and type(v) is not bool and type(lookup) is not bool and _match_value(v, lookup) \
        or (isinstance(v, bool) and isinstance(lookup, bool) and v == lookup)


def _approx_index(vals, lookup) -> int:
    """Binary search (Excel semantics): last position whose value <= lookup (same type), or -1."""
    lo, hi, found = 0, len(vals) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        v = vals[mid]
        # skip blanks / different types like Excel by scanning to a comparable neighbour
        j = mid
        while j <= hi and (v is None or (type_rank_safe(v) != type_rank_safe(lookup))):
            j += 1
            v = vals[j] if j <= hi else None
        if j > hi:
            hi = mid - 1
            continue
        c = compare(v, lookup)
        if c <= 0:
            found = j
            lo = j + 1
        else:
            hi = mid - 1
    return found


def type_rank_safe(v):
    from app.sheet.formula.values import type_rank

    return type_rank(v)


@fn("VLOOKUP", 3, 4, cat="Lookup", syntax="VLOOKUP(lookup_value, table_array, col_index_num, [range_lookup])",
    help="Looks for a value in the first column of a table and returns a value in the same row.")
def f_vlookup(lookup, table, col, approx=MISSING):
    lv = scalar(lookup)
    if isinstance(lv, XLError):
        raise lv
    rows = as_rows(table)
    ci = int(_num(col))
    if ci < 1:
        raise VALUE
    if rows and ci > len(rows[0]):
        raise REF
    first = [r[0] for r in rows]
    if to_bool(_opt(approx, True)):
        i = _approx_index(first, lv)
        if i < 0:
            raise NA
    else:
        pred = _exact_pred(lv)
        i = next((k for k, v in enumerate(first) if pred(v)), -1)
        if i < 0:
            raise NA
    v = rows[i][ci - 1]
    return 0.0 if v is None else v


@fn("HLOOKUP", 3, 4, cat="Lookup", syntax="HLOOKUP(lookup_value, table_array, row_index_num, [range_lookup])")
def f_hlookup(lookup, table, row, approx=MISSING):
    lv = scalar(lookup)
    rows = as_rows(table)
    ri = int(_num(row))
    if ri < 1:
        raise VALUE
    if ri > len(rows):
        raise REF
    first = rows[0]
    if to_bool(_opt(approx, True)):
        i = _approx_index(first, lv)
        if i < 0:
            raise NA
    else:
        pred = _exact_pred(lv)
        i = next((k for k, v in enumerate(first) if pred(v)), -1)
        if i < 0:
            raise NA
    v = rows[ri - 1][i]
    return 0.0 if v is None else v


@fn("LOOKUP", 2, 3, cat="Lookup")
def f_lookup(lookup, vector, result=MISSING):
    lv = scalar(lookup)
    rows = as_rows(vector)
    if result is MISSING:
        if len(rows) >= len(rows[0]):
            keys = [r[0] for r in rows]
            vals = [r[-1] for r in rows]
        else:
            keys, vals = rows[0], rows[-1]
    else:
        keys = list(iter_all(vector))
        vals = list(iter_all(result))
    i = _approx_index(keys, lv)
    if i < 0 or i >= len(vals):
        raise NA
    return vals[i]


@fn("MATCH", 2, 3, cat="Lookup", syntax="MATCH(lookup_value, lookup_array, [match_type])")
def f_match(lookup, arr, mtype=MISSING):
    lv = scalar(lookup)
    vals = list(iter_all(arr))
    t = int(_num(_opt(mtype, 1)))
    if t == 0:
        pred = _exact_pred(lv)
        for k, v in enumerate(vals):
            if pred(v):
                return float(k + 1)
        raise NA
    if t == 1:
        i = _approx_index(vals, lv)
        if i < 0:
            raise NA
        return float(i + 1)
    best = -1
    for k, v in enumerate(vals):  # descending order: smallest value >= lookup
        if v is None:
            continue
        if compare(v, lv) >= 0:
            best = k
        else:
            break
    if best < 0:
        raise NA
    return float(best + 1)


@fn("XMATCH", 2, 4, cat="Lookup")
def f_xmatch(lookup, arr, mode=MISSING, search=MISSING):
    vals = list(iter_all(arr))
    i = _xfind(scalar(lookup), vals, int(_num(_opt(mode, 0))), int(_num(_opt(search, 1))))
    if i < 0:
        raise NA
    return float(i + 1)


def _xfind(lv, vals, mode, search):
    order = range(len(vals)) if search >= 0 else range(len(vals) - 1, -1, -1)
    if mode == 2:
        pred = _exact_pred(lv if isinstance(lv, str) else lv)
        return next((k for k in order if pred(vals[k])), -1)
    exact = next((k for k in order if vals[k] is not None and _match_value(vals[k], lv)), -1)
    if exact >= 0 or mode == 0:
        return exact
    best, best_v = -1, None
    for k in order:
        v = vals[k]
        if v is None or type_rank_safe(v) != type_rank_safe(lv):
            continue
        c = compare(v, lv)
        if mode == -1 and c < 0 and (best_v is None or compare(v, best_v) > 0):
            best, best_v = k, v
        if mode == 1 and c > 0 and (best_v is None or compare(v, best_v) < 0):
            best, best_v = k, v
    return best


@fn("XLOOKUP", 3, 6, cat="Lookup",
    syntax="XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found], [match_mode], [search_mode])")
def f_xlookup(lookup, lookup_arr, return_arr, not_found=MISSING, mode=MISSING, search=MISSING):
    keys = list(iter_all(lookup_arr))
    i = _xfind(scalar(lookup), keys, int(_num(_opt(mode, 0))), int(_num(_opt(search, 1))))
    if i < 0:
        if not_found is not MISSING:
            return not_found
        raise NA
    if isinstance(return_arr, RangeRef):
        if lookup_arr.nrows == 1 if isinstance(lookup_arr, RangeRef) else False:
            return RangeRef(return_arr.book, return_arr.sid, return_arr.r1, return_arr.c1 + i, return_arr.r2, return_arr.c1 + i)
        return RangeRef(return_arr.book, return_arr.sid, return_arr.r1 + i, return_arr.c1, return_arr.r1 + i, return_arr.c2)
    vals = list(iter_all(return_arr))
    return vals[i] if i < len(vals) else NA


@fn("INDEX", 2, 4, cat="Lookup", syntax="INDEX(array, row_num, [column_num])")
def f_index(arr, row, col=MISSING, area=MISSING):
    r = int(_num(_opt(row, 0)))
    c = int(_num(_opt(col, 0)))
    if isinstance(arr, RangeRef):
        if arr.nrows == 1 and col is MISSING:
            r, c = 1, r
        if r < 0 or c < 0 or r > arr.nrows or c > arr.ncols:
            raise REF
        r1, r2 = (arr.r1, arr.r2) if r == 0 else (arr.r1 + r - 1, arr.r1 + r - 1)
        c1, c2 = (arr.c1, arr.c2) if c == 0 else (arr.c1 + c - 1, arr.c1 + c - 1)
        return RangeRef(arr.book, arr.sid, r1, c1, r2, c2)
    rows = as_rows(arr)
    if len(rows) == 1 and col is MISSING:
        r, c = 1, r
    if r < 0 or c < 0 or r > len(rows) or (rows and c > len(rows[0])):
        raise REF
    if r == 0:
        return Array([[row_[c - 1]] for row_ in rows])
    if c == 0:
        return Array([rows[r - 1]])
    return rows[r - 1][c - 1]


@fn("OFFSET", 3, 5, ctx=True, volatile=True, cat="Lookup", syntax="OFFSET(reference, rows, cols, [height], [width])")
def f_offset(ctx, ref, rows, cols, h=MISSING, w=MISSING):
    if not isinstance(ref, RangeRef):
        raise VALUE
    hh = None if h is MISSING else int(_num(h))
    ww = None if w is MISSING else int(_num(w))
    return ref.offset(int(_num(rows)), int(_num(cols)), hh, ww)


@fn("INDIRECT", 1, 2, ctx=True, volatile=True, cat="Lookup", syntax="INDIRECT(ref_text, [a1])")
def f_indirect(ctx, text, a1=MISSING):
    from app.sheet.formula.parser import parse_ref_text
    from app.sheet.formula.tokenizer import valid_cell

    s = to_text(text).strip()
    if not to_bool(_opt(a1, True)):
        m = re.fullmatch(r"(?:(.+)!)?R(\d+)C(\d+)", s, re.I)
        if not m:
            raise REF
        from app.sheet.formula.parser import cell_name, quote_sheet

        s = (quote_sheet(m.group(1).strip("'")) + "!" if m.group(1) else "") + cell_name(int(m.group(2)), int(m.group(3)))
    try:
        target = s.split("!")[-1]
        if ":" not in target and not valid_cell(target.replace("$", "")):
            nm = ctx.book.names.get(s.upper())
            if nm:
                s = nm.lstrip("=")
        node = parse_ref_text(s)
    except Exception:
        raise REF
    sid = ctx.sid if node.sheet is None else ctx.book.sheet_id(node.sheet)
    return RangeRef(ctx.book, sid, node.r1, node.c1, node.r2, node.c2)


@fn("ROW", 0, 1, ctx=True, cat="Lookup")
def f_row(ctx, ref=MISSING):
    if ref is MISSING:
        return float(ctx.row)
    if isinstance(ref, RangeRef):
        return float(ref.r1)
    raise VALUE


@fn("COLUMN", 0, 1, ctx=True, cat="Lookup")
def f_column(ctx, ref=MISSING):
    if ref is MISSING:
        return float(ctx.col)
    if isinstance(ref, RangeRef):
        return float(ref.c1)
    raise VALUE


@fn("ROWS", 1, 1, cat="Lookup")
def f_rows(ref):
    if isinstance(ref, RangeRef):
        return float(ref.nrows)
    return float(len(as_rows(ref)))


@fn("COLUMNS", 1, 1, cat="Lookup")
def f_columns(ref):
    if isinstance(ref, RangeRef):
        return float(ref.ncols)
    rows = as_rows(ref)
    return float(len(rows[0]) if rows else 0)


@fn("ADDRESS", 2, 5, cat="Lookup", syntax="ADDRESS(row_num, column_num, [abs_num], [a1], [sheet_text])")
def f_address(row, col, absn=MISSING, a1=MISSING, sheet=MISSING):
    from app.sheet.formula.parser import quote_sheet
    from app.sheet.formula.tokenizer import index_to_col

    r, c = int(_num(row)), int(_num(col))
    if r < 1 or c < 1:
        raise VALUE
    a = int(_num(_opt(absn, 1)))
    if to_bool(_opt(a1, True)):
        cs = ("$" if a in (1, 3) else "") + index_to_col(c)
        rs = ("$" if a in (1, 2) else "") + str(r)
        out = cs + rs
    else:
        out = (f"R{r}" if a in (1, 2) else f"R[{r}]") + (f"C{c}" if a in (1, 3) else f"C[{c}]")
    if sheet is not MISSING:
        out = quote_sheet(to_text(sheet)) + "!" + out
    return out


@fn("TRANSPOSE", 1, 1, cat="Lookup")
def f_transpose(arr):
    rows = as_rows(arr)
    return Array([list(r) for r in zip(*rows)])


@fn("UNIQUE", 1, 3, cat="Lookup")
def f_unique(arr, by_col=MISSING, once=MISSING):
    rows = as_rows(arr)
    seen, out = {}, []
    for r in rows:
        key = tuple(str(v).lower() if isinstance(v, str) else v for v in r)
        seen[key] = seen.get(key, 0) + 1
    for r in rows:
        key = tuple(str(v).lower() if isinstance(v, str) else v for v in r)
        if to_bool(_opt(once, False)):
            if seen[key] == 1:
                out.append(r)
        elif key in seen:
            out.append(r)
            del seen[key]
    return Array(out or [[NA]])


@fn("SORT", 1, 4, cat="Lookup")
def f_sort(arr, idx=MISSING, order=MISSING, by_col=MISSING):
    rows = [list(r) for r in as_rows(arr)]
    k = int(_num(_opt(idx, 1))) - 1
    desc = _num(_opt(order, 1)) < 0
    from functools import cmp_to_key

    rows.sort(key=cmp_to_key(lambda a, b: compare(a[k], b[k])), reverse=desc)
    return Array(rows)


@fn("FILTER", 2, 3, cat="Lookup")
def f_filter(arr, include, if_empty=MISSING):
    rows = as_rows(arr)
    inc = [bool(v) if not isinstance(v, str) else False for v in iter_all(include)]
    out = [r for r, ok in zip(rows, inc) if ok]
    if not out:
        if if_empty is not MISSING:
            return if_empty
        from app.sheet.formula.values import CALC

        raise CALC
    return Array(out)


@fn("SEQUENCE", 1, 4, cat="Math")
def f_sequence(rows, cols=MISSING, start=MISSING, step=MISSING):
    r, c = int(_num(rows)), int(_num(_opt(cols, 1)))
    s, st = _num(_opt(start, 1)), _num(_opt(step, 1))
    return Array([[s + (i * c + j) * st for j in range(c)] for i in range(r)])


@fn("HYPERLINK", 1, 2, cat="Lookup")
def f_hyperlink(url, name=MISSING):
    return to_text(name) if name is not MISSING else to_text(url)


# ============================================================================= text
@fn("CONCATENATE", 1, None, cat="Text")
def f_concatenate(*args):
    return "".join(to_text(a) for a in args if a is not MISSING)


@fn("CONCAT", 1, None, cat="Text")
def f_concat(*args):
    out = []
    for a in args:
        for v in iter_all(a):
            if v is not None and v is not MISSING:
                if isinstance(v, XLError):
                    raise v
                out.append(to_text(v))
    return "".join(out)


@fn("TEXTJOIN", 3, None, cat="Text", syntax="TEXTJOIN(delimiter, ignore_empty, text1, ...)")
def f_textjoin(delim, ignore_empty, *args):
    d = to_text(delim)
    ign = to_bool(ignore_empty)
    parts = []
    for a in args:
        for v in iter_all(a):
            if isinstance(v, XLError):
                raise v
            t = "" if v is None or v is MISSING else to_text(v)
            if t == "" and ign:
                continue
            parts.append(t)
    return d.join(parts)


@fn("LEFT", 1, 2, cat="Text")
def f_left(text, n=MISSING):
    k = int(_num(_opt(n, 1)))
    if k < 0:
        raise VALUE
    return to_text(text)[:k]


@fn("RIGHT", 1, 2, cat="Text")
def f_right(text, n=MISSING):
    k = int(_num(_opt(n, 1)))
    if k < 0:
        raise VALUE
    t = to_text(text)
    return t[len(t) - k:] if k else ""


@fn("MID", 3, 3, cat="Text", syntax="MID(text, start_num, num_chars)")
def f_mid(text, start, n):
    s, k = int(_num(start)), int(_num(n))
    if s < 1 or k < 0:
        raise VALUE
    return to_text(text)[s - 1:s - 1 + k]


@fn("LEN", 1, 1, cat="Text")
def f_len(text):
    return float(len(to_text(text)))


@fn("LOWER", 1, 1, cat="Text")
def f_lower(text):
    return to_text(text).lower()


@fn("UPPER", 1, 1, cat="Text")
def f_upper(text):
    return to_text(text).upper()


@fn("PROPER", 1, 1, cat="Text")
def f_proper(text):
    return re.sub(r"[A-Za-z\u00C0-\u024F]+", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), to_text(text))


@fn("TRIM", 1, 1, cat="Text")
def f_trim(text):
    return re.sub(r" +", " ", to_text(text).strip(" "))


@fn("CLEAN", 1, 1, cat="Text")
def f_clean(text):
    return "".join(ch for ch in to_text(text) if ord(ch) >= 32)


@fn("SUBSTITUTE", 3, 4, cat="Text", syntax="SUBSTITUTE(text, old_text, new_text, [instance_num])")
def f_substitute(text, old, new, inst=MISSING):
    t, o, n = to_text(text), to_text(old), to_text(new)
    if not o:
        return t
    if inst is MISSING:
        return t.replace(o, n)
    k = int(_num(inst))
    if k < 1:
        raise VALUE
    idx = -1
    for _ in range(k):
        idx = t.find(o, idx + 1)
        if idx < 0:
            return t
    return t[:idx] + n + t[idx + len(o):]


@fn("REPLACE", 4, 4, cat="Text", syntax="REPLACE(old_text, start_num, num_chars, new_text)")
def f_replace(text, start, n, new):
    t = to_text(text)
    s, k = int(_num(start)), int(_num(n))
    if s < 1 or k < 0:
        raise VALUE
    return t[:s - 1] + to_text(new) + t[s - 1 + k:]


@fn("FIND", 2, 3, cat="Text", syntax="FIND(find_text, within_text, [start_num])")
def f_find(find, within, start=MISSING):
    s = int(_num(_opt(start, 1)))
    t = to_text(within)
    if s < 1 or s > len(t) + 1:
        raise VALUE
    i = t.find(to_text(find), s - 1)
    if i < 0:
        raise VALUE
    return float(i + 1)


@fn("SEARCH", 2, 3, cat="Text")
def f_search(find, within, start=MISSING):
    s = int(_num(_opt(start, 1)))
    t = to_text(within)
    if s < 1 or s > len(t) + 1:
        raise VALUE
    pat = to_text(find)
    rx = wildcard_regex(pat).pattern[1:-1]
    m = re.compile(rx, re.I | re.S).search(t, s - 1)
    if not m:
        raise VALUE
    return float(m.start() + 1)


@fn("REPT", 2, 2, cat="Text")
def f_rept(text, n):
    k = int(_num(n))
    if k < 0:
        raise VALUE
    out = to_text(text) * k
    if len(out) > 32767:
        raise VALUE
    return out


@fn("TEXT", 2, 2, cat="Text", syntax="TEXT(value, format_text)")
def f_text(value, fmt):
    from app.sheet.numfmt import format_value

    v = scalar(value)
    if isinstance(v, XLError):
        raise v
    if isinstance(v, str):
        n = None
        try:
            n = to_number(v)
        except XLError:
            pass
        v = n if n is not None else v
    return format_value(v, to_text(fmt))[0]


@fn("VALUE", 1, 1, cat="Text")
def f_value(text):
    v = scalar(text)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return to_number(to_text(v))


@fn("NUMBERVALUE", 1, 3, cat="Text")
def f_numbervalue(text, dec=MISSING, group=MISSING):
    t = to_text(text)
    d = to_text(_opt(dec, "."))
    g = to_text(_opt(group, ","))
    t = t.replace(g, "").replace(d, ".")
    return to_number(t)


@fn("EXACT", 2, 2, cat="Text")
def f_exact(a, b):
    return to_text(a) == to_text(b)


@fn("CHAR", 1, 1, cat="Text")
def f_char(n):
    k = int(_num(n))
    if k < 1 or k > 255:
        raise VALUE
    return bytes([k]).decode("cp1252", errors="replace")


@fn("CODE", 1, 1, cat="Text")
def f_code(text):
    t = to_text(text)
    if not t:
        raise VALUE
    try:
        return float(t[0].encode("cp1252")[0])
    except UnicodeEncodeError:
        return 63.0


@fn("UNICHAR", 1, 1, cat="Text")
def f_unichar(n):
    k = int(_num(n))
    if k < 1 or k > 0x10FFFF:
        raise VALUE
    return chr(k)


@fn("UNICODE", 1, 1, cat="Text")
def f_unicode(text):
    t = to_text(text)
    if not t:
        raise VALUE
    return float(ord(t[0]))


@fn("T", 1, 1, cat="Text")
def f_t(v):
    s = scalar(v)
    return s if isinstance(s, str) else ""


@fn("N", 1, 1, cat="Info")
def f_n(v):
    s = scalar(v)
    if isinstance(s, XLError):
        raise s
    if isinstance(s, bool):
        return 1.0 if s else 0.0
    if isinstance(s, (int, float)):
        return float(s)
    return 0.0


@fn("FIXED", 1, 3, cat="Text")
def f_fixed(x, dec=MISSING, no_commas=MISSING):
    d = int(_num(_opt(dec, 2)))
    v = _round_half_up(_num(x), d)
    s = f"{v:,.{max(d, 0)}f}" if not to_bool(_opt(no_commas, False)) else f"{v:.{max(d, 0)}f}"
    return s


@fn("DOLLAR", 1, 2, cat="Text")
def f_dollar(x, dec=MISSING):
    d = int(_num(_opt(dec, 2)))
    v = _round_half_up(_num(x), d)
    s = f"${abs(v):,.{max(d, 0)}f}"
    return f"({s})" if v < 0 else s


@fn("TEXTBEFORE", 2, 3, cat="Text")
def f_textbefore(text, delim, inst=MISSING):
    t, d = to_text(text), to_text(delim)
    k = int(_num(_opt(inst, 1)))
    parts = t.split(d)
    if len(parts) <= abs(k):
        raise NA
    return d.join(parts[:k]) if k > 0 else d.join(parts[:len(parts) + k])


@fn("TEXTAFTER", 2, 3, cat="Text")
def f_textafter(text, delim, inst=MISSING):
    t, d = to_text(text), to_text(delim)
    k = int(_num(_opt(inst, 1)))
    parts = t.split(d)
    if len(parts) <= abs(k):
        raise NA
    return d.join(parts[k:]) if k > 0 else d.join(parts[len(parts) + k:])


# ============================================================================= date & time
def _date_from(v) -> _dt.datetime:
    s = scalar(v)
    if isinstance(s, str):
        n = parse_date_text(s)
        if n is None:
            raise VALUE
        return serial_to_datetime(n)
    return serial_to_datetime(_num(s))


@fn("DATE", 3, 3, cat="Date", syntax="DATE(year, month, day)")
def f_date(y, m, d):
    yy, mm, dd = int(_num(y)), int(_num(m)), int(_num(d))
    if 0 <= yy < 1900:
        yy += 1900
    yy += (mm - 1) // 12
    mm = (mm - 1) % 12 + 1
    try:
        base = _dt.datetime(yy, mm, 1)
    except ValueError:
        raise NUM
    return datetime_to_serial(base) + dd - 1


@fn("TIME", 3, 3, cat="Date")
def f_time(h, m, s):
    total = int(_num(h)) * 3600 + int(_num(m)) * 60 + int(_num(s))
    if total < 0:
        raise NUM
    return (total % 86400) / 86400.0


@fn("NOW", 0, 0, volatile=True, cat="Date")
def f_now():
    return datetime_to_serial(_dt.datetime.now())


@fn("TODAY", 0, 0, volatile=True, cat="Date")
def f_today():
    return float(int(datetime_to_serial(_dt.datetime.now())))


@fn("YEAR", 1, 1, cat="Date")
def f_year(v):
    return float(_date_from(v).year)


@fn("MONTH", 1, 1, cat="Date")
def f_month(v):
    return float(_date_from(v).month)


@fn("DAY", 1, 1, cat="Date")
def f_day(v):
    return float(_date_from(v).day)


@fn("HOUR", 1, 1, cat="Date")
def f_hour(v):
    return float(_date_from(v).hour)


@fn("MINUTE", 1, 1, cat="Date")
def f_minute(v):
    return float(_date_from(v).minute)


@fn("SECOND", 1, 1, cat="Date")
def f_second(v):
    return float(_date_from(v).second)


@fn("WEEKDAY", 1, 2, cat="Date", syntax="WEEKDAY(serial_number, [return_type])")
def f_weekday(v, rtype=MISSING):
    d = _date_from(v)
    t = int(_num(_opt(rtype, 1)))
    py = d.weekday()  # Monday=0
    if t == 1:
        return float((py + 1) % 7 + 1)
    if t == 2:
        return float(py + 1)
    if t == 3:
        return float(py)
    if 11 <= t <= 17:
        return float((py - (t - 11)) % 7 + 1)
    raise NUM


@fn("WEEKNUM", 1, 2, cat="Date")
def f_weeknum(v, rtype=MISSING):
    d = _date_from(v)
    t = int(_num(_opt(rtype, 1)))
    if t == 21:
        return float(d.isocalendar()[1])
    start_dow = 6 if t in (1, 17) else 0 if t in (2, 11) else (t - 11) % 7
    jan1 = _dt.datetime(d.year, 1, 1)
    offset = (jan1.weekday() - start_dow) % 7
    return float((d - jan1).days + offset) // 7 + 1


@fn("ISOWEEKNUM", 1, 1, cat="Date")
def f_isoweeknum(v):
    return float(_date_from(v).isocalendar()[1])


def _add_months(d: _dt.datetime, months: int) -> _dt.datetime:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    import calendar

    day = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=day)


@fn("EDATE", 2, 2, cat="Date")
def f_edate(start, months):
    return float(int(datetime_to_serial(_add_months(_date_from(start), int(_num(months))))))


@fn("EOMONTH", 2, 2, cat="Date")
def f_eomonth(start, months):
    import calendar

    d = _add_months(_date_from(start).replace(day=1), int(_num(months)))
    return float(int(datetime_to_serial(d.replace(day=calendar.monthrange(d.year, d.month)[1]))))


@fn("DATEDIF", 3, 3, cat="Date", syntax="DATEDIF(start_date, end_date, unit)")
def f_datedif(start, end, unit):
    a, b = _date_from(start), _date_from(end)
    if a > b:
        raise NUM
    u = to_text(unit).upper()
    months = (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0)
    if u == "D":
        return float((b.date() - a.date()).days)
    if u == "M":
        return float(months)
    if u == "Y":
        return float(months // 12)
    if u == "YM":
        return float(months % 12)
    if u == "MD":
        prev = _add_months(a, months)
        return float((b.date() - prev.date()).days)
    if u == "YD":
        ay = a.replace(year=b.year) if not (a.month == 2 and a.day == 29) else a.replace(year=b.year, day=28)
        if ay > b:
            ay = ay.replace(year=b.year - 1)
        return float((b.date() - ay.date()).days)
    raise NUM


@fn("DAYS", 2, 2, cat="Date")
def f_days(end, start):
    return float((_date_from(end).date() - _date_from(start).date()).days)


@fn("DAYS360", 2, 3, cat="Date")
def f_days360(start, end, method=MISSING):
    a, b = _date_from(start), _date_from(end)
    d1, d2 = a.day, b.day
    if to_bool(_opt(method, False)):
        d1, d2 = min(d1, 30), min(d2, 30)
    else:
        if d1 == 31:
            d1 = 30
        if d2 == 31 and d1 >= 30:
            d2 = 30
    return float((b.year - a.year) * 360 + (b.month - a.month) * 30 + d2 - d1)


def _holidays(h):
    if h is MISSING:
        return set()
    return {int(v) for v in flatten([h])}


@fn("NETWORKDAYS", 2, 3, cat="Date")
def f_networkdays(start, end, holidays=MISSING):
    a, b = int(_num(start)), int(_num(end))
    sign = 1
    if a > b:
        a, b, sign = b, a, -1
    hol = _holidays(holidays)
    n = 0
    for s in range(a, b + 1):
        if serial_to_datetime(s).weekday() < 5 and s not in hol:
            n += 1
    return float(n * sign)


@fn("WORKDAY", 2, 3, cat="Date")
def f_workday(start, days, holidays=MISSING):
    s, k = int(_num(start)), int(_num(days))
    hol = _holidays(holidays)
    step = 1 if k >= 0 else -1
    while k:
        s += step
        if serial_to_datetime(s).weekday() < 5 and s not in hol:
            k -= step
    return float(s)


@fn("DATEVALUE", 1, 1, cat="Date")
def f_datevalue(text):
    n = parse_date_text(to_text(text))
    if n is None:
        raise VALUE
    return float(int(n))


@fn("TIMEVALUE", 1, 1, cat="Date")
def f_timevalue(text):
    n = parse_date_text(to_text(text))
    if n is None:
        raise VALUE
    return n - int(n)


@fn("YEARFRAC", 2, 3, cat="Date")
def f_yearfrac(start, end, basis=MISSING):
    a, b = _date_from(start), _date_from(end)
    if a > b:
        a, b = b, a
    bs = int(_num(_opt(basis, 0)))
    days = (b.date() - a.date()).days
    if bs == 0:
        return f_days360(datetime_to_serial(a), datetime_to_serial(b)) / 360
    if bs == 1:
        years = range(a.year, b.year + 1)
        avg = sum(366 if _dt.date(y, 12, 31).timetuple().tm_yday == 366 else 365 for y in years) / len(years)
        return days / avg
    if bs == 2:
        return days / 360
    if bs == 3:
        return days / 365
    if bs == 4:
        return f_days360(datetime_to_serial(a), datetime_to_serial(b), True) / 360
    raise NUM


# ============================================================================= information
def _safe_eval(ctx, f):
    try:
        v = f(ctx)
        return scalar(v) if isinstance(v, RangeRef) else v
    except XLError as e:
        return e


@fn("ISBLANK", 1, 1, cat="Info")
def f_isblank(v):
    return scalar(v) is None


@fn("ISNUMBER", 1, 1, cat="Info")
def f_isnumber(v):
    s = scalar(v)
    return isinstance(s, (int, float)) and not isinstance(s, bool)


@fn("ISTEXT", 1, 1, cat="Info")
def f_istext(v):
    return isinstance(scalar(v), str)


@fn("ISNONTEXT", 1, 1, cat="Info")
def f_isnontext(v):
    return not isinstance(scalar(v), str)


@fn("ISLOGICAL", 1, 1, cat="Info")
def f_islogical(v):
    return isinstance(scalar(v), bool)


@fn("ISERROR", 1, 1, lazy=True, cat="Info")
def f_iserror(ctx, f):
    return isinstance(_safe_eval(ctx, f), XLError)


@fn("ISERR", 1, 1, lazy=True, cat="Info")
def f_iserr(ctx, f):
    v = _safe_eval(ctx, f)
    return isinstance(v, XLError) and v != NA


@fn("ISNA", 1, 1, lazy=True, cat="Info")
def f_isna(ctx, f):
    return _safe_eval(ctx, f) == NA


@fn("ISEVEN", 1, 1, cat="Info")
def f_iseven(v):
    return int(math.floor(_num(v))) % 2 == 0


@fn("ISODD", 1, 1, cat="Info")
def f_isodd(v):
    return int(math.floor(_num(v))) % 2 == 1


@fn("ISREF", 1, 1, lazy=True, cat="Info")
def f_isref(ctx, f):
    try:
        return isinstance(f(ctx), RangeRef)
    except XLError:
        return False


@fn("ISFORMULA", 1, 1, cat="Info")
def f_isformula(ref):
    if not isinstance(ref, RangeRef):
        raise VALUE
    cell = ref.sheet().cells.get((ref.r1, ref.c1))
    return bool(cell is not None and cell.formula)


@fn("NA", 0, 0, cat="Info")
def f_na():
    raise NA


@fn("TYPE", 1, 1, lazy=True, cat="Info")
def f_type(ctx, f):
    try:
        v = f(ctx)
    except XLError:
        return 16.0
    if isinstance(v, RangeRef) and not v.is_cell:
        return 64.0
    v = scalar(v) if isinstance(v, RangeRef) else v
    if isinstance(v, Array):
        return 64.0
    if isinstance(v, bool):
        return 4.0
    if isinstance(v, (int, float)) or v is None:
        return 1.0
    if isinstance(v, str):
        return 2.0
    if isinstance(v, XLError):
        return 16.0
    return 1.0


@fn("ERROR.TYPE", 1, 1, lazy=True, cat="Info")
def f_error_type(ctx, f):
    from app.sheet.formula.values import ERROR_TYPES

    v = _safe_eval(ctx, f)
    if isinstance(v, XLError):
        return float(ERROR_TYPES.get(v.code, 0))
    raise NA


@fn("SHEET", 0, 1, ctx=True, cat="Info")
def f_sheet(ctx, ref=MISSING):
    sid = ref.sid if isinstance(ref, RangeRef) else ctx.sid
    return float(ctx.book.sheet_index(sid) + 1)


@fn("SHEETS", 0, 1, ctx=True, cat="Info")
def f_sheets(ctx, ref=MISSING):
    return float(len(ctx.book.sheets))


# ============================================================================= financial
def _fv(rate, nper, pmt, pv, when):
    if rate == 0:
        return -(pv + pmt * nper)
    f = (1 + rate) ** nper
    return -(pv * f + pmt * (1 + rate * when) * (f - 1) / rate)


@fn("PMT", 3, 5, cat="Financial", syntax="PMT(rate, nper, pv, [fv], [type])", help="Payment for a loan.")
def f_pmt(rate, nper, pv, fv=MISSING, typ=MISSING):
    r, n, p = _num(rate), _num(nper), _num(pv)
    f, t = _num(_opt(fv, 0)), _num(_opt(typ, 0))
    if n == 0:
        raise NUM
    if r == 0:
        return -(p + f) / n
    q = (1 + r) ** n
    return -(r * (p * q + f)) / ((1 + r * t) * (q - 1))


@fn("PV", 3, 5, cat="Financial")
def f_pv(rate, nper, pmt, fv=MISSING, typ=MISSING):
    r, n, m = _num(rate), _num(nper), _num(pmt)
    f, t = _num(_opt(fv, 0)), _num(_opt(typ, 0))
    if r == 0:
        return -(f + m * n)
    q = (1 + r) ** n
    return -(f + m * (1 + r * t) * (q - 1) / r) / q


@fn("FV", 3, 5, cat="Financial")
def f_fv(rate, nper, pmt, pv=MISSING, typ=MISSING):
    return _fv(_num(rate), _num(nper), _num(pmt), _num(_opt(pv, 0)), _num(_opt(typ, 0)))


@fn("NPER", 3, 5, cat="Financial")
def f_nper(rate, pmt, pv, fv=MISSING, typ=MISSING):
    r, m, p = _num(rate), _num(pmt), _num(pv)
    f, t = _num(_opt(fv, 0)), _num(_opt(typ, 0))
    if r == 0:
        if m == 0:
            raise NUM
        return -(p + f) / m
    num = m * (1 + r * t) - f * r
    den = p * r + m * (1 + r * t)
    if num / den <= 0:
        raise NUM
    return math.log(num / den) / math.log(1 + r)


@fn("RATE", 3, 6, cat="Financial")
def f_rate(nper, pmt, pv, fv=MISSING, typ=MISSING, guess=MISSING):
    n, m, p = _num(nper), _num(pmt), _num(pv)
    f, t = _num(_opt(fv, 0)), _num(_opt(typ, 0))
    r = _num(_opt(guess, 0.1))
    for _ in range(100):
        if abs(r) < 1e-12:
            y = p + m * n + f
            dy = m * n * (n - 1) / 2
        else:
            q = (1 + r) ** n
            y = p * q + m * (1 + r * t) * (q - 1) / r + f
            dq = n * (1 + r) ** (n - 1)
            dy = p * dq + m * t * (q - 1) / r + m * (1 + r * t) * (dq * r - (q - 1)) / (r * r)
        if dy == 0:
            break
        nr = r - y / dy
        if abs(nr - r) < 1e-10:
            return nr
        r = nr
    raise NUM


@fn("NPV", 2, None, cat="Financial", syntax="NPV(rate, value1, [value2], ...)")
def f_npv(rate, *vals):
    r = _num(rate)
    return sum(v / (1 + r) ** (i + 1) for i, v in enumerate(flatten(vals)))


@fn("IRR", 1, 2, cat="Financial")
def f_irr(values, guess=MISSING):
    cf = list(flatten([values]))
    if not any(v > 0 for v in cf) or not any(v < 0 for v in cf):
        raise NUM
    r = _num(_opt(guess, 0.1))
    for _ in range(200):
        npv = sum(v / (1 + r) ** i for i, v in enumerate(cf))
        d = sum(-i * v / (1 + r) ** (i + 1) for i, v in enumerate(cf))
        if d == 0:
            break
        nr = r - npv / d
        if abs(nr - r) < 1e-10:
            return nr
        r = nr
        if r <= -1:
            r = -0.99
    raise NUM


@fn("IPMT", 4, 6, cat="Financial")
def f_ipmt(rate, per, nper, pv, fv=MISSING, typ=MISSING):
    r, k, n, p = _num(rate), _num(per), _num(nper), _num(pv)
    f, t = _num(_opt(fv, 0)), _num(_opt(typ, 0))
    if k < 1 or k > n:
        raise NUM
    pmt = f_pmt(r, n, p, f, t)
    if t == 1 and k == 1:
        return 0.0
    bal = -_fv(r, k - 1, pmt, p, t)
    ip = bal * r * -1
    if t == 1:
        ip /= (1 + r)
    return ip


@fn("PPMT", 4, 6, cat="Financial")
def f_ppmt(rate, per, nper, pv, fv=MISSING, typ=MISSING):
    return f_pmt(rate, nper, pv, fv, typ) - f_ipmt(rate, per, nper, pv, fv, typ)


@fn("SLN", 3, 3, cat="Financial")
def f_sln(cost, salvage, life):
    lf = _num(life)
    if lf == 0:
        raise DIV0
    return (_num(cost) - _num(salvage)) / lf


@fn("SYD", 4, 4, cat="Financial")
def f_syd(cost, salvage, life, per):
    c, s, n, p = _num(cost), _num(salvage), _num(life), _num(per)
    if n <= 0 or p < 1 or p > n:
        raise NUM
    return (c - s) * (n - p + 1) * 2 / (n * (n + 1))


@fn("DDB", 4, 5, cat="Financial")
def f_ddb(cost, salvage, life, per, factor=MISSING):
    c, s, n, p = _num(cost), _num(salvage), _num(life), _num(per)
    fac = _num(_opt(factor, 2))
    if n <= 0 or p < 1 or p > n:
        raise NUM
    book, dep = c, 0.0
    for _ in range(int(p)):
        dep = min(book * fac / n, max(0.0, book - s))
        book -= dep
    return dep


@fn("EFFECT", 2, 2, cat="Financial")
def f_effect(nominal, npery):
    r, n = _num(nominal), int(_num(npery))
    if r <= 0 or n < 1:
        raise NUM
    return (1 + r / n) ** n - 1


@fn("NOMINAL", 2, 2, cat="Financial")
def f_nominal(effect, npery):
    r, n = _num(effect), int(_num(npery))
    if r <= 0 or n < 1:
        raise NUM
    return n * ((1 + r) ** (1 / n) - 1)


def function_names_by_category() -> dict[str, list[str]]:
    cats: dict[str, list[str]] = {}
    for name, spec in FUNCTIONS.items():
        cats.setdefault(spec.category, []).append(name)
    for v in cats.values():
        v.sort()
    return cats


from app.sheet.formula import functions_ext as _functions_ext  # noqa: E402  (registers more functions)

del _functions_ext

__all__ = ["FUNCTIONS", "FuncSpec", "make_criteria", "function_names_by_category", "to_int", "NAME"]
