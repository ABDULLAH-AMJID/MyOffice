"""Presentation scripting: object model, runner, storage, security.

Every test drives the *real* objects (a real ``SlideDocument``, the real
interpreter, real files on disk) — no mocks, no stubs of the feature under test.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("qapp")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def slide_doc(qapp):
    from app.slide.widget import SlideDocument

    doc = SlideDocument()
    return doc


@pytest.fixture
def make_png(tmp_path):
    from PySide6.QtGui import QColor, QImage

    def _make(name="pic.png", w=64, h=48):
        img = QImage(w, h, QImage.Format_ARGB32)
        img.fill(QColor("#3366cc"))
        path = tmp_path / name
        img.save(str(path), "PNG")
        return path

    return _make


def run(code, doc, **kw):
    from app.slide.script.runner import run_script

    return run_script(code, None, name=kw.pop("name", "test"), doc=doc, **kw)


def model(doc):
    from app.slide.model import pres_to_dict

    return pres_to_dict(doc.pres)


# =========================================================================== functional
def test_create_five_slides_in_a_loop(slide_doc):
    out = run(
        'p = app.presentation\n'
        'for i in range(5):\n'
        '    p.add_slide(layout="title_only", title=f"Topic {i + 1}")\n'
        'print(p.count)\n', slide_doc)
    assert len(slide_doc.pres.slides) == 6          # the initial slide + five new ones
    assert [slide_doc.pres.slides[i].shapes[0].text for i in range(1, 6)] == [f"Topic {i}" for i in range(1, 6)]
    assert out.strip() == "6"


def test_change_text_on_all_slides_keeps_formatting(slide_doc):
    run('p = app.presentation\n'
        'for i in range(3):\n'
        '    p.add_slide(layout="title_content", title=f"chapter {i}", body="body text")\n', slide_doc)
    before = slide_doc.pres.slides[1].shapes[0].paragraphs[0].runs[0]
    size, color, font = before.size, before.color, before.font

    run('for s in app.presentation.slides:\n'
        '    for sh in s.shapes:\n'
        '        if sh.placeholder == "title":\n'
        '            sh.text = sh.text.upper()\n', slide_doc)

    titles = [s.shapes[0].text for s in slide_doc.pres.slides]
    assert "CHAPTER 0" in titles and "CLICK TO ADD TITLE" in titles
    after = slide_doc.pres.slides[1].shapes[0].paragraphs[0].runs[0]
    assert (after.size, after.color, after.font) == (size, color, font)


def test_add_shapes_and_pictures(slide_doc, make_png):
    pic = make_png()
    run(f'p = app.presentation\n'
        f's = p.slides[0]\n'
        f's.shapes.add_shape("round_rect", 60, 60, 400, 120, text="Hello", fill="#1F3864")\n'
        f'e = s.shapes.add_shape("ellipse", 500, 60, 200, 120)\n'
        f'e.rotation = 45\n'
        f's.shapes.add_picture(r"{pic}", 40, 400, w=160)\n', slide_doc,
        ask_file=lambda path, purpose: True)

    shapes = slide_doc.pres.slides[0].shapes
    kinds = [s.kind for s in shapes]
    assert "roundrect" in kinds and "ellipse" in kinds and "image" in kinds
    box = next(s for s in shapes if s.kind == "roundrect")
    assert box.text == "Hello" and box.fill == "#1f3864"
    img = next(s for s in shapes if s.kind == "image")
    assert img.image and img.image_ext == "png"
    assert img.h == pytest.approx(img.w * 48 / 64, rel=0.02)     # aspect ratio kept


def test_table_and_chart_survive_save_and_reload(slide_doc, tmp_path):
    run('p = app.presentation\n'
        's = p.add_slide(layout="title_only", title="Numbers")\n'
        's.shapes.add_table([["Q", "Sales"], ["Q1", "120"]], x=100, y=100, w=400, h=120)\n'
        's.shapes.add_chart("column", ["Q1", "Q2"], [["Sales", [120, 150]]], title="Sales")\n', slide_doc)
    target = tmp_path / "deck.pptx"
    slide_doc.save_to(str(target))

    from app.slide.widget import SlideDocument

    again = SlideDocument()
    again.load(str(target))
    kinds = [s.kind for s in again.pres.slides[1].shapes]
    assert "table" in kinds and "chart" in kinds
    table = next(s for s in again.pres.slides[1].shapes if s.kind == "table")
    assert table.table[1] == ["Q1", "120"]
    chart = next(s for s in again.pres.slides[1].shapes if s.kind == "chart")
    assert chart.chart["series"][0][0] == "Sales" and chart.chart["series"][0][1] == [120.0, 150.0]


def test_failing_script_leaves_the_deck_untouched(slide_doc):
    from app.macros.errors import MacroError

    run('app.presentation.add_slide(layout="title_only", title="keep me")\n', slide_doc)
    before = model(slide_doc)
    n_undo = slide_doc.undo.index()

    with pytest.raises(MacroError) as err:
        run('s = app.presentation.slides[0]\n'
            's.shapes.add_shape("ellipse", 10, 10, 100, 100)\n'
            'raise ValueError("boom")\n', slide_doc)

    assert "Line 3" in str(err.value) and "ValueError: boom" in str(err.value)
    assert model(slide_doc) == before
    assert slide_doc.undo.index() == n_undo          # no undo entry for a failed run


def test_many_changes_become_one_undo_step(slide_doc):
    start = len(slide_doc.pres.slides)
    run('p = app.presentation\n'
        'for i in range(40):\n'
        '    s = p.add_slide(layout="title_only", title=f"Card {i}")\n'
        '    s.shapes.add_textbox(100, 100, 300, 80, text="x")\n', slide_doc)
    assert len(slide_doc.pres.slides) == start + 40
    before_undo = slide_doc.undo.index()
    slide_doc.undo.undo()
    assert len(slide_doc.pres.slides) == start        # everything reverted at once
    assert slide_doc.undo.index() == before_undo - 1
    slide_doc.undo.redo()
    assert len(slide_doc.pres.slides) == start + 40


def test_script_edits_are_not_overwritten_by_the_stale_canvas(slide_doc):
    """The editor's canvas still shows the pre-script deck at commit time — its
    geometry must never be copied back over what the script just changed."""
    slide_doc.new_slide("blank")
    slide_doc.current = 1
    from PySide6.QtCore import QRectF

    slide_doc.add_shape_at("rect", QRectF(100, 100, 200, 120))
    shape_id = slide_doc.slide.shapes[-1].id

    run('for sh in app.presentation.slides[1].shapes:\n'
        '    sh.x = app.emu(400)\n'
        '    sh.w = app.emu(600)\n'
        '    sh.text = "moved by script"\n', slide_doc)

    shape = next(s for s in slide_doc.pres.slides[1].shapes if s.id == shape_id)
    assert shape.x == app_emu(400)
    assert shape.w == app_emu(600)
    assert shape.text == "moved by script"


def app_emu(pixels):
    from app.slide.model import emu

    return emu(pixels)


def test_timeout_stops_the_script_and_rolls_back(slide_doc):
    from app.macros.errors import MacroTimeout

    before = model(slide_doc)
    started = time.monotonic()
    with pytest.raises(MacroTimeout):
        run('p = app.presentation\np.add_slide(title="never")\nwhile True:\n    pass\n',
            slide_doc, timeout=0.6)
    assert time.monotonic() - started < 10
    assert model(slide_doc) == before


def test_stop_flag_aborts_cleanly(slide_doc):
    from app.macros.errors import MacroStopped

    state = {"stop": False}
    before = model(slide_doc)

    def stopper():
        state["stop"] = True          # asked on the first traced line
        return state["stop"]

    with pytest.raises(MacroStopped):
        run('p = app.presentation\nfor i in range(1000):\n    p.add_slide()\n', slide_doc, stop=stopper)
    assert model(slide_doc) == before


# =========================================================================== storage
def test_sidecar_round_trip(slide_doc, tmp_path):
    deck = tmp_path / "Report.pptx"
    slide_doc.path = deck
    slide_doc.script_store.add("Add agenda", 'p = app.presentation\nprint("hi")\n')
    slide_doc.script_store.add("Fix titles", "print(1)\n")
    slide_doc.save_to(str(deck))

    side = Path(str(deck) + ".moscript")
    assert side.exists()
    data = json.loads(side.read_text(encoding="utf-8"))
    assert data["format"] == "myoffice-script" and data["version"] == 1
    assert [s["name"] for s in data["scripts"]] == ["Add agenda", "Fix titles"]

    from app.slide.widget import SlideDocument

    again = SlideDocument()
    again.load(str(deck))
    assert again.script_store.names() == ["Add agenda", "Fix titles"]
    assert again.script_store.get("Fix titles").code == "print(1)\n"
    assert again.script_store.error is None


def test_malformed_sidecar_never_blocks_opening(slide_doc, tmp_path):
    deck = tmp_path / "Broken.pptx"
    slide_doc.save_to(str(deck))
    Path(str(deck) + ".moscript").write_text("{not json at all", encoding="utf-8")

    from app.slide.widget import SlideDocument

    again = SlideDocument()
    again.load(str(deck))                     # must not raise
    assert len(again.pres.slides) >= 1
    assert again.script_store.error
    assert again.script_store.scripts == []


def test_newer_script_format_is_refused_not_downgraded(tmp_path):
    from app.slide.script.store import ScriptStore

    p = tmp_path / "future.moscript"
    p.write_text(json.dumps({"format": "myoffice-script", "version": 99, "scripts": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        ScriptStore.load_file(p)


def test_sidecar_removed_when_last_script_deleted(slide_doc, tmp_path):
    deck = tmp_path / "Empty.pptx"
    slide_doc.path = deck
    slide_doc.script_store.add("only", "print(1)")
    slide_doc.save_to(str(deck))
    side = Path(str(deck) + ".moscript")
    assert side.exists()

    slide_doc.script_store.remove("only")
    slide_doc.save_to(str(deck))
    assert not side.exists()


def test_bundle_round_trip(slide_doc, tmp_path):
    from app.slide.script import bundle

    deck = tmp_path / "Packaged.pptx"
    slide_doc.path = deck
    slide_doc.script_store.add("Demo", "print('from bundle')")
    slide_doc.save_to(str(deck))

    pkg = bundle.export_bundle(slide_doc, tmp_path / "Packaged.mopkg")
    assert pkg.exists()
    out = bundle.open_bundle(pkg, into_dir=tmp_path / "unpacked")
    assert out["document"].exists()
    assert out["scripts"].exists()

    from app.slide.script.store import ScriptStore

    assert ScriptStore.load_file(out["scripts"]).names() == ["Demo"]


# =========================================================================== security
def test_import_of_system_modules_is_refused(slide_doc):
    from app.macros.errors import MacroError

    for code in ('import os\n', 'import subprocess\n', 'import sys\n'):
        with pytest.raises(MacroError) as err:
            run(code, slide_doc)
        assert "may only import" in str(err.value)


def test_dunder_import_is_refused(slide_doc):
    from app.macros.errors import MacroError

    with pytest.raises(MacroError) as err:
        run('m = __import__("subprocess")\n', slide_doc)
    assert "may only import" in str(err.value)


def test_open_and_exec_are_not_available(slide_doc):
    from app.macros.errors import MacroError

    for code in ('open("x.txt", "w")\n', 'exec("print(1)")\n', 'eval("1+1")\n'):
        with pytest.raises(MacroError) as err:
            run(code, slide_doc)
        assert "NameError" in str(err.value)


def test_picture_outside_document_folder_asks_permission(slide_doc, tmp_path, make_png, monkeypatch):
    from app.macros.errors import MacroError

    outside = make_png("outside.png")
    doc_dir = tmp_path / "deck"
    doc_dir.mkdir()
    slide_doc.path = doc_dir / "deck.pptx"

    asked = []

    def deny(path, purpose):
        asked.append(path)
        return False

    with pytest.raises(MacroError) as err:
        run(f'app.presentation.slides[0].shapes.add_picture(r"{outside}", 10, 10, w=50)\n',
            slide_doc, ask_file=deny)
    assert "denied" in str(err.value).lower()
    assert asked == [str(outside)]

    asked.clear()

    def allow(path, purpose):
        asked.append(path)
        return True

    run(f'app.presentation.slides[0].shapes.add_picture(r"{outside}", 10, 10, w=50)\n',
        slide_doc, ask_file=allow)
    assert asked == [str(outside)]                       # asked exactly once
    assert slide_doc.pres.slides[0].shapes[-1].kind == "image"


def test_picture_inside_document_folder_never_asks(slide_doc, tmp_path, make_png):
    doc_dir = tmp_path / "deck"
    doc_dir.mkdir()
    slide_doc.path = doc_dir / "deck.pptx"
    inside = make_png("local.png")
    inside.rename(doc_dir / "local.png")

    def boom(path, purpose):                              # must never be called
        raise AssertionError("asked for a file inside the document folder")

    run(f'app.presentation.slides[0].shapes.add_picture(r"{doc_dir / "local.png"}", 10, 10, w=50)\n',
        slide_doc, ask_file=boom)
    assert slide_doc.pres.slides[0].shapes[-1].kind == "image"


def test_app_open_is_permission_gated(slide_doc, tmp_path, monkeypatch):
    from app.macros.api import AppAPI
    from app.macros.errors import MacroError
    from app.slide.script.permissions import FileGate

    other = tmp_path / "other" / "deck2.pptx"
    other.parent.mkdir()
    other.write_bytes(b"not really a deck")

    gate = FileGate(None, ask=lambda path, purpose: False)
    api = AppAPI(None, gate=gate, doc=slide_doc)
    with pytest.raises(MacroError):
        api.open(str(other))

    asked = []
    gate2 = FileGate(None, ask=lambda path, purpose: asked.append(path) or True)
    api2 = AppAPI(None, gate=gate2, doc=slide_doc)
    try:
        api2.open(str(other))            # allowed → reaches MainWindow.open_path(None main) and fails there
    except AttributeError:
        pass
    assert asked == [str(other)]


def test_no_ask_means_reads_are_refused(slide_doc, make_png):
    from app.macros.errors import MacroError
    from app.slide.script.permissions import FileGate

    gate = FileGate(None, ask=None)
    with pytest.raises(MacroError):
        gate.read_bytes(make_png("g.png"))


def test_disabled_scripts_are_refused_by_the_dialog(slide_doc):
    """The global off switch stops a run before a single line executes."""
    from app.services.config import cfg
    from app.slide.script import store as store_mod
    from app.slide.script.dialog import ScriptDialog

    cfg().set("scripts.enabled", False, save=False)
    try:
        assert store_mod.enabled() is False
        dialog = ScriptDialog(None, slide_doc)
        dialog.editor.setPlainText('app.presentation.add_slide(title="must not happen")\n')
        dialog.run()
        assert len(slide_doc.pres.slides) == 1
        assert dialog.output.toPlainText().strip() == ""
    finally:
        cfg().set("scripts.enabled", True, save=False)


def test_consent_follows_file_content(tmp_path):
    from app.slide.script import store as store_mod

    side = tmp_path / "a.pptx.moscript"
    side.write_text('{"format":"myoffice-script","version":1,"scripts":[]}', encoding="utf-8")
    assert store_mod.needs_consent(side) is True

    store_mod.approve(side)
    assert store_mod.needs_consent(side) is False

    side.write_text('{"format":"myoffice-script","version":1,"scripts":[{"name":"x","code":""}]}',
                    encoding="utf-8")
    assert store_mod.needs_consent(side) is True        # content changed → ask again


def test_own_scripts_never_ask_permission(slide_doc, tmp_path):
    """A script the user wrote here runs after saving without a trust prompt, and the
    file it produces is remembered as approved for later sessions."""
    from app.slide.script import store as store_mod
    from app.slide.script.dialog import ScriptDialog

    deck = tmp_path / "Mine.pptx"
    slide_doc.path = deck
    dialog = ScriptDialog(None, slide_doc)
    dialog._store().add("mine", 'app.presentation.add_slide(title="ok")\n')
    dialog._reload_deck()
    dialog.list.setCurrentRow(0)
    dialog.b_run.click()                       # first run: no prompt (nothing saved yet)
    assert "✔ finished" in dialog.output.toPlainText()

    dialog.save()                              # writes the sidecar
    side = Path(str(deck) + ".moscript")
    assert side.exists()
    assert store_mod.needs_consent(side) is False          # remembered as approved
    assert dialog._store().self_authored is True

    dialog.b_run.click()                       # runs again with no prompt
    assert dialog.output.toPlainText().count("✔ finished") == 2


def test_trusted_folder_skips_consent(tmp_path, monkeypatch):
    from app.slide.script import store as store_mod

    folder = tmp_path / "trusted"
    folder.mkdir()
    side = folder / "b.pptx.moscript"
    side.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store_mod, "settings", lambda: {**store_mod.DEFAULTS, "trusted_dirs": [str(folder)]})
    assert store_mod.needs_consent(side) is False


# =========================================================================== layering
def _module_source(rel: str) -> str:
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def _imports(rel: str) -> set[str]:
    tree = ast.parse(_module_source(rel))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_api_module_does_not_depend_on_the_macro_runtime():
    imports = _imports("app/slide/script/api.py")
    macro_imports = {i for i in imports if i.startswith("app.macros")}
    assert macro_imports <= {"app.macros.errors"}, macro_imports


def test_model_layer_never_imports_widgets():
    for rel in ("app/slide/script/api.py", "app/slide/script/store.py", "app/slide/script/runner.py"):
        imports = _imports(rel)
        assert "PySide6.QtWidgets" not in imports, rel
        assert not any(i.endswith(".dialog") for i in imports), rel


def test_engine_is_the_single_exec_site():
    """Only app/macros/engine.py may call the exec()/compile() builtins."""
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:      # pragma: no cover - none expected
            continue
        if path.name == "engine.py" and path.parent.name == "macros":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
                offenders.append(f"{path.relative_to(root)}:{node.lineno} {node.func.id}()")
    assert offenders == [], offenders


# =========================================================================== bundled examples
EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "resources" / "slide_scripts").glob("*.py"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_bundled_example_runs_clean(path, monkeypatch, make_png, tmp_path):
    from PySide6.QtWidgets import QInputDialog

    from app.slide.widget import SlideDocument

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (str(make_png("logo.png")), True)))
    doc = SlideDocument()
    if path.stem == "Build_Agenda":                      # needs a few titled slides first
        run('p = app.presentation\nfor i in range(3):\n    p.add_slide(layout="title_only", title=f"Part {i+1}")\n',
            doc)
    output = run(path.read_text(encoding="utf-8"), doc, name=path.stem, ask_file=lambda p, w: True)
    assert output.strip(), f"{path.stem} printed nothing"
    assert doc.script_store.error is None


# =========================================================================== helpers
def test_shape_defaults_match_the_editor(slide_doc):
    """A shape made by a script must look exactly like the same shape drawn by hand."""
    from PySide6.QtCore import QRectF

    from app.slide.script.api import ShapeListAPI, SlideAPI
    from app.slide.script.api import PresentationAPI

    pres_api = PresentationAPI(slide_doc)
    for kind in ("rect", "ellipse", "triangle", "line", "arrow_line"):
        slide_doc.new_slide("blank")
        slide_doc.current = len(slide_doc.pres.slides) - 1
        rect = QRectF(100, 120, 300, 200)
        slide_doc.add_shape_at(kind, rect)                      # the editor's own path
        ui_shape = slide_doc.slide.shapes[-1]

        slide_api = SlideAPI(pres_api, slide_doc.slide)
        api_shape = ShapeListAPI(slide_api).add_shape(
            kind, rect.x(), rect.y(), rect.width(), rect.height())

        assert api_shape._shape.kind == ui_shape.kind
        assert (api_shape._shape.x, api_shape._shape.y) == (ui_shape.x, ui_shape.y)
        assert (api_shape._shape.w, api_shape._shape.h) == (ui_shape.w, ui_shape.h)
        assert api_shape._shape.fill == ui_shape.fill
        assert api_shape._shape.line == ui_shape.line
        assert api_shape._shape.valign == ui_shape.valign


def test_unknown_names_raise_readable_errors(slide_doc):
    from app.macros.errors import MacroError

    for code, fragment in (
        ('app.presentation.add_slide(layout="nope")\n', "layout"),
        ('app.presentation.slides[0].shapes.add_shape("blob", 0, 0, 10, 10)\n', "shape kind"),
        ('app.presentation.slides[99]\n', "out of range"),
        ('app.presentation.slides[0].transition = "sparkle"\n', "transition"),
    ):
        with pytest.raises(MacroError) as err:
            run(code, slide_doc)
        assert fragment in str(err.value).lower(), (code, str(err.value))


def test_app_helpers_exist(slide_doc, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("typed", True)))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    out = run('print(app.emu(96), app.px(914400), app.confirm("sure?"), app.input("name?", ""))\n'
              'print(type(app.slide).__name__)\n', slide_doc)
    assert "914400 96.0 True typed" in out
    assert "PresentationAPI" in out


def test_sleep_returns_quickly(slide_doc):
    started = time.monotonic()
    run('app.sleep(50)\n', slide_doc)
    assert time.monotonic() - started < 3
