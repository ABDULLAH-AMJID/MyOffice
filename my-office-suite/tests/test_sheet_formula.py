"""Spreadsheet formula engine: functions, dependency graph, topological recalc, structure edits."""
import pytest

from app.sheet.formula.values import XLError
from app.sheet.model import Book


@pytest.fixture
def book():
    b = Book()
    sh = b.add_sheet("Data")
    rows = [("Fruit", "Qty", "Price"), ("Apple", 10, 1.5), ("Banana", 20, 0.25), ("Cherry", 5, 4.0), ("Date", 8, 3.0)]
    for r, row in enumerate(rows, start=1):
        for c, v in enumerate(row, start=1):
            b.set_input(sh, r, c, str(v))
    for r in range(2, 6):
        b.set_input(sh, r, 4, f"=B{r}*C{r}")
    return b


def val(b, ref):
    from app.sheet.formula.parser import parse_ref_text

    n = parse_ref_text(ref)
    v = b.sheets[0].value(n.r1, n.c1)
    return v.code if isinstance(v, XLError) else v


@pytest.mark.parametrize("formula,expected", [
    ("=SUM(D2:D5)", 64.0), ("=SUM(B:B)", 43.0), ("=AVERAGE(B2:B5)", 10.75), ("=IF(SUM(B2:B5)>40,\"big\",\"small\")", "big"),
    ("=VLOOKUP(\"Cherry\",A2:D5,4,FALSE)", 20.0), ("=VLOOKUP(\"ch*\",A2:D5,2,FALSE)", 5.0),
    ("=INDEX(A2:A5,MATCH(MAX(D2:D5),D2:D5,0))", "Date"), ("=SUMIF(B2:B5,\">8\",D2:D5)", 20.0),
    ("=COUNTIF(A2:A5,\"*a*\")", 3.0), ("=SUMPRODUCT((B2:B5>8)*(C2:C5))", 1.75), ("=-2^2", 4.0), ("=2^3^2", 64.0),
    ("=1/0", "#DIV/0!"), ("=IFERROR(1/0,\"x\")", "x"), ("=ROUND(2.5,0)", 3.0), ("=ROUND(-2.5,0)", -3.0),
    ("=MOD(-3,2)", 1.0), ("=TEXT(1234.5,\"#,##0.00\")", "1,234.50"), ("=DATE(2026,10,15)", 46310.0),
    ("=XLOOKUP(\"Banana\",A2:A5,C2:C5)", 0.25), ("=STDEV(B2:B5)", 6.5), ("=NOSUCH(1)", "#NAME?"),
    ("=PMT(0.1/12,36,8000)", pytest.approx(-258.1374975507)), ("=IRR({-70000,12000,15000,18000,21000,26000})", pytest.approx(0.0866309480)),
    ("=TEXTJOIN(\",\",TRUE,A2:A5)", "Apple,Banana,Cherry,Date"), ("=LEN(\"اردو\")", 4.0), ("=OFFSET(A1,2,1)", 20.0),
    ("=INDIRECT(\"B\"&3)", 20.0), ("=SUM(A1:B2 B1:C3)", 10.0), ("=COUNTIFS(B2:B5,\">5\",C2:C5,\">1\")", 2.0),
    ("=NETWORKDAYS(DATE(2026,10,1),DATE(2026,10,31))", 22.0), ("=SWITCH(3,1,\"x\",3,\"y\",\"z\")", "y"),
])
def test_functions(book, formula, expected):
    sh = book.sheets[0]
    book.set_input(sh, 10, 6, formula)
    v = sh.value(10, 6)
    v = v.code if isinstance(v, XLError) else v
    assert v == expected


def test_dependency_propagation(book):
    sh = book.sheets[0]
    book.set_input(sh, 1, 6, "=SUM(D2:D5)")
    assert sh.value(1, 6) == 64.0
    changed = book.set_input(sh, 2, 2, "100")
    assert sh.value(2, 4) == 150.0 and sh.value(1, 6) == 199.0
    assert (sh.id, 2, 4) in changed and (sh.id, 1, 6) in changed


def test_cycle_detection(book):
    sh = book.sheets[0]
    book.set_input(sh, 1, 8, "=H2+1")
    book.set_input(sh, 2, 8, "=H1+1")
    assert sh.value(1, 8).code == "#CIRC!"
    book.set_input(sh, 2, 8, "5")
    assert sh.value(1, 8) == 6.0


