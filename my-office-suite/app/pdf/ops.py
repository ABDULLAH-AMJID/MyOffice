"""PDF operations used by the PDF Toolbox, the PDF editor and the tests.

Every function takes file paths, writes its output atomically and returns a result object or the
output path(s).  Long operations accept ``progress(percent, message)`` callbacks.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

log = logging.getLogger(__name__)


class PdfOpError(Exception):
    pass


def _atomic_target(out: str) -> str:
    d = os.path.dirname(os.path.abspath(out)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".pdf.tmp")
    os.close(fd)
    return tmp


def _finish(tmp: str, out: str) -> str:
    from app.services.fileutil import finish_atomic

    return finish_atomic(tmp, out)


def open_pdf(path: str, password: str | None = None) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise PdfOpError(f"Cannot open PDF: {exc}") from exc
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PdfOpError("The PDF is password protected; a correct password is required.")
    return doc


_ARABIC = re.compile("[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_LATIN = re.compile("[A-Za-z]")
_LTR_RUN = re.compile(r"[A-Za-z0-9\u00C0-\u024F][A-Za-z0-9\u00C0-\u024F.,:%/\-+]*")


def visual_to_logical(line: str) -> str:
    """Convert a right-to-left line stored in visual order (as in PDFs) to reading order.

    The transformation is an involution, so it also turns a logical query into its visual form."""
    return _LTR_RUN.sub(lambda m: m.group(0)[::-1], line[::-1])


def fix_rtl_text(text: str) -> str:
    """Restore reading order of Urdu/Arabic lines extracted from PDFs (MuPDF returns visual order)."""
    out = []
    for line in text.split("\n"):
        ar = len(_ARABIC.findall(line))
        out.append(visual_to_logical(line) if ar and ar >= len(_LATIN.findall(line)) else line)
    return "\n".join(out)


def search_variants(query: str) -> list[str]:
    """Strings to look for when searching PDF text (adds the visual form of RTL queries)."""
    return [query, visual_to_logical(query)] if _ARABIC.search(query) else [query]


def parse_ranges(spec: str, page_count: int) -> list[list[int]]:
    """'1-3, 5, 7-' -> [[0,1,2],[4],[6..last]] (0-based page lists per group)."""
    groups = []
    for part in re.split(r"[,;]", spec or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else page_count
        elif part.isdigit():
            a = b = int(part)
        elif part.lower() in ("odd", "even"):
            groups.append([i for i in range(page_count) if (i % 2 == 0) == (part.lower() == "odd")])
            continue
        else:
            raise PdfOpError(f"Invalid page range: {part!r}")
        if a < 1 or b > page_count or a > b:
            raise PdfOpError(f"Page range {part!r} is outside 1-{page_count}")
        groups.append(list(range(a - 1, b)))
    if not groups:
        raise PdfOpError("No pages selected")
    return groups


# ============================================================================= 5. convert
OFFICE_EXTS = (".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp", ".wpt", ".ett",
               ".dpt", ".csv", ".wps", ".et", ".dps", ".html", ".htm")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")


def to_pdf(src: str, out: str | None = None, progress=None) -> str:
    """Convert an office document, image or text file to PDF.

    Word, Excel and PowerPoint files (OOXML), CSV and HTML are rendered by MyOffice's own Writer,
    Spreadsheet and Presentation engines.  Only legacy/OpenDocument formats need LibreOffice (the
    optional Legacy Formats Pack) - it is also the fallback if the built-in export fails, and it is
    preferred for Urdu/Hindi documents when installed, because its PDFs keep that text searchable."""
    from app.services import builtin_pdf
    from app.services.office_convert import available as libreoffice_available

    ext = Path(src).suffix.lower()
    out = out or str(Path(src).with_suffix(".pdf"))
    if ext in IMAGE_EXTS:
        return images_to_pdf([src], out)
    if ext in (".txt", ".md"):
        return text_to_pdf(src, out)
    lo = libreoffice_available()
    if builtin_pdf.can_export(src, libreoffice=lo) and not (lo and builtin_pdf.has_complex_script(src)):
        if progress:
            progress(10, os.path.basename(src))
        try:
            builtin_pdf.export(src, out)
            return out
        except Exception as exc:
            log.warning("Built-in PDF export of %s failed: %s", src, exc)
            if not lo:
                raise PdfOpError(f"Could not convert {os.path.basename(src)}: {exc}") from exc
    if ext in OFFICE_EXTS:
        from app.services.office_convert import ConversionError, convert

        try:
            res = convert(src, "pdf", tempfile.mkdtemp(prefix="myoffice_pdf_"))
        except ConversionError as exc:
            raise PdfOpError(str(exc)) from exc
        shutil.move(res, out)
        return out
    if ext == ".pdf":
        shutil.copyfile(src, out)
        return out
    raise PdfOpError(f"Cannot convert {ext} files to PDF")


def images_to_pdf(images: list[str], out: str, page_size: str = "fit") -> str:
    doc = pymupdf.open()
    for path in images:
        try:
            img = pymupdf.open(path)
            rect = img[0].rect
            pdfbytes = img.convert_to_pdf()
            img.close()
        except Exception as exc:
            raise PdfOpError(f"Cannot read image {path}: {exc}") from exc
        imgpdf = pymupdf.open("pdf", pdfbytes)
        if page_size == "a4":
            page = doc.new_page(width=595, height=842)
            r = page.rect + (36, 36, -36, -36)
            page.show_pdf_page(r, imgpdf, 0, keep_proportion=True)
        else:
            page = doc.new_page(width=rect.width, height=rect.height)
            page.show_pdf_page(page.rect, imgpdf, 0)
        imgpdf.close()
    tmp = _atomic_target(out)
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    return _finish(tmp, out)


def text_to_pdf(src: str, out: str) -> str:
    with open(src, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    import html

    body = html.escape(text).replace("\n", "<br>")
    doc = pymupdf.open()
    story = pymupdf.Story(html=f"<body style='font-family:sans-serif;font-size:11pt'>{body}</body>")
    writer_tmp = _atomic_target(out)
    writer = pymupdf.DocumentWriter(writer_tmp)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (54, 54, -54, -54)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc.close()
    return _finish(writer_tmp, out)


def pdf_to_images(src: str, outdir: str, dpi: int = 150, fmt: str = "png", pages: str = "", progress=None,
                  password: str | None = None) -> list[str]:
    doc = open_pdf(src, password)
    os.makedirs(outdir, exist_ok=True)
    sel = [p for g in parse_ranges(pages, doc.page_count) for p in g] if pages else range(doc.page_count)
    outs = []
    base = Path(src).stem
    for k, i in enumerate(sel):
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        path = os.path.join(outdir, f"{base}_p{i + 1:03d}.{fmt}")
        pix.save(path, jpg_quality=88) if fmt in ("jpg", "jpeg") else pix.save(path)
        outs.append(path)
        if progress:
            progress(int(100 * (k + 1) / len(sel)), f"Page {i + 1}")
    doc.close()
    return outs


def pdf_to_text(src: str, out: str, password: str | None = None) -> str:
    doc = open_pdf(src, password)
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"--- Page {i + 1} ---\n" + fix_rtl_text(page.get_text("text", sort=True)))
    doc.close()
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    os.replace(tmp, out)
    return out


def pdf_to_docx(src: str, out: str, progress=None, password: str | None = None) -> str:
    """Reflow PDF text into an editable Word document (headings by font size, bold/italic, images, tables)."""
    import docx
    from docx.shared import Pt

    doc = open_pdf(src, password)
    d = docx.Document()
    sizes = []
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for sp in line["spans"]:
                    if sp["text"].strip():
                        sizes.append(round(sp["size"]))
    body_size = max(set(sizes), key=sizes.count) if sizes else 11
    for pi, page in enumerate(doc):
        if pi:
            d.add_page_break()
        try:
            tables = page.find_tables()
            table_rects = [pymupdf.Rect(t.bbox) for t in tables.tables]
        except Exception:
            tables, table_rects = None, []
        items = []
        for b in page.get_text("dict")["blocks"]:
            rect = pymupdf.Rect(b["bbox"])
            if any(rect.intersects(tr) for tr in table_rects):
                continue
            items.append((rect.y0, "block", b))
        for ti, tr in enumerate(table_rects):
            items.append((tr.y0, "table", tables.tables[ti]))
        items.sort(key=lambda x: x[0])
        for _y, kind, obj in items:
            if kind == "table":
                data = obj.extract()
                if not data:
                    continue
                t = d.add_table(rows=len(data), cols=max(len(r) for r in data))
                t.style = "Table Grid"
                for i, row in enumerate(data):
                    for j, val in enumerate(row):
                        t.cell(i, j).text = "" if val is None else str(val)
                continue
            b = obj
            if b.get("type") == 1 and b.get("image"):
                try:
                    from docx.shared import Inches

                    w = min(6.0, (b["bbox"][2] - b["bbox"][0]) / 72)
                    d.add_picture(io.BytesIO(b["image"]), width=Inches(max(0.5, w)))
                except Exception:
                    pass
                continue
            for line in b.get("lines", []):
                spans = [s for s in line["spans"] if s["text"]]
                if not spans or not "".join(s["text"] for s in spans).strip():
                    continue
                size = max(s["size"] for s in spans)
                text = "".join(s["text"] for s in spans).strip()
                rtl_line = bool(_ARABIC.search(text))
                if rtl_line:
                    text = fix_rtl_text(text)
                if size >= body_size * 1.5 and len(text) < 120:
                    d.add_heading(text, level=1)
                    continue
                if size >= body_size * 1.2 and len(text) < 120:
                    d.add_heading(text, level=2)
                    continue
                p = d.add_paragraph()
                if rtl_line:  # one run in reading order, right-aligned bidi paragraph
                    r = p.add_run(text)
                    r.font.size = Pt(max(6, round(size)))
                    p.paragraph_format.alignment = 2
                    pPr = p._p.get_or_add_pPr()
                    from docx.oxml.ns import qn
                    from lxml import etree as _et

                    _et.SubElement(pPr, qn("w:bidi"))
                    continue
                for s in spans:
                    r = p.add_run(s["text"])
                    r.font.size = Pt(max(6, round(s["size"])))
                    flags = s.get("flags", 0)
                    r.bold = bool(flags & 16)
                    r.italic = bool(flags & 2)
        if progress:
            progress(int(100 * (pi + 1) / doc.page_count), f"Page {pi + 1}")
    doc.close()
    tmp = out + ".tmp.docx"
    d.save(tmp)
    os.replace(tmp, out)
    return out


def pdf_to_xlsx(src: str, out: str, password: str | None = None) -> str:
    import openpyxl

    doc = open_pdf(src, password)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    found = 0
    for pi, page in enumerate(doc):
        try:
            tabs = page.find_tables().tables
        except Exception:
            tabs = []
        for ti, t in enumerate(tabs):
            ws = wb.create_sheet(f"P{pi + 1}_T{ti + 1}"[:31])
            for row in t.extract():
                ws.append([_maybe_number(v) for v in row])
            found += 1
    if not found:  # no ruled tables: one row per text line, columns split on large gaps
        ws = wb.create_sheet("Text")
        for pi, page in enumerate(doc):
            for b in page.get_text("dict")["blocks"]:
                for line in b.get("lines", []):
                    cells, last_x = [], None
                    for s in line["spans"]:
                        if last_x is not None and s["bbox"][0] - last_x > 12:
                            cells.append("")
                        if cells and last_x is not None and s["bbox"][0] - last_x <= 12:
                            cells[-1] += s["text"]
                        else:
                            cells.append(s["text"])
                        last_x = s["bbox"][2]
                    if any(c.strip() for c in cells):
                        ws.append([_maybe_number(c.strip()) for c in cells if c.strip() != ""])
    doc.close()
    tmp = out + ".tmp.xlsx"
    wb.save(tmp)
    os.replace(tmp, out)
    return out


def _maybe_number(v):
    if v is None:
        return None
    s = str(v).strip()
    try:
        return float(s.replace(",", "")) if re.fullmatch(r"-?[\d,]+(\.\d+)?", s) else s
    except ValueError:
        return s


def pdf_to_pptx(src: str, out: str, dpi: int = 150, password: str | None = None) -> str:
    from pptx import Presentation
    from pptx.util import Emu

    doc = open_pdf(src, password)
    prs = Presentation()
    first = doc[0].rect
    prs.slide_width = Emu(int(first.width * 12700))
    prs.slide_height = Emu(int(first.height * 12700))
    for page in doc:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        s.shapes.add_picture(io.BytesIO(pix.tobytes("png")), 0, 0, prs.slide_width, prs.slide_height)
        notes = page.get_text().strip()
        if notes:
            s.notes_slide.notes_text_frame.text = notes[:5000]
    doc.close()
    tmp = out + ".tmp.pptx"
    prs.save(tmp)
    os.replace(tmp, out)
    return out


def from_pdf(src: str, target: str, out: str | None = None, progress=None, password: str | None = None):
    target = target.lower().lstrip(".")
    base = str(Path(src).with_suffix(""))
    if target == "docx":
        return pdf_to_docx(src, out or base + ".docx", progress, password)
    if target == "xlsx":
        return pdf_to_xlsx(src, out or base + ".xlsx", password)
    if target == "pptx":
        return pdf_to_pptx(src, out or base + ".pptx", password=password)
    if target == "txt":
        return pdf_to_text(src, out or base + ".txt", password)
    if target in ("png", "jpg"):
        return pdf_to_images(src, out or base + "_images", fmt=target, progress=progress, password=password)
    raise PdfOpError(f"Unsupported target format {target}")


# ============================================================================= 6. merge
def merge_pdfs(inputs: list[str], out: str, bookmarks: bool = True, passwords: dict | None = None, progress=None) -> str:
    from pypdf import PdfReader, PdfWriter

    if len(inputs) < 1:
        raise PdfOpError("Select at least one PDF to merge")
    writer = PdfWriter()
    for i, path in enumerate(inputs):
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                pw = (passwords or {}).get(path, "")
                if not reader.decrypt(pw):
                    raise PdfOpError(f"{os.path.basename(path)} is encrypted; provide its password")
        except PdfOpError:
            raise
        except Exception as exc:
            raise PdfOpError(f"Cannot read {os.path.basename(path)}: {exc}") from exc
        start = len(writer.pages)
        writer.append(reader)
        if bookmarks:
            writer.add_outline_item(Path(path).stem, start)
        if progress:
            progress(int(100 * (i + 1) / len(inputs)), os.path.basename(path))
    tmp = _atomic_target(out)
    with open(tmp, "wb") as fh:
        writer.write(fh)
    writer.close()
    return _finish(tmp, out)


# ============================================================================= 7. split
def split_pdf(src: str, outdir: str, mode: str = "ranges", ranges: str = "", every: int = 1,
              password: str | None = None, progress=None) -> list[str]:
    """mode: 'ranges' (one file per range), 'every' (chunks of N pages), 'bookmarks' (top-level outline)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(src)
    if reader.is_encrypted and not reader.decrypt(password or ""):
        raise PdfOpError("The PDF is encrypted; provide the password")
    n = len(reader.pages)
    if mode == "every":
        every = max(1, int(every))
        groups = [list(range(i, min(n, i + every))) for i in range(0, n, every)]
        names = [f"p{g[0] + 1}-{g[-1] + 1}" if len(g) > 1 else f"p{g[0] + 1}" for g in groups]
    elif mode == "bookmarks":
        tops = []
        for item in reader.outline:
            if isinstance(item, list):
                continue
            try:
                tops.append((reader.get_destination_page_number(item), item.title))
            except Exception:
                continue
        tops = sorted(set(tops))
        if not tops:
            raise PdfOpError("The PDF has no bookmarks to split by")
        groups, names = [], []
        for k, (start, title) in enumerate(tops):
            end = tops[k + 1][0] if k + 1 < len(tops) else n
            if end > start:
                groups.append(list(range(start, end)))
                names.append(re.sub(r"[^\w\- ]+", "_", title)[:50] or f"part{k + 1}")
    else:
        groups = parse_ranges(ranges, n)
        names = [f"p{g[0] + 1}-{g[-1] + 1}" if len(g) > 1 else f"p{g[0] + 1}" for g in groups]
    os.makedirs(outdir, exist_ok=True)
    outs = []
    base = Path(src).stem
    for k, (g, name) in enumerate(zip(groups, names)):
        w = PdfWriter()
        for i in g:
            w.add_page(reader.pages[i])
        path = os.path.join(outdir, f"{base}_{name}.pdf")
        with open(path, "wb") as fh:
            w.write(fh)
        outs.append(path)
        if progress:
            progress(int(100 * (k + 1) / len(groups)), os.path.basename(path))
    return outs


