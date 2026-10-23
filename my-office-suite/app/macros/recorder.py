"""Macro recorder: turns spreadsheet actions into Python macro code."""
from __future__ import annotations

from datetime import datetime

from app.services.i18n import _t


class MacroRecorder:
    def __init__(self):
        self.lines: list[str] = []
        self._sheet: str | None = None

    def _use_sheet(self, name: str):
        if name != self._sheet:
            self._sheet = name
            self.lines.append(f"s = app.sheet_named({name!r})")

    def record_input(self, sheet_name: str, ref: str, text: str):
        self._use_sheet(sheet_name)
        self.lines.append(f"s[{ref!r}] = {text!r}")

    def record(self, line: str):
        if line.startswith("sheet."):
            line = "s." + line[len("sheet."):]
            if self._sheet is None:
                self.lines.append("s = app.sheet")
                self._sheet = ""
        self.lines.append(line)

    def code(self, name: str = "Recorded macro") -> str:
        head = [f"# {name}", f"# Recorded with MyOffice on {datetime.now():%Y-%m-%d %H:%M}", ""]
        body = self.lines if self.lines and self.lines[0].startswith("s =") else ["s = app.sheet"] + self.lines
        return "\n".join(head + body) + "\n"

    def save_dialog(self, parent):
        from PySide6.QtWidgets import QInputDialog

        from app.macros.manager import save_macro

        name, ok = QInputDialog.getText(parent, _t("Save Macro"), _t("Macro name:"),
                                        text=f"Macro {datetime.now():%Y%m%d-%H%M}")
        if ok and name.strip():
            path = save_macro(name.strip(), self.code(name.strip()))
            parent.statusChanged.emit(_t("Macro saved: {0}").format(path))
