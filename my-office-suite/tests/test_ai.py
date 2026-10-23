"""MyOffice AI: the OpenAI-compatible client (non-streaming, streaming, models, errors) against a local test
server, and the task helpers (grammar, translate, formula, slides, Chat PDF retrieval and summary)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services import ai


class _Handler(BaseHTTPRequestHandler):
    replies: list = []
    requests: list = []
    responder = None   # optional callable(request body) -> answer text

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send(200, {"data": [{"id": "small-model"}, {"id": "big-model"}]})
        else:
            self._send(404, {"error": {"message": "nope"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        _Handler.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        if self.headers.get("Authorization") == "Bearer bad-key":
            self._send(401, {"error": {"message": "invalid api key"}})
            return
        if _Handler.responder is not None:
            text = _Handler.responder(body)
        else:
            text = _Handler.replies.pop(0) if _Handler.replies else "OK"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(0, len(text), 5):
                chunk = {"choices": [{"delta": {"content": text[i:i + 5]}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture
def settings(server):
    return ai.AISettings(provider="custom", base_url=server, model="small-model", api_key="k", timeout=10)


def test_chat_stream_models_and_errors(settings, server):
    _Handler.replies[:] = ["Hello from the model", "Streaming works fine"]
    assert ai.chat([{"role": "user", "content": "hi"}], s=settings) == "Hello from the model"
    parts = []
    assert ai.chat([{"role": "user", "content": "hi"}], s=settings, on_delta=parts.append) == "Streaming works fine"
    assert len(parts) > 1 and "".join(parts) == "Streaming works fine"
    assert _Handler.requests[-1]["auth"] == "Bearer k" and _Handler.requests[-1]["body"]["stream"] is True
    assert ai.list_models(settings) == ["big-model", "small-model"]
    bad = ai.AISettings(provider="custom", base_url=server, model="m", api_key="bad-key", timeout=5)
    with pytest.raises(ai.AIError, match="rejected the API key"):
        ai.chat([{"role": "user", "content": "x"}], s=bad)
    down = ai.AISettings(provider="ollama", base_url="http://127.0.0.1:9/v1", model="m", timeout=3)
    with pytest.raises(ai.AIError, match="local AI server|refused|cannot be reached"):
        ai.chat([{"role": "user", "content": "x"}], s=down)


def test_settings_are_saved_with_protected_key(user_home):
    s = ai.AISettings(provider="openai", base_url="https://api.openai.com/v1", model="gpt-x", api_key="sk-secret")
    ai.save_settings(s)
    from app.services.config import cfg

    assert "sk-secret" not in json.dumps(cfg().get("ai.api_key"))
    back = ai.settings()
    assert back.api_key == "sk-secret" and back.model == "gpt-x" and ai.configured()


def test_task_helpers(settings, monkeypatch):
    # grammar: the corrected text is turned into individual suggestions
    cs = ai.corrections("She dont like it, and i agree.", "She doesn't like it, and I agree.")
    assert [(c.original, c.suggestion) for c in cs] == [("dont", "doesn't"), ("i", "I")]
    assert ai.apply_corrections("She dont like it, and i agree.", cs[:1]) == "She doesn't like it, and i agree."
    # formula from JSON, from prose, from fenced JSON
    assert ai.parse_formula('{"formula": "SUMIFS(C:C,A:A,\\"North\\")", "explanation": "x"}')[0] == \
        '=SUMIFS(C:C,A:A,"North")'
    assert ai.parse_formula("Use =AVERAGE(B2:B9) * 2 for that.")[0] == "=AVERAGE(B2:B9) * 2"
    # slides from JSON and from Markdown
    title, _sub, slides = ai.parse_slides('```json\n{"title":"Solar","slides":[{"title":"Costs","bullets":["a","b"],'
                                          '"notes":"n"}]}\n```')
    assert title == "Solar" and slides[0].bullets == ["a", "b"] and slides[0].notes == "n"
    _t, _s, md = ai.parse_slides("# Deck\n## Why\n- cheap\n- clean\n## How\n- panels")
    assert [s.title for s in md] == ["Why", "How"] and md[0].bullets == ["cheap", "clean"]
    # translation keeps paragraph alignment, batches long documents
    monkeypatch.setattr(ai, "settings", lambda: settings)
    _Handler.replies[:] = ["[[1]] ایک\n[[2]] دو", "[[1]] تین"]
    out = ai.translate_batches(["one", "two", "three"], "Urdu", max_chars=7)
    assert out == ["ایک", "دو", "تین"]


def test_chat_pdf_retrieval_and_summary(settings, monkeypatch):
    pages = ["Revenue grew by 12 percent in Asia during 2025.", "Costs were flat.",
             "The board approved a dividend of 5 rupees per share."]
    bm = ai.BM25(ai.make_chunks(pages))
    hits = bm.search("dividend per share", 2)
    assert hits[0][1].page == 3
    msgs = ai.pdf_question_messages("What dividend?", [c for _s, c in hits])
    assert "[page 3]" in msgs[-1]["content"]
    assert ai.link_page_refs("Approved [p. 3].") == 'Approved <a href="page:3">[p. 3]</a>.'
    monkeypatch.setattr(ai, "settings", lambda: settings)
    calls = []

    def respond(body):
        system = body["messages"][0]["content"]
        calls.append("part" if "this part" in system else "final")
        return "a part [p. 1]" if calls[-1] == "part" else "Overall summary [p. 1] [p. 3]"
    monkeypatch.setattr(_Handler, "responder", staticmethod(respond))
    text = ai.summarize_pages(pages, budget=60)  # forces map-reduce
    assert text == "Overall summary [p. 1] [p. 3]"
    assert calls[-1] == "final" and calls.count("part") >= 2


def test_network_errors_are_explained_in_plain_words():
    """Found by testing the installed 1.2 build offline under Wine: with no network route the dialog showed
    "The AI request failed: [WinError 10051] Windows Error 0x2743" instead of an explanation."""
    import errno
    import socket
    import urllib.error

    local = ai.AISettings(provider="ollama", base_url="http://localhost:11434/v1", model="m")
    online = ai.AISettings(provider="openai", base_url="https://api.openai.com/v1", model="m", api_key="k")
    win_unreach = OSError(10051, "Windows Error 0x2743")  # what urllib reports on Windows (WSAENETUNREACH)
    posix_unreach = OSError(errno.ENETUNREACH, "Network is unreachable")
    host_unreach = OSError(10065, "Windows Error 0x2751")  # WSAEHOSTUNREACH
    for reason in (win_unreach, posix_unreach, host_unreach):
        msg = str(ai._friendly(urllib.error.URLError(reason), local))
        assert "Start the local AI server" in msg and "WinError" not in msg
        assert "Check the internet connection" in str(ai._friendly(urllib.error.URLError(reason), online))
    dns = socket.gaierror(11001, "getaddrinfo failed")
    assert "Check the internet connection" in str(ai._friendly(urllib.error.URLError(dns), online))
    assert "was interrupted" in str(ai._friendly(ConnectionResetError(10054, "reset"), online))
    assert "Start the local AI server" in str(ai._friendly(urllib.error.URLError(ConnectionRefusedError()), local))
    # anything else keeps the technical detail, so it can still be reported
    assert "The AI request failed: odd" == str(ai._friendly(urllib.error.URLError("odd"), online))