def extract_pages(src: str, out: str, ranges: str, password: str | None = None) -> str:
    doc = open_pdf(src, password)
    sel = [p for g in parse_ranges(ranges, doc.page_count) for p in g]
    new = pymupdf.open()
    for i in sel:
        new.insert_pdf(doc, from_page=i, to_page=i)
    tmp = _atomic_target(out)
    new.save(tmp, garbage=3, deflate=True)
    new.close()
    doc.close()
    return _finish(tmp, out)


# ============================================================================= 8. compress
@dataclass
class CompressResult:
    output: str
    before: int
    after: int
    method: str

    @property
    def saved_pct(self) -> float:
        return 100.0 * (1 - self.after / self.before) if self.before else 0.0


def find_ghostscript() -> str | None:
    """Ghostscript shipped with MyOffice (``gs\\gswin64c.exe`` next to MyOffice.exe) first, then a system copy."""
    from app.services.paths import bundled_program

    bundled = bundled_program("gs", "gswin64c.exe")
    if bundled:
        return bundled
    for name in ("gswin64c", "gswin32c", "gs"):
        p = shutil.which(name)
        if p:
            return p
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            gsdir = Path(base) / "gs"
            if gsdir.is_dir():
                for exe in sorted(gsdir.glob("*/bin/gswin64c.exe"), reverse=True):
                    return str(exe)
    return None


