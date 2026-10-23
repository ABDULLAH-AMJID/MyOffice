"""Spreadsheets 1.2 (.xls, tables, VBA, external data, AI context) and Presentation 1.2 (diagrams, WordArt,
countdown, ink, presenter view, design ideas, AI slides)."""
import sqlite3
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt

from app.sheet import features as SF
from app.sheet.model import Book, Border, Style
from app.slide import features as PF

DATA = Path(__file__).parent / "data"


class _SheetStub:
    """The parts of SheetDocument the features use (undo -> apply directly)."""

    def __init__(self, book, sheet, rc=(1, 1), sel=None):
        self.book, self.sheet, self._rc, self._sel = book, sheet, rc, sel or (1, 1, 1, 1)
        self.modified = False

    def push_states(self, before, after, label):
        self.book.apply_states(self.sheet, after)

    def set_modified(self, v):
        self.modified = v

    def current_rc(self):
        return self._rc

    def sel_bounds(self, clamp=True):
        return self._sel


def _sales_book():
    b = Book()
    sh = b.add_sheet("Sales")
    for r, row in enumerate([("Region", "Q1", "Q2", "Total"), ("North", 120, 135, "=B2+C2"),
                             ("South", 90, 80.5, "=B3+C3"), ("East", 150, 170, "=B4>200")], 1):
        for c, v in enumerate(row, 1):
            b.set_input(sh, r, c, str(v))
    return b, sh


def test_xls_save_and_read_back(tmp_path):
    b, sh = _sales_book()
    head = b.styles.intern(Style(bold=True, fill="#4472c4", color="#ffffff", border_bottom=Border("thin")))
    for c in range(1, 5):
        sh.cells[(1, c)].style = head
    sh.col_widths[1] = 140
    sh.merges.append((6, 1, 6, 4))
    sh.freeze = (1, 0)
    path = str(tmp_path / "t.xls")
    assert SF.save_xls(path, b) == []
    b2 = Book()
    SF.load_xls_formatted(path, b2)
    s2 = b2.sheets[0]
    assert [s2.display(r, 4)[0] for r in (2, 3, 4)] == ["255", "170.5", "FALSE"]  # cached formula results
    st = b2.styles.get(s2.cells[(1, 1)].style)
    assert st.bold and st.fill == "#4472c4" and st.color == "#ffffff"
    assert s2.merges == [(6, 1, 6, 4)] and s2.freeze == (1, 0) and abs(s2.col_widths[1] - 140) <= 2


def test_format_as_table_is_saved_as_excel_table(tmp_path):
    from app.sheet.xlsx_io import load_xlsx, save_xlsx

    b, sh = _sales_book()
    w = _SheetStub(b, sh)
    assert SF.current_region(sh, 2, 2) == (1, 1, 4, 4)
    SF.apply_table(w, 1, 1, 4, 4, "TableStyleMedium7", True, "SalesTable")
    assert b.styles.get(sh.cells[(1, 1)].style).bold and w.modified
    path = str(tmp_path / "t.xlsx")
    save_xlsx(path, b)
    import zipfile

    xml = zipfile.ZipFile(path).read("xl/tables/table1.xml").decode()
    assert 'displayName="SalesTable"' in xml and "TableStyleMedium7" in xml
    b2 = Book()
    load_xlsx(path, b2)
    assert b2.sheets[0].tables == [{"name": "SalesTable", "ref": "A1:D4", "style": "TableStyleMedium7",
                                    "header": True}]
    assert not SF.table_name_ok(b2, "salestable") and SF.table_name_ok(b2, "Other_1")


