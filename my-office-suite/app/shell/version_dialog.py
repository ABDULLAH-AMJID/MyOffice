"""Version History: every save of a document is kept (newest first); open an old version as a copy, compare it
with the current document (Writer), give it a name, or restore it."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from app.services import versions
from app.services.i18n import _t


class VersionHistoryDialog(QDialog):
    def __init__(self, main, doc):
        super().__init__(main)
        self.main, self.doc = main, doc
        self.setWindowTitle(_t("Version History") + f" - {doc.display_name()}")
        self.resize(560, 460)
        v = QVBoxLayout(self)
        info = QLabel(_t("A copy is kept every time you save (the last {0} versions). Restoring first keeps the "
                         "current file as a version, so it can be undone.").format(30))
        info.setWordWrap(True)
        info.setStyleSheet("color:#5f6368")
        v.addWidget(info)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.open_copy())
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.b_open = QPushButton(_t("Open as Copy"))
        self.b_open.clicked.connect(self.open_copy)
        self.b_compare = QPushButton(_t("Compare with Current"))
        self.b_compare.clicked.connect(self.compare)
        self.b_compare.setVisible(doc.kind == "writer")
        self.b_label = QPushButton(_t("Name This Version\u2026"))
        self.b_label.clicked.connect(self.label)
        self.b_restore = QPushButton(_t("Restore"))
        self.b_restore.setObjectName("Primary")
        self.b_restore.clicked.connect(self.restore)
        for b in (self.b_open, self.b_compare, self.b_label):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.b_restore)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.refresh()

    def refresh(self):
        self.list.clear()
        self.items = versions.list_versions(self.doc.path)
        for i, ver in enumerate(self.items):
            text = ver.timestamp.strftime("%d %b %Y  %H:%M:%S") + f"   \u2022   {ver.size / 1024:,.1f} KB"
            if ver.label:
                text += f"   \u2022   {ver.label}"
            if i == 0:
                text += "   " + _t("(latest save)")
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, i)
            self.list.addItem(it)
        if self.items:
            self.list.setCurrentRow(0)
        for b in (self.b_open, self.b_compare, self.b_label, self.b_restore):
            b.setEnabled(bool(self.items))

    def current(self):
        it = self.list.currentItem()
        return self.items[it.data(Qt.UserRole)] if it else None

    def open_copy(self):
        ver = self.current()
        if ver is not None:
            self.main.open_path(str(ver.path), as_copy=True,
                                display=f"{self.doc.display_name()} ({ver.timestamp:%d %b %H:%M})")

    def compare(self):
        """Redline: what changed between the selected version and the document as it is now."""
        ver = self.current()
        if ver is None or self.doc.kind != "writer":
            return
        from app.shell.dialogs import handle_exception, show_info
        from app.writer.compare import compare_into, document_paragraphs, file_paragraphs
        from app.writer.widget import WriterDocument

        try:
            old = file_paragraphs(str(ver.path))
        except Exception as exc:
            handle_exception(self, _t("Compare"), exc)
            return
        result = WriterDocument()
        stats = compare_into(old, document_paragraphs(self.doc.editor.document()), result.editor.document(),
                             _t("Changes since {0}").format(ver.timestamp.strftime("%d %b %H:%M")))
        result.editor.document().clearUndoRedoStacks()
        result.title_text = _t("Comparison")
        result.set_modified(True)
        self.main.add_document(result)
        show_info(self, _t("Compare"), stats.summary())

    def label(self):
        ver = self.current()
        if ver is None:
            return
        text, ok = QInputDialog.getText(self, _t("Name This Version"), _t("Name:"), text=ver.label)
        if ok:
            versions.set_label(ver, text.strip())
            self.refresh()

    def restore(self):
        from app.shell.dialogs import confirm

        ver = self.current()
        if ver is None:
            return
        if not confirm(self, _t("Restore"), _t("Replace the current file with the version from {0}?").format(
                ver.timestamp.strftime("%d %b %Y %H:%M"))):
            return
        versions.restore(ver, self.doc.path)
        path = str(self.doc.path)
        password = getattr(self.doc, "password", None)
        self.doc.set_modified(False)
        idx = self.main.tabs.indexOf(self.doc)
        if idx >= 0:
            self.main.close_tab(idx)
        new = self.main.open_path(path)
        if new is not None and password and not new.password:
            new.password = password
        self.accept()