def compress_pdf(src: str, out: str, level: str = "ebook", progress=None) -> CompressResult:
    """level: screen (72 dpi), ebook (150 dpi), printer (300 dpi), prepress, lossless."""
    before = os.path.getsize(src)
    tmp = _atomic_target(out)
    gs = find_ghostscript()
    method = ""
    if gs and level != "lossless":
        if progress:
            progress(10, "Ghostscript")
        cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", f"-dPDFSETTINGS=/{level}", "-dNOPAUSE",
               "-dQUIET", "-dBATCH", "-dDetectDuplicateImages=true", "-dCompressFonts=true", f"-sOutputFile={tmp}", src]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            if r.returncode == 0 and os.path.getsize(tmp) > 0:
                method = f"Ghostscript /{level}"
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Ghostscript failed: %s", exc)
    if not method:
        if progress:
            progress(20, "Recompressing images")
        doc = pymupdf.open(src)
        dpi = {"screen": 72, "ebook": 150, "printer": 300, "prepress": 300}.get(level)
        if dpi:
            try:
                doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality={"screen": 50, "ebook": 70}.get(level, 85),
                                   lossy=True, lossless=True, bitonal=True, color=True, gray=True, set_to_gray=False)
            except Exception as exc:
                log.warning("rewrite_images failed: %s", exc)
        try:
            doc.subset_fonts()
        except Exception:
            pass
        doc.save(tmp, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)
        doc.close()
        method = "PyMuPDF " + (f"{dpi} dpi" if dpi else "lossless")
    after = os.path.getsize(tmp)
    if after >= before:  # never make it bigger
        shutil.copyfile(src, tmp)
        after = before
        method += " (original kept: already optimal)"
    _finish(tmp, out)
    if progress:
        progress(100, "Done")
    return CompressResult(out, before, after, method)


