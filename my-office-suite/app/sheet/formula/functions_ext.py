"""More Excel functions: engineering, statistical distributions and tests, financial, database, dynamic arrays,
text/regex and matrix functions.  Registered into the same FUNCTIONS table (imported by functions.py)."""
from __future__ import annotations

import cmath
import datetime as _dt
import math
import random
import re
import statistics
import urllib.parse

from app.sheet.formula.functions import (FUNCTIONS, _date_from, _holidays, _num, _nums, _opt, _var, fn,
                                         f_yearfrac, make_criteria)
from app.sheet.formula.values import (DIV0, MISSING, NA, NUM, REF, VALUE, Array, RangeRef, XLError, as_rows,
                                      datetime_to_serial, flatten, iter_all, scalar, to_bool, to_number, to_text)


def _alias(new: str, old: str) -> None:
    FUNCTIONS[new] = FUNCTIONS[old]


def _int(v) -> int:
    return int(math.floor(_num(v) + 1e-12)) if _num(v) >= 0 else -int(math.floor(-_num(v) + 1e-12))


def _pos_int(v) -> int:
    n = int(_num(v))
    if n < 0:
        raise NUM
    return n


# ============================================================================= special functions
def _gammainc_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x < 0 or a <= 0:
        raise NUM
    if x == 0:
        return 0.0
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(1000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    # continued fraction (Lentz) for Q, P = 1 - Q
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if a <= 0 or b <= 0:
        raise NUM
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def _invert(cdf, p: float, lo: float, hi: float, grow: bool = True) -> float:
    """x with cdf(x) = p for a monotone increasing cdf (bisection, expanding the upper bound)."""
    if not 0 <= p <= 1:
        raise NUM
    if grow:
        for _ in range(200):
            if cdf(hi) >= p:
                break
            lo, hi = hi, hi * 2 if hi > 0 else 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-13 * max(1.0, abs(mid)):
            break
    return (lo + hi) / 2


def _t_cdf(t: float, df: float) -> float:
    if df <= 0:
        raise NUM
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2, 0.5, x)
    return 1 - tail if t > 0 else tail


def _chi_cdf(x: float, df: float) -> float:
    if df <= 0:
        raise NUM
    return 0.0 if x <= 0 else _gammainc_p(df / 2, x / 2)


def _f_cdf(x: float, d1: float, d2: float) -> float:
    if d1 <= 0 or d2 <= 0:
        raise NUM
    return 0.0 if x <= 0 else _betainc(d1 / 2, d2 / 2, d1 * x / (d1 * x + d2))


def _beta_pdf(x, a, b):
    if x <= 0 or x >= 1:
        return 0.0
    return math.exp((a - 1) * math.log(x) + (b - 1) * math.log(1 - x) + math.lgamma(a + b) - math.lgamma(a)
                    - math.lgamma(b))


def _comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return 0.0
    return float(math.comb(n, k))


# ============================================================================= math & trig
def _reg_trig(name, f, domain=None):
    def impl(x):
        v = _num(x)
        if domain and not domain(v):
            raise NUM
        try:
            r = f(v)
        except (ValueError, OverflowError, ZeroDivisionError):
            raise DIV0 if name in ("COT", "CSC", "COTH", "CSCH") else NUM
        if r != r or r in (math.inf, -math.inf):
            raise NUM
        return r
    fn(name, 1, 1, cat="Math", syntax=f"{name}(number)")(impl)


_reg_trig("COT", lambda v: 1 / math.tan(v))
_reg_trig("COTH", lambda v: 1 / math.tanh(v))
_reg_trig("CSC", lambda v: 1 / math.sin(v))
_reg_trig("CSCH", lambda v: 1 / math.sinh(v))
_reg_trig("SEC", lambda v: 1 / math.cos(v))
_reg_trig("SECH", lambda v: 1 / math.cosh(v))
_reg_trig("ACOT", lambda v: math.pi / 2 - math.atan(v))
_reg_trig("ACOTH", lambda v: 0.5 * math.log((v + 1) / (v - 1)), lambda v: abs(v) > 1)

_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
          (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


@fn("ROMAN", 1, 2, cat="Math", syntax="ROMAN(number, [form])")
def f_roman(number, form=MISSING):
    n = int(_num(number))
    if n < 0 or n > 3999:
        raise VALUE
    out = []
    for v, s in _ROMAN:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


@fn("ARABIC", 1, 1, cat="Math")
def f_arabic(text):
    s = to_text(text).strip().upper()
    neg = s.startswith("-")
    s = s.lstrip("-")
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if any(ch not in vals for ch in s):
        raise VALUE
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        total += -v if i + 1 < len(s) and vals[s[i + 1]] > v else v
    return float(-total if neg else total)


_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@fn("BASE", 2, 3, cat="Math", syntax="BASE(number, radix, [min_length])")
def f_base(number, radix, min_length=MISSING):
    n, r = int(_num(number)), int(_num(radix))
    if n < 0 or not 2 <= r <= 36:
        raise NUM
    out = ""
    while n:
        n, d = divmod(n, r)
        out = _DIGITS[d] + out
    out = out or "0"
    return out.rjust(int(_num(_opt(min_length, 0))), "0")


@fn("DECIMAL", 2, 2, cat="Math", syntax="DECIMAL(text, radix)")
def f_decimal(text, radix):
    r = int(_num(radix))
    if not 2 <= r <= 36:
        raise NUM
    try:
        return float(int(to_text(text).strip(), r))
    except ValueError:
        raise NUM


@fn("MULTINOMIAL", 1, None, cat="Math")
def f_multinomial(*args):
    vals = [int(v) for v in flatten(args)]
    if any(v < 0 for v in vals):
        raise NUM
    out = math.factorial(sum(vals))
    for v in vals:
        out //= math.factorial(v)
    return float(out)


@fn("SERIESSUM", 4, 4, cat="Math", syntax="SERIESSUM(x, n, m, coefficients)")
def f_seriessum(x, n, m, coeffs):
    xv, nv, mv = _num(x), _num(n), _num(m)
    return sum(a * xv ** (nv + i * mv) for i, a in enumerate(flatten([coeffs])))


def _xy_pairs(a, b):
    xa, xb = list(iter_all(a)), list(iter_all(b))
    if len(xa) != len(xb):
        raise NA
    return [(float(x), float(y)) for x, y in zip(xa, xb) if isinstance(x, (int, float)) and
            isinstance(y, (int, float)) and not isinstance(x, bool) and not isinstance(y, bool)]


@fn("SUMX2MY2", 2, 2, cat="Math")
def f_sumx2my2(a, b):
    return sum(x * x - y * y for x, y in _xy_pairs(a, b))


@fn("SUMX2PY2", 2, 2, cat="Math")
def f_sumx2py2(a, b):
    return sum(x * x + y * y for x, y in _xy_pairs(a, b))


@fn("SUMXMY2", 2, 2, cat="Math")
def f_sumxmy2(a, b):
    return sum((x - y) ** 2 for x, y in _xy_pairs(a, b))


def _matrix(arg):
    import numpy as np

    rows = as_rows(arg)
    try:
        return np.array([[float(to_number(v)) if v is not None else 0.0 for v in r] for r in rows], dtype=float)
    except (TypeError, ValueError, XLError):
        raise VALUE


@fn("MMULT", 2, 2, cat="Math", syntax="MMULT(array1, array2)")
def f_mmult(a, b):
    ma, mb = _matrix(a), _matrix(b)
    if ma.shape[1] != mb.shape[0]:
        raise VALUE
    return Array((ma @ mb).tolist())


@fn("MINVERSE", 1, 1, cat="Math")
def f_minverse(a):
    import numpy as np

    ma = _matrix(a)
    if ma.shape[0] != ma.shape[1]:
        raise VALUE
    try:
        return Array(np.linalg.inv(ma).tolist())
    except np.linalg.LinAlgError:
        raise NUM


@fn("MDETERM", 1, 1, cat="Math")
def f_mdeterm(a):
    import numpy as np

    ma = _matrix(a)
    if ma.shape[0] != ma.shape[1]:
        raise VALUE
    return float(np.linalg.det(ma))


@fn("MUNIT", 1, 1, cat="Math")
def f_munit(n):
    k = int(_num(n))
    if k < 1:
        raise VALUE
    return Array([[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)])


@fn("RANDARRAY", 0, 5, volatile=True, cat="Math", syntax="RANDARRAY([rows], [columns], [min], [max], [integer])")
def f_randarray(rows=MISSING, cols=MISSING, lo=MISSING, hi=MISSING, integer=MISSING):
    r, c = int(_num(_opt(rows, 1))), int(_num(_opt(cols, 1)))
    a, b = _num(_opt(lo, 0)), _num(_opt(hi, 1))
    whole = to_bool(_opt(integer, False))
    if a > b:
        raise VALUE
    gen = (lambda: float(random.randint(int(math.ceil(a)), int(math.floor(b))))) if whole else \
        (lambda: a + random.random() * (b - a))
    return Array([[gen() for _ in range(c)] for _ in range(r)])


@fn("COMBINA", 2, 2, cat="Math")
def f_combina(n, k):
    nv, kv = _pos_int(n), _pos_int(k)
    return _comb(nv + kv - 1, kv) if nv + kv else 1.0


@fn("PERMUTATIONA", 2, 2, cat="Statistical")
def f_permutationa(n, k):
    return float(_pos_int(n) ** _pos_int(k))


@fn("GAMMA", 1, 1, cat="Statistical")
def f_gamma(x):
    v = _num(x)
    if v <= 0 and v == int(v):
        raise NUM
    return math.gamma(v)


@fn(("GAMMALN", "GAMMALN.PRECISE"), 1, 1, cat="Statistical")
def f_gammaln(x):
    v = _num(x)
    if v <= 0:
        raise NUM
    return math.lgamma(v)


# ============================================================================= statistical
@fn("SKEW.P", 1, None, cat="Statistical")
def f_skew_p(*args):
    v = _nums(args)
    n = len(v)
    if n < 1:
        raise DIV0
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / n)
    if sd == 0:
        raise DIV0
    return sum(((x - m) / sd) ** 3 for x in v) / n


