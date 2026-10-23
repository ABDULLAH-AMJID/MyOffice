"""Equations: LaTeX <-> Office Math (OMML) and rendering.

* In MyOffice an equation is an inline picture rendered from LaTeX (matplotlib mathtext) that remembers its LaTeX.
* Saved to DOCX it becomes a real Office Math object (``m:oMath``), editable in Word and LibreOffice:
  LaTeX -> MathML (latex2mathml, MIT) -> OMML (the mapping below).
* Equations in Word documents are read back as LaTeX (OMML -> LaTeX below) and rendered the same way.
"""
from __future__ import annotations

import io
import logging
import re

from lxml import etree

log = logging.getLogger(__name__)

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MML_NS = "http://www.w3.org/1998/Math/MathML"


def m(tag: str) -> str:
    return f"{{{M_NS}}}{tag}"


class EquationError(ValueError):
    pass


# ============================================================================ rendering
def render_png(latex: str, fontsize: float = 12, dpi: int = 192, color: str = "black") -> bytes:
    """PNG bytes of the rendered equation (transparent background)."""
    latex = latex.strip()
    if not latex:
        raise EquationError("The equation is empty.")
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties

    buf = io.BytesIO()
    try:
        mathtext.math_to_image(f"${latex}$", buf, prop=FontProperties(size=fontsize), dpi=dpi, format="png",
                               color=color)
    except Exception as exc:  # ValueError from the mathtext parser
        msg = str(exc).strip().splitlines()
        raise EquationError("This LaTeX cannot be shown: " + (msg[-1] if msg else "syntax error")) from exc
    return buf.getvalue()


# ============================================================================ LaTeX -> OMML
LARGE_OPS = {"\u2211": "\u2211", "\u220f": "\u220f", "\u222b": "\u222b", "\u222c": "\u222c", "\u222d": "\u222d",
             "\u222e": "\u222e", "\u22c3": "\u22c3", "\u22c2": "\u22c2", "\u2210": "\u2210"}
ACCENTS = {"^": "\u0302", "\u02c6": "\u0302", "~": "\u0303", "\u02dc": "\u0303", "\u00af": "\u0305",
           "\u2192": "\u20d7", "\u02d9": "\u0307", "\u00a8": "\u0308", "\u02c7": "\u030c", "\u2015": "\u0305",
           "\u203e": "\u0305", "_": "\u0305", "\u0302": "\u0302", "\u0303": "\u0303", "\u20d7": "\u20d7"}


def latex_to_omml(latex: str) -> etree._Element:
    """An ``m:oMath`` element for ``latex``."""
    import latex2mathml.converter

    try:
        mathml = latex2mathml.converter.convert(latex)
    except Exception as exc:
        raise EquationError(f"LaTeX not understood: {exc}") from exc
    root = etree.fromstring(mathml.encode("utf-8"))
    omath = etree.Element(m("oMath"), nsmap={"m": M_NS, "w": W_NS})
    _append_seq(omath, list(root))
    return omath


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _run(text: str, plain: bool = False) -> etree._Element:
    r = etree.Element(m("r"))
    if plain:
        rpr = etree.SubElement(r, m("rPr"))
        etree.SubElement(rpr, m("sty")).set(m("val"), "p")
    t = etree.SubElement(r, m("t"))
    t.text = text
    if text != text.strip():
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _container(tag: str, children: list) -> etree._Element:
    el = etree.Element(m(tag))
    _append_seq(el, children)
    return el


def _text(el) -> str:
    return "".join(el.itertext())


def _is_large_op(el) -> bool:
    name = _local(el)
    if name in ("munderover", "msubsup", "munder", "mover", "msub", "msup"):
        base = el[0] if len(el) else None
        return base is not None and _local(base) == "mo" and _text(base).strip() in LARGE_OPS
    return name == "mo" and _text(el).strip() in LARGE_OPS


def _append_seq(parent, children: list) -> None:
    i = 0
    while i < len(children):
        el = children[i]
        if _is_large_op(el):
            nxt = children[i + 1] if i + 1 < len(children) else None
            parent.append(_nary(el, nxt))
            i += 2 if nxt is not None else 1
            continue
        for out in _convert(el):
            parent.append(out)
        i += 1