# ============================================================================= 9. OCR
def tesseract_cmd() -> str | None:
    from app.services.config import cfg

    custom = cfg().get("ocr.tesseract_cmd", "")
    if custom and os.path.exists(custom):
        return custom
    from app.services.paths import bundled_program

    bundled = bundled_program("tesseract", "tesseract.exe")  # shipped inside MyOffice Setup
    if bundled:
        return bundled
    p = shutil.which("tesseract")
    if p:
        return p
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            exe = Path(base) / "Tesseract-OCR" / "tesseract.exe"
            if exe.exists():
                return str(exe)
    return None


_ORIG_TESSDATA = os.environ.get("TESSDATA_PREFIX")


def private_tessdata() -> Path | None:
    """OCR language data that MyOffice Setup installs next to MyOffice.exe (Urdu, Hindi, English...).

    Keeping it inside the program folder means uninstalling MyOffice removes it, and a Tesseract
    installation of the user's own is left untouched."""
    from app.services.paths import install_dir

    base = install_dir()
    if base is None:
        return None
    d = base / "tessdata"
    return d if d.is_dir() and any(d.glob("*.traineddata")) else None


def _select_tessdata(lang: str | None) -> None:
    """Point Tesseract at MyOffice's language folder when it has *every* requested language,
    otherwise at Tesseract's own data (or whatever TESSDATA_PREFIX the user configured)."""
    d = private_tessdata()
    wanted = [x for x in (lang or "").split("+") if x]
    if d is not None and wanted and all((d / f"{x}.traineddata").exists() for x in wanted):
        os.environ["TESSDATA_PREFIX"] = str(d)
    elif _ORIG_TESSDATA is None:
        os.environ.pop("TESSDATA_PREFIX", None)
    else:
        os.environ["TESSDATA_PREFIX"] = _ORIG_TESSDATA


