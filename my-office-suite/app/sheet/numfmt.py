"""Excel number-format rendering (``0.00``, ``#,##0``, ``0%``, ``yyyy-mm-dd``, ``[Red]``, sections ...)."""
from __future__ import annotations

import datetime as _dt
import math
import re
from fractions import Fraction
from functools import lru_cache

from app.sheet.formula.values import XLError, serial_to_datetime

COLORS = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#00a000", "blue": "#0000ff",
          "yellow": "#c0a000", "magenta": "#ff00ff", "cyan": "#00a0a0"}

BUILTIN_FORMATS = {
    "General": "General", "Number": "0.00", "Number (no decimals)": "0", "Thousands": "#,##0",
    "Thousands (2 dp)": "#,##0.00", "Currency": "$#,##0.00", "Rupee": "\"Rs\" #,##0.00",
    "Accounting": "_($* #,##0.00_);_($* (#,##0.00);_($* \"-\"??_);_(@_)", "Percent": "0%", "Percent (2 dp)": "0.00%",
    "Scientific": "0.00E+00", "Fraction": "# ?/?", "Short date": "yyyy-mm-dd", "Long date": "dddd, mmmm d, yyyy",
    "Date (d/m/y)": "dd/mm/yyyy", "Time": "h:mm:ss", "Time (AM/PM)": "h:mm AM/PM", "Date & time": "yyyy-mm-dd hh:mm",
    "Text": "@",
}
DATE_DEFAULT = "yyyy-mm-dd"


def xl_round(v: float, decimals: int) -> float:
    """Round half away from zero like Excel (on the shortest decimal representation)."""
    from decimal import ROUND_HALF_UP, Decimal

    try:
        return float(Decimal(repr(v)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))
    except Exception:
        return round(v, decimals)


def general(v: float) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if v != v:
        return "#NUM!"
    if v in (math.inf, -math.inf):
        return "#NUM!"
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e11 or a < 1e-9:
        s = f"{v:.5E}"
        mant, exp = s.split("E")
        mant = mant.rstrip("0").rstrip(".")
        e = int(exp)
        return f"{mant}E{'+' if e >= 0 else '-'}{abs(e):02d}"
    if v == int(v):
        return str(int(v))
    int_digits = len(str(int(a))) if a >= 1 else 1
    decimals = max(0, 10 - int_digits)
    s = f"{v:.{decimals}f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _split_sections(fmt: str) -> list[str]:
    out, cur, i, q, br = [], "", 0, False, False
    while i < len(fmt):
        ch = fmt[i]
        if ch == '"' and not br:
            q = not q
        elif ch == "\\" and not q and i + 1 < len(fmt):
            cur += ch + fmt[i + 1]
            i += 2
            continue
        elif ch == "[" and not q:
            br = True
        elif ch == "]" and not q:
            br = False
        if ch == ";" and not q and not br:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out


_DATE_CHARS = re.compile(r'(?:"[^"]*"|\\.|\[[^\]]*\])|([ymdhsYMDHS])')


def _is_date(section: str) -> bool:
    for m in _DATE_CHARS.finditer(section):
        if m.group(1):
            return True
    return bool(re.search(r"\[(h+|m+|s+)\]", section, re.I))


@lru_cache(maxsize=512)
def _parse(fmt: str):
    sections = _split_sections(fmt)
    parsed = []
    for s in sections:
        color = None
        cond = None
        body = s
        for m in re.finditer(r"\[([^\]]+)\]", s):
            tag = m.group(1)
            low = tag.lower()
            if low in COLORS:
                color = COLORS[low]
                body = body.replace(m.group(0), "", 1)
            elif low.startswith("color"):
                color = "#000000"
                body = body.replace(m.group(0), "", 1)
            elif re.fullmatch(r"(<=|>=|<>|<|>|=)-?[0-9.]+", tag):
                cond = tag
                body = body.replace(m.group(0), "", 1)
            elif tag.startswith("$"):  # locale/currency [$Rs-420]
                sym = tag[1:].split("-")[0]
                body = body.replace(m.group(0), f'"{sym}"', 1)
        parsed.append((body, color, cond, _is_date(body)))
    return parsed


