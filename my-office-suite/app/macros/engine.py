"""The one place in MyOffice that executes user Python.

Both the spreadsheet/writer macros (``app/macros/api.py``) and the presentation
scripts (``app/slide/script``) run through :func:`execute_python`, so timeout,
stop handling, output capture and error formatting can never drift apart.

A word on what this is and is not: executing Python with a reduced
``__builtins__`` is a *guardrail against mistakes* (runaway loops, accidental
imports, huge output), **not** a security boundary — hostile Python can reach
the interpreter through class introspection.  See
``docs/SCRIPTING_RESEARCH.md`` §4.2 and ``docs/SCRIPTING_DESIGN.md`` §6.5.
Every caller is expected to gate *who may run* a script (consent, trusted
locations) rather than relying on this module to contain it.
"""
from __future__ import annotations

import io
import sys
import time
import traceback
from contextlib import redirect_stdout
from typing import Callable

from app.macros.errors import MacroError, MacroStopped, MacroTimeout
from app.services.i18n import _t

#: How often (seconds) the tracer may pump the Qt event loop so Stop stays clickable.
PUMP_INTERVAL = 0.05


def execute_python(code: str, namespace: dict, timeout: float = 30.0, name: str = "macro",
                   stop: Callable[[], bool] | None = None,
                   pump: Callable[[], None] | None = None) -> str:
    """Run ``code`` in ``namespace``; return whatever it printed.

    ``stop``  — polled on every traced line; returning True aborts with MacroStopped.
    ``pump``  — called at most every 50 ms; the UI passes ``QApplication.processEvents``
                so a Stop button click is delivered while a long script runs.

    Raises:
        MacroTimeout  if the script runs longer than ``timeout`` seconds.
        MacroStopped  if ``stop()`` returns True.
        MacroError    for syntax errors and any exception raised by the script,
                      formatted as ``Line N: Type: message``.
    """
    out = io.StringIO()
    deadline = time.monotonic() + max(0.1, float(timeout))
    last_pump = [time.monotonic()]

    def tracer(frame, event, _arg):
        now = time.monotonic()
        if now > deadline:
            raise MacroTimeout(_t("The script ran longer than {0} seconds and was stopped.").format(int(timeout)))
        if stop is not None and stop():
            raise MacroStopped(_t("Stopped by user."))
        if pump is not None and now - last_pump[0] >= PUMP_INTERVAL:
            last_pump[0] = now
            pump()
        return tracer

    old_trace = sys.gettrace()
    try:
        compiled = compile(code, f"<{name}>", "exec")
        sys.settrace(tracer)
        with redirect_stdout(out):
            exec(compiled, namespace)  # noqa: S102 - documented guardrail, see module docstring
    except MacroError:
        raise
    except SyntaxError as exc:
        raise MacroError(_t("Syntax error in line {0}: {1}").format(exc.lineno, exc.msg)) from exc
    except SystemExit as exc:  # a script calling exit() must not kill the app
        raise MacroError(_t("The script tried to exit MyOffice (SystemExit).")) from exc
    except KeyboardInterrupt as exc:
        raise MacroStopped(_t("Stopped by user.")) from exc
    except BaseException as exc:  # noqa: BLE001 - every failure becomes a readable message
        line = _script_line(exc, name)
        raise MacroError(f"Line {line}: {exc.__class__.__name__}: {exc}") from exc
    finally:
        sys.settrace(old_trace)
    return out.getvalue()


def _script_line(exc: BaseException, name: str) -> int | str:
    """Best-effort line number inside the user's script (not inside our API)."""
    try:
        tb = traceback.extract_tb(exc.__traceback__)
    except Exception:
        return "?"
    target = f"<{name}>"
    for frame in reversed(tb):
        if frame.filename == target:
            return frame.lineno
    # The failure happened inside the API on behalf of the script: report the
    # deepest script frame we do have, or "?" when the traceback is foreign.
    return tb[-1].lineno if tb else "?"