def ocr_languages() -> list[str]:
    import pytesseract

    cmd = tesseract_cmd()
    if not cmd:
        return []
    pytesseract.pytesseract.tesseract_cmd = cmd
    langs: set[str] = set()
    try:
        _select_tessdata(None)
        langs |= set(pytesseract.get_languages(config=""))
    except Exception:
        pass
    d = private_tessdata()
    if d is not None:
        langs |= {p.stem for p in d.glob("*.traineddata")}
    return sorted(langs)


def usable_ocr_choices(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (code, label) OCR language choices whose languages are all installed - MyOffice Setup ships Urdu, Hindi
    and English, so e.g. "ara+eng" is only offered when Arabic data was added.  Without Tesseract every choice is
    kept and the OCR call itself explains what is missing."""
    try:
        have = set(ocr_languages())
    except Exception:
        have = set()
    if not have:
        return list(items)
    usable = [(code, label) for code, label in items if all(x in have for x in code.split("+") if x)]
    return usable or list(items)


def _setup_tesseract(lang: str | None = None):
    import pytesseract

    cmd = tesseract_cmd()
    if not cmd:
        raise PdfOpError("Tesseract OCR is not installed. Install it (with Urdu/English data) or set its path in "
                         "Settings \u2192 OCR.")
    pytesseract.pytesseract.tesseract_cmd = cmd
    _select_tessdata(lang)
    return pytesseract


def ocr_image(path_or_image, lang: str = "urd+eng") -> str:
    from PIL import Image

    pt = _setup_tesseract(lang)
    img = Image.open(path_or_image) if isinstance(path_or_image, (str, os.PathLike)) else path_or_image
    return pt.image_to_string(img, lang=lang)


RTL_LANGS = {"urd", "ara", "fas", "pus", "snd", "uig", "heb", "yid", "kur"}


def _add_tsv_text_layer(page, data: dict, scale: float) -> int:
    """Invisible text from Tesseract's word table, one span per line in *logical* order.

    Tesseract's own PDF renderer stores right-to-left words in visual order, which makes Urdu text
    unsearchable after extraction; building the layer from the TSV data keeps reading order intact."""
    from app.services.paths import resource_path

    fontfile = resource_path("fonts", "NotoNastaliqUrdu.ttf")
    lines: dict = {}
    for i, txt in enumerate(data["text"]):
        word = (txt or "").replace("\u200e", "").replace("\u200f", "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not word or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((word, data["left"][i], data["top"][i], data["width"][i], data["height"][i]))
    if not lines:
        return 0
    page.insert_font(fontname="ocrtl", fontfile=str(fontfile))
    font = pymupdf.Font(fontfile=str(fontfile))
    for key in sorted(lines):
        words = lines[key]
        text = " ".join(w[0] for w in words)
        x0 = min(w[1] for w in words) * scale
        y0 = min(w[2] for w in words) * scale
        x1 = max(w[1] + w[3] for w in words) * scale
        y1 = max(w[2] + w[4] for w in words) * scale
        size = max(3.0, (y1 - y0) * 0.75)
        width = font.text_length(text, fontsize=size)
        if width > 0 and width > (x1 - x0):
            size = max(2.0, size * (x1 - x0) / width)
        page.insert_text(pymupdf.Point(x0, y1 - (y1 - y0) * 0.2), text, fontname="ocrtl", fontsize=size, render_mode=3)
    return len(lines)


def _ocr_dpi(doc, page, dpi: int) -> int:
    """Never upscale a scanned page beyond the native resolution of its image (blurs glyphs)."""
    try:
        imgs = page.get_images(full=True)
        if len(imgs) == 1:
            width_px = doc.extract_image(imgs[0][0])["width"]
            native = width_px / max(1.0, page.rect.width / 72.0)
            if 70 <= native < dpi:
                return int(native)
    except Exception:
        pass
    return dpi


def ocr_pdf(src: str, out: str, lang: str = "urd+eng", dpi: int = 300, skip_text_pages: bool = True,
            progress=None, password: str | None = None) -> dict:
    """Add an invisible OCR text layer to every page (output stays visually identical)."""
    from PIL import Image

    pt = _setup_tesseract(lang)
    doc = open_pdf(src, password)
    done = skipped = 0
    rtl = any(code in RTL_LANGS for code in lang.split("+"))
    for i, page in enumerate(doc):
        if progress:
            progress(int(100 * i / max(1, doc.page_count)), f"OCR page {i + 1}/{doc.page_count}")
        if skip_text_pages and len(page.get_text().strip()) > 30:
            skipped += 1
            continue
        pix = page.get_pixmap(dpi=_ocr_dpi(doc, page, dpi), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if rtl:
            data = pt.image_to_data(img, lang=lang, output_type=pt.Output.DICT)
            _add_tsv_text_layer(page, data, page.rect.width / pix.width)
        else:
            pdf_bytes = pt.image_to_pdf_or_hocr(img, extension="pdf", lang=lang, config="-c textonly_pdf=1")
            ocr = pymupdf.open("pdf", pdf_bytes)
            page.show_pdf_page(page.rect, ocr, 0, overlay=True)
            ocr.close()
        done += 1
    tmp = _atomic_target(out)
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    _finish(tmp, out)
    if progress:
        progress(100, "Done")
    return {"output": out, "ocr_pages": done, "skipped_pages": skipped}


def ocr_to_text(src: str, out: str, lang: str = "urd+eng", dpi: int = 300, progress=None) -> str:
    """OCR images or PDFs straight to a UTF-8 text file."""
    from PIL import Image

    _setup_tesseract(lang)
    ext = Path(src).suffix.lower()
    parts = []
    if ext in IMAGE_EXTS:
        parts.append(ocr_image(src, lang))
    else:
        doc = open_pdf(src)
        for i, page in enumerate(doc):
            if progress:
                progress(int(100 * i / doc.page_count), f"OCR page {i + 1}")
            pix = page.get_pixmap(dpi=_ocr_dpi(doc, page, dpi), alpha=False)
            parts.append(f"--- Page {i + 1} ---\n" + ocr_image(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), lang))
        doc.close()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return out


# ============================================================================= 10. sign
def create_self_signed_p12(path: str, name: str, email: str = "", password: str = "", org: str = "",
                           days: int = 1095) -> str:
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, name)]
    if org:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    if email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email))
    subject = x509.Name(attrs)
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .sign(key, hashes.SHA256()))
    enc = serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
    data = pkcs12.serialize_key_and_certificates(name.encode(), key, cert, None, enc)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def sign_pdf(src: str, out: str, p12_path: str, p12_password: str = "", page: int = 0,
             box: tuple | None = None, reason: str = "", location: str = "", contact: str = "",
             field_name: str = "Signature1", appearance_text: str | None = None) -> str:
    """Cryptographically sign a PDF (PAdES via pyHanko).  ``box`` = (x1, y1, x2, y2) in PDF points
    from the bottom-left of page ``page`` (0-based) for a visible signature; None = invisible."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko.sign.fields import SigFieldSpec

    try:
        signer = signers.SimpleSigner.load_pkcs12(p12_path, passphrase=p12_password.encode() if p12_password else None)
    except Exception as exc:
        raise PdfOpError(f"Cannot load the certificate: {exc}") from exc
    if signer is None:
        raise PdfOpError("Cannot load the certificate (wrong password?)")
    tmp = _atomic_target(out)
    with open(src, "rb") as inf:
        w = IncrementalPdfFileWriter(inf, strict=False)
        existing = [f[0] for f in fields.enumerate_sig_fields(w)]
        name = field_name
        n = 1
        while name in existing:
            n += 1
            name = f"{field_name}_{n}"
        if box is not None:
            fields.append_signature_field(w, SigFieldSpec(sig_field_name=name, on_page=page, box=tuple(int(v) for v in box)))
        meta = signers.PdfSignatureMetadata(field_name=name, reason=reason or None, location=location or None,
                                            contact_info=contact or None)
        stamp_style = None
        if box is not None:
            from pyhanko.stamp import TextStampStyle

            stamp_style = TextStampStyle(stamp_text=appearance_text or "Digitally signed by %(signer)s\nDate: %(ts)s")
        pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=stamp_style)
        with open(tmp, "wb") as outf:
            pdf_signer.sign_pdf(w, output=outf)
    return _finish(tmp, out)


