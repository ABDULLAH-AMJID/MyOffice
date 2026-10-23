"""Presentation: model, PPTX round trip incl. transitions/animations/notes, rendering, PDF export."""
import pymupdf

from app.slide.model import THEMES, Animation, Presentation, Shape, Theme, Transition, emu, make_slide


def deck():
    p = Presentation(theme=Theme(**THEMES["Ocean"].__dict__))
    s1 = make_slide(p, "title", "Hello", "World")
    s1.transition = Transition("fade", 0.7)
    s2 = make_slide(p, "title_content", "Agenda", "One\nTwo")
    s2.shapes[1].animation = Animation("fly_in", "click", 0.6, 0, "left", 0)
    star = Shape("star5", emu(600), emu(300), emu(150), emu(150), fill="#e76f51")
    star.animation = Animation("zoom", "after", 0.5, 0.2, "bottom", 1)
    s2.shapes.append(star)
    s2.notes = "Speaker notes"
    t = Shape("table", emu(50), emu(400), emu(400), emu(120))
    t.table = [["A", "B"], ["1", "2"]]
    c = Shape("chart", emu(500), emu(150), emu(500), emu(300))
    c.chart = {"kind": "column", "categories": ["Q1", "Q2"], "series": [["Sales", [3, 5]]], "title": "Sales"}
    s2.shapes += [t, c]
    p.slides = [s1, s2]
    return p


def test_pptx_round_trip(qapp, tmp_path):
    from app.slide.pptx_io import load_pptx, save_pptx

    path = tmp_path / "deck.pptx"
    save_pptx(str(path), deck())
    p2 = load_pptx(str(path))
    assert len(p2.slides) == 2
    assert p2.slides[0].transition.kind == "fade"
    kinds = [s.kind for s in p2.slides[1].shapes]
    assert "star5" in kinds and "table" in kinds and "chart" in kinds
    anims = {s.kind: (s.animation.effect, s.animation.trigger) for s in p2.slides[1].shapes if s.animation.effect != "none"}
    assert anims["star5"] == ("zoom", "after") and anims["text"] == ("fly_in", "click")
    assert p2.slides[1].notes == "Speaker notes"
    chart = [s for s in p2.slides[1].shapes if s.kind == "chart"][0].chart
    assert chart["categories"] == ["Q1", "Q2"] and chart["series"][0][1] == [3.0, 5.0]


def test_render_and_pdf(qapp, tmp_path):
    from app.slide.canvas import export_pdf, render_slide

    p = deck()
    img = render_slide(p, p.slides[1], 400)
    assert img.width() == 400 and not img.isNull()
    out = tmp_path / "deck.pdf"
    assert export_pdf(p, str(out)) == 2
    assert "Agenda" in pymupdf.open(str(out))[1].get_text()


def test_slide_document_editing(qapp, tmp_path):
    from PySide6.QtCore import QRectF

    from app.slide.widget import SlideDocument

    d = SlideDocument()
    d.new_slide("title_content")
    assert len(d.pres.slides) == 2
    d.add_shape_at("rect", QRectF(100, 100, 200, 100))
    d.canvas.commit_text_edits()
    assert d.slide.shapes[-1].kind == "rect"
    d.undo.undo()
    assert all(s.kind != "rect" for s in d.slide.shapes)
    d.undo.redo()
    assert d.slide.shapes[-1].kind == "rect"
    d.apply_theme("Midnight")
    assert d.pres.theme.name == "Midnight"
    d.save_to(str(tmp_path / "x.pptx"))
    d.set_modified(False)