def test_vba_viewer_and_macros_kept_in_xlsm(tmp_path):
    from app.sheet.xlsx_io import load_xlsx, save_xlsx

    # MS-OVBA 3.2 compression examples
    assert SF._decompress(bytes.fromhex("01 19 B0 00 61 62 63 64 65 66 67 68 00 69 6A 6B 6C 6D 6E 6F 70 00 71 72 73 "
                                        "74 75 76 2E")) == b"abcdefghijklmnopqrstuv."
    data = (DATA / "SimpleMacro.xlsm").read_bytes()
    mods = dict(SF.vba_modules(SF.vba_from_package(data)))
    assert "Module1" in mods and "Sub TestMacro()" in mods["Module1"]
    b = Book()
    info = load_xlsx(str(DATA / "SimpleMacro.xlsm"), b)
    assert info.get("vba") and b.vba_source
    out = tmp_path / "resaved.xlsm"
    save_xlsx(str(out), b)
    assert "Module1" in dict(SF.vba_modules(SF.vba_from_package(out.read_bytes())))


def test_external_data_sources(tmp_path):
    csv = tmp_path / "d.csv"
    csv.write_text("name;amount\nali;10\nsara;12,5\n", encoding="utf-8")
    assert SF.read_text_table(str(csv)) == [["name", "amount"], ["ali", "10"], ["sara", "12,5"]]
    js = tmp_path / "d.json"
    js.write_text('{"items": [{"city": "Lahore", "pop": 13}, {"city": "Karachi", "pop": 17}]}', encoding="utf-8")
    assert SF.json_rows(str(js)) == [["city", "pop"], ["Lahore", 13], ["Karachi", 17]]
    db = tmp_path / "d.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table orders(id integer, total real)")
    con.executemany("insert into orders values (?, ?)", [(1, 99.5), (2, 150.0)])
    con.commit()
    con.close()
    assert SF.sqlite_tables(str(db)) == ["orders"]
    rows = SF.sqlite_rows(str(db), "select * from orders where total > 100")
    assert rows == [["id", "total"], [2, 150.0]]
    b = Book()
    sh = b.add_sheet("S")
    w = _SheetStub(b, sh, rc=(3, 2))
    SF.insert_rows(w, rows, "import")
    assert sh.display(3, 2)[0] == "id" and sh.value(4, 3) == 150.0
    # AI context and statistics are computed locally
    b2, sh2 = _sales_book()
    w2 = _SheetStub(b2, sh2, sel=(1, 1, 4, 3))
    assert "Sales" in SF.sheet_context(w2) and "B2=" in SF.sheet_context(w2)
    summary = SF.selection_summary(w2)
    assert "Q1" in summary and "mean" in summary.lower() or "120" in summary


def test_smart_diagrams_wordart_and_countdown(qapp, tmp_path):
    from app.slide.canvas import render_slide
    from app.slide.model import Shape, emu
    from app.slide.widget import SlideDocument

    w = SlideDocument()
    W, H = w.pres.size_px
    area = QRectF(W * 0.07, H * 0.27, W * 0.86, H * 0.65)
    for kind, _label in PF.DIAGRAMS:
        outline = PF.parse_outline("CEO\n  Sales\n  Tech" if kind == "hierarchy" else "Plan\nBuild\nTest\nShip")
        shapes = PF.build_diagram(kind, outline, area, "#4472c4", True)
        assert shapes, kind
    w.slide.shapes.extend(PF.build_diagram("process", PF.parse_outline("A\nB\nC"), area, "#4472c4", True))
    w.slide.shapes.append(Shape("text", emu(10), emu(10), emu(400), emu(100),
                                paragraphs=PF.wordart_runs("Hello", "Sunset", 40), shadow=True))
    cd = Shape("roundrect", emu(W * 0.4), emu(H * 0.7), emu(W * 0.2), emu(H * 0.2), name="countdown:75")
    cd.set_text("01:15", size=40)
    w.slide.shapes.append(cd)
    w.commit("t")
    assert not render_slide(w.pres, w.slide, 480).isNull()
    path = tmp_path / "f.pptx"
    w.save_to(str(path))
    w2 = SlideDocument()
    w2.load(str(path))
    run = next(r for s in w2.slide.shapes for p in s.paragraphs for r in p.runs if r.text == "Hello")
    assert run.gradient == "#ffb347,#ff3d00" and run.outline == "#7f1d00"
    assert sum(1 for s in w2.slide.shapes if s.kind == "chevron") == 3
    assert any(s.name == "countdown:75" for s in w2.slide.shapes)
    from app.slide.show import SlideShow

    show = SlideShow(w2, 0)
    show.resize(960, 540)
    show.show()
    qapp.processEvents()
    assert [(lab.text(), secs) for lab, _s, secs in show._countdowns] == [("01:15", 75)]
    show._tick_countdowns()
    assert show._countdowns[0][0].text() == "01:14"
    show._tick.stop()
    show.hide()
    w.set_modified(False)
    w2.set_modified(False)


