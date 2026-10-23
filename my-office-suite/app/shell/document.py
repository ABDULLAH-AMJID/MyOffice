"""Base class shared by every document tab (Writer, Spreadsheet, Presentation, PDF)."""
from __future__ import annotations

import itertools
import logging
import os
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from app.services import backup
from app.services.config import cfg
from app.services.i18n import _t

log = logging.getLogger(__name__)
_serial = itertools.count(1)  # unique id of every document widget (auto-recovery snapshots)
# "Document 1", "Book 1", "Presentation 1" ... counted per document type, like Office; opened files use no number
_untitled_numbers: dict[str, itertools.count] = {}


class DocumentError(Exception):
    """A user-facing error while loading or saving a document."""


class OpenCancelled(Exception):
    """The user cancelled opening a document (e.g. declined loading a huge file)."""


class DocumentWidget(QWidget):
    kind = "base"
    default_ext = ""
    writable_exts: tuple[str, ...] = ()
    modifiedChanged = Signal(bool)
    titleChanged = Signal()
    statusChanged = Signal(str)
    openRequested = Signal(object)     # ask the shell to add another DocumentWidget as a tab
    openPathRequested = Signal(str)    # ask the shell to open a file

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Path | None = None
        self._modified = False
        self._untitled: int | None = None   # number of an untitled document, given when it is first shown
        self.doc_id = f"{self.kind}{next(_serial)}_{id(self) & 0xFFFF:x}"
        self.read_only = False
        self.title_override: str | None = None   # e.g. template name for a new document
        self.password: str | None = None         # "password to open" (DOCX/XLSX/PPTX, AES-256 like Office)

    # ------------------------------------------------------------------ naming
    def display_name(self) -> str:
        if self.path:
            return self.path.name
        if self.title_override:
            return self.title_override
        if self._untitled is None:
            self._untitled = next(_untitled_numbers.setdefault(self.kind, itertools.count(1)))
        return f"{self.untitled_label()} {self._untitled}"

    def untitled_label(self) -> str:
        return _t("Untitled")

    def tab_title(self) -> str:
        return ("● " if self._modified else "") + self.display_name()

    # ------------------------------------------------------------------ modified flag
    def is_modified(self) -> bool:
        return self._modified

    def set_modified(self, value: bool = True) -> None:
        value = bool(value)
        if value != self._modified:
            self._modified = value
            self.modifiedChanged.emit(value)
            self.titleChanged.emit()

    # ------------------------------------------------------------------ abstract API
    def load(self, path: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def save_to(self, path: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def export_pdf_to(self, path: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def save_filters(self) -> str:
        return ""

    def print_document(self) -> None:
        pass

    def status_text(self) -> str:
        return ""

    def ribbon_widget(self):
        return None

    def recovery_ext(self) -> str:
        return self.default_ext

    def write_recovery(self, path: str) -> None:
        """Write a snapshot without touching the document's path/modified state."""
        old_path, old_mod = self.path, self._modified
        try:
            self.save_to(path)
        finally:
            self.path, self._modified = old_path, old_mod

    # ------------------------------------------------------------------ save logic
    def _can_save_in_place(self) -> bool:
        return (self.path is not None and not self.read_only
                and self.path.suffix.lower() in self.writable_exts)

    def save(self) -> bool:
        if not self._can_save_in_place():
            return self.save_as()
        return self._save_path(str(self.path))

    def save_as(self) -> bool:
        from app.shell.dialogs import get_save_file

        suggested = (self.path.stem if self.path else self.display_name()) + self.default_ext
        path = get_save_file(self, _t("Save As"), self.save_filters(), suggested, self.default_ext.lstrip("."))
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += self.default_ext
        return self._save_path(path)

    def _save_path(self, path: str) -> bool:
        from app.shell.dialogs import handle_exception

        try:
            if os.path.exists(path):
                try:
                    backup.make_backup(path)
                except Exception as exc:  # backup failure must not block saving
                    log.warning("Backup failed for %s: %s", path, exc)
            from app.services import ooxml_crypto

            if self.password and os.path.splitext(path)[1].lower() in ooxml_crypto.OOXML_EXTS:
                import tempfile

                fd, plain = tempfile.mkstemp(prefix="myoffice_plain_", suffix=os.path.splitext(path)[1].lower(),
                                             dir=os.path.dirname(os.path.abspath(path)) or None)
                os.close(fd)
                try:
                    self.save_to(plain)
                    ooxml_crypto.encrypt_file(plain, path, self.password)
                finally:
                    if os.path.exists(plain):
                        os.unlink(plain)
            else:
                self.save_to(path)
        except Exception as exc:
            handle_exception(self, _t("Could not save the file"), exc)
            return False
        try:
            from app.services import versions

            versions.record(path)
        except Exception as exc:  # version history must never block saving
            log.warning("Version not recorded for %s: %s", path, exc)
        self.path = Path(path)
        self.read_only = False
        self.set_modified(False)
        self.titleChanged.emit()
        cfg().add_recent(path)
        self.statusChanged.emit(_t("Saved: {0}").format(path))
        return True

    def export_pdf(self) -> bool:
        from app.shell.dialogs import get_save_file, handle_exception, show_info

        base = self.path.stem if self.path else self.display_name()
        path = get_save_file(self, _t("Export to PDF"), "PDF (*.pdf)", base + ".pdf", "pdf")
        if not path:
            return False
        try:
            self.export_pdf_to(path)
        except Exception as exc:
            handle_exception(self, _t("PDF export failed"), exc)
            return False
        self.statusChanged.emit(_t("Exported: {0}").format(path))
        show_info(self, _t("Export to PDF"), _t("The PDF was created:\n{0}").format(path))
        return True

    def close_request(self) -> bool:
        from app.shell.dialogs import ask_save_changes

        if not self._modified:
            return True
        choice = ask_save_changes(self, self.display_name())
        if choice == "save":
            return self.save()
        return choice == "discard"