def _nary(el, operand) -> etree._Element:
    name = _local(el)
    if name == "mo":
        op, sub, sup = _text(el).strip(), None, None
    else:
        op = _text(el[0]).strip()
        if name in ("munderover", "msubsup"):
            sub, sup = el[1], el[2] if len(el) > 2 else None
        elif name in ("munder", "msub"):
            sub, sup = el[1], None
        else:
            sub, sup = None, el[1]
    nary = etree.Element(m("nary"))
    pr = etree.SubElement(nary, m("naryPr"))
    etree.SubElement(pr, m("chr")).set(m("val"), op)
    if op in ("\u222b", "\u222c", "\u222d", "\u222e"):
        etree.SubElement(pr, m("limLoc")).set(m("val"), "subSup")
    if sub is None:
        etree.SubElement(pr, m("subHide")).set(m("val"), "1")
    if sup is None:
        etree.SubElement(pr, m("supHide")).set(m("val"), "1")
    nary.append(_container("sub", [sub] if sub is not None else []))
    nary.append(_container("sup", [sup] if sup is not None else []))
    nary.append(_container("e", [operand] if operand is not None else []))
    return nary


def _fence(children: list):
    """mrow( mo-fence ... mo-fence ) -> m:d"""
    if len(children) >= 2 and _local(children[0]) == "mo" and _local(children[-1]) == "mo" and (
            children[0].get("fence") == "true" or children[0].get("stretchy") == "true") and (
            children[-1].get("fence") == "true" or children[-1].get("stretchy") == "true"):
        beg, end = _text(children[0]).strip(), _text(children[-1]).strip()
        d = etree.Element(m("d"))
        pr = etree.SubElement(d, m("dPr"))
        etree.SubElement(pr, m("begChr")).set(m("val"), beg)
        etree.SubElement(pr, m("endChr")).set(m("val"), end)
        d.append(_container("e", children[1:-1]))
        return d
    return None


