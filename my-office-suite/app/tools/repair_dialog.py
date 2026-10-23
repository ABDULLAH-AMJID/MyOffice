"""File Repair dialog."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from app.services.i18n import _t
from app.services.repair import repair_file
from app.shell.dialogs import get_open_file, get_save_file, show_error


class RepairDialog(QDialog):
    def __init__(self, main, path: str = ""):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(_t("File Repair"))
        self.resize(620, 420)
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.src = QLineEdit(path)
        b = QPushButton(_t("Browse\u2026"))
        b.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.src, 1)
        row.addWidget(b)
        f.addRow(_t("Damaged file:"), row)
        self.out = QLineEdit()
        self.out.setPlaceholderText(_t("Default: \u201cname (repaired).ext\u201d next to the original"))
        ob = QPushButton(_t("Browse\u2026"))
        ob.clicked.connect(lambda: self.out.setText(get_save_file(self, _t("Save repaired file as"), "*.*",
                                                                   os.path.basename(self.src.text())) or self.out.text()))
        row2 = QHBoxLayout()
        row2.addWidget(self.out, 1)
        row2.addWidget(ob)
        f.addRow(_t("Save repaired copy as:"), row2)
        v.addLayout(f)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        v.addWidget(self.report, 1)
        btns = QHBoxLayout()
        self.run_btn = QPushButton(_t("Repair"))
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self.run)
        self.open_btn = QPushButton(_t("Open Repaired File"))
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open)
        btns.addStretch(1)
        btns.addWidget(self.run_btn)
        btns.addWidget(self.open_btn)
        v.addLayout(btns)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.result = None

    def _browse(self):
        p = get_open_file(self, _t("Choose damaged file"),
                          "Documents (*.docx *.xlsx *.pptx *.pdf *.csv *.txt *.wpt *.ett *.dpt *.docm *.xlsm);;All files (*)")
        if p:
            self.src.setText(p)

    def run(self):
        from app.shell.tasks import TaskError, run_task

        src = self.src.text().strip()
        if not src or not os.path.exists(src):
            show_error(self, _t("File Repair"), _t("Choose an existing file."))
            return
        try:
            rep = run_task(self, _t("Repairing\u2026"), repair_file, src, self.out.text().strip() or None)
        except TaskError as exc:
            self.report.setPlainText(str(exc.original))
            return
        self.result = rep
        self.report.setPlainText(rep.summary())
        self.open_btn.setEnabled(rep.ok)

    def _open(self):
        if self.result and self.result.ok:
            self.main.open_path(self.result.output)
            self.accept()


def open_dialog(main):
    RepairDialog(main).exec()
