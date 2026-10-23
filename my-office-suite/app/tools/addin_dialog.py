"""Add-in Manager dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTextBrowser, QVBoxLayout)

from app.services.addinmgr import AddinManager, user_addins_dir
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_file, handle_exception, show_info
from app.shell.icons import icon


class AddinManagerDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(_t("Add-in Manager"))
        self.resize(780, 480)
        if getattr(main, "addins", None) is None:
            main.addins = AddinManager(main)
        self.mgr: AddinManager = main.addins
        v = QVBoxLayout(self)
        split = QSplitter()
        self.list = QListWidget()
        self.list.itemChanged.connect(self._toggled)
        self.list.currentItemChanged.connect(self._show)
        split.addWidget(self.list)
        self.details = QTextBrowser()
        split.addWidget(self.details)
        split.setSizes([320, 440])
        v.addWidget(split, 1)
        row = QHBoxLayout()
        for text, fn in ((_t("Install from ZIP\u2026"), self.install), (_t("Uninstall"), self.uninstall),
                         (_t("Reload"), self.reload), (_t("Open Add-ins Folder"),
                                                        lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_addins_dir()))))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        note = QLabel(_t("Python add-ins contain a manifest.json and a plugin.py with register(api). Native Qt plugins "
                         "(.dll/.so) are loaded with QPluginLoader."))
        note.setObjectName("Muted")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.refresh()

    def refresh(self):
        self.mgr.discover()
        self.list.blockSignals(True)
        self.list.clear()
        for info in sorted(self.mgr.addins.values(), key=lambda a: a.name.lower()):
            state = _t("loaded") if info.loaded else (_t("error") if info.error else (_t("disabled") if not info.enabled else _t("not loaded")))
            it = QListWidgetItem(icon("addins"), f"{info.name}  {info.version}  \u2014  {state}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if info.enabled else Qt.Unchecked)
            it.setData(Qt.UserRole, info.id)
            if info.error:
                it.setForeground(Qt.red)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _info(self, it=None):
        it = it or self.list.currentItem()
        return self.mgr.addins.get(it.data(Qt.UserRole)) if it else None

    def _show(self, it, _prev=None):
        info = self._info(it)
        if info is None:
            self.details.clear()
            return
        rows = [(_t("Identifier"), info.id), (_t("Version"), info.version), (_t("Type"), info.kind),
                (_t("Author"), info.author), (_t("Location"), str(info.path)),
                (_t("Bundled"), _t("yes") if info.builtin else _t("no")),
                (_t("Requires"), f"MyOffice \u2265 {info.min_app_version}"),
                (_t("Status"), _t("loaded") if info.loaded else _t("not loaded"))]
        html = f"<h3>{info.name}</h3><p>{info.description}</p><table>"
        html += "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows)
        html += "</table>"
        if info.error:
            html += f"<p style='color:#c00000'><b>{_t('Error')}:</b> {info.error}</p>"
        funcs = self.mgr.functions.get(info.id, [])
        if funcs:
            html += f"<p><b>{_t('Spreadsheet functions')}:</b> {', '.join(funcs)}</p>"
        acts = [a['text'] for a in self.mgr.ribbon_actions if a['addin'] == info.id]
        if acts:
            html += f"<p><b>{_t('Ribbon buttons')}:</b> {', '.join(acts)}</p>"
        self.details.setHtml(html)

    def _toggled(self, it):
        info = self._info(it)
        if info is None:
            return
        self.mgr.set_enabled(info.id, it.checkState() == Qt.Checked)
        self.refresh()

    def install(self):
        path = get_open_file(self, _t("Install Add-in"), "Add-in packages (*.zip)")
        if not path:
            return
        try:
            info = self.mgr.install_zip(path)
            show_info(self, _t("Install Add-in"), _t("Installed {0} {1}.").format(info.name, info.version) +
                      (f"\n\n{info.error}" if info.error else ""))
        except Exception as exc:
            handle_exception(self, _t("Install Add-in"), exc)
        self.refresh()

    def uninstall(self):
        info = self._info()
        if info is None:
            return
        if confirm(self, _t("Uninstall"), _t("Uninstall {0}?").format(info.name)):
            try:
                self.mgr.uninstall(info.id)
            except Exception as exc:
                handle_exception(self, _t("Uninstall"), exc)
            self.refresh()

    def reload(self):
        info = self._info()
        if info is not None and info.loaded:
            self.mgr.unload(info)
        if info is not None and info.enabled:
            self.mgr.load(info)
        self.refresh()


def open_dialog(main):
    AddinManagerDialog(main).exec()
