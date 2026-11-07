"""Runs a presentation script against a live ``SlideDocument``.

The whole run is one transaction:

* take a snapshot of the model first (``SlideDocument.snapshot()``),
* suppress the editor's per-action undo commits while the script works,
* on success push **one** undo entry named ``Macro: <name>``,
* on any failure restore the snapshot, so a broken script leaves the deck exactly
  as it was.

The interpreter lives in :mod:`app.macros.engine`; the objects the script sees
live in :mod:`app.slide.script.api`.
"""
from __future__ import annotations

import datetime
import math
import re
from typing import Callable

from app.macros.api import SAFE_BUILTINS, AppAPI
from app.macros.engine import execute_python
from app.macros.errors import MacroError
from app.services.i18n import _t
from app.slide.script.permissions import FileGate


def build_namespace(main, doc, gate: FileGate | None = None) -> dict:
    """The globals a presentation script runs with."""
    app = AppAPI(main, gate=gate, doc=doc)
    return {"__builtins__": SAFE_BUILTINS, "app": app, "math": math, "datetime": datetime, "re": re}


def run_script(code: str, main, name: str = "script", doc=None, timeout: float = 30.0,
               stop: Callable[[], bool] | None = None, pump: Callable[[], None] | None = None,
               gate: FileGate | None = None, ask_file: Callable[[str, str], bool] | None = None) -> str:
    """Execute ``code``; returns captured output.  Raises MacroError on any failure.

    ``ask_file(path, purpose) -> bool`` is the permission prompt for reads outside
    the document's folder; pass ``None`` to deny those reads outright.
    """
    doc = doc if doc is not None else (main.current_of_kind("slide") if main is not None else None)
    if doc is None:
        raise MacroError(_t("Open a presentation first."))
    if not hasattr(doc, "begin_script_run"):
        raise MacroError(_t("This document cannot run scripts."))

    gate = gate or FileGate(getattr(doc, "path", None), ask=ask_file)
    before = doc.begin_script_run()
    try:
        namespace = build_namespace(main, doc, gate)
        output = execute_python(code, namespace, timeout=timeout, name=name, stop=stop, pump=pump)
    except BaseException:
        doc.end_script_run()
        doc.restore(before)
        doc.finish_script_run()
        doc.apply_pending_selection()
        raise
    doc.end_script_run()
    doc.commit(_t("Macro: {0}").format(name))
    doc.rebuild_all()
    doc.finish_script_run()
    doc.apply_pending_selection()
    return output
