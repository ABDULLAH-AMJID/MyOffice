"""Presentation scripting ("MyOffice Script").

Public entry points::

    from app.slide.script.runner import run_script      # execute code against a deck
    from app.slide.script.store import ScriptStore      # sidecar storage / trust
    from app.slide.script.dialog import ScriptDialog    # the editor UI
    from app.slide.script.api import PresentationAPI    # the object model
"""
from __future__ import annotations

__all__ = ["run_script", "ScriptStore", "ScriptDialog", "PresentationAPI"]


def __getattr__(name):
    # Lazy re-exports so importing the package never drags in Qt widgets.
    if name == "run_script":
        from app.slide.script.runner import run_script

        return run_script
    if name == "ScriptStore":
        from app.slide.script.store import ScriptStore

        return ScriptStore
    if name == "ScriptDialog":
        from app.slide.script.dialog import ScriptDialog

        return ScriptDialog
    if name == "PresentationAPI":
        from app.slide.script.api import PresentationAPI

        return PresentationAPI
    raise AttributeError(name)
