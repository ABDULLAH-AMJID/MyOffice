"""Macro manager: list, edit, run, import/export Python macros."""
from __future__ import annotations

import keyword
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

from app.macros.api import MacroError, run_macro
from app.services.config import cfg
from app.services.i18n import _t
from app.services.paths import resource_path, user_subdir
from app.shell.dialogs import confirm, get_open_file, get_save_file, show_error


def macros_dir() -> Path:
    return user_subdir("macros")


def list_macros() -> list[tuple[str, Path, bool]]:
    out = []
    for folder, builtin in ((resource_path("macros"), True), (macros_dir(), False)):
        if folder.is_dir():
            for p in sorted(folder.glob("*.py")):
                out.append((p.stem.replace("_", " "), p, builtin))
    return out


def save_macro(name: str, code: str) -> Path:
    safe = re.sub(r"[^\w\- ]+", "_", name).strip().replace(" ", "_") or "macro"
    path = macros_dir() / f"{safe}.py"
    path.write_text(code, encoding="utf-8")
    return path


def is_trusted(path: Path) -> bool:
    trusted = [Path(p) for p in cfg().get("macros.trusted_dirs", [])] + [macros_dir(), resource_path("macros")]
    try:
        rp = path.resolve()
        return any(str(rp).startswith(str(t.resolve())) for t in trusted)
    except OSError:
        return False


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.rules = []

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            f.setFontItalic(italic)
            return f
        kw = fmt("#0033b3", True)
        for w in keyword.kwlist:
            self.rules.append((QRegularExpression(rf"\b{w}\b"), kw))
        self.rules.append((QRegularExpression(r"\b(app|sheet|writer|s)\b"), fmt("#871094", True)))
        self.rules.append((QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), fmt("#1750eb")))
        self.rules.append((QRegularExpression(r"\"[^\"\\n]*\"|'[^'\\n]*'"), fmt("#067d17")))
        self.rules.append((QRegularExpression(r"#[^\n]*"), fmt("#8c8c8c", italic=True)))

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class MacroDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(_t("Macros"))
        self.resize(940, 620)
        v = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load)
        lv.addWidget(self.list, 1)
        for text, fn in ((_t("New"), self.new), (_t("Import\u2026"), self.import_), (_t("Export\u2026"), self.export),
                         (_t("Delete"), self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            lv.addWidget(b)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setObjectName("H2")
        rv.addWidget(self.title)
        self.editor = QPlainTextEdit()
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        self.editor.setFont(f)
        self.editor.setTabStopDistance(32)
        self.highlighter = PythonHighlighter(self.editor.document())
        rv.addWidget(self.editor, 3)
        row = QHBoxLayout()
        self.run_btn = QPushButton(_t("\u25b6 Run"))
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self.run)
        save = QPushButton(_t("Save"))
        save.clicked.connect(self.save)
        row.addWidget(self.run_btn)
        row.addWidget(save)
        row.addStretch(1)
        help_ = QLabel(_t("API: app.sheet[\"A1\"] = 1 \u2022 app.sheet.range(\"A1:B9\").values \u2022 "
                          "app.sheet.format(\"A1\", bold=True) \u2022 app.writer.insert(\"text\") \u2022 app.message(\"Hi\")"))
        help_.setObjectName("Muted")
        row.addWidget(help_)
        rv.addLayout(row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(150)
        self.output.setPlaceholderText(_t("Output of print() appears here"))
        rv.addWidget(self.output, 1)
        split.addWidget(right)
        split.setSizes([240, 700])
        v.addWidget(split)
        self.current: tuple | None = None
        self.refresh()

    def refresh(self, select: Path | None = None):
        self.list.clear()
        for name, path, builtin in list_macros():
            it = QListWidgetItem(name + (_t("  (example)") if builtin else ""))
            it.setData(Qt.UserRole, (str(path), builtin))
            self.list.addItem(it)
            if select is not None and Path(path) == select:
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)

    def _load(self, it, _prev=None):
        if it is None:
            return
        path, builtin = it.data(Qt.UserRole)
        self.current = (Path(path), builtin)
        self.title.setText(it.text())
        self.editor.setPlainText(Path(path).read_text(encoding="utf-8"))

    def new(self):
        name, ok = QInputDialog.getText(self, _t("New Macro"), _t("Name:"))
        if ok and name.strip():
            path = save_macro(name.strip(), f"# {name.strip()}\ns = app.sheet\ns[\"A1\"] = \"Hello from a macro\"\n")
            self.refresh(path)

    def save(self):
        if self.current is None:
            return
        path, builtin = self.current
        if builtin:
            name, ok = QInputDialog.getText(self, _t("Save Macro"), _t("Examples are read-only. Save a copy as:"),
                                            text=path.stem + "_copy")
            if not ok or not name.strip():
                return
            path = save_macro(name.strip(), self.editor.toPlainText())
            self.refresh(path)
        else:
            path.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.output.appendPlainText(_t("Saved {0}").format(path))

    def delete(self):
        if self.current is None:
            return
        path, builtin = self.current
        if builtin:
            show_error(self, _t("Delete"), _t("Example macros cannot be deleted."))
            return
        if confirm(self, _t("Delete Macro"), _t("Delete {0}?").format(path.stem)):
            path.unlink(missing_ok=True)
            self.refresh()

    def import_(self):
        p = get_open_file(self, _t("Import Macro"), "Python (*.py)")
        if not p:
            return
        if not confirm(self, _t("Import Macro"), _t("Macros can change your documents. Only import macros from people "
                                                    "you trust. Import \u201c{0}\u201d?").format(Path(p).name)):
            return
        dest = macros_dir() / Path(p).name
        shutil.copyfile(p, dest)
        self.refresh(dest)

    def export(self):
        if self.current is None:
            return
        out = get_save_file(self, _t("Export Macro"), "Python (*.py)", self.current[0].name, "py")
        if out:
            Path(out).write_text(self.editor.toPlainText(), encoding="utf-8")

    def run(self):
        code = self.editor.toPlainText()
        name = self.current[0].stem if self.current else "macro"
        if self.current is not None and not is_trusted(self.current[0]):
            if not confirm(self, _t("Run Macro"), _t("This macro is not in a trusted location. Run it anyway?")):
                return
        self.output.appendPlainText(f"\u25b6 {name}")
        try:
            out = run_macro(code, self.main, name=name)
            if out:
                self.output.appendPlainText(out.rstrip())
            self.output.appendPlainText(_t("\u2714 finished"))
        except MacroError as exc:
            self.output.appendPlainText(f"\u2716 {exc}")