def _nums_a(args):
    return list(flatten(args, skip_text_in_refs=False, skip_bool_in_refs=False))


@fn("STDEVPA", 1, None, cat="Statistical")
def f_stdevpa(*args):
    return math.sqrt(_var(_nums_a(args), False))


@fn("VARA", 1, None, cat="Statistical")
def f_vara(*args):
    return _var(_nums_a(args), True)


@fn("VARPA", 1, None, cat="Statistical")
def f_varpa(*args):
    return _var(_nums_a(args), False)


@fn("PERCENTRANK.EXC", 2, 3, cat="Statistical")
def f_percentrank_exc(arr, x, sig=MISSING):
    vals = sorted(_nums([arr]))
    xv = _num(x)
    n = len(vals)
    if not n or xv < vals[0] or xv > vals[-1]:
        raise NA
    below = sum(1 for v in vals if v < xv)
    if xv in vals:
        rank = (below + 1) / (n + 1)
    else:
        hi = next(v for v in vals if v > xv)
        lo = max(v for v in vals if v < xv)
        rank = (below + (xv - lo) / (hi - lo)) / (n + 1)
    d = int(_num(_opt(sig, 3)))
    return math.floor(rank * 10 ** d) / 10 ** d


@fn("MODE.MULT", 1, None, cat="Statistical")
def f_mode_mult(*args):
    vals = _nums(args)
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values()) if counts else 0
    if best < 2:
        raise NA
    seen, out = set(), []
    for v in vals:
        if counts[v] == best and v not in seen:
            seen.add(v)
            out.append([v])
    return Array(out)


@fn("PROB", 3, 4, cat="Statistical", syntax="PROB(x_range, prob_range, lower_limit, [upper_limit])")
def f_prob(xr, pr, lower, upper=MISSING):
    pairs = _xy_pairs(xr, pr)
    if abs(sum(p for _, p in pairs) - 1) > 1e-9 or any(p < 0 or p > 1 for _, p in pairs):
        raise NUM
    lo = _num(lower)
    hi = _num(_opt(upper, lo))
    return sum(p for x, p in pairs if lo <= x <= hi)


@fn("FISHER", 1, 1, cat="Statistical")
def f_fisher(x):
    v = _num(x)
    if not -1 < v < 1:
        raise NUM
    return 0.5 * math.log((1 + v) / (1 - v))


@fn("FISHERINV", 1, 1, cat="Statistical")
def f_fisherinv(y):
    v = _num(y)
    return (math.exp(2 * v) - 1) / (math.exp(2 * v) + 1)


@fn(("CONFIDENCE.NORM", "CONFIDENCE"), 3, 3, cat="Statistical")
def f_confidence_norm(alpha, sd, size):
    a, s, n = _num(alpha), _num(sd), _num(size)
    if not 0 < a < 1 or s <= 0 or n < 1:
        raise NUM
    return statistics.NormalDist().inv_cdf(1 - a / 2) * s / math.sqrt(n)


@fn("CONFIDENCE.T", 3, 3, cat="Statistical")
def f_confidence_t(alpha, sd, size):
    a, s, n = _num(alpha), _num(sd), _num(size)
    if not 0 < a < 1 or s <= 0 or n < 2:
        raise NUM if n >= 1 else DIV0
    return f_t_inv_2t(a, n - 1) * s / math.sqrt(n)


@fn(("BINOM.DIST", "BINOMDIST"), 4, 4, cat="Statistical")
def f_binom_dist(k, n, p, cumulative):
    kv, nv, pv = int(_num(k)), int(_num(n)), _num(p)
    if kv < 0 or kv > nv or not 0 <= pv <= 1:
        raise NUM
    pmf = lambda i: _comb(nv, i) * pv ** i * (1 - pv) ** (nv - i)
    return sum(pmf(i) for i in range(kv + 1)) if to_bool(cumulative) else pmf(kv)


@fn(("BINOM.INV", "CRITBINOM"), 3, 3, cat="Statistical")
def f_binom_inv(n, p, alpha):
    nv, pv, a = int(_num(n)), _num(p), _num(alpha)
    if nv < 0 or not 0 <= pv <= 1 or not 0 <= a <= 1:
        raise NUM
    total = 0.0
    for k in range(nv + 1):
        total += _comb(nv, k) * pv ** k * (1 - pv) ** (nv - k)
        if total >= a - 1e-12:
            return float(k)
    return float(nv)


@fn("BINOM.DIST.RANGE", 3, 4, cat="Statistical")
def f_binom_dist_range(n, p, s, s2=MISSING):
    nv, pv = int(_num(n)), _num(p)
    a = int(_num(s))
    b = int(_num(_opt(s2, a)))
    if not 0 <= a <= b <= nv or not 0 <= pv <= 1:
        raise NUM
    return sum(_comb(nv, i) * pv ** i * (1 - pv) ** (nv - i) for i in range(a, b + 1))


@fn(("NEGBINOM.DIST", "NEGBINOMDIST"), 3, 4, cat="Statistical")
def f_negbinom_dist(f, s, p, cumulative=MISSING):
    fv, sv, pv = int(_num(f)), int(_num(s)), _num(p)
    if fv < 0 or sv < 1 or not 0 <= pv <= 1:
        raise NUM
    pmf = lambda i: _comb(i + sv - 1, sv - 1) * pv ** sv * (1 - pv) ** i
    return sum(pmf(i) for i in range(fv + 1)) if to_bool(_opt(cumulative, False)) else pmf(fv)


@fn(("HYPGEOM.DIST", "HYPGEOMDIST"), 4, 5, cat="Statistical")
def f_hypgeom_dist(k, n, K, N, cumulative=MISSING):
    kv, nv, Kv, Nv = int(_num(k)), int(_num(n)), int(_num(K)), int(_num(N))
    if min(kv, nv, Kv, Nv) < 0 or nv > Nv or Kv > Nv or kv > min(nv, Kv):
        raise NUM
    pmf = lambda i: _comb(Kv, i) * _comb(Nv - Kv, nv - i) / _comb(Nv, nv)
    return sum(pmf(i) for i in range(max(0, nv - Nv + Kv), kv + 1)) if to_bool(_opt(cumulative, False)) else pmf(kv)


@fn(("POISSON.DIST", "POISSON"), 3, 3, cat="Statistical")
def f_poisson_dist(x, mean, cumulative):
    k, lam = int(_num(x)), _num(mean)
    if k < 0 or lam < 0:
        raise NUM
    pmf = lambda i: math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1)) if lam > 0 else (1.0 if i == 0 else 0.0)
    return sum(pmf(i) for i in range(k + 1)) if to_bool(cumulative) else pmf(k)


@fn(("EXPON.DIST", "EXPONDIST"), 3, 3, cat="Statistical")
def f_expon_dist(x, lam, cumulative):
    xv, lv = _num(x), _num(lam)
    if xv < 0 or lv <= 0:
        raise NUM
    return 1 - math.exp(-lv * xv) if to_bool(cumulative) else lv * math.exp(-lv * xv)


@fn(("GAMMA.DIST", "GAMMADIST"), 4, 4, cat="Statistical")
def f_gamma_dist(x, alpha, beta, cumulative):
    xv, a, b = _num(x), _num(alpha), _num(beta)
    if xv < 0 or a <= 0 or b <= 0:
        raise NUM
    if to_bool(cumulative):
        return _gammainc_p(a, xv / b)
    if xv == 0:
        return 0.0 if a > 1 else (1 / b if a == 1 else math.inf)
    return math.exp((a - 1) * math.log(xv) - xv / b - math.lgamma(a) - a * math.log(b))


@fn(("GAMMA.INV", "GAMMAINV"), 3, 3, cat="Statistical")
def f_gamma_inv(p, alpha, beta):
    pv, a, b = _num(p), _num(alpha), _num(beta)
    if not 0 <= pv < 1 or a <= 0 or b <= 0:
        raise NUM
    return _invert(lambda x: _gammainc_p(a, x / b), pv, 0.0, max(1.0, a * b * 4))


@fn("BETA.DIST", 4, 6, cat="Statistical", syntax="BETA.DIST(x, alpha, beta, cumulative, [A], [B])")
def f_beta_dist(x, alpha, beta, cumulative, lo=MISSING, hi=MISSING):
    a, b = _num(alpha), _num(beta)
    A, B = _num(_opt(lo, 0)), _num(_opt(hi, 1))
    xv = _num(x)
    if a <= 0 or b <= 0 or not A <= xv <= B or A == B:
        raise NUM
    z = (xv - A) / (B - A)
    return _betainc(a, b, z) if to_bool(cumulative) else _beta_pdf(z, a, b) / (B - A)


@fn("BETADIST", 3, 5, cat="Statistical")
def f_betadist(x, alpha, beta, lo=MISSING, hi=MISSING):
    return f_beta_dist(x, alpha, beta, True, lo, hi)


@fn(("BETA.INV", "BETAINV"), 3, 5, cat="Statistical")
def f_beta_inv(p, alpha, beta, lo=MISSING, hi=MISSING):
    pv, a, b = _num(p), _num(alpha), _num(beta)
    A, B = _num(_opt(lo, 0)), _num(_opt(hi, 1))
    if not 0 < pv < 1 or a <= 0 or b <= 0:
        raise NUM
    z = _invert(lambda t: _betainc(a, b, t), pv, 0.0, 1.0, grow=False)
    return A + z * (B - A)


@fn("LOGNORM.DIST", 4, 4, cat="Statistical")
def f_lognorm_dist(x, mean, sd, cumulative):
    xv, m, s = _num(x), _num(mean), _num(sd)
    if xv <= 0 or s <= 0:
        raise NUM
    if to_bool(cumulative):
        return statistics.NormalDist(m, s).cdf(math.log(xv))
    return math.exp(-((math.log(xv) - m) ** 2) / (2 * s * s)) / (xv * s * math.sqrt(2 * math.pi))