def _convert(el) -> list:
    name = _local(el)
    kids = list(el)
    if name in ("mrow", "mstyle", "mpadded", "semantics", "math", "mphantom", "menclose"):
        if name == "mrow":
            d = _fence(kids)
            if d is not None:
                return [d]
        holder = etree.Element("holder")
        _append_seq(holder, kids)
        return list(holder)
    if name == "mi":
        t = _text(el)
        return [_run(t, plain=len(t) > 1 or el.get("mathvariant") == "normal")]
    if name in ("mn", "mo"):
        t = _text(el)
        return [_run(t, plain=name == "mn")] if t else []
    if name == "mtext":
        return [_run(_text(el), plain=True)]
    if name == "mspace":
        return [_run(" ", plain=True)]
    if name == "mfrac":
        f = etree.Element(m("f"))
        if el.get("linethickness") in ("0", "0pt", "0px"):
            etree.SubElement(etree.SubElement(f, m("fPr")), m("type")).set(m("val"), "noBar")
        f.append(_container("num", kids[:1]))
        f.append(_container("den", kids[1:2]))
        return [f]
    if name in ("msup", "msub", "msubsup"):
        tag = {"msup": "sSup", "msub": "sSub", "msubsup": "sSubSup"}[name]
        s = etree.Element(m(tag))
        s.append(_container("e", kids[:1]))
        if name == "msup":
            s.append(_container("sup", kids[1:2]))
        elif name == "msub":
            s.append(_container("sub", kids[1:2]))
        else:
            s.append(_container("sub", kids[1:2]))
            s.append(_container("sup", kids[2:3]))
        return [s]
    if name == "msqrt":
        r = etree.Element(m("rad"))
        etree.SubElement(etree.SubElement(r, m("radPr")), m("degHide")).set(m("val"), "1")
        etree.SubElement(r, m("deg"))
        r.append(_container("e", kids))
        return [r]
    if name == "mroot":
        r = etree.Element(m("rad"))
        r.append(_container("deg", kids[1:2]))
        r.append(_container("e", kids[:1]))
        return [r]
    if name == "mover" and len(kids) == 2:
        acc = _text(kids[1]).strip()
        if acc in ("\u00af", "\u203e", "\u2015", "_") and el.get("accent") != "true":
            b = etree.Element(m("bar"))
            etree.SubElement(etree.SubElement(b, m("barPr")), m("pos")).set(m("val"), "top")
            b.append(_container("e", kids[:1]))
            return [b]
        if acc in ACCENTS:
            a = etree.Element(m("acc"))
            etree.SubElement(etree.SubElement(a, m("accPr")), m("chr")).set(m("val"), ACCENTS[acc])
            a.append(_container("e", kids[:1]))
            return [a]
        lim = etree.Element(m("limUpp"))
        lim.append(_container("e", kids[:1]))
        lim.append(_container("lim", kids[1:2]))
        return [lim]
    if name == "munder" and len(kids) == 2:
        lim = etree.Element(m("limLow"))
        lim.append(_container("e", kids[:1]))
        lim.append(_container("lim", kids[1:2]))
        return [lim]
    if name == "munderover" and len(kids) == 3:
        low = etree.Element(m("limLow"))
        low.append(_container("e", kids[:1]))
        low.append(_container("lim", kids[1:2]))
        upp = etree.Element(m("limUpp"))
        etree.SubElement(upp, m("e")).append(low)
        upp.append(_container("lim", kids[2:3]))
        return [upp]
    if name == "mtable":
        mat = etree.Element(m("m"))
        for tr in kids:
            mr = etree.SubElement(mat, m("mr"))
            for td in tr:
                mr.append(_container("e", list(td)))
        return [mat]
    if name == "mfenced":
        d = etree.Element(m("d"))
        pr = etree.SubElement(d, m("dPr"))
        etree.SubElement(pr, m("begChr")).set(m("val"), el.get("open", "("))
        etree.SubElement(pr, m("endChr")).set(m("val"), el.get("close", ")"))
        for k in kids:
            d.append(_container("e", [k]))
        return [d]
    # unknown element: keep its text
    t = _text(el)
    return [_run(t)] if t else []


# ============================================================================ OMML -> LaTeX
_SPECIAL = {"#": r"\#", "$": r"\$", "%": r"\%", "&": r"\&", "{": r"\{", "}": r"\}", "\\": r"\backslash "}
NARY_CMD = {"\u2211": r"\sum", "\u220f": r"\prod", "\u222b": r"\int", "\u222c": r"\iint", "\u222d": r"\iiint",
            "\u222e": r"\oint", "\u22c3": r"\bigcup", "\u22c2": r"\bigcap", "\u2210": r"\coprod"}
ACC_CMD = {"\u0302": r"\hat", "\u0303": r"\tilde", "\u0305": r"\bar", "\u0304": r"\bar", "\u20d7": r"\vec",
           "\u0307": r"\dot", "\u0308": r"\ddot", "\u030c": r"\check", "\u0306": r"\breve"}
FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "lim", "max", "min", "sup", "inf", "det",
         "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "deg", "gcd", "Pr", "arg", "dim", "ker", "lg"}
DELIM = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|", "\u2016": r"\Vert",
         "\u27e8": r"\langle", "\u27e9": r"\rangle", "\u2308": r"\lceil", "\u2309": r"\rceil", "\u230a": r"\lfloor",
         "\u230b": r"\rfloor", "": "."}


def _val(el, tag, default=None):
    if el is None:
        return default
    sub = el.find(m(tag))
    if sub is None:
        return default
    return sub.get(m("val"), default)


def omml_to_latex(el) -> str:
    """LaTeX for an ``m:oMath`` / ``m:oMathPara`` element (good enough to render and to edit)."""
    return _ltx(el).strip()


def _grp(s: str) -> str:
    s = s.strip()
    return "{" + s + "}"


def _kids(el) -> str:
    return "".join(_ltx(c) for c in el)


def _child(el, tag) -> str:
    c = el.find(m(tag))
    return _kids(c) if c is not None else ""


