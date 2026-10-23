"""Interactive Keyboard Shortcuts Reference, Rebinding Studio & Custom Snippet/Action Builder."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from app.services.i18n import tr
from app.services.keymap import KeymapManager, ShortcutEntry
from app.shell.tools_registry import TOOLS


class NewCustomShortcutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Create Custom Keyboard Shortcut")
        self.resize(500, 360)
        v = QVBoxLayout(self)
        f = QFormLayout()

        self.label_in = QLineEdit()
        self.label_in.setPlaceholderText("e.g. Insert Company Signature  or  Open Monthly Budget")
        self.seq_edit = QKeySequenceEdit()
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Insert Text Snippet / Signature into Editor", "snippet")
        self.kind_combo.addItem("Open Specific File, Folder, or Website URL", "open_target")
        self.kind_combo.addItem("Launch Any MyOffice Tool", "tool")

        self.snippet_box = QPlainTextEdit()
        self.snippet_box.setPlaceholderText("Type the text, email signature, address, or code block to paste...")
        self.path_row_host = QHBoxLayout()
        self.target_path = QLineEdit()
        self.target_path.setPlaceholderText(r"C:\Documents\Budget.xlsx  or  https://...")
        b_pick = QPushButton("Browse File\u2026")
        b_pick.clicked.connect(self._browse)
        self.path_row_host.addWidget(self.target_path, 1)
        self.path_row_host.addWidget(b_pick)

        self.tool_combo = QComboBox()
        for t in TOOLS:
            self.tool_combo.addItem(f"[{t.category.upper()}] {t.name}", t.id)

        f.addRow("Action Name", self.label_in)
        f.addRow("Press Shortcut Keys", self.seq_edit)
        f.addRow("Action Type", self.kind_combo)
        v.addLayout(f)

        self.lbl_payload = QLabel("Text to Insert:")
        v.addWidget(self.lbl_payload)
        v.addWidget(self.snippet_box, 1)
        v.addLayout(self.path_row_host)
        v.addWidget(self.tool_combo)

        self.kind_combo.currentIndexChanged.connect(self._sync_ui)
        self._sync_ui()

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose file to open on shortcut", "")
        if p:
            self.target_path.setText(p)

    def _sync_ui(self):
        k = self.kind_combo.currentData()
        self.snippet_box.setVisible(k == "snippet")
        self.target_path.setVisible(k == "open_target")
        self.tool_combo.setVisible(k == "tool")
        self.lbl_payload.setText({
            "snippet": "Text / Signature to Insert:",
            "open_target": "File, Folder, or URL to Open:",
            "tool": "MyOffice Tool to Launch:",
        }[k])

    def values(self) -> tuple[str, str, str, str]:
        k = self.kind_combo.currentData()
        target = (
            self.snippet_box.toPlainText() if k == "snippet"
            else (self.target_path.text().strip() if k == "open_target" else self.tool_combo.currentData())
        )
        return (
            self.label_in.text().strip() or "Custom Shortcut",
            self.seq_edit.keySequence().toString(QKeySequence.PortableText),
            k,
            target,
        )


class ShortcutsDialog(QDialog):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.km: KeymapManager = mw.keymap
        self.setWindowTitle(tr("Keyboard Shortcuts & Custom Keybindings"))
        self.resize(840, 580)

        v = QVBoxLayout(self)

        # Top search + category filter + Custom Action button
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search command name, tool, or shortcut keys (e.g. 'Save', 'Vault', 'Ctrl+Shift')\u2026")
        self.search.textChanged.connect(self._populate)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("All Categories", "")
        cats = []
        for e in self.km.entries():
            if e.category not in cats:
                cats.append(e.category)
                self.cat_combo.addItem(e.category, e.category)
        self.cat_combo.currentIndexChanged.connect(self._populate)

        b_new_custom = QPushButton("+ Add Custom Shortcut\u2026")
        b_new_custom.setProperty("accent", True)
        b_new_custom.clicked.connect(self._add_custom)

        top.addWidget(self.search, 2)
        top.addWidget(self.cat_combo, 1)
        top.addWidget(b_new_custom)
        v.addLayout(top)

        # Shortcuts Table
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Category", "Action / Command", "Current Shortcut", "Default", "Description"])
        self.tree.setColumnWidth(0, 145)
        self.tree.setColumnWidth(1, 235)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 110)
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(self._on_select)
        v.addWidget(self.tree, 1)

        # Interactive Key Sequence Editor Bar
        edit_box = QGroupBox("Customize Selected Shortcut")
        eb = QHBoxLayout(edit_box)
        self.selected_lbl = QLabel("Select any row above, click the box on the right, and press your preferred keys:")
        self.key_edit = QKeySequenceEdit()
        self.key_edit.setMinimumWidth(190)

        self.b_assign = QPushButton("Assign Keys")
        self.b_assign.setProperty("accent", True)
        self.b_assign.clicked.connect(self._assign_selected)

        self.b_clear = QPushButton("Unbind")
        self.b_clear.clicked.connect(self._unbind_selected)

        self.b_reset_one = QPushButton("Reset Default")
        self.b_reset_one.clicked.connect(self._reset_selected)

        self.b_del_custom = QPushButton("Delete Custom")
        self.b_del_custom.setVisible(False)
        self.b_del_custom.clicked.connect(self._delete_custom)

        eb.addWidget(self.selected_lbl, 1)
        eb.addWidget(self.key_edit)
        eb.addWidget(self.b_assign)
        eb.addWidget(self.b_clear)
        eb.addWidget(self.b_reset_one)
        eb.addWidget(self.b_del_custom)
        v.addWidget(edit_box)

        # Bottom Profile Import / Export / Reset All
        foot = QHBoxLayout()
        b_imp = QPushButton("Import Profile (.json)\u2026")
        b_imp.clicked.connect(self._import_json)
        b_exp = QPushButton("Export Profile (.json)\u2026")
        b_exp.clicked.connect(self._export_json)
        b_reset_all = QPushButton("Reset All Shortcuts to Defaults")
        b_reset_all.clicked.connect(self._reset_all)
        b_close = QPushButton("Done")
        b_close.clicked.connect(self.accept)

        foot.addWidget(b_imp)
        foot.addWidget(b_exp)
        foot.addWidget(b_reset_all)
        foot.addStretch(1)
        foot.addWidget(b_close)
        v.addLayout(foot)

        self._populate()

    def _populate(self):
        prev_id = self.tree.currentItem().data(0, Qt.UserRole).cmd_id if self.tree.currentItem() else ""
        q = self.search.text().strip().lower()
        cat_filter = self.cat_combo.currentData() or ""
        self.tree.clear()
        bold_font = QFont()
        bold_font.setBold(True)

        restore_item = None
        for ent in self.km.entries():
            if cat_filter and ent.category != cat_filter:
                continue
            cur_seq = self.km.sequence_for(ent.cmd_id)
            haystack = f"{ent.category} {ent.label} {cur_seq} {ent.default_seq} {ent.description}".lower()
            if q and q not in haystack:
                continue
            row = QTreeWidgetItem([
                ent.category,
                ent.label,
                cur_seq or "(Unbound)",
                ent.default_seq or "—",
                ent.description,
            ])
            row.setData(0, Qt.UserRole, ent)
            if cur_seq != ent.default_seq or ent.is_custom:
                row.setFont(2, bold_font)
            self.tree.addTopLevelItem(row)
            if ent.cmd_id == prev_id:
                restore_item = row

        if restore_item:
            self.tree.setCurrentItem(restore_item)
        elif self.tree.topLevelItemCount() > 0:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _current_entry(self) -> ShortcutEntry | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _on_select(self, cur: QTreeWidgetItem | None, _prev=None):
        if not cur:
            return
        ent: ShortcutEntry = cur.data(0, Qt.UserRole)
        seq = self.km.sequence_for(ent.cmd_id)
        self.selected_lbl.setText(f"<b>{ent.label}</b>:")
        self.key_edit.setKeySequence(QKeySequence(seq))
        self.b_del_custom.setVisible(ent.is_custom)

    def _assign_selected(self):
        ent = self._current_entry()
        if not ent:
            return
        seq = self.key_edit.keySequence().toString(QKeySequence.PortableText)
        if not seq:
            QMessageBox.information(self, "Press Keys First", "Click inside the key box and press your key combination.")
            return
        clash = self.km.find_conflict(seq, exclude_id=ent.cmd_id)
        if clash is not None:
            ans = QMessageBox.question(
                self, "Shortcut Already In Use",
                f"\"{seq}\" is currently assigned to:\n\n  \u2022 {clash.label} ({clash.category})\n\n"
                f"Reassign \"{seq}\" to \"{ent.label}\" and unbind the previous action?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        self.km.set_sequence(ent.cmd_id, seq, clear_conflicts=True)
        self._populate()

    def _unbind_selected(self):
        ent = self._current_entry()
        if ent:
            self.km.set_sequence(ent.cmd_id, "", clear_conflicts=False)
            self._populate()

    def _reset_selected(self):
        ent = self._current_entry()
        if ent:
            self.km.reset_one(ent.cmd_id)
            self._populate()

    def _delete_custom(self):
        ent = self._current_entry()
        if ent and ent.is_custom:
            self.km.remove_custom_shortcut(ent.cmd_id)
            self._populate()

    def _add_custom(self):
        dlg = NewCustomShortcutDialog(self)
        if dlg.exec() == QDialog.Accepted:
            label, seq, kind, target = dlg.values()
            if not target:
                QMessageBox.warning(self, "Missing Target", "Enter the snippet text, file/URL path, or tool first.")
                return
            self.km.add_custom_shortcut(label, seq, kind, target)
            # Ensure 'Custom Actions' appears in category filter if newly added
            if self.cat_combo.findData("Custom Actions") < 0:
                self.cat_combo.addItem("Custom Actions", "Custom Actions")
            self._populate()

    def _reset_all(self):
        if QMessageBox.question(
            self, "Reset All Shortcuts",
            "Restore all built-in shortcuts to factory defaults? (Your custom snippets remain intact.)",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.km.reset_all()
            self._populate()

    def _export_json(self):
        dest, _ = QFileDialog.getSaveFileName(self, "Export Shortcuts Profile", "myoffice_shortcuts.json", "JSON (*.json)")
        if dest:
            self.km.export_profile(dest)
            QMessageBox.information(self, "Exported", f"Saved shortcuts profile to:\n{dest}")

    def _import_json(self):
        src, _ = QFileDialog.getOpenFileName(self, "Import Shortcuts Profile", "", "JSON (*.json)")
        if src:
            n = self.km.import_profile(src)
            self._populate()
            QMessageBox.information(self, "Imported", f"Applied {n} customized/custom shortcut entries.")
