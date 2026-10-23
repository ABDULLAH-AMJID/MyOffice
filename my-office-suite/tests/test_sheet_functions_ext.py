"""The extended function library (engineering, distributions, tests, financial, database, dynamic arrays, regex).

Expected values are Microsoft's documented examples where they exist."""
import pytest

from app.sheet.formula.functions import FUNCTIONS
from app.sheet.formula.values import XLError
from app.sheet.model import Book

APPROX = 1e-6


@pytest.fixture(scope="module")
def book():
    b = Book()
    sh = b.add_sheet("Data")
    db = [("Tree", "Height", "Age", "Yield", "Profit"), ("Apple", 18, 20, 14, 105), ("Pear", 12, 12, 10, 96),
          ("Cherry", 13, 14, 9, 105), ("Apple", 14, 15, 10, 75), ("Pear", 9, 8, 8, 76.8), ("Apple", 8, 9, 6, 45)]
    for r, row in enumerate(db, start=1):
        for c, v in enumerate(row, start=1):
            b.set_input(sh, r, c, str(v))
    crit = [("Tree", "Height"), ("Apple", ">10")]
    for r, row in enumerate(crit, start=10):
        for c, v in enumerate(row, start=1):
            b.set_input(sh, r, c, str(v))
    return b


def ev(b, formula):
    sh = b.sheets[0]
    b.set_input(sh, 50, 10, formula)
    v = sh.value(50, 10)
    return v.code if isinstance(v, XLError) else v


def test_more_than_400_functions():
    assert len(FUNCTIONS) >= 400