@fn("LOGNORMDIST", 3, 3, cat="Statistical")
def f_lognormdist(x, mean, sd):
    return f_lognorm_dist(x, mean, sd, True)


@fn(("LOGNORM.INV", "LOGINV"), 3, 3, cat="Statistical")
def f_lognorm_inv(p, mean, sd):
    pv, m, s = _num(p), _num(mean), _num(sd)
    if not 0 < pv < 1 or s <= 0:
        raise NUM
    return math.exp(statistics.NormalDist(m, s).inv_cdf(pv))


@fn(("WEIBULL.DIST", "WEIBULL"), 4, 4, cat="Statistical")
def f_weibull_dist(x, alpha, beta, cumulative):
    xv, a, b = _num(x), _num(alpha), _num(beta)
    if xv < 0 or a <= 0 or b <= 0:
        raise NUM
    if to_bool(cumulative):
        return 1 - math.exp(-((xv / b) ** a))
    return a / b ** a * xv ** (a - 1) * math.exp(-((xv / b) ** a))


@fn("T.DIST", 3, 3, cat="Statistical")
def f_t_dist(x, df, cumulative):
    t, d = _num(x), _num(df)
    if d < 1:
        raise DIV0
    if to_bool(cumulative):
        return _t_cdf(t, d)
    return math.exp(math.lgamma((d + 1) / 2) - math.lgamma(d / 2)) / math.sqrt(d * math.pi) * \
        (1 + t * t / d) ** (-(d + 1) / 2)


@fn("T.DIST.2T", 2, 2, cat="Statistical")
def f_t_dist_2t(x, df):
    t, d = _num(x), _num(df)
    if t < 0 or d < 1:
        raise NUM
    return 2 * (1 - _t_cdf(t, d))


@fn("T.DIST.RT", 2, 2, cat="Statistical")
def f_t_dist_rt(x, df):
    t, d = _num(x), _num(df)
    if d < 1:
        raise NUM
    return 1 - _t_cdf(t, d)


@fn("TDIST", 3, 3, cat="Statistical")
def f_tdist(x, df, tails):
    t = _num(x)
    if t < 0:
        raise NUM
    return f_t_dist_rt(t, df) * int(_num(tails))


@fn("T.INV", 2, 2, cat="Statistical")
def f_t_inv(p, df):
    pv, d = _num(p), _num(df)
    if not 0 < pv < 1 or d < 1:
        raise NUM
    if pv == 0.5:
        return 0.0
    if pv > 0.5:
        return _invert(lambda t: _t_cdf(t, d), pv, 0.0, 10.0)
    return -_invert(lambda t: _t_cdf(t, d), 1 - pv, 0.0, 10.0)


@fn(("T.INV.2T", "TINV"), 2, 2, cat="Statistical")
def f_t_inv_2t(p, df):
    pv, d = _num(p), _num(df)
    if not 0 < pv <= 1 or d < 1:
        raise NUM
    return _invert(lambda t: _t_cdf(t, d), 1 - pv / 2, 0.0, 10.0)


@fn("CHISQ.DIST", 3, 3, cat="Statistical")
def f_chisq_dist(x, df, cumulative):
    xv, d = _num(x), _num(df)
    if xv < 0 or d < 1:
        raise NUM
    if to_bool(cumulative):
        return _chi_cdf(xv, d)
    if xv == 0:
        return 0.0 if d > 2 else (0.5 if d == 2 else math.inf)
    k = d / 2
    return math.exp((k - 1) * math.log(xv) - xv / 2 - k * math.log(2) - math.lgamma(k))


@fn(("CHISQ.DIST.RT", "CHIDIST"), 2, 2, cat="Statistical")
def f_chisq_dist_rt(x, df):
    xv, d = _num(x), _num(df)
    if xv < 0 or d < 1:
        raise NUM
    return 1 - _chi_cdf(xv, d)


@fn("CHISQ.INV", 2, 2, cat="Statistical")
def f_chisq_inv(p, df):
    pv, d = _num(p), _num(df)
    if not 0 <= pv < 1 or d < 1:
        raise NUM
    return _invert(lambda x: _chi_cdf(x, d), pv, 0.0, max(10.0, d * 3))


@fn(("CHISQ.INV.RT", "CHIINV"), 2, 2, cat="Statistical")
def f_chisq_inv_rt(p, df):
    pv = _num(p)
    if not 0 < pv <= 1:
        raise NUM
    return f_chisq_inv(1 - pv, df)


@fn("F.DIST", 4, 4, cat="Statistical")
def f_f_dist(x, d1, d2, cumulative):
    xv, a, b = _num(x), _num(d1), _num(d2)
    if xv < 0 or a < 1 or b < 1:
        raise NUM
    if to_bool(cumulative):
        return _f_cdf(xv, a, b)
    if xv == 0:
        return 0.0
    return math.sqrt((a * xv) ** a * b ** b / (a * xv + b) ** (a + b)) / (
        xv * math.exp(math.lgamma(a / 2) + math.lgamma(b / 2) - math.lgamma((a + b) / 2)))


@fn(("F.DIST.RT", "FDIST"), 3, 3, cat="Statistical")
def f_f_dist_rt(x, d1, d2):
    xv, a, b = _num(x), _num(d1), _num(d2)
    if xv < 0 or a < 1 or b < 1:
        raise NUM
    return 1 - _f_cdf(xv, a, b)


@fn("F.INV", 3, 3, cat="Statistical")
def f_f_inv(p, d1, d2):
    pv, a, b = _num(p), _num(d1), _num(d2)
    if not 0 <= pv < 1 or a < 1 or b < 1:
        raise NUM
    return _invert(lambda x: _f_cdf(x, a, b), pv, 0.0, 10.0)


@fn(("F.INV.RT", "FINV"), 3, 3, cat="Statistical")
def f_f_inv_rt(p, d1, d2):
    pv = _num(p)
    if not 0 < pv <= 1:
        raise NUM
    return f_f_inv(1 - pv, d1, d2)


