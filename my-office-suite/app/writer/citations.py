"""Citations & bibliography.

Sources are stored as CSL-JSON items (the Citation Style Language data model) inside the
document (custom XML part).  Five widely used styles are implemented natively: APA 7,
MLA 9, Chicago author-date 17, Harvard (Cite Them Right) and IEEE.  Citations are live
fields (``P_CITE`` character property), so changing the style or the sources updates every
in-text citation and the bibliography.
"""
from __future__ import annotations

import json
import re
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from app.services.i18n import _t
from app.writer.model import P_BIBLIO, P_CITE, PX_PER_PT

STYLES = ["APA", "MLA", "Chicago", "Harvard", "IEEE"]
TYPES = {"article-journal": "Journal article", "book": "Book", "chapter": "Book chapter",
         "paper-conference": "Conference paper", "webpage": "Web page", "report": "Report", "thesis": "Thesis",
         "article-newspaper": "Newspaper article"}

Seg = tuple[str, bool]  # (text, italic)


# ============================================================================ helpers
def _year(item) -> str:
    try:
        return str(item["issued"]["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        return "n.d."


def _authors(item) -> list[dict]:
    a = item.get("author") or item.get("editor") or []
    return [x for x in a if isinstance(x, dict)]


def _family(a: dict) -> str:
    return a.get("family") or a.get("literal") or ""


def _initials(given: str, sep: str = ". ", end: str = ".") -> str:
    parts = [p for p in re.split(r"[\s.]+", given or "") if p]
    if not parts:
        return ""
    out = []
    for p in parts:
        if "-" in p:
            out.append("-".join(x[0].upper() + "." for x in p.split("-") if x))
        else:
            out.append(p[0].upper())
    return sep.join(o.rstrip(".") for o in out) + end


def _pages(item) -> str:
    return (item.get("page") or "").replace("--", "\u2013").replace("-", "\u2013")


def _join_names(names: list[str], conj: str, oxford: bool = True) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} {conj} {names[1]}"
    return ", ".join(names[:-1]) + ("," if oxford else "") + f" {conj} {names[-1]}"


def _doi_url(item) -> str:
    if item.get("DOI"):
        doi = item["DOI"]
        return doi if doi.startswith("http") else f"https://doi.org/{doi}"
    return item.get("URL", "")


# ============================================================================ in-text citations
def cite_text(style: str, items: list[dict], locator: str = "", numbers: list[int] | None = None) -> str:
    style = style if style in STYLES else "APA"
    if style == "IEEE":
        nums = numbers or [0] * len(items)
        if locator and len(nums) == 1:
            return f"[{nums[0]}, p. {locator}]"
        return ", ".join(f"[{n}]" for n in nums)
    parts = []
    for it in items:
        au = _authors(it)
        fam = [_family(a) for a in au] or [it.get("title", "Anon.")[:30]]
        y = _year(it)
        if style == "APA":
            name = fam[0] if len(fam) == 1 else (f"{fam[0]} & {fam[1]}" if len(fam) == 2 else f"{fam[0]} et al.")
            parts.append(f"{name}, {y}")
        elif style == "MLA":
            name = fam[0] if len(fam) == 1 else (f"{fam[0]} and {fam[1]}" if len(fam) == 2 else f"{fam[0]} et al.")
            parts.append(name)
        elif style == "Chicago":
            name = _join_names(fam, "and") if len(fam) <= 3 else f"{fam[0]} et al."
            parts.append(f"{name} {y}")
        else:  # Harvard
            name = _join_names(fam, "and", oxford=False) if len(fam) <= 3 else f"{fam[0]} et al."
            parts.append(f"{name}, {y}")
    text = "; ".join(parts)
    if locator:
        text += {"APA": f", p. {locator}", "MLA": f" {locator}", "Chicago": f", {locator}",
                 "Harvard": f", p. {locator}"}[style]
    return f"({text})"


# ============================================================================ bibliography entries
def bib_entry(style: str, it: dict, number: int = 0) -> list[Seg]:
    style = style if style in STYLES else "APA"
    return {"APA": _apa, "MLA": _mla, "Chicago": _chicago, "Harvard": _harvard, "IEEE": _ieee}[style](it, number)


def _apa(it, _n) -> list[Seg]:
    au = _authors(it)
    names = [f"{_family(a)}, {_initials(a.get('given', ''))}".rstrip(", ") for a in au]
    if len(names) > 20:
        names = names[:19] + ["\u2026 " + names[-1]]
    if len(names) >= 2:
        auth = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        auth = names[0] if names else ""
    segs: list[Seg] = []
    title = it.get("title", "")
    t = it.get("type", "book")
    head = f"{auth} ({_year(it)}). " if auth else ""
    if not auth:
        head = ""
    segs.append((head, False))
    if t in ("article-journal", "article-newspaper"):
        if not auth:
            segs.append((f"{title}. ({_year(it)}). ", False))
        else:
            segs.append((f"{title}. ", False))
        cont = it.get("container-title", "")
        if cont:
            segs.append((cont, True))
            if it.get("volume"):
                segs.append((", ", False))
                segs.append((str(it["volume"]), True))
            if it.get("issue"):
                segs.append((f"({it['issue']})", False))
            if _pages(it):
                segs.append((f", {_pages(it)}", False))
            segs.append((". ", False))
    elif t in ("chapter", "paper-conference"):
        segs.append((f"{title}. ", False))
        if it.get("container-title"):
            segs.append(("In ", False))
            segs.append((it["container-title"], True))
            if _pages(it):
                segs.append((f" (pp. {_pages(it)})", False))
            segs.append((". ", False))
        if it.get("publisher"):
            segs.append((f"{it['publisher']}. ", False))
    else:
        segs.append((title, True))
        if it.get("edition"):
            segs.append((f" ({it['edition']} ed.)", False))
        segs.append((". ", False))
        if t == "webpage" and it.get("container-title"):
            segs.append((f"{it['container-title']}. ", False))
        if it.get("publisher"):
            segs.append((f"{it['publisher']}. ", False))
    link = _doi_url(it)
    if link:
        segs.append((link, False))
    return _tidy(segs)


def _mla(it, _n) -> list[Seg]:
    au = _authors(it)
    if not au:
        auth = ""
    elif len(au) == 1:
        auth = f"{_family(au[0])}, {au[0].get('given', '')}".rstrip(", ")
    elif len(au) == 2:
        auth = f"{_family(au[0])}, {au[0].get('given', '')}, and {au[1].get('given', '')} {_family(au[1])}".strip()
    else:
        auth = f"{_family(au[0])}, {au[0].get('given', '')}, et al"
    segs: list[Seg] = [((auth + ". ") if auth else "", False)]
    t = it.get("type", "book")
    title = it.get("title", "")
    if t in ("article-journal", "article-newspaper", "chapter", "paper-conference", "webpage"):
        segs.append((f"\u201c{title}.\u201d ", False))
        if it.get("container-title"):
            segs.append((it["container-title"], True))
            segs.append((", ", False))
        bits = []
        if it.get("volume"):
            bits.append(f"vol. {it['volume']}")
        if it.get("issue"):
            bits.append(f"no. {it['issue']}")
        if it.get("publisher") and t != "article-journal":
            bits.append(it["publisher"])
        bits.append(_year(it))
        if _pages(it):
            bits.append(f"pp. {_pages(it)}")
        segs.append((", ".join(bits) + ".", False))
    else:
        segs.append((title, True))
        segs.append((". ", False))
        bits = [b for b in (it.get("publisher"), _year(it)) if b]
        segs.append((", ".join(bits) + ".", False))
    link = _doi_url(it)
    if link:
        segs.append((" " + link.replace("https://", "") + ".", False))
    return _tidy(segs)


def _chicago(it, _n) -> list[Seg]:
    au = _authors(it)
    names = []
    for i, a in enumerate(au):
        names.append(f"{_family(a)}, {a.get('given', '')}".rstrip(", ") if i == 0
                     else f"{a.get('given', '')} {_family(a)}".strip())
    auth = _join_names(names, "and") if len(names) <= 10 else ", ".join(names[:7]) + ", et al."
    segs: list[Seg] = [(f"{auth}. {_year(it)}. " if auth else f"{_year(it)}. ", False)]
    t = it.get("type", "book")
    title = it.get("title", "")
    if t in ("article-journal", "article-newspaper"):
        segs.append((f"\u201c{title}.\u201d ", False))
        if it.get("container-title"):
            segs.append((it["container-title"], True))
        tail = ""
        if it.get("volume"):
            tail += f" {it['volume']}"
        if it.get("issue"):
            tail += f" ({it['issue']})"
        if _pages(it):
            tail += f": {_pages(it)}"
        segs.append((tail + ". ", False))
    elif t in ("chapter", "paper-conference"):
        segs.append((f"\u201c{title}.\u201d In ", False))
        segs.append((it.get("container-title", ""), True))
        if _pages(it):
            segs.append((f", {_pages(it)}", False))
        segs.append((". ", False))
        pub = ": ".join(x for x in (it.get("publisher-place"), it.get("publisher")) if x)
        if pub:
            segs.append((pub + ". ", False))
    else:
        segs.append((title, True))
        segs.append((". ", False))
        pub = ": ".join(x for x in (it.get("publisher-place"), it.get("publisher")) if x)
        if pub:
            segs.append((pub + ". ", False))
    link = _doi_url(it)
    if link:
        segs.append((link + ".", False))
    return _tidy(segs)


def _harvard(it, _n) -> list[Seg]:
    au = _authors(it)
    names = [f"{_family(a)}, {_initials(a.get('given', ''), sep='.', end='.')}".rstrip(", ") for a in au]
    auth = _join_names(names, "and", oxford=False) if len(names) <= 3 else f"{names[0]} et al."
    segs: list[Seg] = [(f"{auth} ({_year(it)}) " if auth else f"({_year(it)}) ", False)]
    t = it.get("type", "book")
    title = it.get("title", "")
    if t in ("article-journal", "article-newspaper"):
        segs.append((f"\u2018{title}\u2019, ", False))
        if it.get("container-title"):
            segs.append((it["container-title"], True))
        tail = ""
        if it.get("volume"):
            tail += f", {it['volume']}"
            if it.get("issue"):
                tail += f"({it['issue']})"
        if _pages(it):
            tail += f", pp. {_pages(it)}"
        segs.append((tail + ".", False))
    elif t == "webpage":
        segs.append((title, True))
        segs.append((". ", False))
        if it.get("URL"):
            segs.append((f"Available at: {it['URL']}", False))
            try:
                acc = it["accessed"]["date-parts"][0]
                segs.append((f" (Accessed: {'/'.join(str(x) for x in reversed(acc))})", False))
            except (KeyError, IndexError, TypeError):
                pass
            segs.append((".", False))
        return _tidy(segs)
    else:
        segs.append((title, True))
        segs.append((". ", False))
        if it.get("edition"):
            segs.append((f"{it['edition']} edn. ", False))
        pub = ": ".join(x for x in (it.get("publisher-place"), it.get("publisher")) if x)
        if pub:
            segs.append((pub + ".", False))
    if it.get("DOI"):
        segs.append((f" doi:{it['DOI']}.", False))
    return _tidy(segs)


def _ieee(it, n) -> list[Seg]:
    au = _authors(it)
    names = [f"{_initials(a.get('given', ''))} {_family(a)}".strip() for a in au]
    if len(names) > 6:
        auth = f"{names[0]} et al."
    else:
        auth = _join_names(names, "and", oxford=len(names) > 2)
    segs: list[Seg] = [(f"[{n}] ", False), (f"{auth}, " if auth else "", False)]
    t = it.get("type", "book")
    title = it.get("title", "")
    if t in ("article-journal", "article-newspaper", "paper-conference", "chapter"):
        segs.append((f"\u201c{title},\u201d ", False))
        if t in ("paper-conference", "chapter"):
            segs.append(("in ", False))
        segs.append((it.get("container-title", ""), True))
        bits = []
        if it.get("volume"):
            bits.append(f"vol. {it['volume']}")
        if it.get("issue"):
            bits.append(f"no. {it['issue']}")
        if _pages(it):
            bits.append(f"pp. {_pages(it)}")
        bits.append(_year(it))
        segs.append((", " + ", ".join(bits), False))
        if it.get("DOI"):
            segs.append((f", doi: {it['DOI']}", False))
        segs.append((".", False))
    elif t == "webpage":
        segs.append((f"\u201c{title}.\u201d ", False))
        if it.get("container-title"):
            segs.append((it["container-title"] + ". ", False))
        if it.get("URL"):
            segs.append((f"[Online]. Available: {it['URL']}", False))
    else:
        segs.append((title, True))
        if it.get("edition"):
            segs.append((f", {it['edition']} ed", False))
        segs.append((". ", False))
        pub = ": ".join(x for x in (it.get("publisher-place"), it.get("publisher")) if x)
        segs.append(((pub + ", " if pub else "") + _year(it) + ".", False))
    return _tidy(segs)


def _tidy(segs: list[Seg]) -> list[Seg]:
    out = [(re.sub(r"\s{2,}", " ", t).replace("..", ".").replace(" .", "."), i) for t, i in segs if t]
    if out:
        out[-1] = (out[-1][0].rstrip(), out[-1][1])
    return out


def sort_key(it: dict):
    au = _authors(it)
    return ((_family(au[0]) if au else it.get("title", "")).lower(), _year(it), it.get("title", "").lower())


def bibliography_heading(style: str) -> str:
    return "Works Cited" if style == "MLA" else ("Bibliography" if style == "Chicago" else "References")


# ============================================================================ BibTeX import
_BIB_TYPES = {"article": "article-journal", "book": "book", "inproceedings": "paper-conference",
              "conference": "paper-conference", "incollection": "chapter", "inbook": "chapter",
              "phdthesis": "thesis", "mastersthesis": "thesis", "techreport": "report", "misc": "webpage",
              "online": "webpage", "electronic": "webpage"}


def _bib_names(value: str) -> list[dict]:
    out = []
    for name in re.split(r"\s+and\s+", value.strip()):
        name = name.strip().strip("{}")
        if not name:
            continue
        if "," in name:
            fam, given = [x.strip() for x in name.split(",", 1)]
        else:
            parts = name.split()
            fam, given = parts[-1], " ".join(parts[:-1])
        out.append({"family": fam, "given": given})
    return out


def parse_bibtex(text: str) -> list[dict]:
    items = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        kind = m.group(1).lower()
        if kind in ("comment", "preamble", "string"):
            continue
        pos = m.end()
        depth = 1
        i = pos
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[pos:i - 1]
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)", body):
            val = fm.group(2).strip()
            if val[0] in "{\"":
                val = val[1:-1]
            fields[fm.group(1).lower()] = re.sub(r"[{}]", "", re.sub(r"\s+", " ", val)).strip()
        it = {"id": m.group(2), "type": _BIB_TYPES.get(kind, "book"), "title": fields.get("title", "")}
        if "author" in fields:
            it["author"] = _bib_names(fields["author"])
        elif "editor" in fields:
            it["editor"] = _bib_names(fields["editor"])
        for src, dst in (("journal", "container-title"), ("booktitle", "container-title"),
                         ("publisher", "publisher"), ("address", "publisher-place"), ("volume", "volume"),
                         ("number", "issue"), ("pages", "page"), ("url", "URL"), ("doi", "DOI"),
                         ("edition", "edition"), ("school", "publisher"), ("institution", "publisher")):
            if src in fields and dst not in it:
                it[dst] = fields[src]
        if fields.get("year", "").isdigit():
            it["issued"] = {"date-parts": [[int(fields["year"])]]}
        items.append(it)
    return items


