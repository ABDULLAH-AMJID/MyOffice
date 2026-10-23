"""Instant Command Palette (`Ctrl+Shift+P` / `Alt+Q`) — search and execute any command, tool, or recent document."""
from __future__ import annotations

import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from app.services.config import cfg
from app.shell.tools_registry import TOOLS


class CommandPaletteDialog(QDialog):
    def __init__(self, mw, initial_query: str = ""):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("Command Palette — Quick Action & Tool Launcher")
        self.resize(640, 460)

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        self.search = QLineEdit(initial_query)
        self.search.setPlaceholderText("Type any action, tool, or recent file name… (Enter to run, Esc to close)")
        self.search.setStyleSheet("padding: 8px 12px; font-size: 14px;")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._run_selected)
        v.addWidget(self.search)

        self.list = QListWidget()
        self.list.setStyleSheet("font-size: 13px;")
        self.list.itemActivated.connect(lambda _it: self._run_selected())
        v.addWidget(self.list, 1)

        foot = QHBoxLayout()
        hint = QLabel("Tip: Press <b>Ctrl+K</b> anytime to customize any keyboard shortcut.")
        foot.addWidget(hint, 1)
        b_cust = QPushButton("Customize Shortcuts\u2026")
        b_cust.clicked.connect(lambda: (self.accept(), self.mw.open_shortcuts_dialog()))
        b_run = QPushButton("Run Selected")
        b_run.setProperty("accent", True)
        b_run.clicked.connect(self._run_selected)
        foot.addWidget(b_cust)
        foot.addWidget(b_run)
        v.addLayout(foot)

        self._all_items = self._collect_all()
        self._filter()
        self.search.setFocus()

    def _collect_all(self) -> list[tuple[str, str, callable]]:
        items: list[tuple[str, str, callable]] = []
        km = self.mw.keymap
        for ent in km.entries():
            seq = km.sequence_for(ent.cmd_id)
            right = f"   [{seq}]" if seq else ""
            title = f"{ent.label}{right}  —  {ent.category}"
            items.append((title, f"{ent.label} {ent.category} {seq} {ent.description}".lower(),
                          lambda cid=ent.cmd_id: km.trigger(cid)))

        for t in TOOLS:
            title = f"Tool: {t.name}  —  {t.description}"
            items.append((title, f"{t.name} {t.category} {t.description}".lower(),
                          lambda tid=t.id: self.mw.open_tool(tid)))

        for p in cfg().recent_files()[:15]:
            if p and os.path.exists(p):
                nm = os.path.basename(p)
                items.append((f"Recent File: {nm}  ({p})", f"recent {nm} {p}".lower(),
                              lambda fp=p: self.mw.open_path(fp)))
        return items

    def _filter(self):
        tokens = [tok for tok in self.search.text().strip().lower().split() if tok]
        self.list.clear()
        for label, hay, fn in self._all_items:
            if all(tok in hay for tok in tokens):
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, fn)
                self.list.addItem(it)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _run_selected(self):
        it = self.list.currentItem()
        if not it:
            return
        fn = it.data(Qt.UserRole)
        self.accept()
        if callable(fn):
            fn()
