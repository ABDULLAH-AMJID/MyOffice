"""Universal Shared File Document Tab (`SharedFileDocument`) for code, plain text, configs & arbitrary binary files.

Allows MyOffice to open, edit, preview, version-sync, and collaborate in real time over LAN on **every** file format
beyond native Writer/Sheet/Slide/PDF/Photos — e.g. `.txt`, `.md`, `.json`, `.yaml`, `.yml`, `.xml`, `.html`, `.css`,
`.js`, `.ts`, `.py`, `.sql`, `.ini`, `.log`, `.bat`, `.ps1`, `.sh`, as well as arbitrary binary files (`.zip`, `.mp4`,
`.dwg`, `.psd`, `.apk`, `.exe`, etc.) with live revision sharing + Team Chat.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from app.services.i18n import _t
from app.shell.document import DocumentWidget
from app.shell.icons import icon
from app.shell.ribbon import Ribbon

TEXT_LIKE_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".xml", ".svg", ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".php", ".rb", ".sql", ".sh", ".bash", ".bat",
    ".cmd", ".ps1", ".tex", ".bib", ".gitignore", ".dockerfile",
}


def is_probably_text(data: bytes, ext: str = "") -> bool:
    if ext.lower() in TEXT_LIKE_EXTS:
        return True
    if not data:
        return True
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class SharedFileDocument(DocumentWidget):
    kind = "code"
    default_ext = ".txt"
    writable_exts = tuple(sorted(TEXT_LIKE_EXTS))
    binaryReplaced = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_binary = False
        self._raw_bytes = b""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.body_wrap = QWidget()
        bw = QVBoxLayout(self.body_wrap)
        bw.setContentsMargins(0, 0, 0, 0)
        bw.setSpacing(0)

        # Text / Code editor view
        self.editor = QPlainTextEdit()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        self.editor.setFont(mono)
        self.editor.setTabStopDistance(32)
        self.editor.textChanged.connect(self._on_text_changed)
        bw.addWidget(self.editor, 1)

        # Binary file collaboration card (shown automatically for `.zip`, `.mp4`, `.psd`, `.dwg`, etc.)
        self.bin_card = QFrame()
        self.bin_card.setVisible(False)
        bc = QVBoxLayout(self.bin_card)
        bc.setContentsMargins(36, 36, 36, 36)
        bc.setSpacing(14)
        bc.addStretch(1)
        self.bin_title = QLabel("")
        self.bin_title.setAlignment(Qt.AlignCenter)
        self.bin_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.bin_meta = QLabel("")
        self.bin_meta.setAlignment(Qt.AlignCenter)
        self.bin_meta.setWordWrap(True)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        b_open_ext = QPushButton("Open in Default Windows App")
        b_open_ext.clicked.connect(self._open_external)
        b_replace = QPushButton("Upload / Replace File Version\u2026")
        b_replace.setProperty("accent", True)
        b_replace.clicked.connect(self._replace_from_disk)
        b_save_copy = QPushButton("Save Local Copy As\u2026")
        b_save_copy.clicked.connect(self.save_as)
        btn_row.addWidget(b_open_ext)
        btn_row.addWidget(b_replace)
        btn_row.addWidget(b_save_copy)
        btn_row.addStretch(1)
        bc.addWidget(self.bin_title)
        bc.addWidget(self.bin_meta)
        bc.addLayout(btn_row)
        bc.addStretch(1)
        bw.addWidget(self.bin_card, 1)

        root.addWidget(self.body_wrap, 1)

        strip = QWidget()
        strip.setObjectName("StatusStrip")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(8, 2, 8, 2)
        self.info_lbl = QLabel(_t("Ready"))
        self.info_lbl.setObjectName("Muted")
        sl.addWidget(self.info_lbl, 1)
        root.addWidget(strip)

        self.ribbon = self._build_ribbon()
        self._suppress_mod = False

    def _build_ribbon(self) -> Ribbon:
        rb = Ribbon(self)
        home = rb.page(_t("Home"))
        g_edit = home.group(_t("Edit"))
        a_undo = QAction(icon("undo"), _t("Undo"), self)
        a_undo.setShortcut(QKeySequence.Undo)
        a_undo.triggered.connect(self.editor.undo)
        a_redo = QAction(icon("redo"), _t("Redo"), self)
        a_redo.setShortcut(QKeySequence.Redo)
        a_redo.triggered.connect(self.editor.redo)
        a_wrap = QAction(icon("align-l"), _t("Word Wrap"), self)
        a_wrap.setCheckable(True)
        a_wrap.setChecked(True)
        a_wrap.toggled.connect(
            lambda on: self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)
        )
        g_edit.small(a_undo)
        g_edit.small(a_redo)
        g_edit.large(a_wrap)

        g_ext = home.group(_t("External & Sync"))
        a_ext = QAction(icon("open"), _t("Open Externally"), self)
        a_ext.triggered.connect(self._open_external)
        a_rep = QAction(icon("refresh"), _t("Push Updated File"), self)
        a_rep.triggered.connect(self._replace_from_disk)
        g_ext.large(a_ext)
        g_ext.large(a_rep)
        return rb

    def ribbon_widget(self):
        return self.ribbon

    def untitled_label(self) -> str:
        return _t("Note")

    def save_filters(self) -> str:
        if self.path and self.path.suffix:
            ext = self.path.suffix
            return f"File (*{ext});;All files (*)"
        return "Text / Code (*.txt *.md *.json *.py);;All files (*)"

    def _can_save_in_place(self) -> bool:
        return self.path is not None and not self.read_only

    def _on_text_changed(self):
        if self._suppress_mod or self.is_binary:
            return
        self.set_modified(True)
        txt = self.editor.toPlainText()
        self.info_lbl.setText(f"{txt.count(chr(10)) + 1} lines \u2022 {len(txt)} chars")

    def load(self, path: str) -> None:
        p = Path(path)
        raw = p.read_bytes()
        self.path = p
        self._apply_bytes(raw, p.name)
        self.set_modified(False)

    def _apply_bytes(self, raw: bytes, display_filename: str = "") -> None:
        ext = os.path.splitext(display_filename or (self.path.name if self.path else ""))[1]
        self._raw_bytes = raw
        if is_probably_text(raw, ext):
            self.is_binary = False
            self.bin_card.setVisible(False)
            self.editor.setVisible(True)
            self._suppress_mod = True
            try:
                self.editor.setPlainText(raw.decode("utf-8", errors="replace"))
            finally:
                self._suppress_mod = False
            txt = self.editor.toPlainText()
            self.info_lbl.setText(f"UTF-8 Text / Code \u2022 {txt.count(chr(10)) + 1} lines \u2022 {len(raw):,} bytes")
        else:
            self.is_binary = True
            self.editor.setVisible(False)
            self.bin_card.setVisible(True)
            sha = hashlib.sha256(raw).hexdigest()[:16]
            mime = mimetypes.guess_type(display_filename)[0] or "application/octet-stream"
            self.bin_title.setText(display_filename or "Shared Binary File")
            self.bin_meta.setText(
                f"Type: {mime}  \u2022  Size: {len(raw) / 1024:.1f} KB ({len(raw):,} bytes)\n"
                f"SHA-256 Prefix: {sha}  \u2022  Live LAN File Sync & Team Chat Ready"
            )
            self.info_lbl.setText(f"Binary File ({mime}) \u2022 {len(raw):,} bytes")

    def apply_remote_binary(self, raw: bytes) -> None:
        if self.path:
            self.path.write_bytes(raw)
        self._apply_bytes(raw, self.display_name())
        self.set_modified(False)

    def save_to(self, path: str) -> None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if self.is_binary:
            out.write_bytes(self._raw_bytes)
        else:
            data = self.editor.toPlainText().encode("utf-8")
            self._raw_bytes = data
            out.write_bytes(data)

    def export_pdf_to(self, path: str) -> None:
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrinter

        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        doc = QTextDocument(self.editor.toPlainText() if not self.is_binary else self.bin_meta.text())
        doc.print_(printer)

    def _open_external(self):
        if self.path and self.path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def _replace_from_disk(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose updated version to share with team", "")
        if not chosen:
            return
        raw = Path(chosen).read_bytes()
        if self.path:
            shutil.copy2(chosen, self.path)
        self._apply_bytes(raw, self.display_name())
        self.binaryReplaced.emit(raw)
