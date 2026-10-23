"""MyOffice AI - one client for every OpenAI-compatible chat API.

The same ``/chat/completions`` protocol is spoken by OpenAI, Azure OpenAI, Google Gemini, Anthropic, OpenRouter, Groq
and - on the user's own PC, without internet - by Ollama, LM Studio, llama.cpp and Jan.  MyOffice ships no model and
no account: the user picks a provider in *AI Settings*.  With a local server nothing leaves the computer.

Everything here is GUI-free and runs in worker threads.  The task helpers (rewrite, grammar, translate, formula,
slides, Chat PDF) build the prompts and parse the answers robustly, because small local models do not always follow
formatting instructions exactly.
"""
from __future__ import annotations

import base64
import difflib
import errno
import json
import logging
import math
import re
import socket
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

from app.services.config import cfg
from app.version import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)

# provider id -> (label, base URL, example model, needs an API key)
PRESETS: dict[str, tuple[str, str, str, bool]] = {
    "ollama": ("Ollama - on this PC, offline", "http://localhost:11434/v1", "llama3.2", False),
    "lmstudio": ("LM Studio - on this PC, offline", "http://localhost:1234/v1", "", False),
    "openai": ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini", True),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", True),
    "anthropic": ("Anthropic Claude", "https://api.anthropic.com/v1", "claude-3-5-haiku-latest", True),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini", True),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "llama-3.1-8b-instant", True),
    "azure": ("Azure OpenAI", "https://YOUR-RESOURCE.openai.azure.com", "your-deployment", True),
    "custom": ("Other OpenAI-compatible server", "", "", False),
}
AZURE_API_VERSION = "2024-10-21"
LANGUAGES = ["English", "Urdu", "Hindi", "Arabic", "Spanish", "French", "German", "Chinese (Simplified)",
             "Japanese", "Korean", "Portuguese", "Russian", "Turkish", "Italian", "Persian", "Bengali", "Punjabi",
             "Indonesian", "Malay", "Dutch"]


class AIError(RuntimeError):
    pass


class AINotConfigured(AIError):
    pass


class AICancelled(AIError):
    pass


@dataclass
class AISettings:
    provider: str = "ollama"
    base_url: str = PRESETS["ollama"][1]
    model: str = PRESETS["ollama"][2]
    api_key: str = ""
    timeout: float = 120.0
    temperature: float = 0.3

    @property
    def is_local(self) -> bool:
        host = re.sub(r"^\w+://", "", self.base_url).split("/")[0].split(":")[0].lower()
        return host in ("localhost", "127.0.0.1", "::1", "[::1]") or host.endswith(".local")


# ----------------------------------------------------------------------------- key storage
def _protect(secret: str) -> str:
    """Windows: encrypt with DPAPI for the current user; elsewhere a reversible encoding (the config folder is
    private to the user anyway)."""
    if not secret:
        return ""
    data = secret.encode("utf-8")
    if sys.platform == "win32":
        try:
            return "dpapi:" + base64.b64encode(_dpapi(data, True)).decode()
        except OSError:
            log.warning("DPAPI unavailable, storing the API key encoded only")
    return "b64:" + base64.b64encode(data).decode()


def _unprotect(stored: str) -> str:
    if not stored:
        return ""
    kind, _, payload = stored.partition(":")
    try:
        raw = base64.b64decode(payload)
        if kind == "dpapi":
            return _dpapi(raw, False).decode("utf-8")
        if kind == "b64":
            return raw.decode("utf-8")
    except Exception as exc:  # moved to another user / PC: the key has to be entered again
        log.warning("Stored API key could not be read: %s", exc)
    return ""


def _dpapi(data: bytes, encrypt: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = BLOB(len(data), buf), BLOB()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)):  # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def settings() -> AISettings:
    c = cfg()
    provider = c.get("ai.provider", "ollama")
    preset = PRESETS.get(provider, PRESETS["custom"])
    return AISettings(provider=provider, base_url=c.get("ai.base_url", preset[1]) or preset[1],
                      model=c.get("ai.model", preset[2]) or preset[2], api_key=_unprotect(c.get("ai.api_key", "")),
                      timeout=float(c.get("ai.timeout", 120)), temperature=float(c.get("ai.temperature", 0.3)))