@fn(("T.TEST", "TTEST"), 4, 4, cat="Statistical", syntax="T.TEST(array1, array2, tails, type)")
def f_t_test(a1, a2, tails, kind):
    tl, ty = int(_num(tails)), int(_num(kind))
    if tl not in (1, 2) or ty not in (1, 2, 3):
        raise NUM
    if ty == 1:
        pairs = _xy_pairs(a1, a2)
        d = [x - y for x, y in pairs]
        n = len(d)
        if n < 2:
            raise DIV0
        md = sum(d) / n
        sd = math.sqrt(_var(d, True))
        if sd == 0:
            raise DIV0
        t, df = md / (sd / math.sqrt(n)), n - 1
    else:
        x, y = _nums([a1]), _nums([a2])
        n1, n2 = len(x), len(y)
        if n1 < 2 or n2 < 2:
            raise DIV0
        m1, m2 = sum(x) / n1, sum(y) / n2
        v1, v2 = _var(x, True), _var(y, True)
        if ty == 2:
            sp = ((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)
            t, df = (m1 - m2) / math.sqrt(sp * (1 / n1 + 1 / n2)), n1 + n2 - 2
        else:
            se = v1 / n1 + v2 / n2
            t = (m1 - m2) / math.sqrt(se)
            df = se ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return tl * (1 - _t_cdf(abs(t), df))


@fn(("Z.TEST", "ZTEST"), 2, 3, cat="Statistical")
def f_z_test(arr, x, sigma=MISSING):
    vals = _nums([arr])
    n = len(vals)
    if n < 2 and sigma is MISSING:
        raise DIV0
    m = sum(vals) / n
    s = _num(sigma) if sigma is not MISSING else math.sqrt(_var(vals, True))
    if s == 0:
        raise DIV0
    return 1 - statistics.NormalDist().cdf((m - _num(x)) / (s / math.sqrt(n)))


@fn(("CHISQ.TEST", "CHITEST"), 2, 2, cat="Statistical")
def f_chisq_test(actual, expected):
    ra, re_ = as_rows(actual), as_rows(expected)
    if len(ra) != len(re_) or any(len(a) != len(b) for a, b in zip(ra, re_)):
        raise NA
    chi = 0.0
    for a_row, e_row in zip(ra, re_):
        for a, e in zip(a_row, e_row):
            if isinstance(a, (int, float)) and isinstance(e, (int, float)):
                if e == 0:
                    raise DIV0
                chi += (a - e) ** 2 / e
    r, c = len(ra), len(ra[0]) if ra else 0
    df = (r - 1) * (c - 1) if r > 1 and c > 1 else max(r, c) - 1
    if df < 1:
        raise NA
    return 1 - _chi_cdf(chi, df)


@fn(("F.TEST", "FTEST"), 2, 2, cat="Statistical")
def f_f_test(a1, a2):
    x, y = _nums([a1]), _nums([a2])
    if len(x) < 2 or len(y) < 2:
        raise DIV0
    v1, v2 = _var(x, True), _var(y, True)
    if v2 == 0 or v1 == 0:
        raise DIV0
    f = v1 / v2
    p = _f_cdf(f, len(x) - 1, len(y) - 1)
    return 2 * min(p, 1 - p)


@fn("GAUSS", 1, 1, cat="Statistical")
def f_gauss(z):
    return statistics.NormalDist().cdf(_num(z)) - 0.5


@fn("PHI", 1, 1, cat="Statistical")
def f_phi(x):
    return statistics.NormalDist().pdf(_num(x))


def _regression(known_y, known_x, const=True):
    import numpy as np

    ys = [v for v in iter_all(known_y)]
    if known_x is MISSING:
        xs = [[float(i + 1)] for i in range(len(ys))]
    else:
        rows = as_rows(known_x)
        ry = as_rows(known_y)
        if len(rows) == len(ys) and len(ry) == len(ys):
            xs = [[to_number(v) for v in r] for r in rows]           # columns are variables
        elif len(rows) == 1 and len(rows[0]) == len(ys):
            xs = [[to_number(v)] for v in rows[0]]
        elif rows and len(rows[0]) == len(ys):
            xs = [[to_number(rows[k][i]) for k in range(len(rows))] for i in range(len(ys))]
        else:
            raise REF
    try:
        y = np.array([float(to_number(v)) for v in ys])
    except (TypeError, ValueError, XLError):
        raise VALUE
    X = np.array(xs, dtype=float)
    A = np.column_stack([X, np.ones(len(y))]) if const else X
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return y, X, A, coef


@fn("LINEST", 1, 4, cat="Statistical", syntax="LINEST(known_y's, [known_x's], [const], [stats])")
def f_linest(known_y, known_x=MISSING, const=MISSING, stats=MISSING):
    import numpy as np

    c = to_bool(_opt(const, True))
    y, X, A, coef = _regression(known_y, known_x, c)
    k = X.shape[1]
    slopes = list(coef[:k])[::-1]
    b = float(coef[k]) if c else 0.0
    first = [float(s) for s in slopes] + [b]
    if not to_bool(_opt(stats, False)):
        return Array([first])
    n = len(y)
    pred = A @ coef
    ssres = float(((y - pred) ** 2).sum())
    sstot = float(((y - y.mean()) ** 2).sum()) if c else float((y ** 2).sum())
    df = n - k - (1 if c else 0)
    r2 = 1 - ssres / sstot if sstot else 1.0
    sey = math.sqrt(ssres / df) if df > 0 else 0.0
    try:
        cov = np.linalg.inv(A.T @ A) * (sey ** 2)
        se = [math.sqrt(max(0.0, cov[i, i])) for i in range(A.shape[1])]
    except np.linalg.LinAlgError:
        se = [0.0] * A.shape[1]
    se_row = [se[i] for i in range(k)][::-1] + ([se[k]] if c else [NA])
    ssreg = sstot - ssres
    fstat = (ssreg / k) / (ssres / df) if df > 0 and ssres else NUM
    pad = [NA] * (k - 1)
    return Array([first, se_row, [r2, sey] + pad, [fstat, float(df)] + pad, [ssreg, ssres] + pad])


@fn("LOGEST", 1, 4, cat="Statistical")
def f_logest(known_y, known_x=MISSING, const=MISSING, stats=MISSING):
    ys = [to_number(v) for v in iter_all(known_y)]
    if any(v <= 0 for v in ys):
        raise NUM
    res = f_linest(Array([[math.log(v)] for v in ys]), known_x, const, False)
    return Array([[math.exp(v) for v in res.rows[0]]])


@fn("TREND", 1, 4, cat="Statistical", syntax="TREND(known_y's, [known_x's], [new_x's], [const])")
def f_trend(known_y, known_x=MISSING, new_x=MISSING, const=MISSING):
    import numpy as np

    c = to_bool(_opt(const, True))
    y, X, A, coef = _regression(known_y, known_x, c)
    if new_x is MISSING:
        N = X
        shape_src = known_y
    else:
        rows = as_rows(new_x)
        if X.shape[1] == 1:
            N = np.array([[float(to_number(v))] for v in iter_all(new_x)])
        else:
            N = np.array([[float(to_number(v)) for v in r] for r in rows])
        shape_src = new_x
    pred = (np.column_stack([N, np.ones(len(N))]) if c else N) @ coef
    rows = as_rows(shape_src)
    if len(rows) == 1 and X.shape[1] == 1:
        return Array([list(map(float, pred))])
    return Array([[float(v)] for v in pred])


@fn("GROWTH", 1, 4, cat="Statistical")
def f_growth(known_y, known_x=MISSING, new_x=MISSING, const=MISSING):
    ys = [to_number(v) for v in iter_all(known_y)]
    if any(v <= 0 for v in ys):
        raise NUM
    res = f_trend(Array([[math.log(v)] for v in ys]), known_x, new_x, const)
    return Array([[math.exp(v) for v in r] for r in res.rows])


# ============================================================================= engineering
def _from_base(text, base: int, bits: int = 40) -> int:
    s = to_text(text).strip().upper()
    if not s:
        return 0
    if len(s) > 10:
        raise NUM
    try:
        n = int(s, base)
    except ValueError:
        raise NUM
    if n >= base ** 10 // 2 and len(s) == 10:  # two's complement, 10 digits
        n -= base ** 10
    return n


def _to_base(n: int, base: int, places=MISSING) -> str:
    lim = {2: 512, 8: 536870912, 16: 549755813888}[base]
    if n < -lim or n >= lim:
        raise NUM
    if n < 0:
        s = _int_to(base ** 10 + n, base)
        return s
    s = _int_to(n, base)
    if places is not MISSING:
        p = int(_num(places))
        if p < len(s):
            raise NUM
        s = s.rjust(p, "0")
    return s


def _int_to(n: int, base: int) -> str:
    out = ""
    while n:
        n, d = divmod(n, base)
        out = _DIGITS[d] + out
    return out or "0"


for _src, _sb in (("BIN", 2), ("OCT", 8), ("HEX", 16)):
    fn(f"{_src}2DEC", 1, 1, cat="Engineering")(lambda t, _b=_sb: float(_from_base(t, _b)))
    for _dst, _db in (("BIN", 2), ("OCT", 8), ("HEX", 16)):
        if _dst != _src:
            fn(f"{_src}2{_dst}", 1, 2, cat="Engineering")(
                lambda t, places=MISSING, _b=_sb, _d=_db: _to_base(_from_base(t, _b), _d, places))
for _dst, _db in (("BIN", 2), ("OCT", 8), ("HEX", 16)):
    fn(f"DEC2{_dst}", 1, 2, cat="Engineering")(
        lambda n, places=MISSING, _d=_db: _to_base(int(_num(n)), _d, places))


def _bits(v) -> int:
    n = _num(v)
    if n < 0 or n != int(n) or n >= 2 ** 48:
        raise NUM
    return int(n)


fn("BITAND", 2, 2, cat="Engineering")(lambda a, b: float(_bits(a) & _bits(b)))
fn("BITOR", 2, 2, cat="Engineering")(lambda a, b: float(_bits(a) | _bits(b)))
fn("BITXOR", 2, 2, cat="Engineering")(lambda a, b: float(_bits(a) ^ _bits(b)))


@fn("BITLSHIFT", 2, 2, cat="Engineering")
def f_bitlshift(n, s):
    v, k = _bits(n), int(_num(s))
    r = v << k if k >= 0 else v >> -k
    if r >= 2 ** 48:
        raise NUM
    return float(r)


@fn("BITRSHIFT", 2, 2, cat="Engineering")
def f_bitrshift(n, s):
    return f_bitlshift(n, -int(_num(s)))


fn("DELTA", 1, 2, cat="Engineering")(lambda a, b=MISSING: 1.0 if _num(a) == _num(_opt(b, 0)) else 0.0)
fn("GESTEP", 1, 2, cat="Engineering")(lambda a, b=MISSING: 1.0 if _num(a) >= _num(_opt(b, 0)) else 0.0)


@fn(("ERF", "ERF.PRECISE"), 1, 2, cat="Engineering")
def f_erf(lo, hi=MISSING):
    if hi is MISSING:
        return math.erf(_num(lo))
    return math.erf(_num(hi)) - math.erf(_num(lo))


fn(("ERFC", "ERFC.PRECISE"), 1, 1, cat="Engineering")(lambda x: math.erfc(_num(x)))


@fn("BESSELJ", 2, 2, cat="Engineering")
def f_besselj(x, n):
    xv, nv = _num(x), int(_num(n))
    if nv < 0:
        raise NUM
    return sum((-1) ** k * (xv / 2) ** (2 * k + nv) / (math.factorial(k) * math.factorial(k + nv)) for k in range(60))


@fn("BESSELI", 2, 2, cat="Engineering")
def f_besseli(x, n):
    xv, nv = _num(x), int(_num(n))
    if nv < 0:
        raise NUM
    return sum((xv / 2) ** (2 * k + nv) / (math.factorial(k) * math.factorial(k + nv)) for k in range(60))


# complex numbers ("3+4i")
_CPLX = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)?(?:([+-](?:\d+\.?\d*|\.\d+)?(?:[eE][+-]?\d+)?)([ij]))?$")


def _cparse(v) -> complex:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return complex(v, 0)
    s = to_text(v).strip().replace(" ", "")
    if not s:
        return 0j
    if s in ("i", "+i", "j", "+j"):
        return 1j
    if s in ("-i", "-j"):
        return -1j
    m = re.fullmatch(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)?([ij])?", s)
    if m and m.group(0):
        num = m.group(1)
        if m.group(2):
            return complex(0, float(num) if num not in (None, "+", "-") else (-1.0 if num == "-" else 1.0))
        return complex(float(num), 0)
    m = _CPLX.match(s)
    if not m:
        raise NUM
    re_part = float(m.group(1)) if m.group(1) else 0.0
    im_txt = m.group(2)
    im_part = 0.0
    if im_txt is not None:
        im_part = float(im_txt) if im_txt not in ("+", "-") else (1.0 if im_txt == "+" else -1.0)
    return complex(re_part, im_part)


def _cfmt(z: complex, suffix: str = "i") -> str:
    def num(x):
        x = round(x, 14)
        return str(int(x)) if x == int(x) and abs(x) < 1e15 else repr(x)
    r, i = z.real, z.imag
    if i == 0:
        return num(r)
    if r == 0:
        im = "" if i == 1 else "-" if i == -1 else num(i)
        return f"{im}{suffix}"
    im = "+" if i == 1 else "-" if i == -1 else ("+" + num(i) if i > 0 else num(i))
    return f"{num(r)}{im}{suffix}"


@fn("COMPLEX", 2, 3, cat="Engineering", syntax="COMPLEX(real_num, i_num, [suffix])")
def f_complex(re_, im, suffix=MISSING):
    sfx = to_text(_opt(suffix, "i")) or "i"
    if sfx not in ("i", "j"):
        raise VALUE
    return _cfmt(complex(_num(re_), _num(im)), sfx)