@dataclass
class SignatureInfo:
    field: str
    signer: str
    signed_at: str
    intact: bool
    valid: bool
    trusted: bool
    coverage: str
    summary: str = ""


def verify_signatures(path: str) -> list[SignatureInfo]:
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    out = []
    with open(path, "rb") as fh:
        r = PdfFileReader(fh, strict=False)
        for sig in r.embedded_signatures:
            vc = ValidationContext(allow_fetching=False, trust_roots=[sig.signer_cert])
            try:
                st = validate_pdf_signature(sig, vc)
                signer = sig.signer_cert.subject.human_friendly
                out.append(SignatureInfo(sig.field_name, signer, str(getattr(st, "signer_reported_dt", "") or ""),
                                         bool(st.intact), bool(st.valid), bool(st.trusted),
                                         str(st.coverage).split(".")[-1], st.summary()))
            except Exception as exc:
                out.append(SignatureInfo(sig.field_name, "?", "", False, False, False, "", f"Validation error: {exc}"))
    return out


def stamp_signature_image(src: str, out: str, image: str | bytes, page: int, rect: tuple, password: str | None = None) -> str:
    """'Fill & Sign': place a handwritten signature image (not cryptographic)."""
    doc = open_pdf(src, password)
    r = pymupdf.Rect(*rect)
    if isinstance(image, (bytes, bytearray)):
        doc[page].insert_image(r, stream=bytes(image), keep_proportion=True)
    else:
        doc[page].insert_image(r, filename=image, keep_proportion=True)
    tmp = _atomic_target(out)
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    return _finish(tmp, out)


# ============================================================================= 11. protect / unlock
def protect_pdf(src: str, out: str, user_password: str, owner_password: str | None = None, allow_print: bool = True,
                allow_modify: bool = False, allow_copy: bool = False, allow_annotate: bool = True,
                password: str | None = None) -> str:
    from pypdf import PdfReader, PdfWriter
    from pypdf.constants import UserAccessPermissions as P

    reader = PdfReader(src)
    if reader.is_encrypted and not reader.decrypt(password or ""):
        raise PdfOpError("The PDF is already encrypted; unlock it first")
    writer = PdfWriter(clone_from=reader)
    perms = P(0)
    if allow_print:
        perms |= P.PRINT | P.PRINT_TO_REPRESENTATION
    if allow_modify:
        perms |= P.MODIFY | P.ASSEMBLE_DOC
    if allow_copy:
        perms |= P.EXTRACT | P.EXTRACT_TEXT_AND_GRAPHICS
    if allow_annotate:
        perms |= P.ADD_OR_MODIFY | P.FILL_FORM_FIELDS
    writer.encrypt(user_password=user_password, owner_password=owner_password or user_password,
                   permissions_flag=perms, algorithm="AES-256")
    tmp = _atomic_target(out)
    with open(tmp, "wb") as fh:
        writer.write(fh)
    return _finish(tmp, out)