def test_ink_presenter_view_and_keep_ink(qapp):
    from PySide6.QtGui import QMouseEvent

    from app.slide.show import SlideShow
    from app.slide.widget import SlideDocument

    w = SlideDocument()
    w.new_slide()
    show = SlideShow(w, 0)
    show.resize(960, 540)
    show.show()
    qapp.processEvents()
    show.set_ink_tool("pen")
    r = show.ink.slide_rect()
    assert r.width() > 500

    def ev(t, x, y, btn=Qt.LeftButton, btns=Qt.LeftButton):
        p = QPointF(r.x() + x, r.y() + y)
        return QMouseEvent(t, p, p, btn, btns, Qt.NoModifier)
    show.ink.mousePressEvent(ev(QEvent.MouseButtonPress, 100, 100))
    for i in range(10):
        show.ink.mouseMoveEvent(ev(QEvent.MouseMove, 100 + 20 * i, 100 + 5 * i, Qt.NoButton))
    show.ink.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, 300, 150))
    assert len(show.ink.strokes[0]) == 1 and len(show.ink.strokes[0][0]["points"]) == 11
    pv = PF.PresenterView(w, show)
    assert pv.counter.text().endswith("2")
    show.next_step()
    qapp.processEvents()
    assert "2" in pv.counter.text().split()[1]
    before = len(w.pres.slides[0].shapes)
    PF.keep_ink(w, show.ink)
    assert len(w.pres.slides[0].shapes) == before + 1 and w.pres.slides[0].shapes[-1].kind == "image"
    pv._tick.stop()
    show._tick.stop()
    show.ink.strokes.clear()
    pv.hide()
    show.hide()
    w.set_modified(False)


def test_design_ideas_and_ai_slide_builder(qapp, monkeypatch):
    from app.services.ai import SlideOutline
    from app.slide.model import THEMES
    from app.slide.widget import SlideDocument

    w = SlideDocument()
    styled = PF.styled_slide(w.slide, THEMES["Midnight"], 1, w.pres.width, w.pres.height)
    assert styled.background == THEMES["Midnight"].background and styled.shapes[0].name == "Design accent"
    captured = {}

    class FakeDialog:
        def __init__(self, parent, build):
            captured["build"] = build

        def exec(self):
            captured["build"]("Solar Energy", "For small businesses",
                              [SlideOutline("Why solar", ["Cheaper bills", "Clean"], "Say hello"),
                               SlideOutline("Costs", ["Panels", "Inverter"], "")])
    import app.shell.ai_dialogs as AD

    monkeypatch.setattr(AD, "ensure_ai", lambda parent: True)
    monkeypatch.setattr(AD, "AISlidesDialog", FakeDialog)
    PF.ai_slides(w)
    assert len(w.pres.slides) == 3
    assert any("Solar Energy" in s.text for s in w.pres.slides[0].shapes)
    assert w.pres.slides[1].notes == "Say hello"
    assert any("Cheaper bills" in s.text for s in w.pres.slides[1].shapes)
    w.set_modified(False)