def save_settings(s: AISettings) -> None:
    c = cfg()
    c.set("ai.provider", s.provider)
    c.set("ai.base_url", s.base_url.strip().rstrip("/"))
    c.set("ai.model", s.model.strip())
    c.set("ai.api_key", _protect(s.api_key.strip()))
    c.set("ai.timeout", float(s.timeout))
    c.set("ai.temperature", float(s.temperature))
    c.set("ai.configured", True, save=True)


def configured() -> bool:
    s = settings()
    if not cfg().get("ai.configured", False) or not s.base_url or not s.model:
        return False
    return bool(s.api_key) or not PRESETS.get(s.provider, PRESETS["custom"])[3]


# ----------------------------------------------------------------------------- HTTP
def _endpoint(s: AISettings, path: str) -> tuple[str, dict]:
    base = s.base_url.rstrip("/")
    headers = {"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"}
    if s.provider == "azure":
        if path == "chat/completions":
            url = f"{base}/openai/deployments/{s.model}/chat/completions?api-version={AZURE_API_VERSION}"
        else:
            url = f"{base}/openai/{path}?api-version={AZURE_API_VERSION}"
        if s.api_key:
            headers["api-key"] = s.api_key
    else:
        url = f"{base}/{path}"
        if s.api_key:
            headers["Authorization"] = f"Bearer {s.api_key}"
        if s.provider == "anthropic":
            headers["x-api-key"] = s.api_key
            headers["anthropic-version"] = "2023-06-01"
        if s.provider == "openrouter":
            headers["HTTP-Referer"] = "https://myoffice.local"
            headers["X-Title"] = APP_NAME
    return url, headers


def _friendly(exc: Exception, s: AISettings) -> AIError:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", "replace")
            detail = json.loads(body).get("error", body)
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)
        except Exception:
            detail = ""
        detail = str(detail)[:400]
        if exc.code in (401, 403):
            return AIError(f"The AI provider rejected the API key (HTTP {exc.code}). Check it in AI Settings. {detail}")
        if exc.code == 404:
            return AIError(f"The model \"{s.model}\" or the address was not found (HTTP 404). Check AI Settings. "
                           f"{detail}")
        if exc.code == 429:
            return AIError(f"The AI provider says there are too many requests or the quota is used up (HTTP 429). "
                           f"{detail}")
        return AIError(f"The AI provider returned HTTP {exc.code}. {detail}")
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        if s.is_local:
            return AIError(f"Nothing answers at {s.base_url}. Start the local AI server (for example Ollama or LM "
                           f"Studio) and try again.")
        return AIError(f"The connection to {s.base_url} was refused.")
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
        return AIError(f"The AI provider did not answer within {int(s.timeout)} seconds.")
    if _unreachable(reason):
        if s.is_local:
            return AIError(f"Nothing answers at {s.base_url}. Start the local AI server (for example Ollama or LM "
                           f"Studio) and try again.")
        return AIError("The AI provider cannot be reached. Check the internet connection, or use a local AI "
                       "server for offline work.")
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return AIError("The connection to the AI provider was interrupted. Try again.")
    return AIError(f"The AI request failed: {reason}")


# No route to the server: network adapter off, no Wi-Fi, VPN or firewall.  POSIX errno values plus the Winsock codes
# WSAENETDOWN, WSAENETUNREACH, WSAEHOSTDOWN and WSAEHOSTUNREACH, which Python on Windows reports as errno/winerror
# (the system text is not always available, e.g. "[WinError 10051] Windows Error 0x2743").
_UNREACHABLE_CODES = frozenset({getattr(errno, n) for n in ("ENETDOWN", "ENETUNREACH", "EHOSTDOWN", "EHOSTUNREACH")
                                if hasattr(errno, n)} | {10050, 10051, 10064, 10065})


def _unreachable(reason) -> bool:
    if isinstance(reason, socket.gaierror):  # the name of the server cannot be resolved (offline)
        return True
    if isinstance(reason, OSError) and {reason.errno, getattr(reason, "winerror", None)} & _UNREACHABLE_CODES:
        return True
    text = str(reason)
    return any(k in text for k in ("Name or service not known", "getaddrinfo", "unreachable", "Network is down"))