fn("IMREAL", 1, 1, cat="Engineering")(lambda z: _cparse(z).real)
fn("IMAGINARY", 1, 1, cat="Engineering")(lambda z: _cparse(z).imag)
fn("IMABS", 1, 1, cat="Engineering")(lambda z: abs(_cparse(z)))
fn("IMCONJUGATE", 1, 1, cat="Engineering")(lambda z: _cfmt(_cparse(z).conjugate()))


@fn("IMARGUMENT", 1, 1, cat="Engineering")
def f_imargument(z):
    c = _cparse(z)
    if c == 0:
        raise DIV0
    return cmath.phase(c)


@fn("IMSUM", 1, None, cat="Engineering")
def f_imsum(*args):
    return _cfmt(sum((_cparse(v) for a in args for v in iter_all(a) if v is not None), 0j))


@fn("IMPRODUCT", 1, None, cat="Engineering")
def f_improduct(*args):
    out = 1 + 0j
    for a in args:
        for v in iter_all(a):
            if v is not None:
                out *= _cparse(v)
    return _cfmt(out)


fn("IMSUB", 2, 2, cat="Engineering")(lambda a, b: _cfmt(_cparse(a) - _cparse(b)))


@fn("IMDIV", 2, 2, cat="Engineering")
def f_imdiv(a, b):
    d = _cparse(b)
    if d == 0:
        raise NUM
    return _cfmt(_cparse(a) / d)


fn("IMPOWER", 2, 2, cat="Engineering")(lambda z, n: _cfmt(_cparse(z) ** _num(n)))
fn("IMSQRT", 1, 1, cat="Engineering")(lambda z: _cfmt(cmath.sqrt(_cparse(z))))
fn("IMEXP", 1, 1, cat="Engineering")(lambda z: _cfmt(cmath.exp(_cparse(z))))
fn("IMSIN", 1, 1, cat="Engineering")(lambda z: _cfmt(cmath.sin(_cparse(z))))
fn("IMCOS", 1, 1, cat="Engineering")(lambda z: _cfmt(cmath.cos(_cparse(z))))
fn("IMTAN", 1, 1, cat="Engineering")(lambda z: _cfmt(cmath.tan(_cparse(z))))


def _imlog(z, f):
    c = _cparse(z)
    if c == 0:
        raise NUM
    return _cfmt(f(c))


fn("IMLN", 1, 1, cat="Engineering")(lambda z: _imlog(z, cmath.log))
fn("IMLOG10", 1, 1, cat="Engineering")(lambda z: _imlog(z, cmath.log10))
fn("IMLOG2", 1, 1, cat="Engineering")(lambda z: _imlog(z, lambda c: cmath.log(c) / math.log(2)))