def new_key(item: dict, existing: set[str]) -> str:
    au = _authors(item)
    base = re.sub(r"\W+", "", (_family(au[0]) if au else (item.get("title") or "src").split()[0]).lower()) or "src"
    base += _year(item).replace("n.d.", "")
    key = base
    n = 1
    while key in existing:
        key = f"{base}{chr(ord('a') + n - 1)}" if n <= 26 else f"{base}_{n}"
        n += 1
    return key


# ============================================================================ document operations
def _cite_spans(doc: QTextDocument):
    """[(start, end, value)] of citation fields in document order (adjacent fragments merged)."""
    spans = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            f = it.fragment()
            it += 1
            if not f.isValid():
                continue
            fmt = f.charFormat()
            if fmt.hasProperty(P_CITE):
                val = str(fmt.property(P_CITE))
                if spans and spans[-1][1] == f.position() and spans[-1][2] == val:
                    spans[-1] = (spans[-1][0], f.position() + f.length(), val)
                else:
                    spans.append((f.position(), f.position() + f.length(), val))
        block = block.next()
    return spans


def parse_cite_value(val: str) -> tuple[list[str], str]:
    parts = val.split("|")
    keys = [k for k in parts[0].split(";") if k]
    locator = parts[1] if len(parts) > 1 else ""
    return keys, locator