def _cond_ok(cond: str, v: float) -> bool:
    m = re.fullmatch(r"(<=|>=|<>|<|>|=)(-?[0-9.]+)", cond)
    op, n = m.group(1), float(m.group(2))
    return {"<": v < n, ">": v > n, "=": v == n, "<=": v <= n, ">=": v >= n, "<>": v != n}[op]


def format_value(value, fmt: str | None) -> tuple[str, str | None]:
    """Return (text, colour or None)."""
    if value is None:
        return "", None
    if isinstance(value, XLError):
        return value.code, None
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), None
    if isinstance(value, (_dt.datetime, _dt.date)):
        from app.sheet.formula.values import datetime_to_serial

        value = datetime_to_serial(value)
        if not fmt or fmt == "General":
            fmt = DATE_DEFAULT
    fmt = fmt or "General"
    if isinstance(value, str):
        secs = _parse(fmt)
        if len(secs) >= 4:
            body, color, _c, _d = secs[3]
            return _literal_text(body, value), color
        if len(secs) == 1 and "@" in secs[0][0]:
            return _literal_text(secs[0][0], value), secs[0][1]
        return value, None
    if not isinstance(value, (int, float)):
        return str(value), None
    v = float(value)
    if fmt == "General":
        return general(v), None
    secs = _parse(fmt)
    # choose section
    explicit_cond = any(c for _b, _col, c, _d in secs[:2])
    if explicit_cond:
        chosen = None
        for body, color, cond, is_date in secs[:3]:
            if cond and _cond_ok(cond, v):
                chosen = (body, color, is_date, False)
                break
        if chosen is None:
            body, color, cond, is_date = secs[min(len(secs) - 1, 2 if len(secs) > 2 else len(secs) - 1)]
            chosen = (body, color, is_date, v < 0)
        body, color, is_date, need_minus = chosen
    elif v > 0 or (v == 0 and len(secs) < 3) or len(secs) == 1:
        body, color, _c, is_date = secs[0]
        need_minus = v < 0
    elif v < 0:
        body, color, _c, is_date = secs[1]
        need_minus = False
        v = -v
    else:
        body, color, _c, is_date = secs[2]
        need_minus = False
    if body.strip().lower() == "general":
        return ("-" if need_minus else "") + general(abs(v) if need_minus else v), color
    if is_date:
        try:
            return _format_date(body, v), color
        except XLError:
            return "#" * 8, color
    text = _format_number(body, abs(v) if need_minus else v)
    if need_minus and re.search(r"[1-9]", text):
        text = "-" + text
    return text, color


def _literal_text(body: str, text: str) -> str:
    out, i, q = "", 0, False
    while i < len(body):
        ch = body[i]
        if ch == '"':
            q = not q
        elif q:
            out += ch
        elif ch == "\\" and i + 1 < len(body):
            out += body[i + 1]
            i += 1
        elif ch == "@":
            out += text
        elif ch == "_":
            out += " "
            i += 1
        elif ch == "*":
            i += 1
        else:
            out += ch
        i += 1
    return out


def _tokens(body: str):
    """Split a number format body into (kind, text) where kind is 'lit' or 'code'."""
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            j = body.find('"', i + 1)
            j = len(body) if j < 0 else j
            out.append(("lit", body[i + 1:j]))
            i = j + 1
        elif ch == "\\" and i + 1 < len(body):
            out.append(("lit", body[i + 1]))
            i += 2
        elif ch == "_" and i + 1 < len(body):
            out.append(("lit", " "))
            i += 2
        elif ch == "*" and i + 1 < len(body):
            out.append(("lit", body[i + 1]))
            i += 2
        elif ch in "+-" and not (out and out[-1] == ("code", "E") or out and out[-1] == ("code", "e")):
            out.append(("lit", ch))
            i += 1
        elif ch in "Ee" and not (i + 1 < len(body) and body[i + 1] in "+-"):
            out.append(("lit", ch))
            i += 1
        elif ch in "0#?.,%Ee+-/":
            out.append(("code", ch))
            i += 1
        elif ch == "@":
            out.append(("lit", ""))
            i += 1
        else:
            out.append(("lit", ch))
            i += 1
    return out