def _ltx(el) -> str:
    if not isinstance(el.tag, str):
        return ""
    q = etree.QName(el)
    name, ns = q.localname, q.namespace
    if ns == W_NS:
        if name in ("t",):
            return _escape(el.text or "")
        if name in ("r", "ins", "smartTag"):
            return _kids(el)
        return ""
    if ns != M_NS:
        return _kids(el)
    if name in ("oMath", "e", "num", "den", "sub", "sup", "deg", "lim", "fName", "box", "borderBox", "phant"):
        return _kids(el)
    if name == "oMathPara":
        return r"\ ,\ ".join(_ltx(c) for c in el if etree.QName(c).localname == "oMath")
    if name == "r":
        sty = _val(el.find(m("rPr")), "sty")
        text = "".join(t.text or "" for t in el.iter(m("t"), f"{{{W_NS}}}t"))
        word = text.strip()
        if word in FUNCS:
            return "\\" + word + " "
        esc = _escape(text)
        if sty == "p" and len(word) > 1 and word.isalpha():
            return r"\mathrm{" + esc + "}"
        return esc
    if name in ("rPr", "ctrlPr", "fPr", "radPr", "naryPr", "dPr", "sSupPr", "sSubPr", "sSubSupPr", "accPr", "barPr",
                "funcPr", "limLowPr", "limUppPr", "mPr", "eqArrPr", "groupChrPr", "sPrePr", "boxPr", "argPr",
                "oMathParaPr", "borderBoxPr", "phantPr"):
        return ""
    if name == "f":
        num, den = _child(el, "num"), _child(el, "den")
        if _val(el.find(m("fPr")), "type") in ("lin", "skw"):
            return f"{_grp(num)}/{_grp(den)}"
        if _val(el.find(m("fPr")), "type") == "noBar":
            return r"\genfrac{}{}{0}{}" + _grp(num) + _grp(den)
        return r"\frac" + _grp(num) + _grp(den)
    if name == "sSup":
        return _grp(_child(el, "e")) + "^" + _grp(_child(el, "sup"))
    if name == "sSub":
        return _grp(_child(el, "e")) + "_" + _grp(_child(el, "sub"))
    if name == "sSubSup":
        return _grp(_child(el, "e")) + "_" + _grp(_child(el, "sub")) + "^" + _grp(_child(el, "sup"))
    if name == "sPre":
        return "{}_" + _grp(_child(el, "sub")) + "^" + _grp(_child(el, "sup")) + _grp(_child(el, "e"))
    if name == "rad":
        hide = _val(el.find(m("radPr")), "degHide") in ("1", "on", "true")
        deg = _child(el, "deg")
        if hide or not deg.strip():
            return r"\sqrt" + _grp(_child(el, "e"))
        return r"\sqrt[" + deg + "]" + _grp(_child(el, "e"))
    if name == "nary":
        pr = el.find(m("naryPr"))
        op = _val(pr, "chr", "\u222b")
        cmd = NARY_CMD.get(op, _escape(op))
        out = cmd
        if _val(pr, "subHide") not in ("1", "on", "true"):
            sub = _child(el, "sub")
            if sub.strip():
                out += "_" + _grp(sub)
        if _val(pr, "supHide") not in ("1", "on", "true"):
            sup = _child(el, "sup")
            if sup.strip():
                out += "^" + _grp(sup)
        return out + " " + _grp(_child(el, "e"))
    if name == "d":
        pr = el.find(m("dPr"))
        beg = _val(pr, "begChr", "(")
        end = _val(pr, "endChr", ")")
        sep = _val(pr, "sepChr", "|")  # ECMA-376: the default separator is a vertical bar
        parts = [_kids(e) for e in el.findall(m("e"))]
        inner = (" " + _escape(sep) + " ").join(parts)
        return r"\left" + DELIM.get(beg, _escape(beg)) + " " + inner + r" \right" + DELIM.get(end, _escape(end))
    if name == "func":
        fname = _child(el, "fName").strip()
        if fname.startswith(r"\mathrm{"):
            plain = fname[8:-1]
            if plain in FUNCS:
                fname = "\\" + plain + " "
        return fname + _grp(_child(el, "e"))
    if name == "limLow":
        return _grp(_child(el, "e")) + "_" + _grp(_child(el, "lim"))
    if name == "limUpp":
        return _grp(_child(el, "e")) + "^" + _grp(_child(el, "lim"))
    if name == "acc":
        ch = _val(el.find(m("accPr")), "chr", "\u0302")
        return ACC_CMD.get(ch, r"\hat") + _grp(_child(el, "e"))
    if name == "bar":
        return r"\overline" + _grp(_child(el, "e"))
    if name == "groupChr":
        return _grp(_child(el, "e"))
    if name == "eqArr":
        return r"\ ;\ ".join(_kids(e) for e in el.findall(m("e")))
    if name == "m":
        rows = [r"\ ".join(_kids(e) for e in mr.findall(m("e"))) for mr in el.findall(m("mr"))]
        return r"\left[" + r"\ ;\ ".join(rows) + r"\right]"
    if name == "t":
        return _escape(el.text or "")
    return _kids(el)


