"""Batch Rename: rename many files at once with a pattern (name, counter, date, extension), find & replace,
upper/lower case - with a live preview and Undo."""
from __future__ import annotations

import os
import re
from datetime import datetime

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from app.services.i18n import _t

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def new_names(paths: list[str], pattern: str, start: int = 1, digits: int = 3, find: str = "", replace: str = "",
              case: str = "keep", regex: bool = False, date_source: str = "modified") -> list[str]:
    """New file names (not paths) for ``paths``.  Tokens: {name} {n} {date} {time} {ext} {parent}."""
    out = []
    for i, p in enumerate(paths):
        stem, ext = os.path.splitext(os.path.basename(p))
        try:
            ts = os.path.getmtime(p) if date_source == "modified" else os.path.getctime(p)
        except OSError:
            ts = datetime.now().timestamp()
        dt = datetime.fromtimestamp(ts)
        name = pattern.format(name=stem, n=str(start + i).zfill(digits), date=dt.strftime("%Y-%m-%d"),
                              time=dt.strftime("%H-%M-%S"), ext=ext.lstrip("."),
                              parent=os.path.basename(os.path.dirname(p)))
        if find:
            name = re.sub(find, replace, name) if regex else name.replace(find, replace)
        if case == "lower":
            name = name.lower()
        elif case == "upper":
            name = name.upper()
        elif case == "title":
            name = name.title()
        name = INVALID.sub("_", name).strip().rstrip(".") or stem
        out.append(name + ext.lower() if case == "lower" else name + ext)
    return out


def plan(paths: list[str], names: list[str]) -> list[tuple[str, str]]:
    """(old path, new path) pairs; raises ValueError for duplicates or clashes with other files."""
    pairs = [(p, os.path.join(os.path.dirname(p), n)) for p, n in zip(paths, names)]
    targets = [os.path.normcase(t) for _p, t in pairs]
    if len(set(targets)) != len(targets):
        raise ValueError(_t("Two files would get the same name. Use {n} in the pattern."))
    sources = {os.path.normcase(p) for p, _t2 in pairs}
    for _p, t in pairs:
        if os.path.exists(t) and os.path.normcase(t) not in sources:
            raise ValueError(_t("A file named {0} already exists.").format(os.path.basename(t)))
    return [(p, t) for p, t in pairs if p != t]