def test_insert_delete_rows_adjust_references(book):
    sh = book.sheets[0]
    book.set_input(sh, 1, 6, "=SUM(D2:D5)")
    book.insert_rows(sh, 3, 2)
    assert sh.cells[(1, 6)].formula == "=SUM(D2:D7)" and sh.value(1, 6) == 64.0
    book.delete_rows(sh, 3, 2)
    assert sh.cells[(1, 6)].formula == "=SUM(D2:D5)"
    book.delete_cols(sh, 2, 1)
    assert sh.cells[(2, 3)].formula == "=#REF!*B2"


def test_cross_sheet_and_names(book):
    sh = book.sheets[0]
    other = book.add_sheet("Info")
    book.set_input(other, 1, 1, "=Data!D2*2")
    assert other.value(1, 1) == 30.0
    book.names["TOTALS"] = "=Data!$D$2:$D$5"
    book.engine.rebuild()
    book.set_input(other, 2, 1, "=SUM(Totals)")
    assert other.value(2, 1) == 64.0
    book.rename_sheet(sh, "Sales")
    assert other.cells[(1, 1)].formula == "=Sales!D2*2"


def test_copy_translation_and_fill():
    from app.sheet.formula.parser import translate_formula

    assert translate_formula("=A1+$B$1+C$2", 2, 1) == "=B3+$B$1+D$2"
    b = Book()
    sh = b.add_sheet()
    b.set_input(sh, 1, 1, "1")
    b.set_input(sh, 2, 1, "3")
    states = b.fill_states(sh, (1, 1, 2, 1), (1, 1, 5, 1))
    b.apply_states(sh, states)
    assert [sh.value(r, 1) for r in range(1, 6)] == [1.0, 3.0, 5.0, 7.0, 9.0]


def test_xlsx_round_trip(tmp_path):
    from app.sheet.model import ChartSpec, Style
    from app.sheet.xlsx_io import load_any, save_xlsx

    b = Book()
    sh = b.add_sheet("Sales")
    b.set_input(sh, 1, 1, "Q1")
    b.set_input(sh, 2, 1, "120")
    b.set_input(sh, 3, 1, "=A2*2")
    sh.cells[(1, 1)].style = b.styles.intern(Style(bold=True, fill="#ffff00"))
    sh.merges.append((5, 1, 5, 3))
    sh.charts.append(ChartSpec("column", "Sales!A1:A2", "Chart"))
    p = tmp_path / "t.xlsx"
    save_xlsx(str(p), b)
    b2 = Book()
    load_any(str(p), b2)
    s2 = b2.sheets[0]
    assert s2.value(3, 1) == 240.0 and s2.cells[(3, 1)].formula == "=A2*2"
    assert b2.styles.get(s2.cells[(1, 1)].style).bold
    assert s2.merges == [(5, 1, 5, 3)] and len(s2.charts) == 1


def test_number_formats():
    from app.sheet.numfmt import format_value

    assert format_value(1234.5, "#,##0")[0] == "1,235"
    assert format_value(0.256, "0%")[0] == "26%"
    assert format_value(46310, "yyyy-mm-dd")[0] == "2026-10-15"
    assert format_value(-5, "#,##0;(#,##0)")[0] == "(5)"
    assert format_value(1.5, "[h]:mm")[0] == "36:00"


def test_pivot_and_analysis():
    from app.sheet.analysis import descriptive, regression
    from app.sheet.pivot import compute_pivot
    import pandas as pd

    df = pd.DataFrame({"Region": ["N", "S", "N", "S"], "Sales": [10, 20, 30, 40]})
    pv = compute_pivot(df, ["Region"], [], "Sales", "sum", True)
    assert float(pv.loc["N"].iloc[0]) == 40 and float(pv.loc["S"].iloc[0]) == 60
    stats = descriptive([("Sales", [10.0, 20.0, 30.0, 40.0])])
    assert dict((r[0], r[1]) for r in stats[1:])["Mean"] == 25.0
    reg = regression([2.0, 4.0, 6.0, 8.0], [("x", [1.0, 2.0, 3.0, 4.0])])
    coeff = [r for r in reg if r and r[0] == "x"][0][1]
    assert abs(coeff - 2.0) < 1e-9