def citation_numbers(doc: QTextDocument) -> dict[str, int]:
    order: dict[str, int] = {}
    for _s, _e, val in _cite_spans(doc):
        for k in parse_cite_value(val)[0]:
            if k not in order:
                order[k] = len(order) + 1
    return order


def insert_citation(cursor: QTextCursor, keys: list[str], locator: str, sources: list[dict], style: str) -> None:
    by_id = {s["id"]: s for s in sources}
    items = [by_id[k] for k in keys if k in by_id]
    if not items:
        return
    doc = cursor.document()
    value = ";".join(keys) + "|" + locator + "|" + uuid.uuid4().hex[:8]
    numbers = None
    if style == "IEEE":
        order = citation_numbers(doc)
        nxt = len(order) + 1
        numbers = []
        for k in keys:
            if k not in order:
                order[k] = nxt
                nxt += 1
            numbers.append(order[k])
    fmt = QTextCharFormat(cursor.charFormat())
    fmt.setProperty(P_CITE, value)
    cursor.insertText(cite_text(style, items, locator, numbers), fmt)
    after = QTextCharFormat(cursor.charFormat())
    after.clearProperty(P_CITE)
    cursor.setCharFormat(after)


def update_citations(doc: QTextDocument, sources: list[dict], style: str) -> int:
    by_id = {s["id"]: s for s in sources}
    order = citation_numbers(doc)
    spans = _cite_spans(doc)
    c = QTextCursor(doc)
    c.beginEditBlock()
    for start, end, val in reversed(spans):
        keys, locator = parse_cite_value(val)
        items = [by_id[k] for k in keys if k in by_id]
        text = cite_text(style, items, locator, [order[k] for k in keys if k in by_id]) if items else "(?)"
        c.setPosition(start + 1)  # charFormat() reports the character before the cursor
        fmt = QTextCharFormat(c.charFormat())
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        c.insertText(text, fmt)
    c.endEditBlock()
    return len(spans)


