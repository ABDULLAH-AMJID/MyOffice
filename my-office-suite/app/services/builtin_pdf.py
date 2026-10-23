"""Office documents -> PDF with MyOffice's own engines, so no external program is needed.

* Word (.docx .docm .dotx .dotm, OOXML .wpt) and HTML  -> Writer layout engine  (app.writer.render)
* Excel (.xlsx .xlsm .xltx .xltm, OOXML .ett), CSV/TSV -> Spreadsheet printing  (app.sheet.printing)
  plus legacy .xls/.et (cell values, via xlrd) when LibreOffice is not installed
* PowerPoint (.pptx .pptm .ppsx .potx, OOXML .dpt)     -> Presentation renderer (app.slide.canvas)

These are the same exporters as File > Export to PDF in the editors.  LibreOffice is only needed for
the old binary and OpenDocument formats (.doc .ppt .odt .ods .odp .rtf ...), see legacy_pack.py.

The renderers use QPixmap / QGraphicsScene, which Qt allows in the GUI thread only.  A call from a
worker thread (PDF Toolbox, ``run_task``) is therefore executed in the GUI thread while the worker
waits.  Without a QApplication (``MyOffice.exe --convert``) an offscreen one is created.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path

WRITER_EXTS = (".docx", ".docm", ".dotx", ".dotm", ".html", ".htm")
SHEET_EXTS = (".xlsx", ".xlsm", ".xltx", ".xltm", ".csv", ".tsv")
SLIDE_EXTS = (".pptx", ".pptm", ".ppsx", ".potx")
# WPS template extensions contain OOXML when they are zip files, the legacy binary formats otherwise
OOXML_TEMPLATES = {".wpt": "writer", ".ett": "sheet", ".dpt": "slide"}
LEGACY_SHEET_EXTS = (".xls", ".et")


def kind_of(path: str, libreoffice: bool = False) -> str | None:
    """'writer', 'sheet', 'slide' or None.  ``libreoffice``: LibreOffice is installed, so legacy
    .xls files go there (keeps the formatting) instead of the values-only reader."""
    ext = Path(path).suffix.lower()
    if ext in WRITER_EXTS:
        return "writer"
    if ext in SHEET_EXTS:
        return "sheet"
    if ext in SLIDE_EXTS:
        return "slide"
    if ext in OOXML_TEMPLATES and os.path.isfile(path) and zipfile.is_zipfile(path):
        return OOXML_TEMPLATES[ext]
    if ext in LEGACY_SHEET_EXTS and not libreoffice:
        return "sheet"
    return None


def can_export(path: str, libreoffice: bool = False) -> bool:
    return kind_of(path, libreoffice) is not None


# Arabic-script (Urdu) and Indic (Hindi...) text: Qt's PDF engine draws it correctly, but the shaped
# glyphs get no usable Unicode mapping, so the text cannot be searched or copied in the PDF.
_COMPLEX_SCRIPT = re.compile("[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff\u0900-\u0dff]")


def has_complex_script(path: str, limit: int = 32 << 20) -> bool:
    """True if an OOXML/HTML/CSV document contains right-to-left or Indic text (reads at most ``limit`` bytes)."""
    try:
        if zipfile.is_zipfile(path):
            budget = limit
            with zipfile.ZipFile(path) as z:
                for info in z.infolist():
                    name = info.filename.lower()
                    if not name.endswith(".xml") or "/media/" in name or budget <= 0:
                        continue
                    with z.open(info) as fh:
                        data = fh.read(min(info.file_size, budget))
                    budget -= len(data)
                    if _COMPLEX_SCRIPT.search(data.decode("utf-8", "ignore")):
                        return True
            return False
        with open(path, "rb") as fh:
            return bool(_COMPLEX_SCRIPT.search(fh.read(limit).decode("utf-8", "ignore")))
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return False


def _ensure_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:  # command line: render without showing anything
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0] if sys.argv else "MyOffice"])
        from app.services import fonts

        fonts.load_bundled_fonts()
        fonts.install_substitutions()
    return app


def call_in_gui_thread(fn):
    """Run ``fn()`` in the GUI thread and return its result (re-raising its exception).

    From a worker thread the call is queued to the GUI thread and the worker blocks until it is done;
    the GUI thread must be running an event loop (it is: run_task waits in a QEventLoop)."""
    from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal, Slot

    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() == app.thread():
        return fn()

    class _Call(QObject):
        request = Signal(object)

        def __init__(self):
            super().__init__()
            self.request.connect(self.run, Qt.BlockingQueuedConnection)

        @Slot(object)
        def run(self, box):
            try:
                box["result"] = box["fn"]()
            except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
                box["error"] = exc

    call = _Call()
    call.moveToThread(app.thread())
    box = {"fn": fn}
    call.request.emit(box)  # returns when run() has finished in the GUI thread
    call.deleteLater()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def export(src: str, out: str) -> int:
    """Render ``src`` to the PDF file ``out`` (written atomically).  Returns the number of pages."""
    kind = kind_of(src)
    if kind is None:
        raise ValueError(f"MyOffice cannot export {Path(src).suffix or src} files to PDF by itself")
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    from app.pdf.ops import _atomic_target, _finish

    _ensure_app()
    tmp = _atomic_target(out)
    try:
        pages = call_in_gui_thread(lambda: _EXPORTERS[kind](src, tmp))
        if not pages or os.path.getsize(tmp) == 0:
            raise ValueError("the document has no printable content")
        _finish(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return pages


def _writer_pdf(src: str, out: str) -> int:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage, QTextDocument

    from app.writer import render
    from app.writer.docx_io import load_docx
    from app.writer.model import HeaderFooter, PageSetup

    doc = QTextDocument()
    title = Path(src).stem
    if Path(src).suffix.lower() in (".html", ".htm"):
        from app.writer.mailmerge import _decode

        with open(src, "rb") as fh:
            doc.setHtml(_decode(fh.read()))
        return render.export_pdf(doc, PageSetup(), HeaderFooter(), out, title=title)
    counter = [0]

    def register_image(data: bytes, ext: str, img: QImage | None = None) -> str:
        counter[0] += 1
        name = f"img_{counter[0]}.{ext}"
        doc.addResource(QTextDocument.ImageResource, QUrl(name), img if img is not None else QImage.fromData(data))
        return name

    imp = load_docx(src, doc, register_image)
    return render.export_pdf(doc, imp.page, imp.hf, out, title=imp.title or title, watermark=imp.watermark)


def _sheet_pdf(src: str, out: str) -> int:
    from app.sheet.model import Book
    from app.sheet.printing import export_pdf
    from app.sheet.xlsx_io import load_any

    book = Book()
    load_any(src, book)
    if not book.sheets:
        book.add_sheet()
    return export_pdf(book, out, title=Path(src).stem)


def _slide_pdf(src: str, out: str) -> int:
    from app.slide.canvas import export_pdf
    from app.slide.pptx_io import load_pptx

    pres = load_pptx(src)
    return export_pdf(pres, out, title=Path(src).stem)


_EXPORTERS = {"writer": _writer_pdf, "sheet": _sheet_pdf, "slide": _slide_pdf}

__all__ = ["can_export", "kind_of", "export", "call_in_gui_thread", "has_complex_script"]