def _format_number(body: str, v: float) -> str:
    toks = _tokens(body)
    codes = "".join(t for k, t in toks if k == "code")
    if not codes.strip(",.%+-/") and not any(c in codes for c in "0#?"):
        return "".join(t for _k, t in toks)
    if "%" in codes:
        v *= 100 ** codes.count("%")
    if "/" in codes and any(c in codes for c in "?#0"):
        return _format_fraction(toks, v)
    sci = re.search(r"[Ee][+-]", codes)
    # trailing thousands scaling: "#,##0,," divides by 1e6
    m = re.search(r"([0#?])(,+)(?![0#?])", codes)
    if m and not sci:
        v /= 1000 ** len(m.group(2))
    int_part_codes, _, frac_codes = codes.split("E")[0].split("e")[0].partition(".")
    decimals = sum(1 for c in frac_codes if c in "0#?")
    min_decimals = sum(1 for c in frac_codes if c == "0")
    thousands = "," in int_part_codes.strip(",") and bool(re.search(r"[0#?],[0#?]", int_part_codes))
    min_int = sum(1 for c in int_part_codes if c == "0")
    if sci:
        exp = 0 if v == 0 else int(math.floor(math.log10(abs(v))))
        mant = v / (10 ** exp) if v else 0.0
        mant = xl_round(mant, decimals)
        if abs(mant) >= 10:
            mant /= 10
            exp += 1
        mant_s = f"{mant:.{decimals}f}"
        exp_digits = len(re.search(r"[Ee][+-]([0#]+)", codes).group(1)) if re.search(r"[Ee][+-]([0#]+)", codes) else 2
        sign = "+" if exp >= 0 else "-"
        if re.search(r"[Ee]-", codes) and exp >= 0:
            sign = ""
        num = f"{mant_s}E{sign}{abs(exp):0{exp_digits}d}"
    else:
        num = f"{xl_round(v + 0.0, decimals):.{decimals}f}"
        ip, _, fp = num.partition(".")
        if fp and decimals > min_decimals:
            fp = fp.rstrip("0")
            fp = fp + "0" * max(0, min_decimals - len(fp))
        if ip == "0" and min_int == 0:
            ip = ""
        if thousands and ip:
            ip = f"{int(ip):,}"
        ip = ip.zfill(min_int) if min_int > len(ip) else ip
        num = ip + ("." + fp if fp or "." in frac_codes or (decimals and fp) else ("." if body.count(".") and decimals == 0 and "." in codes else ""))
        if not num:
            num = "0" if min_int else ""
    # rebuild with literals: literals before the first code and after the last code
    first = next((i for i, (k, t) in enumerate(toks) if k == "code" and t in "0#?."), None)
    last = max((i for i, (k, t) in enumerate(toks) if k == "code"), default=None)
    prefix = "".join(t for k, t in toks[:first] if k == "lit") if first is not None else ""
    suffix = "".join(t if k == "lit" else ("%" if t == "%" else "") for k, t in toks[(last + 1 if last is not None else 0):])
    pct_inside = "%" if "%" in codes and not suffix.count("%") else ""
    middle_lits = ""
    if first is not None and last is not None:
        middle_lits = "".join(t for k, t in toks[first:last + 1] if k == "lit")
    return prefix + num + middle_lits + pct_inside + suffix