def unlock_pdf(src: str, out: str, password: str) -> str:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(src)
    if reader.is_encrypted:
        if not reader.decrypt(password):
            raise PdfOpError("Wrong password")
    writer = PdfWriter(clone_from=reader)
    tmp = _atomic_target(out)
    with open(tmp, "wb") as fh:
        writer.write(fh)
    return _finish(tmp, out)


def pdf_info(path: str) -> dict:
    doc = pymupdf.open(path)
    info = {"pages": doc.page_count if not doc.needs_pass else None, "encrypted": doc.is_encrypted,
            "needs_password": doc.needs_pass, "metadata": doc.metadata, "size": os.path.getsize(path)}
    doc.close()
    return info


# ============================================================================= 12. watermark
def _watermark_pdf_bytes(width: float, height: float, text: str = "", image: str | None = None, opacity: float = 0.25,
                         rotation: float = 45, font_size: float = 60, color: str = "#888888", tile: bool = False) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.saveState()
    c.setFillAlpha(opacity)
    c.setStrokeAlpha(opacity)
    font = "Helvetica-Bold"
    if text and re.search(r"[^\x00-\x7f]", text):
        for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
                     "C:/Windows/Fonts/segoeuib.ttf"):
            if os.path.exists(cand):
                try:
                    pdfmetrics.registerFont(TTFont("WMFont", cand))
                    font = "WMFont"
                except Exception:
                    pass
                break
        if re.search("[\u0600-\u06FF]", text):
            try:
                from app.sheet.charts import shape_label

                text = shape_label(text)
            except Exception:
                pass
    positions = [(width / 2, height / 2)]
    if tile:
        positions = [(x, y) for x in (width * 0.25, width * 0.75) for y in (height * 0.2, height * 0.5, height * 0.8)]
    for (x, y) in positions:
        c.saveState()
        c.translate(x, y)
        c.rotate(rotation)
        if image:
            ir = ImageReader(image)
            iw, ih = ir.getSize()
            scale = min(width * 0.5 / iw, height * 0.5 / ih) * (0.5 if tile else 1)
            c.drawImage(ir, -iw * scale / 2, -ih * scale / 2, iw * scale, ih * scale, mask="auto")
        if text:
            c.setFillColor(HexColor(color))
            fs = font_size * (0.6 if tile else 1)
            c.setFont(font, fs)
            c.drawCentredString(0, -fs / 3, text)
        c.restoreState()
    c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


def watermark_pdf(src: str, out: str, text: str = "", image: str | None = None, opacity: float = 0.25,
                  rotation: float = 45, font_size: float = 60, color: str = "#888888", tile: bool = False,
                  behind: bool = False, pages: str = "", password: str | None = None, progress=None) -> str:
    from pypdf import PdfReader, PdfWriter

    if not text and not image:
        raise PdfOpError("Give a watermark text or image")
    reader = PdfReader(src)
    if reader.is_encrypted and not reader.decrypt(password or ""):
        raise PdfOpError("The PDF is encrypted; provide the password")
    writer = PdfWriter(clone_from=reader)
    n = len(writer.pages)
    sel = set(p for g in parse_ranges(pages, n) for p in g) if pages else set(range(n))
    cache: dict = {}
    for i, page in enumerate(writer.pages):
        if i not in sel:
            continue
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        key = (round(w), round(h))
        if key not in cache:
            cache[key] = PdfReader(io.BytesIO(_watermark_pdf_bytes(w, h, text, image, opacity, rotation, font_size,
                                                                   color, tile))).pages[0]
        page.merge_page(cache[key], over=not behind)
        if progress:
            progress(int(100 * (i + 1) / n), f"Page {i + 1}")
    tmp = _atomic_target(out)
    with open(tmp, "wb") as fh:
        writer.write(fh)
    return _finish(tmp, out)


# ============================================================================= 13. extract
def extract_images(src: str, outdir: str, min_size: int = 32, password: str | None = None, progress=None) -> list[str]:
    doc = open_pdf(src, password)
    os.makedirs(outdir, exist_ok=True)
    seen, outs = set(), []
    base = Path(src).stem
    for pi, page in enumerate(doc):
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if info["width"] < min_size or info["height"] < min_size:
                continue
            path = os.path.join(outdir, f"{base}_p{pi + 1}_x{xref}.{info['ext']}")
            with open(path, "wb") as fh:
                fh.write(info["image"])
            outs.append(path)
        if progress:
            progress(int(100 * (pi + 1) / doc.page_count), f"Page {pi + 1}")
    doc.close()
    return outs


def extract_tables(src: str, out: str, password: str | None = None) -> str:
    return pdf_to_xlsx(src, out, password)