def execute(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Rename in two steps (through temporary names) so that swapping names works; returns the done pairs."""
    temp = []
    for i, (src, dst) in enumerate(pairs):
        tmp = os.path.join(os.path.dirname(src), f".~rename_{os.getpid()}_{i}")
        os.rename(src, tmp)
        temp.append((tmp, dst, src))
    done = []
    for tmp, dst, src in temp:
        os.rename(tmp, dst)
        done.append((src, dst))
    return done


class BatchRenameDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.setWindowTitle(_t("Batch Rename"))
        self.resize(820, 600)
        self.paths: list[str] = []
        self.undo_pairs: list[tuple[str, str]] = []
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        add = QPushButton(_t("Add Files\u2026"))
        add.clicked.connect(self._add)
        folder = QPushButton(_t("Add Folder\u2026"))
        folder.clicked.connect(self._add_folder)
        clear = QPushButton(_t("Clear"))
        clear.clicked.connect(lambda: (self.paths.clear(), self.refresh()))
        for b in (add, folder, clear):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        form = QFormLayout()
        self.pattern = QLineEdit("{name}")
        self.pattern.setToolTip(_t("{name} original name, {n} counter, {date} {time} file date, {parent} folder name"))
        form.addRow(_t("New name:"), self.pattern)
        nrow = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.digits = QSpinBox()
        self.digits.setRange(1, 8)
        self.digits.setValue(3)
        nrow.addWidget(QLabel(_t("Start at")))
        nrow.addWidget(self.start)
        nrow.addWidget(QLabel(_t("Digits")))
        nrow.addWidget(self.digits)
        nrow.addStretch(1)
        form.addRow(_t("Counter {n}:"), nrow)
        frow = QHBoxLayout()
        self.find = QLineEdit()
        self.replace = QLineEdit()
        self.regex = QCheckBox(_t("Regular expression"))
        frow.addWidget(self.find)
        frow.addWidget(QLabel("\u2192"))
        frow.addWidget(self.replace)
        frow.addWidget(self.regex)
        form.addRow(_t("Find / replace:"), frow)
        self.case = QComboBox()
        for key, label in (("keep", "Keep"), ("lower", "lower case"), ("upper", "UPPER CASE"), ("title", "Title Case")):
            self.case.addItem(_t(label), key)
        form.addRow(_t("Case:"), self.case)
        v.addLayout(form)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([_t("Current name"), _t("New name")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        self.status = QLabel("")
        v.addWidget(self.status)
        bb = QDialogButtonBox()
        self.go = bb.addButton(_t("Rename"), QDialogButtonBox.AcceptRole)
        self.undo_btn = bb.addButton(_t("Undo"), QDialogButtonBox.ActionRole)
        self.undo_btn.setEnabled(False)
        bb.addButton(QDialogButtonBox.Close)
        self.go.clicked.connect(self.run)
        self.undo_btn.clicked.connect(self.undo)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        for w in (self.pattern, self.find, self.replace):
            w.textChanged.connect(self.refresh)
        for w in (self.start, self.digits):
            w.valueChanged.connect(self.refresh)
        self.case.currentIndexChanged.connect(self.refresh)
        self.regex.toggled.connect(self.refresh)

    def _add(self):
        from app.shell.dialogs import get_open_files

        self.paths += [p for p in get_open_files(self, _t("Add Files"), "All files (*)") if p not in self.paths]
        self.refresh()

    def _add_folder(self):
        from app.shell.dialogs import get_directory

        d = get_directory(self, _t("Add Folder"))
        if d:
            self.paths += sorted(os.path.join(d, f) for f in os.listdir(d)
                                 if os.path.isfile(os.path.join(d, f)) and os.path.join(d, f) not in self.paths)
            self.refresh()

    def names(self) -> list[str]:
        try:
            return new_names(self.paths, self.pattern.text() or "{name}", self.start.value(), self.digits.value(),
                             self.find.text(), self.replace.text(), self.case.currentData(), self.regex.isChecked())
        except (KeyError, IndexError, ValueError, re.error) as exc:
            self.status.setText(_t("Pattern error: {0}").format(exc))
            return [os.path.basename(p) for p in self.paths]

    def refresh(self):
        names = self.names()
        self.table.setRowCount(len(self.paths))
        for i, (p, n) in enumerate(zip(self.paths, names)):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(p)))
            self.table.setItem(i, 1, QTableWidgetItem(n))
        self.status.setText(_t("{0} files").format(len(self.paths)))

    def run(self):
        from app.shell.dialogs import show_error

        try:
            pairs = plan(self.paths, self.names())
            done = execute(pairs)
        except (ValueError, OSError) as exc:
            show_error(self, _t("Batch Rename"), str(exc))
            return
        self.undo_pairs = done
        mapping = dict(done)
        self.paths = [mapping.get(p, p) for p in self.paths]
        self.undo_btn.setEnabled(bool(done))
        self.refresh()
        self.status.setText(_t("{0} files renamed.").format(len(done)))

    def undo(self):
        from app.shell.dialogs import show_error

        try:
            back = execute([(dst, src) for src, dst in self.undo_pairs])
        except OSError as exc:
            show_error(self, _t("Batch Rename"), str(exc))
            return
        mapping = dict(back)
        self.paths = [mapping.get(p, p) for p in self.paths]
        self.undo_pairs = []
        self.undo_btn.setEnabled(False)
        self.refresh()
        self.status.setText(_t("Undone."))


def open_dialog(main) -> None:
    BatchRenameDialog(main).exec()