def _format_fraction(toks, v: float) -> str:
    codes = "".join(t for k, t in toks if k == "code")
    den_part = codes.split("/")[1]
    den_digits = sum(1 for c in den_part if c in "?#0") or 1
    fixed = re.search(r"/\s*([0-9]+)", "".join(t for _k, t in toks))
    whole_part = bool(re.match(r"[#0?]+\s*[#0?]+\s*/", codes.replace(" ", " ")))
    sign = "-" if v < 0 else ""
    v = abs(v)
    whole = int(v) if whole_part else 0
    frac = v - whole
    if fixed:
        den = int(fixed.group(1))
        num = round(frac * den)
    else:
        f = Fraction(frac).limit_denominator(10 ** den_digits - 1)
        num, den = f.numerator, f.denominator
    if num == den and den:
        whole += 1
        num = 0
    if num == 0:
        return f"{sign}{whole}" if whole_part else f"{sign}0"
    return f"{sign}{str(whole) + ' ' if whole_part and whole else ''}{num}/{den}"


_DATE_TOKEN = re.compile(r'"[^"]*"|\\.|\[[hms]+\]|yyyy|yy|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|AM/PM|am/pm|A/P|a/p|\.0+|.',
                         re.I)


def _format_date(body: str, v: float) -> str:
    dt = serial_to_datetime(v)
    toks = [t for t in _DATE_TOKEN.findall(body)]
    ampm = any(t.upper() in ("AM/PM", "A/P") for t in toks)
    out = []
    for i, t in enumerate(toks):
        low = t.lower()
        if t.startswith('"'):
            out.append(t[1:-1])
        elif t.startswith("\\"):
            out.append(t[1:])
        elif low.startswith("[h"):
            out.append(str(int(v * 24)).zfill(len(low) - 2))
        elif low.startswith("[m"):
            out.append(str(int(round(v * 1440))).zfill(len(low) - 2))
        elif low.startswith("[s"):
            out.append(str(int(round(v * 86400))).zfill(len(low) - 2))
        elif low == "yyyy":
            out.append(f"{dt.year:04d}")
        elif low == "yy":
            out.append(f"{dt.year % 100:02d}")
        elif low in ("mm", "m"):
            prev = next((x.lower().lstrip("[") for x in reversed(toks[:i]) if x.strip("[ ") and x.lower().lstrip("[")[0] in "hsdy"), "")
            nxt = next((x.lower().lstrip("[") for x in toks[i + 1:] if x.strip("[ ") and x.lower().lstrip("[")[0] in "hsdy"), "")
            minute = prev.startswith("h") or nxt.startswith("s")
            val = dt.minute if minute else dt.month
            out.append(f"{val:02d}" if low == "mm" else str(val))
        elif low == "mmm":
            out.append(dt.strftime("%b"))
        elif low == "mmmm":
            out.append(dt.strftime("%B"))
        elif low == "mmmmm":
            out.append(dt.strftime("%B")[0])
        elif low == "dddd":
            out.append(dt.strftime("%A"))
        elif low == "ddd":
            out.append(dt.strftime("%a"))
        elif low == "dd":
            out.append(f"{dt.day:02d}")
        elif low == "d":
            out.append(str(dt.day))
        elif low in ("hh", "h"):
            h = dt.hour
            if ampm:
                h = h % 12 or 12
            out.append(f"{h:02d}" if low == "hh" else str(h))
        elif low in ("ss", "s"):
            out.append(f"{dt.second:02d}" if low == "ss" else str(dt.second))
        elif low == "am/pm":
            out.append("AM" if dt.hour < 12 else "PM")
        elif low == "a/p":
            out.append("A" if dt.hour < 12 else "P")
        elif low.startswith("."):
            frac = (v * 86400) % 1
            out.append(("." + f"{frac:.{len(low) - 1}f}"[2:]))
        else:
            out.append(t)
    return "".join(out)


def is_date_format(fmt: str | None) -> bool:
    if not fmt or fmt == "General":
        return False
    return any(d for _b, _c, _k, d in _parse(fmt)[:1])


def is_percent_format(fmt: str | None) -> bool:
    return bool(fmt) and "%" in fmt.replace('"%"', "")