# ============================================================================= 14. edit helpers (used by the editor)
def replace_text(src: str, out: str, find: str, replace: str, password: str | None = None) -> int:
    """Find & replace text: redact each occurrence and write the new text at the same place."""
    doc = open_pdf(src, password)
    count = 0
    for page in doc:
        hits = page.search_for(find)
        if not hits:
            continue
        infos = []
        for r in hits:
            size, color = 11.0, (0, 0, 0)
            for b in page.get_text("dict", clip=r)["blocks"]:
                for line in b.get("lines", []):
                    for sp in line["spans"]:
                        size = sp["size"]
                        c = sp["color"]
                        color = ((c >> 16) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)
            infos.append((r, size, color))
            page.add_redact_annot(r, fill=(1, 1, 1))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        for r, size, color in infos:
            page.insert_text(pymupdf.Point(r.x0, r.y1 - size * 0.2), replace, fontsize=size, fontname="helv", color=color)
            count += 1
    tmp = _atomic_target(out)
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    _finish(tmp, out)
    return count


# ============================================================================= 15. stamps, page numbers & flattening
def add_page_numbers_pdf(src: str, out: str, fmt: str = "Page {n} of {total}", position: str = "bottom-center",
                         start_at: int = 1, font_size: float = 10.0, margin: float = 26.0, color: str = "#334155",
                         pages: str = "", password: str | None = None, progress=None) -> str:
    """ Stamp clean vector page numbers / Bates numbers onto *src*."""
    doc = open_pdf(src, password)
    total = doc.page_count
    sel = set(p for g in parse_ranges(pages, total) for p in g) if pages.strip() else set(range(total))
    hx = color.lstrip("#")
    rgb = tuple(int(hx[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) if len(hx) == 6 else (0.2, 0.25, 0.33)

    align_map = {"left": 0, "center": 1, "right": 2}
    vpos, hpos = (position.split("-", 1) + ["center"])[:2]
    align = align_map.get(hpos, 1)

    for idx, page in enumerate(doc):
        if idx in sel:
            num = start_at + idx
            label = fmt.format(n=num, total=total + start_at - 1)
            r = page.rect
            box_h = font_size * 2.1
            y0 = (margin if vpos == "top" else max(4.0, r.height - margin - box_h))
            box = pymupdf.Rect(margin, y0, r.width - margin, y0 + box_h)
            page.insert_textbox(box, label, fontsize=font_size, fontname="helv", color=rgb, align=align)
        if progress:
            progress(int(100 * (idx + 1) / max(1, total)), f"Page {idx + 1}")

    tmp = _atomic_target(out)
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    return _finish(tmp, out)


def stamp_pdf(src: str, out: str, label: str = "APPROVED", subtitle: str = "", color: str = "#DC2626",
              position: str = "top-right", scale: float = 1.0, pages: str = "1",
              password: str | None = None, progress=None) -> str:
    """Apply a crisp vector commercial rubber stamp (double border + title + date/author line) to selected pages."""
    doc = open_pdf(src, password)
    total = doc.page_count
    sel = set(p for g in parse_ranges(pages, total) for p in g) if pages.strip() else set(range(total))
    hx = color.lstrip("#")
    rgb = tuple(int(hx[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) if len(hx) == 6 else (0.86, 0.15, 0.15)
    tint = tuple(min(1.0, c * 0.12 + 0.88) for c in rgb)

    w_box = 168.0 * max(0.5, scale)
    h_box = (52.0 if subtitle else 38.0) * max(0.5, scale)
    margin = 28.0

    for idx, page in enumerate(doc):
        if idx in sel:
            pr = page.rect
            x0 = pr.width - margin - w_box if "right" in position else (
                (pr.width - w_box) / 2 if "center" in position else margin
            )
            y0 = margin if "top" in position else (
                (pr.height - h_box) / 2 if "middle" in position else pr.height - margin - h_box
            )
            rect = pymupdf.Rect(x0, y0, x0 + w_box, y0 + h_box)
            inner = pymupdf.Rect(x0 + 3, y0 + 3, x0 + w_box - 3, y0 + h_box - 3)
            sh = page.new_shape()
            sh.draw_rect(rect)
            sh.finish(color=rgb, fill=tint, width=2.0)
            sh.draw_rect(inner)
            sh.finish(color=rgb, width=0.8)
            sh.commit()

            title_rect = pymupdf.Rect(x0 + 6, y0 + (6 if subtitle else 9), x0 + w_box - 6, y0 + h_box - (16 if subtitle else 4))
            page.insert_textbox(title_rect, label.upper(), fontsize=13.5 * scale, fontname="hebo", color=rgb, align=1)
            if subtitle:
                sub_rect = pymupdf.Rect(x0 + 6, y0 + h_box - 17 * scale, x0 + w_box - 6, y0 + h_box - 3)
                page.insert_textbox(sub_rect, subtitle, fontsize=7.5 * scale, fontname="helv", color=rgb, align=1)
        if progress:
            progress(int(100 * (idx + 1) / max(1, total)), f"Page {idx + 1}")

    tmp = _atomic_target(out)
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    return _finish(tmp, out)


def flatten_pdf(src: str, out: str, password: str | None = None, progress=None) -> str:
    """Permanently bake interactive forms, annotations, highlights, and stamps into static PDF content streams."""
    doc = open_pdf(src, password)
    if progress:
        progress(35, "Baking forms & annotations\u2026")
    doc.bake()
    tmp = _atomic_target(out)
    doc.save(tmp, garbage=4, deflate=True)
    doc.close()
    if progress:
        progress(100, "Done")
    return _finish(tmp, out)


@dataclass
class ToolResult:
    outputs: list = field(default_factory=list)
    message: str = ""