def _escape(text: str) -> str:
    return "".join(_SPECIAL.get(ch, ch) for ch in text)


def plain_text(latex: str) -> str:
    """A readable linear form (used when an equation cannot be drawn)."""
    s = re.sub(r"\\(frac)\{([^{}]*)\}\{([^{}]*)\}", r"(\2)/(\3)", latex)
    s = re.sub(r"\\left|\\right|\\mathrm|\\,|\\;|\\ ", "", s)
    s = s.replace("\\", "").replace("{", "").replace("}", "")
    return s


# symbol palette of the equation dialog: (label shown on the button, LaTeX inserted)
PALETTE = [
    ("x\u00b2", "^{2}"), ("x\u2099", "_{n}"), ("a/b", r"\frac{a}{b}"), ("\u221a", r"\sqrt{x}"), ("\u207f\u221a", r"\sqrt[n]{x}"),
    ("\u2211", r"\sum_{i=1}^{n} "), ("\u222b", r"\int_{a}^{b} "), ("\u220f", r"\prod_{i=1}^{n} "),
    ("lim", r"\lim_{x \to \infty} "), ("( )", r"\left( x \right)"), ("[ ]", r"\left[ x \right]"),
    ("\u00b1", r"\pm "), ("\u00d7", r"\times "), ("\u00f7", r"\div "), ("\u00b7", r"\cdot "), ("\u2260", r"\neq "),
    ("\u2264", r"\leq "), ("\u2265", r"\geq "), ("\u2248", r"\approx "), ("\u221e", r"\infty "),
    ("\u2192", r"\rightarrow "), ("\u2202", r"\partial "), ("\u2207", r"\nabla "), ("\u2208", r"\in "),
    ("\u03b1", r"\alpha "), ("\u03b2", r"\beta "), ("\u03b3", r"\gamma "), ("\u03b4", r"\delta "),
    ("\u03b8", r"\theta "), ("\u03bb", r"\lambda "), ("\u03bc", r"\mu "), ("\u03c0", r"\pi "), ("\u03c3", r"\sigma "),
    ("\u03c9", r"\omega "), ("\u0394", r"\Delta "), ("\u03a3", r"\Sigma "), ("\u03a9", r"\Omega "),
    ("x\u0302", r"\hat{x}"), ("x\u0304", r"\overline{x}"), ("x\u20d7", r"\vec{x}"), ("sin", r"\sin "),
    ("log", r"\log "),
]
EXAMPLES = [
    ("Quadratic formula", r"x = \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}"),
    ("Pythagoras", r"a^{2} + b^{2} = c^{2}"),
    ("Area of a circle", r"A = \pi r^{2}"),
    ("Sum of 1..n", r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}"),
    ("Gaussian integral", r"\int_{-\infty}^{\infty} e^{-x^{2}} dx = \sqrt{\pi}"),
    ("Mean", r"\bar{x} = \frac{1}{n} \sum_{i=1}^{n} x_{i}"),
    ("Euler's identity", r"e^{i \pi} + 1 = 0"),
    ("Compound interest", r"A = P \left( 1 + \frac{r}{n} \right)^{nt}"),
]
