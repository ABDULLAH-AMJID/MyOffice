"""All 42 tools are registered and resolvable; shell opens every document type."""
import importlib

from app.shell.tools_registry import TOOLS


def test_all_tools_resolve(qapp):
    from app.shell.main_window import MainWindow

    assert len(TOOLS) == 49
    win = MainWindow()
    for t in TOOLS:
        mod, _, attr = t.handler.partition(":")
        if mod == "main":
            assert callable(getattr(win, attr)), t.id
        else:
            assert callable(getattr(importlib.import_module(mod), attr)), t.id
    win.close()


def test_open_all_document_kinds(qapp, tmp_path, sample_pdfs):
    import docx
    import openpyxl
    from pptx import Presentation

    from app.shell.main_window import MainWindow

    d = docx.Document()
    d.add_paragraph("hi")
    d.save(str(tmp_path / "a.docx"))
    wb = openpyxl.Workbook()
    wb.active["A1"] = 1
    wb.save(str(tmp_path / "a.xlsx"))
    Presentation().save(str(tmp_path / "a.pptx"))
    from PIL import Image

    Image.new("RGB", (64, 48), "#3366cc").save(str(tmp_path / "a.png"))
    win = MainWindow()
    kinds = []
    for p in (tmp_path / "a.docx", tmp_path / "a.xlsx", tmp_path / "a.pptx", sample_pdfs[0], tmp_path / "a.png"):
        doc = win.open_path(str(p))
        assert doc is not None
        kinds.append(doc.kind)
    assert kinds == ["writer", "sheet", "slide", "pdf", "photos"]
    tb = win.open_toolbox()
    assert len(tb.forms) == 13
    for doc in win.documents():
        doc.set_modified(False)
    win.close()


def test_untitled_documents_are_numbered_per_type(qapp, tmp_path):
    """Found in the installed 1.2 build: after opening three files the first new presentation was called
    "Presentation 4", because every document - also opened files - took a number from one shared counter."""
    import re

    from pptx import Presentation

    from app.shell.main_window import MainWindow

    Presentation().save(str(tmp_path / "opened.pptx"))
    win = MainWindow()

    def number(doc):
        return int(re.fullmatch(r".* (\d+)", doc.display_name()).group(1))

    first = win.new_document("slide")
    book = win.new_document("sheet")
    opened = win.open_path(str(tmp_path / "opened.pptx"))
    assert opened.display_name() == "opened.pptx"
    second = win.new_document("slide")
    assert number(second) == number(first) + 1          # opened files and other types use no number
    assert number(win.new_document("sheet")) == number(book) + 1
    assert first.display_name().startswith("Presentation ") and book.display_name().startswith("Book ")
    assert len({d.doc_id for d in win.documents()}) == len(win.documents())   # recovery ids stay unique
    for doc in win.documents():
        doc.set_modified(False)
    win.close()


def test_home_page_has_no_empty_ribbon_band(qapp):
    """Found in the installed 1.2 build: with documents open, the Home page showed an empty band as tall as a ribbon
    (a QStackedWidget is as tall as its tallest page); a collapsed ribbon had the same band under it."""
    from PySide6.QtWidgets import QApplication

    from app.shell.main_window import MainWindow

    def settle():
        for _ in range(4):
            QApplication.processEvents()

    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    doc = win.new_document("writer")
    win.new_document("slide")
    win.focus_page(doc)
    settle()
    full = win.ribbon_stack.height()
    assert full >= 100
    win.focus_page(win.home)
    settle()
    assert win.ribbon_stack.height() == 0
    win.focus_page(doc)
    settle()
    assert win.ribbon_stack.height() == full
    doc.ribbon.toggle_collapsed()           # Ctrl+F1 / double-click on a ribbon tab
    settle()
    assert win.ribbon_stack.height() < full / 2
    for d in win.documents():
        d.set_modified(False)
    win.close()


def test_restored_window_stays_maximized(qapp):
    """Found in the installed 1.2 build: after a screen recording (which minimizes MyOffice) the maximized window
    came back un-maximized, because showNormal() also clears "maximized".  The same applied when a second instance
    brought the window to the front."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    from app.shell.dialogs import restore_window

    w = QWidget()
    w.showMaximized()
    w.showMinimized()
    assert w.windowState() & Qt.WindowState.WindowMinimized
    restore_window(w)
    state = w.windowState()
    assert state & Qt.WindowState.WindowMaximized and not state & Qt.WindowState.WindowMinimized
    w.showNormal()
    w.showMinimized()
    restore_window(w)
    assert not w.windowState() & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized)
    w.close()
