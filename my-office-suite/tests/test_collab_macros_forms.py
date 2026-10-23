"""Real-time collaboration (y-websocket + pycrdt), macros (Python) and forms (JSON Schema)."""
import socket
import time

import pytest


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def test_collaboration_sheet_and_writer(qapp):
    from PySide6.QtGui import QTextCursor

    from app.collab.binding import CollabSession
    from app.collab.server import CollabServer
    from app.sheet.widget import SheetDocument
    from app.writer.widget import WriterDocument

    port = free_port()
    srv = CollabServer("127.0.0.1", port, persist=False)
    srv.start()
    try:
        a, b = SheetDocument(), SheetDocument()
        a.commit_input(1, 1, "5")
        a.commit_input(2, 1, "=A1*10")
        sa = CollabSession(a, f"ws://127.0.0.1:{port}", "room1", "A")
        sb = CollabSession(b, f"ws://127.0.0.1:{port}", "room1", "B")
        sa.start()
        pump(qapp, 1.0)
        sb.start()
        pump(qapp, 2.0)
        assert b.sheet.value(2, 1) == 50.0
        b.commit_input(1, 1, "7")
        pump(qapp, 1.0)
        assert a.sheet.value(2, 1) == 70.0
        w1, w2 = WriterDocument(), WriterDocument()
        c = w1.editor.textCursor()
        c.insertText("Hello اردو")
        s1 = CollabSession(w1, f"ws://127.0.0.1:{port}", "doc", "A")
        s2 = CollabSession(w2, f"ws://127.0.0.1:{port}", "doc", "B")
        s1.start()
        pump(qapp, 1.0)
        s2.start()
        pump(qapp, 2.0)
        assert w2.editor.document().toPlainText() == "Hello اردو"
        c2 = w2.editor.textCursor()
        c2.movePosition(QTextCursor.End)
        c2.insertText("!")
        pump(qapp, 1.0)
        assert w1.editor.document().toPlainText() == "Hello اردو!"
        for s in (sa, sb, s1, s2):
            s.stop()
        for d in (a, b, w1, w2):
            d.set_modified(False)
    finally:
        srv.stop()


def test_macros(qapp):
    from app.macros.api import MacroError, run_macro
    from app.shell.main_window import MainWindow

    win = MainWindow()
    doc = win.new_sheet()
    out = run_macro("s = app.sheet\ns['A1'] = 21\ns['A2'] = '=A1*2'\nprint(s['A2'])", win, name="t")
    assert out.strip() == "42.0"
    doc.undo.undo()
    assert doc.sheet.value(2, 1) is None and doc.sheet.value(1, 1) is None
    with pytest.raises(MacroError):
        run_macro("import os", win)
    with pytest.raises(MacroError):
        run_macro("while True:\n    pass", win, timeout=0.5)
    from app.macros.recorder import MacroRecorder

    rec = MacroRecorder()
    doc.recorder = rec
    doc.commit_input(3, 3, "hello")
    doc.recorder = None
    assert "s['C3'] = 'hello'" in rec.code()
    for d in win.documents():
        d.set_modified(False)
    win.close()


def test_forms_schema_and_validation(tmp_path):
    from app.forms import schema as S

    form = S.sample_form()
    js = S.to_json_schema(form)
    assert js["$schema"].endswith("2020-12/schema") and "rating" in js["properties"]
    assert S.from_json_schema(js).fields[3].kind == "rating"
    assert S.validate(form, {"name": "Ali", "rating": 4, "consent": True}) == []
    errs = S.validate(form, {"name": "", "rating": 9, "consent": False, "email": "nope"})
    assert any("required" in e for e in errs) and any("e-mail" in e for e in errs) and any("maximum" in e for e in errs)
    S.store_response(form, {"name": "Ali", "rating": 4, "consent": True})
    assert S.load_responses(form)[-1]["name"] == "Ali"
    html = S.export_html(form, str(tmp_path / "f.html"))
    assert "Customer Feedback" in open(html, encoding="utf-8").read()
