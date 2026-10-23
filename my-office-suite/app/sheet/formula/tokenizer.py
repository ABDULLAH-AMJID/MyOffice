"""Excel formula tokenizer."""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ROWS = 1_048_576
MAX_COLS = 16_384  # XFD

ERROR_CODES = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!",
               "#CALC!", "#CIRC!")


class FormulaSyntaxError(ValueError):
    pass


@dataclass(slots=True)
class Token:
    kind: str      # NUM STR BOOL ERR REF FUNC NAME OP LP RP SEP LBRACE RBRACE ROWSEP EOF
    text: str
    pos: int
    value: object = None


_SHEET = r"(?:'(?:[^']|'')+'|[A-Za-z_\u00C0-\uFFFF][\w.\u00C0-\uFFFF]*)!"
_CELL = r"\$?[A-Za-z]{1,3}\$?[0-9]{1,7}"
_PATTERNS = [
    ("WS", r"[ \t\r\n]+"),
    ("STR", r'"(?:[^"]|"")*"'),
    ("ERR", "|".join(re.escape(e) for e in ERROR_CODES)),
    ("FUNC", r"(?:_xlfn\.|_xlws\.)?[A-Za-z_][A-Za-z0-9_.]*(?=\s*\()"),
    ("REF", rf"(?:{_SHEET})?(?:{_CELL}(?::{_CELL})?|\$?[A-Za-z]{{1,3}}:\$?[A-Za-z]{{1,3}}|\$?[0-9]{{1,7}}:\$?[0-9]{{1,7}})(?![\w(])"),
    ("NUM", r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"),
    ("BOOL", r"(?:TRUE|FALSE)(?![\w(.])"),
    ("NAME", r"[A-Za-z_\\\u00C0-\uFFFF][\w.\u00C0-\uFFFF]*"),
    ("OP", r"<>|<=|>=|[-+*/^&=<>%:]"),
    ("LP", r"\("),
    ("RP", r"\)"),
    ("SEP", r","),
    ("ROWSEP", r";"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
]
_MASTER = re.compile("|".join(f"(?P<{k}>{p})" for k, p in _PATTERNS), re.IGNORECASE)


def col_to_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def index_to_col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_CELL_RE = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})$")


def valid_cell(text: str) -> bool:
    m = _CELL_RE.match(text)
    if not m:
        return False
    return col_to_index(m.group(2)) <= MAX_COLS and 1 <= int(m.group(4)) <= MAX_ROWS


def tokenize(formula: str) -> list[Token]:
    """Tokenize a formula (without the leading '=')."""
    tokens: list[Token] = []
    pos = 0
    n = len(formula)
    while pos < n:
        m = _MASTER.match(formula, pos)
        if not m:
            raise FormulaSyntaxError(f"Unexpected character {formula[pos]!r} at {pos + 1}")
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "REF":
            ref = text.split("!")[-1]
            parts = ref.split(":")
            if all(_CELL_RE.match(p) for p in parts) and not all(valid_cell(p) for p in parts):
                kind = "NAME"  # e.g. XYZ1 beyond column XFD is a defined name
        if kind == "WS":
            # whitespace is the intersection operator only between two references
            if tokens and tokens[-1].kind in ("REF", "RP"):
                nxt = _MASTER.match(formula, m.end())
                if nxt and nxt.lastgroup in ("REF",) and formula[m.end():m.end() + 1] not in (")", ","):
                    tokens.append(Token("OP", " ", pos))
        elif kind == "STR":
            tokens.append(Token("STR", text, pos, text[1:-1].replace('""', '"')))
        elif kind == "NUM":
            tokens.append(Token("NUM", text, pos, float(text)))
        elif kind == "BOOL":
            tokens.append(Token("BOOL", text, pos, text.upper() == "TRUE"))
        elif kind == "ERR":
            tokens.append(Token("ERR", text.upper(), pos))
        elif kind == "FUNC":
            name = text.upper()
            for prefix in ("_XLFN.", "_XLWS."):
                if name.startswith(prefix):
                    name = name[len(prefix):]
            tokens.append(Token("FUNC", text, pos, name))
        else:
            tokens.append(Token(kind, text, pos))
        pos = m.end()
    tokens.append(Token("EOF", "", n))
    return tokens