@pytest.mark.parametrize("formula,expected", [
    # engineering
    ('=BIN2DEC("1111111111")', -1.0), ("=DEC2HEX(255)", "FF"), ("=DEC2BIN(-1)", "1111111111"),
    ('=HEX2DEC("FFFFFFFFFF")', -1.0), ('=OCT2BIN("7",3)', "111"), ("=DEC2OCT(58,3)", "072"),
    ("=BITAND(13,25)", 9.0), ("=BITXOR(5,3)", 6.0), ("=BITLSHIFT(4,2)", 16.0), ("=DELTA(5,5)", 1.0),
    ('=CONVERT(1,"mi","km")', 1.609344), ('=CONVERT(100,"C","F")', 212.0), ('=CONVERT(1,"lbm","kg")', 0.45359237),
    ('=CONVERT(1,"gal","l")', 3.785411784), ('=CONVERT(1,"ft2","m2")', 0.09290304), ('=CONVERT(1,"kg","m")', "#N/A"),
    ('=IMSUM("3+4i","1-2i")', "4+2i"), ('=IMPRODUCT("3+4i","5-3i")', "27+11i"), ('=IMABS("5+12i")', 13.0),
    ("=COMPLEX(3,4)", "3+4i"), ('=IMDIV("-238+240i","10+24i")', "5+12i"), ('=IMREAL("6-9i")', 6.0),
    ("=ERF(1)", 0.8427007929), ("=BESSELJ(1.9,2)", 0.329925829),
    # math
    ("=ROMAN(1999)", "MCMXCIX"), ('=ARABIC("MCMXCIX")', 1999.0), ("=BASE(255,16)", "FF"), ('=DECIMAL("FF",16)', 255.0),
    ("=MULTINOMIAL(2,3,4)", 1260.0), ("=SUMXMY2({2,3,9},{6,5,11})", 24.0), ("=COMBINA(4,3)", 20.0),
    ("=INDEX(MMULT({1,2;3,4},{5;6}),2,1)", 39.0), ("=MDETERM({1,2;3,4})", -2.0),
    ("=INDEX(MINVERSE({4,7;2,6}),1,1)", 0.6), ("=COT(1)", 0.6420926159), ("=GAMMALN(4)", 1.791759469),
    # distributions (Excel documentation examples)
    ("=BINOM.DIST(6,10,0.5,FALSE)", 0.205078125), ("=BINOM.DIST(6,10,0.5,TRUE)", 0.828125),
    ("=BINOM.INV(6,0.5,0.75)", 4.0), ("=POISSON.DIST(2,5,FALSE)", 0.084224337), ("=POISSON.DIST(2,5,TRUE)", 0.124652019),
    ("=EXPON.DIST(0.2,10,TRUE)", 0.864664717), ("=GAMMA.DIST(10.00001131,9,2,TRUE)", 0.068094),
    ("=GAMMA.INV(0.068094,9,2)", 10.0000112), ("=BETA.DIST(2,8,10,TRUE,1,3)", 0.6854706),
    ("=BETA.INV(0.685470581,8,10,1,3)", 2.0), ("=LOGNORM.DIST(4,3.5,1.2,TRUE)", 0.0390836),
    ("=WEIBULL.DIST(105,20,100,TRUE)", 0.929581), ("=HYPGEOM.DIST(1,4,8,20,TRUE)", 2255 / 4845),
    ("=NEGBINOM.DIST(10,5,0.25,TRUE)", 0.3135141), ("=T.INV.2T(0.05,10)", 2.228138852),
    ("=T.DIST.2T(2.228138852,10)", 0.05), ("=CHISQ.INV.RT(0.05,10)", 18.30703805), ("=F.INV.RT(0.05,5,10)", 3.32583453),
    ("=CONFIDENCE.NORM(0.05,2.5,50)", 0.692951912), ("=CONFIDENCE.T(0.05,1,50)", 0.284196855),
    ("=FISHER(0.75)", 0.972955075), ("=T.TEST({3,4,5,8,9,1,2,4,5},{6,19,3,2,14,4,5,17,1},2,1)", 0.196016),
    ("=INDEX(LINEST({1,9,5,7},{0,4,2,3}),1,1)", 2.0), ("=INDEX(LINEST({1,9,5,7},{0,4,2,3}),1,2)", 1.0),
    ("=TREND({1,9,5,7},{0,4,2,3},5)", 11.0),
    # financial (Excel documentation examples)
    ("=XNPV(0.09,{-10000,2750,4250,3250,2750},{39448,39508,39751,39859,39904})", 2086.6476),
    ("=XIRR({-10000,2750,4250,3250,2750},{39448,39508,39751,39859,39904})", 0.373362535),
    ("=CUMIPMT(0.09/12,360,125000,13,24,0)", -11135.23213), ("=CUMPRINC(0.09/12,360,125000,13,24,0)", -934.1071234),
    ("=MIRR({-120000,39000,30000,21000,37000,46000},0.1,0.12)", 0.126094), ("=DB(1000000,100000,6,1,7)", 186083.3333),
    ("=VDB(2400,300,10,0,1)", 480.0), ("=TBILLPRICE(DATE(2008,3,31),DATE(2008,6,1),0.09)", 98.45),
    ("=DOLLARDE(1.02,16)", 1.125), ("=DOLLARFR(1.125,16)", 1.02), ("=PDURATION(0.025,2000,2200)", 3.859866163),
    ("=RRI(96,10000,11000)", 0.0009933), ("=FVSCHEDULE(1,{0.09,0.11,0.1})", 1.33089),
    # dates
    ("=NETWORKDAYS.INTL(DATE(2006,1,1),DATE(2006,1,31))", 22.0),
    ("=NETWORKDAYS.INTL(DATE(2006,2,28),DATE(2006,1,31))", -21.0), ("=WORKDAY.INTL(DATE(2012,1,1),90,11)", 41013.0),
    ("=WORKDAY.INTL(DATE(2012,1,1),30,0)", "#NUM!"),
    # dynamic arrays and text
    ("=INDEX(TAKE({1,2,3;4,5,6;7,8,9},2),2,3)", 6.0), ("=INDEX(DROP({1,2,3;4,5,6;7,8,9},1,1),2,2)", 9.0),
    ("=ROWS(VSTACK({1,2},{3,4;5,6}))", 3.0), ("=COLUMNS(HSTACK({1;2},{3,4;5,6}))", 3.0),
    ("=INDEX(CHOOSECOLS({1,2,3;4,5,6},3,1),2,2)", 4.0), ('=INDEX(TEXTSPLIT("a,b;c,d",",",";"),2,1)', "c"),
    ("=INDEX(TOCOL({1,2;3,4}),3,1)", 3.0), ("=INDEX(WRAPROWS({1,2,3,4,5},2),3,1)", 5.0),
    ('=INDEX(SORTBY({"a";"b";"c"},{3;1;2}),1,1)', "b"), ("=ROWS(EXPAND({1,2},3,3,0))", 3.0),
    ('=REGEXTEST("abc123","[0-9]+")', True), ('=REGEXEXTRACT("abc123def","[0-9]+")', "123"),
    ('=REGEXREPLACE("2026-10-17","(\\d+)-(\\d+)-(\\d+)","$3/$2/$1")', "17/10/2026"),
    ('=ENCODEURL("a b&c")', "a%20b%26c"), ('=ASC("\uff21\uff22\uff23")', "ABC"),
    # database (Excel's tree example)
    ('=DSUM(A1:E7,"Profit",A10:A11)', 225.0), ('=DMAX(A1:E7,"Profit",A10:B11)', 105.0),
    ('=DCOUNT(A1:E7,"Age",A10:B11)', 2.0), ('=DAVERAGE(A1:E7,"Yield",A10:B11)', 12.0),
    ('=DGET(A1:E7,"Yield",A10:B11)', "#NUM!"), ('=DMIN(A1:E7,"Profit",A10:A11)', 45.0),
])
def test_extended_functions(book, formula, expected):
    got = ev(book, formula)
    if isinstance(expected, float) and isinstance(got, float):
        assert got == pytest.approx(expected, rel=1e-5, abs=1e-6), formula
    else:
        assert got == expected, formula
