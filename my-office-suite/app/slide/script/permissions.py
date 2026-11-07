"""Capability gate for script file reads.

Scripts cannot use ``open()`` (it is not in the macro builtins) — the only way a
script touches the filesystem is through API methods that take a path, such as
``shapes.add_picture("logo.png")``.  Everything that does so is routed through
:class:`FileGate`, which lets paths inside the document's folder through and asks
the user about anything else, once per file per run.  Untitled documents have no
folder of their own, so nothing is free except the working directory.

This is a *permission* mechanism, not a sandbox: see
``docs/SCRIPTING_DESIGN.md`` §6.5.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.macros.errors import MacroError
from app.services.i18n import _t

MAX_READ_BYTES = 64 * 1024 * 1024     # 64 MB — a picture, not a disk image


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


class FileGate:
    """Allow reads inside the deck's folder; ask about everything else."""

    def __init__(self, doc_path: str | None = None, ask: Callable[[str, str], bool] | None = None):
        # "Free" means inside the folder the document was saved in.  With no saved document
        # there is no such folder: fall back to the process working directory (the install
        # folder for a desktop app) rather than the home folder — on Windows the whole user
        # profile (Documents, Desktop, Downloads, TEMP) lives under home, which would have
        # made every read free.
        self.base = Path(doc_path).resolve().parent if doc_path else Path.cwd().resolve()
        self.ask = ask
        self.granted: set[Path] = set()
        self.denied: set[Path] = set()
        self.log: list[str] = []

    def allow(self, path):
        """Pre-authorise a path or a folder (used for pictures the document already owns)."""
        try:
            self.granted.add(Path(path).resolve())
        except OSError:
            pass

    def check(self, path) -> Path:
        p = Path(str(path)).expanduser()
        try:
            rp = p.resolve()
        except OSError as exc:
            raise MacroError(_t("Cannot resolve the path {0!r}: {1}").format(str(path), exc)) from exc
        if rp in self.granted or _inside(rp, self.base):
            return rp
        if rp in self.denied:
            raise MacroError(_t("The script is not allowed to read {0}.").format(rp))
        if self.ask is None:
            raise MacroError(_t("This script may not read files outside the document's folder ({0}).").format(rp))
        self.log.append(str(rp))
        if not self.ask(str(rp), _t("The script wants to read this file")):
            self.denied.add(rp)
            raise MacroError(_t("Permission to read {0} was denied.").format(rp.name))
        self.granted.add(rp)
        return rp

    def read_bytes(self, path, purpose: str = "") -> bytes:
        rp = self.check(path)
        try:
            size = rp.stat().st_size
        except OSError as exc:
            raise MacroError(_t("Cannot read {0}: {1}").format(rp.name, exc)) from exc
        if size > MAX_READ_BYTES:
            raise MacroError(_t("{0} is too large for a script to read (limit {1} MB).")
                             .format(rp.name, MAX_READ_BYTES // (1024 * 1024)))
        try:
            with open(rp, "rb") as fh:
                return fh.read()
        except OSError as exc:
            raise MacroError(_t("Cannot read {0}: {1}").format(rp.name, exc)) from exc