def _bib_block_range(doc: QTextDocument):
    first = last = None
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().hasProperty(P_BIBLIO):
            if first is None:
                first = block
            last = block
        elif first is not None:
            break
        block = block.next()
    return first, last


def insert_or_update_bibliography(doc: QTextDocument, cursor: QTextCursor | None, sources: list[dict],
                                  style: str, base_size: float = 11.0) -> bool:
    """Regenerate the bibliography in place (or insert it at ``cursor``).  Returns True if written."""
    order = citation_numbers(doc)
    cited = [s for s in sources if s["id"] in order] or list(sources)
    if style == "IEEE":
        cited.sort(key=lambda s: order.get(s["id"], 10 ** 6))
    else:
        cited.sort(key=sort_key)
    first, last = _bib_block_range(doc)
    c = QTextCursor(doc)
    c.beginEditBlock()
    if first is not None:
        c.setPosition(first.position())
        c.setPosition(last.position() + last.length() - 1, QTextCursor.KeepAnchor)
        c.removeSelectedText()
        reuse = True
    else:
        if cursor is None:
            c.endEditBlock()
            return False
        c.setPosition(cursor.position())
        if c.block().length() > 1:
            c.movePosition(QTextCursor.EndOfBlock)
            c.insertBlock()
        reuse = True
    heading_bf = QTextBlockFormat()
    heading_bf.setProperty(P_BIBLIO, True)
    heading_bf.setHeadingLevel(1)
    heading_bf.setTopMargin(12 * PX_PER_PT)
    heading_bf.setBottomMargin(6 * PX_PER_PT)
    from app.writer.model import P_STYLE

    heading_bf.setProperty(P_STYLE, "Heading 1")
    hcf = QTextCharFormat()
    hcf.setFontPointSize(16)
    hcf.setFontWeight(QFont.Bold)
    if reuse:
        c.setBlockFormat(heading_bf)
        c.setBlockCharFormat(hcf)
    c.insertText(bibliography_heading(style), hcf)
    entry_bf = QTextBlockFormat()
    entry_bf.setProperty(P_BIBLIO, True)
    entry_bf.setProperty(P_STYLE, "Normal")
    entry_bf.setLeftMargin(48)
    entry_bf.setTextIndent(-48)
    entry_bf.setBottomMargin(6 * PX_PER_PT)
    entry_bf.setLineHeight(150 if style in ("APA", "MLA") else 115, QTextBlockFormat.ProportionalHeight.value)
    normal = QTextCharFormat()
    normal.setFontPointSize(base_size)
    normal.setFontWeight(QFont.Normal)
    italic = QTextCharFormat(normal)
    italic.setFontItalic(True)
    for idx, src in enumerate(cited, start=1):
        c.insertBlock(entry_bf, normal)
        for text, it in bib_entry(style, src, order.get(src["id"], idx)):
            c.insertText(text, italic if it else normal)
    c.endEditBlock()
    return True


