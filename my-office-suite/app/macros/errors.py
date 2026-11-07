"""Shared macro/script exceptions.

Lives in its own module so that document modules (e.g. ``app.slide.script``) can
raise and catch the same errors as the macro subsystem without importing the
whole macro API and creating an import cycle.
"""
from __future__ import annotations


class MacroError(Exception):
    """A script failed.  ``str(exc)`` is user-facing text."""


class MacroTimeout(MacroError):
    """The script exceeded its time budget and was stopped."""


class MacroStopped(MacroError):
    """The user pressed Stop."""