def chat(messages: list[dict], *, temperature: float | None = None, max_tokens: int | None = None,
         s: AISettings | None = None, on_delta=None, is_cancelled=None, json_mode: bool = False) -> str:
    """Send a chat request and return the answer text.  ``on_delta(text)`` streams partial output."""
    s = s or settings()
    if not s.base_url or not s.model:
        raise AINotConfigured("Choose an AI provider and a model in AI Settings first.")
    url, headers = _endpoint(s, "chat/completions")
    body = {"model": s.model, "messages": messages,
            "temperature": s.temperature if temperature is None else temperature}
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    if on_delta is not None:
        body["stream"] = True
    if json_mode and s.provider in ("openai", "azure", "groq", "openrouter"):
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=s.timeout)
    except Exception as exc:
        raise _friendly(exc, s) from exc
    with resp:
        if on_delta is None:
            try:
                data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                raise _friendly(exc, s) from exc
            return _message_text(data)
        parts: list[str] = []
        try:
            for raw in resp:
                if is_cancelled and is_cancelled():
                    raise AICancelled("Stopped.")
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content") or \
                    (choices[0].get("message") or {}).get("content") or ""
                if delta:
                    parts.append(delta)
                    on_delta(delta)
        except AICancelled:
            raise
        except Exception as exc:
            if not parts:
                raise _friendly(exc, s) from exc
            log.warning("AI stream ended early: %s", exc)
        return "".join(parts)


def _message_text(data: dict) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("The AI provider sent an answer MyOffice does not understand: " + json.dumps(data)[:300])
    if isinstance(content, list):  # content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return strip_thinking(content or "")


def strip_thinking(text: str) -> str:
    """Reasoning models (DeepSeek-R1, Qwen3...) may prefix their answer with <think>...</think>."""
    return re.sub(r"(?s)^\s*<think>.*?</think>\s*", "", text)


def list_models(s: AISettings | None = None) -> list[str]:
    s = s or settings()
    url, headers = _endpoint(s, "models")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=min(s.timeout, 20)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise _friendly(exc, s) from exc
    items = data.get("data") or data.get("models") or []
    names = sorted({(m.get("id") or m.get("name") or "").removeprefix("models/") for m in items if isinstance(m, dict)})
    return [n for n in names if n]


def test_connection(s: AISettings) -> str:
    answer = chat([{"role": "user", "content": "Reply with the single word: OK"}], s=s, temperature=0, max_tokens=8)
    return answer.strip()


# ----------------------------------------------------------------------------- helpers for answers
def extract_json(text: str):
    """The first JSON object/array in ``text`` (models like to wrap JSON in prose or ```json fences)."""
    text = strip_thinking(text)
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except ValueError:
                            break
            start = text.find(opener, start + 1)
    raise AIError("The AI answer did not contain the expected data. Try again or use a larger model.")


def clean_text(text: str) -> str:
    """Remove a chatty preamble/fences that some models add around rewritten text."""
    text = strip_thinking(text).strip()
    text = re.sub(r"^```\w*\n(.*)\n```$", r"\1", text, flags=re.S)
    text = re.sub(r"(?i)^(here(?:'s| is) (?:the |your )?(?:rewritten|corrected|improved|translated|revised)[^:\n]*:)"
                  r"\s*\n", "", text)
    return text.strip()


# ----------------------------------------------------------------------------- Writer tasks
WRITE_ACTIONS = {
    "write": "Write new text about the user's topic.",
    "continue": "Continue the text naturally with one or two more paragraphs in the same style.",
    "improve": "Rewrite the text so it reads clearly and fluently. Keep its meaning and language.",
    "shorten": "Make the text about half as long. Keep the key information.",
    "expand": "Expand the text with more detail and examples. Keep the style.",
    "formal": "Rewrite the text in a formal, professional tone.",
    "casual": "Rewrite the text in a friendly, casual tone.",
    "summarize": "Summarize the text in a few sentences.",
    "bullets": "Turn the text into a concise bulleted list (one point per line, starting with '- ').",
    "custom": "Follow the user's instruction for the text.",
}