# ============================================================================ dialogs
class SourceEditDialog(QDialog):
    FIELDS = [("title", "Title"), ("container-title", "Journal / Book / Website"), ("publisher", "Publisher"),
              ("publisher-place", "Place"), ("volume", "Volume"), ("issue", "Issue"), ("page", "Pages"),
              ("edition", "Edition"), ("URL", "URL"), ("DOI", "DOI")]

    def __init__(self, parent, item: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Source"))
        self.item = dict(item or {"type": "book"})
        f = QFormLayout(self)
        self.type = QComboBox()
        for k, v in TYPES.items():
            self.type.addItem(_t(v), k)
        self.type.setCurrentIndex(max(0, self.type.findData(self.item.get("type", "book"))))
        f.addRow(_t("Type:"), self.type)
        self.authors = QLineEdit("; ".join(f"{_family(a)}, {a.get('given', '')}".rstrip(", ") for a in _authors(self.item)))
        self.authors.setPlaceholderText(_t("Family, Given; Family, Given"))
        f.addRow(_t("Authors:"), self.authors)
        self.year = QLineEdit(_year(self.item) if self.item.get("issued") else "")
        f.addRow(_t("Year:"), self.year)
        self.edits = {}
        for key, label in self.FIELDS:
            e = QLineEdit(str(self.item.get(key, "")))
            f.addRow(_t(label) + ":", e)
            self.edits[key] = e
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def value(self) -> dict:
        it = dict(self.item)
        it["type"] = self.type.currentData()
        authors = []
        for chunk in self.authors.text().split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "," in chunk:
                fam, given = [x.strip() for x in chunk.split(",", 1)]
            else:
                parts = chunk.split()
                fam, given = parts[-1], " ".join(parts[:-1])
            authors.append({"family": fam, "given": given})
        it["author"] = authors
        y = self.year.text().strip()
        if y.isdigit():
            it["issued"] = {"date-parts": [[int(y)]]}
        else:
            it.pop("issued", None)
        for key, e in self.edits.items():
            v = e.text().strip()
            if v:
                it[key] = v
            else:
                it.pop(key, None)
        return it


class SourceManagerDialog(QDialog):
    def __init__(self, parent, sources: list[dict]):
        super().__init__(parent)
        self.setWindowTitle(_t("Manage Sources"))
        self.resize(820, 440)
        self.sources = [dict(s) for s in sources]
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([_t("Key"), _t("Type"), _t("Authors"), _t("Title"), _t("Year")])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for label, fn in ((_t("New\u2026"), self.add), (_t("Edit\u2026"), self.edit), (_t("Delete"), self.delete),
                          (_t("Import CSL-JSON / BibTeX\u2026"), self.import_file), (_t("Export CSL-JSON\u2026"), self.export_file)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(len(self.sources))
        for r, s in enumerate(self.sources):
            vals = [s.get("id", ""), TYPES.get(s.get("type", ""), s.get("type", "")),
                    "; ".join(_family(a) for a in _authors(s)), s.get("title", ""), _year(s)]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))

    def _row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def add(self):
        d = SourceEditDialog(self)
        if d.exec():
            it = d.value()
            it["id"] = new_key(it, {s["id"] for s in self.sources})
            self.sources.append(it)
            self.refresh()

    def edit(self, *_):
        r = self._row()
        if r < 0:
            return
        d = SourceEditDialog(self, self.sources[r])
        if d.exec():
            it = d.value()
            it["id"] = self.sources[r]["id"]
            self.sources[r] = it
            self.refresh()

    def delete(self):
        r = self._row()
        if r >= 0:
            del self.sources[r]
            self.refresh()

    def import_file(self):
        from app.shell.dialogs import get_open_file, show_error

        path = get_open_file(self, _t("Import Sources"), "CSL-JSON / BibTeX (*.json *.bib);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
            items = json.loads(text) if path.lower().endswith(".json") else parse_bibtex(text)
            if isinstance(items, dict):
                items = items.get("items", [items])
            existing = {s["id"] for s in self.sources}
            for it in items:
                if not isinstance(it, dict):
                    continue
                if not it.get("id") or it["id"] in existing:
                    it["id"] = new_key(it, existing)
                existing.add(it["id"])
                self.sources.append(it)
            self.refresh()
        except Exception as exc:
            show_error(self, _t("Import Sources"), _t("Could not import sources: {0}").format(exc))

    def export_file(self):
        from app.shell.dialogs import get_save_file

        path = get_save_file(self, _t("Export Sources"), "CSL-JSON (*.json)", "sources.json", "json")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.sources, fh, ensure_ascii=False, indent=2)


class InsertCitationDialog(QDialog):
    def __init__(self, parent, sources: list[dict]):
        super().__init__(parent)
        self.setWindowTitle(_t("Insert Citation"))
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        for s in sorted(sources, key=sort_key):
            label = f"{'; '.join(_family(a) for a in _authors(s)) or '?'} ({_year(s)}) \u2014 {s.get('title', '')}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, s["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda it: it.setCheckState(
            Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
        lay.addWidget(self.list)
        f = QFormLayout()
        self.locator = QLineEdit()
        self.locator.setPlaceholderText(_t("e.g. 12 or 12-14"))
        f.addRow(_t("Page (optional):"), self.locator)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def keys(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        if not out and self.list.currentItem() is not None:
            out.append(self.list.currentItem().data(Qt.UserRole))
        return out