# CONVERT: unit -> (category, factor to the base unit, metric prefixes allowed)
_UNITS = {
    # length (m)
    "m": ("len", 1, True), "mi": ("len", 1609.344, False), "Nmi": ("len", 1852, False), "in": ("len", 0.0254, False),
    "ft": ("len", 0.3048, False), "yd": ("len", 0.9144, False), "ang": ("len", 1e-10, True),
    "ell": ("len", 1.143, False), "ly": ("len", 9.46073047258e15, True), "parsec": ("len", 3.08567758128e16, True),
    "pc": ("len", 3.08567758128e16, True), "Pica": ("len", 0.0254 / 72, False), "pica": ("len", 0.0254 / 6, False),
    "survey_mi": ("len", 1609.347219, False),
    # mass (g)
    "g": ("mass", 1, True), "sg": ("mass", 14593.90294, False), "lbm": ("mass", 453.59237, False),
    "u": ("mass", 1.66053906660e-24, True), "ozm": ("mass", 28.349523125, False), "grain": ("mass", 0.06479891, False),
    "cwt": ("mass", 45359.237, False), "shweight": ("mass", 45359.237, False), "uk_cwt": ("mass", 50802.34544, False),
    "stone": ("mass", 6350.29318, False), "ton": ("mass", 907184.74, False), "uk_ton": ("mass", 1016046.9088, False),
    # time (s)
    "yr": ("time", 31557600, False), "day": ("time", 86400, False), "d": ("time", 86400, False),
    "hr": ("time", 3600, False), "mn": ("time", 60, False), "min": ("time", 60, False), "sec": ("time", 1, True),
    "s": ("time", 1, True),
    # pressure (Pa)
    "Pa": ("press", 1, True), "p": ("press", 1, True), "atm": ("press", 101325, True), "at": ("press", 101325, True),
    "mmHg": ("press", 133.322, True), "psi": ("press", 6894.757293, True), "Torr": ("press", 133.322368, False),
    # force (N)
    "N": ("force", 1, True), "dyn": ("force", 1e-5, True), "dy": ("force", 1e-5, True), "lbf": ("force", 4.448222, False),
    "pond": ("force", 0.00980665, True),
    # energy (J)
    "J": ("energy", 1, True), "e": ("energy", 1e-7, True), "c": ("energy", 4.184, True), "cal": ("energy", 4.1868, True),
    "eV": ("energy", 1.602176634e-19, True), "ev": ("energy", 1.602176634e-19, True),
    "HPh": ("energy", 2684519.537696, False), "hh": ("energy", 2684519.537696, False), "Wh": ("energy", 3600, True),
    "wh": ("energy", 3600, True), "flb": ("energy", 1.3558179483314, False), "BTU": ("energy", 1055.05585262, False),
    "btu": ("energy", 1055.05585262, False),
    # power (W)
    "HP": ("power", 745.69987158227, False), "h": ("power", 745.69987158227, False), "PS": ("power", 735.49875, False),
    "W": ("power", 1, True), "w": ("power", 1, True),
    # magnetism (T)
    "T": ("mag", 1, True), "ga": ("mag", 1e-4, True),
    # speed (m/s)
    "m/s": ("speed", 1, True), "m/sec": ("speed", 1, True), "m/h": ("speed", 1 / 3600, True),
    "m/hr": ("speed", 1 / 3600, True), "mph": ("speed", 0.44704, False), "kn": ("speed", 1852 / 3600, False),
    "admkn": ("speed", 1853.184 / 3600, False),
    # area (m2)
    "m2": ("area", 1, True), "m^2": ("area", 1, True), "ha": ("area", 10000, False), "ar": ("area", 100, True),
    "in2": ("area", 0.0254 ** 2, False), "in^2": ("area", 0.0254 ** 2, False), "ft2": ("area", 0.3048 ** 2, False),
    "ft^2": ("area", 0.3048 ** 2, False), "yd2": ("area", 0.9144 ** 2, False), "yd^2": ("area", 0.9144 ** 2, False),
    "mi2": ("area", 1609.344 ** 2, False), "mi^2": ("area", 1609.344 ** 2, False),
    "uk_acre": ("area", 4046.8564224, False), "us_acre": ("area", 4046.87261, False),
    "Nmi2": ("area", 1852 ** 2, False),
    # volume (l)
    "l": ("vol", 1, True), "L": ("vol", 1, True), "lt": ("vol", 1, True), "m3": ("vol", 1000, True),
    "m^3": ("vol", 1000, True), "tsp": ("vol", 0.00492892159375, False), "tspm": ("vol", 0.005, False),
    "tbs": ("vol", 0.01478676478125, False), "oz": ("vol", 0.0295735295625, False), "cup": ("vol", 0.2365882365, False),
    "pt": ("vol", 0.473176473, False), "us_pt": ("vol", 0.473176473, False), "uk_pt": ("vol", 0.56826125, False),
    "qt": ("vol", 0.946352946, False), "uk_qt": ("vol", 1.1365225, False), "gal": ("vol", 3.785411784, False),
    "uk_gal": ("vol", 4.54609, False), "in3": ("vol", 0.016387064, False), "in^3": ("vol", 0.016387064, False),
    "ft3": ("vol", 28.316846592, False), "ft^3": ("vol", 28.316846592, False), "yd3": ("vol", 764.554857984, False),
    "barrel": ("vol", 158.987294928, False), "bushel": ("vol", 35.23907016688, False),
    # information
    "bit": ("info", 1, True), "byte": ("info", 8, True),
}
_PREFIX = {"Y": 1e24, "Z": 1e21, "E": 1e18, "P": 1e15, "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "h": 1e2,
           "da": 1e1, "e": 1e1, "d": 1e-1, "c": 1e-2, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
           "a": 1e-18, "z": 1e-21, "y": 1e-24, "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40}
_TEMPS = {"C", "cel", "F", "fah", "K", "kel", "Rank", "Reau"}


def _unit(u: str):
    if u in _UNITS:
        return _UNITS[u][0], _UNITS[u][1]
    for plen in (2, 1):
        pre, base = u[:plen], u[plen:]
        if pre in _PREFIX and base in _UNITS and _UNITS[base][2]:
            cat, f, _metric = _UNITS[base]
            power = 2 if base.endswith(("2", "^2")) else 3 if base.endswith(("3", "^3")) else 1
            return cat, f * _PREFIX[pre] ** power
    raise NA


def _to_kelvin(v, u):
    return {"C": v + 273.15, "cel": v + 273.15, "F": (v - 32) * 5 / 9 + 273.15, "fah": (v - 32) * 5 / 9 + 273.15,
            "K": v, "kel": v, "Rank": v * 5 / 9, "Reau": v * 1.25 + 273.15}[u]


def _from_kelvin(k, u):
    return {"C": k - 273.15, "cel": k - 273.15, "F": (k - 273.15) * 9 / 5 + 32, "fah": (k - 273.15) * 9 / 5 + 32,
            "K": k, "kel": k, "Rank": k * 9 / 5, "Reau": (k - 273.15) * 0.8}[u]


@fn("CONVERT", 3, 3, cat="Engineering", syntax="CONVERT(number, from_unit, to_unit)")
def f_convert(number, from_unit, to_unit):
    v = _num(number)
    a, b = to_text(from_unit), to_text(to_unit)
    if a in _TEMPS or b in _TEMPS:
        if not (a in _TEMPS and b in _TEMPS):
            raise NA
        return _from_kelvin(_to_kelvin(v, a), b)
    ca, fa = _unit(a)
    cb, fb = _unit(b)
    if ca != cb:
        raise NA
    return v * fa / fb


# ============================================================================= text
for _name, _base in (("LEFTB", "LEFT"), ("RIGHTB", "RIGHT"), ("MIDB", "MID"), ("LENB", "LEN"), ("FINDB", "FIND"),
                     ("SEARCHB", "SEARCH"), ("REPLACEB", "REPLACE")):
    if _base in FUNCTIONS:
        _alias(_name, _base)


@fn("ASC", 1, 1, cat="Text")
def f_asc(text):
    out = []
    for ch in to_text(text):
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        elif o == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


@fn(("DBCS", "JIS"), 1, 1, cat="Text")
def f_dbcs(text):
    out = []
    for ch in to_text(text):
        o = ord(ch)
        if 0x21 <= o <= 0x7E:
            out.append(chr(o + 0xFEE0))
        elif ch == " ":
            out.append("\u3000")
        else:
            out.append(ch)
    return "".join(out)


fn("PHONETIC", 1, 1, cat="Text")(lambda ref: to_text(scalar(ref)))
fn("ENCODEURL", 1, 1, cat="Text")(lambda t: urllib.parse.quote(to_text(t), safe=""))


@fn("TEXTSPLIT", 2, 6, cat="Text",
    syntax="TEXTSPLIT(text, col_delimiter, [row_delimiter], [ignore_empty], [match_mode], [pad_with])")
def f_textsplit(text, col_delim, row_delim=MISSING, ignore_empty=MISSING, match_mode=MISSING, pad=MISSING):
    s = to_text(text)
    flags = re.I if int(_num(_opt(match_mode, 0))) == 1 else 0

    def delims(d):
        if d is MISSING or d is None:
            return None
        vals = [to_text(v) for v in iter_all(d) if v is not None and to_text(v) != ""]
        return re.compile("|".join(re.escape(v) for v in sorted(vals, key=len, reverse=True)), flags) if vals else None
    cd, rd = delims(col_delim), delims(row_delim)
    ign = to_bool(_opt(ignore_empty, False))
    rows = rd.split(s) if rd else [s]
    out = []
    for r in rows:
        cells = cd.split(r) if cd else [r]
        if ign:
            cells = [c for c in cells if c != ""]
        if ign and not cells:
            continue
        out.append(cells)
    width = max((len(r) for r in out), default=1)
    filler = NA if pad is MISSING else scalar(pad)
    return Array([r + [filler] * (width - len(r)) for r in out] or [[""]])


@fn("VALUETOTEXT", 1, 2, cat="Text")
def f_valuetotext(v, fmt=MISSING):
    x = scalar(v)
    strict = int(_num(_opt(fmt, 0))) == 1
    if isinstance(x, str):
        return f'"{x}"' if strict else x
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if x is None:
        return ""
    return to_text(x)


@fn("ARRAYTOTEXT", 1, 2, cat="Text")
def f_arraytotext(arr, fmt=MISSING):
    strict = int(_num(_opt(fmt, 0))) == 1
    rows = as_rows(arr)
    conv = lambda v: f_valuetotext(v, 1 if strict else 0)
    if strict:
        return "{" + ";".join(",".join(conv(v) for v in r) for r in rows) + "}"
    return ", ".join(conv(v) for r in rows for v in r)


def _regex(pattern, case):
    try:
        return re.compile(to_text(pattern), re.I if int(_num(_opt(case, 0))) == 1 else 0)
    except re.error:
        raise VALUE


@fn("REGEXTEST", 2, 3, cat="Text", syntax="REGEXTEST(text, pattern, [case_sensitivity])")
def f_regextest(text, pattern, case=MISSING):
    return _regex(pattern, case).search(to_text(text)) is not None


@fn("REGEXEXTRACT", 2, 4, cat="Text", syntax="REGEXEXTRACT(text, pattern, [return_mode], [case_sensitivity])")
def f_regexextract(text, pattern, mode=MISSING, case=MISSING):
    rx = _regex(pattern, case)
    s = to_text(text)
    md = int(_num(_opt(mode, 0)))
    if md == 1:
        found = [m.group(0) for m in rx.finditer(s)]
        if not found:
            raise NA
        return Array([[f] for f in found])
    m = rx.search(s)
    if not m:
        raise NA
    if md == 2:
        groups = m.groups() or (m.group(0),)
        return Array([[g if g is not None else "" for g in groups]])
    return m.group(0)


@fn("REGEXREPLACE", 3, 5, cat="Text", syntax="REGEXREPLACE(text, pattern, replacement, [occurrence], [case])")
def f_regexreplace(text, pattern, replacement, occurrence=MISSING, case=MISSING):
    rx = _regex(pattern, case)
    rep = re.sub(r"\$(\d+)", r"\\\1", to_text(replacement))
    occ = int(_num(_opt(occurrence, 0)))
    s = to_text(text)
    if occ == 0:
        return rx.sub(rep, s)
    matches = list(rx.finditer(s))
    if not matches or abs(occ) > len(matches):
        return s
    m = matches[occ - 1] if occ > 0 else matches[occ]
    return s[:m.start()] + m.expand(rep) + s[m.end():]


# ============================================================================= date
def _weekend_mask(weekend) -> set[int]:
    """Python weekday numbers (Mon=0) that are weekend days."""
    if weekend is MISSING or weekend is None:
        return {5, 6}
    w = scalar(weekend)
    if isinstance(w, str):
        if len(w) != 7 or any(ch not in "01" for ch in w) or w == "1111111":
            raise VALUE
        return {i for i, ch in enumerate(w) if ch == "1"}
    code = int(_num(w))
    table = {1: {5, 6}, 2: {6, 0}, 3: {0, 1}, 4: {1, 2}, 5: {2, 3}, 6: {3, 4}, 7: {4, 5},
             11: {6}, 12: {0}, 13: {1}, 14: {2}, 15: {3}, 16: {4}, 17: {5}}
    if code not in table:
        raise NUM
    return table[code]


@fn("NETWORKDAYS.INTL", 2, 4, cat="Date", syntax="NETWORKDAYS.INTL(start, end, [weekend], [holidays])")
def f_networkdays_intl(start, end, weekend=MISSING, holidays=MISSING):
    a, b = int(_num(start)), int(_num(end))
    sign = 1
    if a > b:
        a, b, sign = b, a, -1
    wk, hol = _weekend_mask(weekend), _holidays(holidays)
    base = _dt.date(1899, 12, 30)
    n = 0
    for d in range(a, b + 1):
        if (base + _dt.timedelta(days=d)).weekday() not in wk and d not in hol:
            n += 1
    return float(sign * n)


@fn("WORKDAY.INTL", 2, 4, cat="Date", syntax="WORKDAY.INTL(start, days, [weekend], [holidays])")
def f_workday_intl(start, days, weekend=MISSING, holidays=MISSING):
    d, k = int(_num(start)), int(_num(days))
    wk, hol = _weekend_mask(weekend), _holidays(holidays)
    base = _dt.date(1899, 12, 30)
    step = 1 if k >= 0 else -1
    left = abs(k)
    while left:
        d += step
        if (base + _dt.timedelta(days=d)).weekday() not in wk and d not in hol:
            left -= 1
    return float(d)


# ============================================================================= financial
def _pmt(rate, nper, pv, fv=0.0, when=0):
    if rate == 0:
        return -(pv + fv) / nper
    f = (1 + rate) ** nper
    return -(rate * (pv * f + fv)) / ((1 + rate * when) * (f - 1))


def _ipmt(rate, per, nper, pv, when=0):
    pmt = _pmt(rate, nper, pv, 0.0, when)
    if per == 1 and when == 1:
        return 0.0
    bal = pv * (1 + rate) ** (per - 1) + pmt * ((1 + rate) ** (per - 1) - 1) / rate * (1 + rate * when) if rate else \
        pv + pmt * (per - 1)
    ip = -bal * rate
    return ip / (1 + rate) if when == 1 else ip


@fn("CUMIPMT", 6, 6, cat="Financial", syntax="CUMIPMT(rate, nper, pv, start_period, end_period, type)")
def f_cumipmt(rate, nper, pv, start, end, kind):
    r, n, p = _num(rate), int(_num(nper)), _num(pv)
    s, e, t = int(_num(start)), int(_num(end)), int(_num(kind))
    if r <= 0 or n <= 0 or p <= 0 or s < 1 or e < s or e > n or t not in (0, 1):
        raise NUM
    return sum(_ipmt(r, k, n, p, t) for k in range(s, e + 1))


@fn("CUMPRINC", 6, 6, cat="Financial", syntax="CUMPRINC(rate, nper, pv, start_period, end_period, type)")
def f_cumprinc(rate, nper, pv, start, end, kind):
    r, n, p = _num(rate), int(_num(nper)), _num(pv)
    s, e, t = int(_num(start)), int(_num(end)), int(_num(kind))
    if r <= 0 or n <= 0 or p <= 0 or s < 1 or e < s or e > n or t not in (0, 1):
        raise NUM
    pmt = _pmt(r, n, p, 0.0, t)
    return sum(pmt - _ipmt(r, k, n, p, t) for k in range(s, e + 1))


def _cashflows(values, dates):
    vs = [float(v) for v in flatten([values])]
    ds = [float(to_number(v)) for v in iter_all(dates) if v is not None]
    if len(vs) != len(ds) or len(vs) < 2:
        raise NUM
    return vs, ds


@fn("XNPV", 3, 3, cat="Financial", syntax="XNPV(rate, values, dates)")
def f_xnpv(rate, values, dates):
    r = _num(rate)
    vs, ds = _cashflows(values, dates)
    if r <= -1:
        raise NUM
    return sum(v / (1 + r) ** ((d - ds[0]) / 365) for v, d in zip(vs, ds))


@fn("XIRR", 2, 3, cat="Financial", syntax="XIRR(values, dates, [guess])")
def f_xirr(values, dates, guess=MISSING):
    vs, ds = _cashflows(values, dates)
    if not any(v > 0 for v in vs) or not any(v < 0 for v in vs):
        raise NUM
    npv = lambda r: sum(v / (1 + r) ** ((d - ds[0]) / 365) for v, d in zip(vs, ds))
    r = _num(_opt(guess, 0.1))
    for _ in range(100):
        f = npv(r)
        df = sum(-(d - ds[0]) / 365 * v / (1 + r) ** ((d - ds[0]) / 365 + 1) for v, d in zip(vs, ds))
        if df == 0:
            break
        nr = r - f / df
        if nr <= -1:
            nr = (r - 1) / 2
        if abs(nr - r) < 1e-12:
            return nr
        r = nr
    lo, hi = -0.9999, 10.0
    if npv(lo) * npv(hi) > 0:
        raise NUM
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


@fn("MIRR", 3, 3, cat="Financial", syntax="MIRR(values, finance_rate, reinvest_rate)")
def f_mirr(values, finance, reinvest):
    vs = [float(v) for v in flatten([values])]
    fr, rr = _num(finance), _num(reinvest)
    n = len(vs)
    pos = sum(v * (1 + rr) ** (n - 1 - i) for i, v in enumerate(vs) if v > 0)
    neg = sum(v / (1 + fr) ** i for i, v in enumerate(vs) if v < 0)
    if pos == 0 or neg == 0:
        raise DIV0
    return (pos / -neg) ** (1 / (n - 1)) - 1


@fn("DB", 4, 5, cat="Financial", syntax="DB(cost, salvage, life, period, [month])")
def f_db(cost, salvage, life, period, month=MISSING):
    c, s, n, p = _num(cost), _num(salvage), _num(life), int(_num(period))
    m = int(_num(_opt(month, 12)))
    if c < 0 or s < 0 or n <= 0 or p < 1 or not 1 <= m <= 12 or p > n + 1:
        raise NUM
    rate = round(1 - (s / c) ** (1 / n), 3) if c > 0 else 0
    total = c * rate * m / 12
    if p == 1:
        return total
    for k in range(2, p + 1):
        dep = (c - total) * rate if k <= n else (c - total) * rate * (12 - m) / 12
        if k == p:
            return dep
        total += dep
    return 0.0


@fn("VDB", 5, 7, cat="Financial", syntax="VDB(cost, salvage, life, start, end, [factor], [no_switch])")
def f_vdb(cost, salvage, life, start, end, factor=MISSING, no_switch=MISSING):
    c, s, n = _num(cost), _num(salvage), _num(life)
    a, b = _num(start), _num(end)
    fac = _num(_opt(factor, 2))
    noswitch = to_bool(_opt(no_switch, False))
    if a < 0 or b < a or b > n or n <= 0 or c < s:
        raise NUM
    book, total, period = c, 0.0, 0

    def dep_in(per_book, remaining):
        ddb = per_book * fac / n
        if not noswitch:
            sl = (per_book - s) / remaining if remaining > 0 else 0
            ddb = max(ddb, sl)
        return max(0.0, min(ddb, per_book - s))
    deps = []
    for period in range(int(math.ceil(n))):
        d = dep_in(book, n - period)
        deps.append(d)
        book -= d
    for i, d in enumerate(deps):
        lo, hi = max(a, i), min(b, i + 1)
        if hi > lo:
            total += d * (hi - lo)
    return total


@fn("PDURATION", 3, 3, cat="Financial")
def f_pduration(rate, pv, fv):
    r, p, f = _num(rate), _num(pv), _num(fv)
    if r <= 0 or p <= 0 or f <= 0:
        raise NUM
    return (math.log(f) - math.log(p)) / math.log(1 + r)


@fn("RRI", 3, 3, cat="Financial")
def f_rri(nper, pv, fv):
    n, p, f = _num(nper), _num(pv), _num(fv)
    if n <= 0 or p == 0:
        raise NUM
    return (f / p) ** (1 / n) - 1


@fn("ISPMT", 4, 4, cat="Financial")
def f_ispmt(rate, per, nper, pv):
    r, k, n, p = _num(rate), _num(per), _num(nper), _num(pv)
    if n == 0:
        raise DIV0
    return p * r * (k / n - 1)


def _yf(settle, maturity, basis):
    return f_yearfrac(settle, maturity, basis)


def _bond_dates(settle, maturity):
    if _num(settle) >= _num(maturity):
        raise NUM


@fn("DISC", 4, 5, cat="Financial", syntax="DISC(settlement, maturity, pr, redemption, [basis])")
def f_disc(settle, maturity, pr, redemption, basis=MISSING):
    _bond_dates(settle, maturity)
    p, r = _num(pr), _num(redemption)
    if p <= 0 or r <= 0:
        raise NUM
    return (r - p) / r / _yf(settle, maturity, basis)


@fn("INTRATE", 4, 5, cat="Financial", syntax="INTRATE(settlement, maturity, investment, redemption, [basis])")
def f_intrate(settle, maturity, inv, redemption, basis=MISSING):
    _bond_dates(settle, maturity)
    i, r = _num(inv), _num(redemption)
    if i <= 0 or r <= 0:
        raise NUM
    return (r - i) / i / _yf(settle, maturity, basis)


@fn("RECEIVED", 4, 5, cat="Financial", syntax="RECEIVED(settlement, maturity, investment, discount, [basis])")
def f_received(settle, maturity, inv, disc, basis=MISSING):
    _bond_dates(settle, maturity)
    i, d = _num(inv), _num(disc)
    if i <= 0 or d <= 0:
        raise NUM
    den = 1 - d * _yf(settle, maturity, basis)
    if den <= 0:
        raise NUM
    return i / den


@fn("PRICEDISC", 4, 5, cat="Financial", syntax="PRICEDISC(settlement, maturity, discount, redemption, [basis])")
def f_pricedisc(settle, maturity, disc, redemption, basis=MISSING):
    _bond_dates(settle, maturity)
    d, r = _num(disc), _num(redemption)
    if d <= 0 or r <= 0:
        raise NUM
    return r - d * r * _yf(settle, maturity, basis)


@fn("YIELDDISC", 4, 5, cat="Financial", syntax="YIELDDISC(settlement, maturity, pr, redemption, [basis])")
def f_yielddisc(settle, maturity, pr, redemption, basis=MISSING):
    _bond_dates(settle, maturity)
    p, r = _num(pr), _num(redemption)
    if p <= 0 or r <= 0:
        raise NUM
    return (r - p) / p / _yf(settle, maturity, basis)


def _tbill_days(settle, maturity):
    s, m = int(_num(settle)), int(_num(maturity))
    if s >= m or m - s > 366:
        raise NUM
    return m - s


@fn("TBILLPRICE", 3, 3, cat="Financial")
def f_tbillprice(settle, maturity, disc):
    d = _num(disc)
    if d <= 0:
        raise NUM
    return 100 * (1 - d * _tbill_days(settle, maturity) / 360)


@fn("TBILLYIELD", 3, 3, cat="Financial")
def f_tbillyield(settle, maturity, pr):
    p = _num(pr)
    if p <= 0:
        raise NUM
    return (100 - p) / p * 360 / _tbill_days(settle, maturity)


@fn("TBILLEQ", 3, 3, cat="Financial")
def f_tbilleq(settle, maturity, disc):
    d = _num(disc)
    days = _tbill_days(settle, maturity)
    if d <= 0:
        raise NUM
    if days <= 182:
        return 365 * d / (360 - d * days)
    price = 100 * (1 - d * days / 360)
    b = days / 365
    return (-2 * b + 2 * math.sqrt(b * b - (2 * b - 1) * (1 - 100 / price))) / (2 * b - 1)


@fn("DOLLARDE", 2, 2, cat="Financial")
def f_dollarde(dollar, fraction):
    d, f = _num(dollar), int(_num(fraction))
    if f < 0:
        raise NUM
    if f == 0:
        raise DIV0
    ip = math.trunc(d)
    digits = math.ceil(math.log10(f)) if f > 1 else 1
    return ip + (d - ip) * 10 ** digits / f


@fn("DOLLARFR", 2, 2, cat="Financial")
def f_dollarfr(dollar, fraction):
    d, f = _num(dollar), int(_num(fraction))
    if f < 0:
        raise NUM
    if f == 0:
        raise DIV0
    ip = math.trunc(d)
    digits = math.ceil(math.log10(f)) if f > 1 else 1
    return ip + (d - ip) * f / 10 ** digits


@fn("FVSCHEDULE", 2, 2, cat="Financial")
def f_fvschedule(principal, schedule):
    v = _num(principal)
    for r in iter_all(schedule):
        if r is not None:
            v *= 1 + to_number(r)
    return v


@fn("ACCRINTM", 4, 5, cat="Financial", syntax="ACCRINTM(issue, settlement, rate, par, [basis])")
def f_accrintm(issue, settle, rate, par, basis=MISSING):
    r, p = _num(rate), _num(par)
    if r <= 0 or p <= 0 or _num(issue) >= _num(settle):
        raise NUM
    return p * r * _yf(issue, settle, basis)


# ============================================================================= lookup & dynamic arrays
def _rows(arg) -> list[list]:
    return [list(r) for r in as_rows(arg)]


@fn("TAKE", 2, 3, cat="Lookup", syntax="TAKE(array, rows, [columns])")
def f_take(arr, rows, cols=MISSING):
    data = _rows(arr)
    r = int(_num(rows)) if rows is not MISSING and rows is not None else len(data)
    if r == 0:
        raise VALUE
    data = data[:r] if r > 0 else data[r:]
    if cols is not MISSING and cols is not None:
        c = int(_num(cols))
        if c == 0:
            raise VALUE
        data = [row[:c] if c > 0 else row[c:] for row in data]
    return Array(data)


@fn("DROP", 2, 3, cat="Lookup", syntax="DROP(array, rows, [columns])")
def f_drop(arr, rows, cols=MISSING):
    data = _rows(arr)
    r = int(_num(rows)) if rows is not MISSING and rows is not None else 0
    data = data[r:] if r >= 0 else data[:r]
    if cols is not MISSING and cols is not None:
        c = int(_num(cols))
        data = [row[c:] if c >= 0 else row[:c] for row in data]
    if not data or not data[0]:
        raise VALUE
    return Array(data)


def _pick(n: int, size: int) -> int:
    if n == 0 or abs(n) > size:
        raise VALUE
    return n - 1 if n > 0 else size + n


@fn("CHOOSECOLS", 2, None, cat="Lookup", syntax="CHOOSECOLS(array, col_num1, [col_num2], ...)")
def f_choosecols(arr, *nums):
    data = _rows(arr)
    width = len(data[0]) if data else 0
    idx = [_pick(int(v), width) for n in nums for v in flatten([n])]
    return Array([[row[i] for i in idx] for row in data])


@fn("CHOOSEROWS", 2, None, cat="Lookup", syntax="CHOOSEROWS(array, row_num1, [row_num2], ...)")
def f_chooserows(arr, *nums):
    data = _rows(arr)
    idx = [_pick(int(v), len(data)) for n in nums for v in flatten([n])]
    return Array([list(data[i]) for i in idx])


@fn("VSTACK", 1, None, cat="Lookup")
def f_vstack(*arrays):
    blocks = [_rows(a) for a in arrays]
    width = max((len(r) for b in blocks for r in b), default=1)
    return Array([r + [NA] * (width - len(r)) for b in blocks for r in b])


@fn("HSTACK", 1, None, cat="Lookup")
def f_hstack(*arrays):
    blocks = [_rows(a) for a in arrays]
    height = max(len(b) for b in blocks)
    out = []
    for i in range(height):
        row = []
        for b in blocks:
            w = max((len(r) for r in b), default=1)
            row += (b[i] + [NA] * (w - len(b[i]))) if i < len(b) else [NA] * w
        out.append(row)
    return Array(out)


def _flat_values(arr, ignore, by_col):
    data = _rows(arr)
    if by_col:
        data = [list(c) for c in zip(*data)] if data else []
    ig = int(_num(_opt(ignore, 0)))
    out = []
    for row in data:
        for v in row:
            if (ig in (1, 3) and v is None) or (ig in (2, 3) and isinstance(v, XLError)):
                continue
            out.append(v if v is not None else 0.0)
    return out


@fn("TOCOL", 1, 3, cat="Lookup", syntax="TOCOL(array, [ignore], [scan_by_column])")
def f_tocol(arr, ignore=MISSING, by_col=MISSING):
    vals = _flat_values(arr, ignore, to_bool(_opt(by_col, False)))
    return Array([[v] for v in vals] or [[NA]])


@fn("TOROW", 1, 3, cat="Lookup", syntax="TOROW(array, [ignore], [scan_by_column])")
def f_torow(arr, ignore=MISSING, by_col=MISSING):
    vals = _flat_values(arr, ignore, to_bool(_opt(by_col, False)))
    return Array([vals or [NA]])


def _wrap(vector, count, pad, rows_first: bool):
    vals = [v for v in iter_all(vector)]
    n = int(_num(count))
    if n < 1:
        raise NUM
    filler = NA if pad is MISSING else scalar(pad)
    chunks = [vals[i:i + n] for i in range(0, len(vals), n)]
    chunks = [c + [filler] * (n - len(c)) for c in chunks]
    return Array(chunks if rows_first else [list(c) for c in zip(*chunks)])


fn("WRAPROWS", 2, 3, cat="Lookup", syntax="WRAPROWS(vector, wrap_count, [pad_with])")(
    lambda v, n, pad=MISSING: _wrap(v, n, pad, True))
fn("WRAPCOLS", 2, 3, cat="Lookup", syntax="WRAPCOLS(vector, wrap_count, [pad_with])")(
    lambda v, n, pad=MISSING: _wrap(v, n, pad, False))


@fn("EXPAND", 2, 4, cat="Lookup", syntax="EXPAND(array, rows, [columns], [pad_with])")
def f_expand(arr, rows, cols=MISSING, pad=MISSING):
    data = _rows(arr)
    r = int(_num(rows)) if rows is not MISSING and rows is not None else len(data)
    w = max(len(x) for x in data) if data else 1
    c = int(_num(cols)) if cols is not MISSING and cols is not None else w
    if r < len(data) or c < w:
        raise VALUE
    filler = NA if pad is MISSING else scalar(pad)
    out = [row + [filler] * (c - len(row)) for row in data]
    out += [[filler] * c for _ in range(r - len(data))]
    return Array(out)


@fn("SORTBY", 2, None, cat="Lookup", syntax="SORTBY(array, by_array1, [sort_order1], ...)")
def f_sortby(arr, *pairs):
    from app.sheet.formula.values import compare

    data = _rows(arr)
    keys = []
    i = 0
    while i < len(pairs):
        by = [v for v in iter_all(pairs[i])]
        order = int(_num(pairs[i + 1])) if i + 1 < len(pairs) and pairs[i + 1] is not MISSING else 1
        if len(by) != len(data):
            raise VALUE
        keys.append((by, order))
        i += 2
    idx = list(range(len(data)))
    import functools

    def cmp(a, b):
        for by, order in keys:
            c = compare(by[a], by[b])
            if c:
                return c * order
        return 0
    idx.sort(key=functools.cmp_to_key(cmp))
    return Array([data[i] for i in idx])


@fn("AREAS", 1, 1, cat="Lookup")
def f_areas(ref):
    if isinstance(ref, (list, tuple)):
        return float(len(ref))
    if isinstance(ref, RangeRef):
        return 1.0
    raise VALUE


# ============================================================================= database
def _db_columns(database):
    rows = as_rows(database)
    if len(rows) < 2:
        raise VALUE
    header = [to_text(h).strip().lower() if h is not None else "" for h in rows[0]]
    return header, rows[1:]


def _db_field(header, field) -> int:
    f = scalar(field)
    if isinstance(f, (int, float)) and not isinstance(f, bool):
        i = int(f) - 1
        if not 0 <= i < len(header):
            raise VALUE
        return i
    name = to_text(f).strip().lower()
    if name not in header:
        raise VALUE
    return header.index(name)


def _db_rows(database, criteria):
    header, records = _db_columns(database)
    crit = as_rows(criteria)
    if len(crit) < 2:
        raise VALUE
    chead = [to_text(h).strip().lower() if h is not None else "" for h in crit[0]]
    tests = []  # OR of rows, AND within a row
    for row in crit[1:]:
        conds = []
        for name, value in zip(chead, row):
            if value is None or value == "" or not name:
                continue
            if name not in header:
                raise VALUE
            conds.append((header.index(name), make_criteria(value)))
        tests.append(conds)
    if not any(tests):
        return header, records
    return header, [r for r in records if any(all(pred(r[i] if i < len(r) else None) for i, pred in t)
                                              for t in tests if t)]


def _db_values(database, field, criteria, numeric=True):
    header, recs = _db_rows(database, criteria)
    i = _db_field(header, field)
    vals = [r[i] if i < len(r) else None for r in recs]
    if numeric:
        return [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return vals


def _db(name, agg, numeric=True):
    fn(name, 3, 3, cat="Database", syntax=f"{name}(database, field, criteria)")(
        lambda database, field, criteria: agg(_db_values(database, field, criteria, numeric)))


def _need(vals, k=1):
    if len(vals) < k:
        raise DIV0
    return vals


_db("DSUM", lambda v: float(sum(v)))
_db("DAVERAGE", lambda v: sum(_need(v)) / len(v))
_db("DCOUNT", lambda v: float(len(v)))
_db("DCOUNTA", lambda v: float(sum(1 for x in v if x is not None and x != "")), numeric=False)
_db("DMAX", lambda v: max(v) if v else 0.0)
_db("DMIN", lambda v: min(v) if v else 0.0)
_db("DPRODUCT", lambda v: math.prod(v) if v else 0.0)
_db("DSTDEV", lambda v: math.sqrt(_var(_need(v, 2), True)))
_db("DSTDEVP", lambda v: math.sqrt(_var(_need(v), False)))
_db("DVAR", lambda v: _var(_need(v, 2), True))
_db("DVARP", lambda v: _var(_need(v), False))


@fn("DGET", 3, 3, cat="Database", syntax="DGET(database, field, criteria)")
def f_dget(database, field, criteria):
    vals = _db_values(database, field, criteria, numeric=False)
    if not vals:
        raise VALUE
    if len(vals) > 1:
        raise NUM
    return vals[0]


# ============================================================================= information
@fn("INFO", 1, 1, cat="Info")
def f_info(kind):
    import platform

    k = to_text(kind).lower()
    if k == "osversion":
        return f"{platform.system()} {platform.release()}"
    if k == "system":
        return "pcdos" if platform.system() == "Windows" else "mac" if platform.system() == "Darwin" else "unix"
    if k == "release":
        from app.version import APP_VERSION

        return APP_VERSION
    if k == "recalc":
        return "Automatic"
    if k == "numfile":
        return 1.0
    if k == "origin":
        return "$A:$A$1"
    raise VALUE


@fn("CELL", 1, 2, cat="Info", syntax="CELL(info_type, [reference])")
def f_cell(kind, ref=MISSING):
    k = to_text(kind).lower()
    if not isinstance(ref, RangeRef):
        raise VALUE
    from app.sheet.formula.values import deref

    if k == "row":
        return float(ref.r1)
    if k in ("col", "column"):
        return float(ref.c1)
    if k == "address":
        col, n = "", ref.c1
        while n:
            n, rem = divmod(n - 1, 26)
            col = chr(65 + rem) + col
        return f"${col}${ref.r1}"
    if k == "contents":
        return deref(ref)
    if k == "type":
        v = deref(ref)
        return "b" if v is None else "l" if isinstance(v, str) else "v"
    raise VALUE


__all__ = ["datetime_to_serial", "_date_from"]