def writer_messages(action: str, text: str, instruction: str = "", language: str = "") -> list[dict]:
    system = ("You are the writing assistant inside the office suite MyOffice. " + WRITE_ACTIONS.get(action, "") +
              " Answer with the resulting text only - no introduction, no quotes, no Markdown headings.")
    if language:
        system += f" Write in {language}."
    else:
        system += " Write in the same language as the given text."
    user = ""
    if instruction:
        user += f"Instruction: {instruction}\n\n"
    if text:
        user += f"Text:\n{text}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user or instruction}]


@dataclass
class Correction:
    start: int          # character offset in the original text
    end: int
    original: str
    suggestion: str


def grammar_messages(text: str, variant: str = "American English") -> list[dict]:
    return [{"role": "system", "content":
             "You are a careful proofreader. Correct spelling, grammar and punctuation errors in the user's text "
             f"({variant} spelling for English). Change nothing else: keep wording, style, line breaks and "
             "formatting characters. Answer with the corrected text only."},
            {"role": "user", "content": text}]


def corrections(original: str, corrected: str) -> list[Correction]:
    """Word-level differences between the original and the AI-corrected text, as individual suggestions."""
    corrected = clean_text(corrected)
    tok = re.compile(r"\w+|[^\w\s]+|\s+", re.U)
    a = [(m.start(), m.group()) for m in tok.finditer(original)]
    b = [m.group() for m in tok.finditer(corrected)]
    sm = difflib.SequenceMatcher(None, [t for _, t in a], b, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        start = a[i1][0] if i1 < len(a) else len(original)
        end = (a[i2 - 1][0] + len(a[i2 - 1][1])) if i2 > i1 else start
        orig = original[start:end]
        sug = "".join(b[j1:j2])
        if orig.strip() == sug.strip() and orig.strip():
            continue  # whitespace-only change
        out.append(Correction(start, end, orig, sug))
    return out


def apply_corrections(text: str, chosen: list[Correction]) -> str:
    for c in sorted(chosen, key=lambda c: c.start, reverse=True):
        text = text[:c.start] + c.suggestion + text[c.end:]
    return text


def translate_messages(paragraphs: list[str], target: str) -> list[dict]:
    numbered = "\n".join(f"[[{i + 1}]] {p}" for i, p in enumerate(paragraphs))
    return [{"role": "system", "content":
             f"Translate each numbered paragraph into {target}. Keep the [[n]] markers at the start of each "
             "paragraph, keep numbers, names and formatting, and output nothing else."},
            {"role": "user", "content": numbered}]


def parse_translation(answer: str, count: int) -> list[str]:
    answer = strip_thinking(answer)
    found = {int(m.group(1)): m.group(2).strip()
             for m in re.finditer(r"\[\[(\d+)\]\]\s*(.*?)(?=\n?\s*\[\[\d+\]\]|\Z)", answer, re.S)}
    if found:
        return [found.get(i + 1, "") for i in range(count)]
    lines = [ln.strip() for ln in answer.strip().splitlines() if ln.strip()]
    if count == 1:
        return [" ".join(lines)]
    return (lines + [""] * count)[:count]


def translate_batches(paragraphs: list[str], target: str, progress=None, is_cancelled=None,
                      max_chars: int = 3000) -> list[str]:
    """Translate paragraph by paragraph in batches (keeps the side-by-side alignment for long documents)."""
    out: list[str] = [""] * len(paragraphs)
    batch: list[int] = []
    size = 0

    def flush():
        nonlocal batch, size
        todo = [i for i in batch if paragraphs[i].strip()]
        if todo:
            res = parse_translation(chat(translate_messages([paragraphs[i] for i in todo], target), temperature=0.1),
                                    len(todo))
            for i, t in zip(todo, res):
                out[i] = t
        batch, size = [], 0

    for i, p in enumerate(paragraphs):
        if is_cancelled and is_cancelled():
            raise AICancelled("Stopped.")
        if batch and size + len(p) > max_chars:
            flush()
            if progress:
                progress(int(100 * i / max(1, len(paragraphs))), "")
        batch.append(i)
        size += len(p)
    flush()
    if progress:
        progress(100, "")
    return out


# ----------------------------------------------------------------------------- Spreadsheet tasks
def formula_messages(request: str, context: str, cell: str) -> list[dict]:
    return [{"role": "system", "content":
             "You write Microsoft Excel formulas. MyOffice supports the standard Excel functions, including XLOOKUP, "
             "FILTER, SORT, UNIQUE, SEQUENCE, LET, IFS, SWITCH, TEXTJOIN and SUMIFS. Use English function names and "
             "commas between arguments. Answer as JSON: {\"formula\": \"=...\", \"explanation\": \"one or two "
             "sentences\"}."},
            {"role": "user", "content": f"Sheet context:\n{context}\n\nThe formula goes into cell {cell}.\n"
                                        f"Task: {request}"}]


def parse_formula(answer: str) -> tuple[str, str]:
    try:
        data = extract_json(answer)
        formula = str(data.get("formula", "")).strip()
        explanation = str(data.get("explanation", "")).strip()
    except (AIError, AttributeError):
        formula, explanation = _scan_formula(strip_thinking(answer)), ""
    formula = formula.strip("` ")
    if formula and not formula.startswith("="):
        formula = "=" + formula
    if not formula:
        raise AIError("The AI answer did not contain a formula. Describe the calculation in more detail.")
    return formula, explanation


def _scan_formula(text: str) -> str:
    """The formula in a prose answer: from '=' up to where the expression ends (balanced brackets, no prose)."""
    m = re.search(r"=\s*[A-Z_][A-Z0-9_.]*\s*\(|=\s*[A-Z]{1,3}\d+", text)
    if not m:
        return ""
    i, depth, quote = m.start(), 0, False
    out = []
    while i < len(text):
        ch = text[i]
        if quote:
            quote = ch != '"'
        elif ch == '"':
            quote = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                break
        elif ch in "\n`" and depth == 0:
            break
        elif ch.isspace() and depth == 0:
            rest = text[i:].lstrip()
            last = "".join(out).rstrip()[-1:]
            if not rest or (rest[0] not in "+-*/&^<>=," and last not in "+-*/&^<>=,"):
                break
        out.append(ch)
        i += 1
    return "".join(out).strip().rstrip(".;")


def analysis_messages(summary: str, question: str = "") -> list[dict]:
    return [{"role": "system", "content":
             "You are a data analyst. The user gives you statistics computed from a spreadsheet range and a sample "
             "of rows. Point out the most important patterns, trends, outliers and suggestions in short bullet "
             "points. Only use numbers that appear in the data."},
            {"role": "user", "content": summary + (f"\n\nQuestion: {question}" if question else "")}]


# ----------------------------------------------------------------------------- Presentation tasks
@dataclass
class SlideOutline:
    title: str
    bullets: list[str] = field(default_factory=list)
    notes: str = ""


def slides_messages(topic: str, count: int, language: str = "", audience: str = "") -> list[dict]:
    lang = f" Write in {language}." if language else ""
    aud = f" The audience is: {audience}." if audience else ""
    return [{"role": "system", "content":
             "You create presentation outlines. Answer as JSON only: {\"title\": \"deck title\", \"subtitle\": "
             "\"...\", \"slides\": [{\"title\": \"...\", \"bullets\": [\"3 to 5 short points\"], \"notes\": "
             "\"speaker notes\"}]}." + lang + aud},
            {"role": "user", "content": f"Create {count} content slides about: {topic}"}]


def parse_slides(answer: str) -> tuple[str, str, list[SlideOutline]]:
    try:
        data = extract_json(answer)
        if isinstance(data, list):
            data = {"slides": data}
        slides = [SlideOutline(str(s.get("title", "")).strip(),
                               [str(b).strip() for b in (s.get("bullets") or s.get("points") or []) if str(b).strip()],
                               str(s.get("notes", "")).strip())
                  for s in data.get("slides", []) if isinstance(s, dict)]
        if slides:
            return str(data.get("title", "")).strip(), str(data.get("subtitle", "")).strip(), slides
    except AIError:
        pass
    # Markdown fallback: "# Title", "## Slide", "- bullet"
    title, slides = "", []
    for line in strip_thinking(answer).splitlines():
        s = line.strip()
        if re.match(r"^#\s", s) and not title:
            title = s.lstrip("# ").strip()
        elif re.match(r"^(#{2,}\s|\d+[.)]\s|slide\s*\d+[:.])", s, re.I):
            slides.append(SlideOutline(re.sub(r"^(#{2,}\s|\d+[.)]\s|slide\s*\d+[:.]\s*)", "", s, flags=re.I).strip()))
        elif re.match(r"^[-*\u2022]\s", s) and slides:
            slides[-1].bullets.append(s[1:].strip())
    if not slides:
        raise AIError("The AI answer did not contain slides. Try again or use a larger model.")
    return title, "", slides


# ----------------------------------------------------------------------------- Chat PDF (retrieval)
_WORD = re.compile(r"\w+", re.U)


def _terms(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if len(w) > 1]


@dataclass
class Chunk:
    page: int        # 1-based
    text: str


def make_chunks(pages: list[str], size: int = 900, overlap: int = 150) -> list[Chunk]:
    out = []
    for i, text in enumerate(pages):
        text = re.sub(r"[ \t]+", " ", text or "").strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                cut = text.rfind(". ", start + size // 2, end)
                if cut > 0:
                    end = cut + 1
            out.append(Chunk(i + 1, text[start:end].strip()))
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return out


class BM25:
    """Okapi BM25 over the chunks - finds the passages that answer a question, entirely on this PC."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(_terms(c.text)) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 6) -> list[tuple[float, Chunk]]:
        q = _terms(query)
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg or 1)))
            if s > 0:
                scores.append((s, self.chunks[i]))
        scores.sort(key=lambda x: -x[0])
        return scores[:k]


def pdf_question_messages(question: str, hits: list[Chunk], history: list[tuple[str, str]] | None = None,
                          language: str = "") -> list[dict]:
    context = "\n\n".join(f"[page {c.page}]\n{c.text}" for c in hits)
    lang = f" Answer in {language}." if language else " Answer in the language of the question."
    msgs = [{"role": "system", "content":
             "You answer questions about a PDF document using only the given excerpts. After every statement cite "
             "the page in square brackets like [p. 3]. If the excerpts do not contain the answer, say so." + lang}]
    for q, a in (history or [])[-3:]:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {question}"})
    return msgs


def pdf_summary_messages(text_with_pages: str, language: str = "", partial: bool = False) -> list[dict]:
    lang = f" Write in {language}." if language else ""
    task = ("Summarize this part of a document in a few bullet points, citing pages like [p. 3]." if partial else
            "Write a structured summary of the document: a one-paragraph overview, then the key points as bullets "
            "citing pages like [p. 3], then the main conclusions.")
    return [{"role": "system", "content": task + lang}, {"role": "user", "content": text_with_pages}]


def summarize_pages(pages: list[str], language: str = "", progress=None, is_cancelled=None,
                    budget: int = 12000) -> str:
    """Map-reduce summary for documents longer than one request."""
    tagged = [f"[page {i + 1}]\n{t.strip()}" for i, t in enumerate(pages) if t and t.strip()]
    if not tagged:
        raise AIError("The PDF has no text. Run OCR first for scanned documents.")
    whole = "\n\n".join(tagged)
    if len(whole) <= budget:
        return clean_text(chat(pdf_summary_messages(whole, language)))
    parts, cur = [], ""
    for t in tagged:
        if cur and len(cur) + len(t) > budget:
            parts.append(cur)
            cur = ""
        cur += t[:budget] + "\n\n"
    if cur:
        parts.append(cur)
    partials = []
    for i, p in enumerate(parts):
        if is_cancelled and is_cancelled():
            raise AICancelled("Stopped.")
        partials.append(clean_text(chat(pdf_summary_messages(p, language, partial=True))))
        if progress:
            progress(int(90 * (i + 1) / len(parts)), "")
    return clean_text(chat(pdf_summary_messages("\n\n".join(partials), language)))


PAGE_REF = re.compile(r"\[(?:p\.?|page|pages?)\s*(\d+)(?:\s*[-\u2013,]\s*(\d+))?\]", re.I)


def link_page_refs(html_text: str) -> str:
    """Turn [p. 3] citations into clickable links (page:3)."""
    return PAGE_REF.sub(lambda m: f'<a href="page:{m.group(1)}">{m.group(0)}</a>', html_text)
