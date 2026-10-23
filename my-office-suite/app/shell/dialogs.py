"""Common dialogs: errors, confirmations, settings, about."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from app.services.config import cfg
from app.services.i18n import LANGUAGES, _t
from app.version import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)


def restore_window(w) -> None:
    """Bring a window back from the taskbar.  Unlike showNormal(), this keeps a maximized or full-screen window
    maximized or full screen."""
    w.setWindowState((w.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
    w.show()
    w.raise_()
    w.activateWindow()


def show_error(parent, title: str, message: str, details: str | None = None) -> None:
    box = QMessageBox(QMessageBox.Critical, title, message, QMessageBox.Ok, parent)
    if details:
        box.setDetailedText(details)
    box.exec()


def show_info(parent, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def show_warning(parent, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


def confirm(parent, title: str, message: str) -> bool:
    return QMessageBox.question(parent, title, message, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


def handle_exception(parent, title: str, exc: BaseException) -> None:
    """Show a user-friendly error for an exception (TaskError aware)."""
    from app.shell.tasks import TaskError

    details = None
    if isinstance(exc, TaskError):
        details = exc.tb
        exc = exc.original
    log.error("%s: %s", title, exc, exc_info=False)
    msg = str(exc) or exc.__class__.__name__
    show_error(parent, title, msg, details)


def ask_save_changes(parent, name: str) -> str:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(_t("Unsaved changes"))
    box.setText(_t("Do you want to save the changes to \"{0}\"?").format(name))
    save = box.addButton(_t("Save"), QMessageBox.AcceptRole)
    discard = box.addButton(_t("Don't Save"), QMessageBox.DestructiveRole)
    box.addButton(_t("Cancel"), QMessageBox.RejectRole)
    box.setDefaultButton(save)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return "save"
    if clicked is discard:
        return "discard"
    return "cancel"


def _last_dir() -> str:
    d = cfg().get("ui.last_dir", "")
    return d if d and os.path.isdir(d) else str(Path.home())


def _remember_dir(path: str) -> None:
    if path:
        cfg().set("ui.last_dir", str(Path(path).parent), save=True)


def get_open_file(parent, title: str, filters: str) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, _last_dir(), filters)
    _remember_dir(path)
    return path


def get_open_files(parent, title: str, filters: str) -> list[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, title, _last_dir(), filters)
    if paths:
        _remember_dir(paths[0])
    return paths


def get_save_file(parent, title: str, filters: str, suggested: str = "", default_suffix: str = "") -> str:
    start = suggested if os.path.isabs(suggested or "") else os.path.join(_last_dir(), suggested or "")
    dlg = QFileDialog(parent, title, start, filters)
    dlg.setAcceptMode(QFileDialog.AcceptSave)
    if default_suffix:
        dlg.setDefaultSuffix(default_suffix)
    if dlg.exec() != QDialog.Accepted:
        return ""
    path = dlg.selectedFiles()[0]
    _remember_dir(path)
    return path


def get_directory(parent, title: str) -> str:
    d = QFileDialog.getExistingDirectory(parent, title, _last_dir())
    if d:
        cfg().set("ui.last_dir", d, save=True)
    return d


class SettingsDialog(QDialog):
    """Application settings (language, theme, autosave, backup, servers, OCR)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_t("Settings"))
        self.resize(560, 440)
        c = cfg()
        tabs = QTabWidget(self)

        general = QWidget()
        f = QFormLayout(general)
        self.lang = QComboBox()
        for code, info in LANGUAGES.items():
            self.lang.addItem(f"{info['native']} ({info['name']})", code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(c.get("lang"))))
        f.addRow(_t("Interface language:"), self.lang)
        self.theme = QComboBox()
        self.theme.addItem(_t("Light"), "light")
        self.theme.addItem(_t("Dark"), "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(c.get("theme"))))
        f.addRow(_t("Theme:"), self.theme)
        self.author = QLineEdit(c.get("author", ""))
        f.addRow(_t("Author name:"), self.author)
        self.autosave = QSpinBox()
        self.autosave.setRange(0, 60)
        self.autosave.setSuffix(" " + _t("min"))
        self.autosave.setValue(int(c.get("autosave_minutes", 3)))
        self.autosave.setSpecialValueText(_t("Off"))
        f.addRow(_t("Auto-recovery interval:"), self.autosave)
        self.single = QCheckBox(_t("Open files in the running window"))
        self.single.setChecked(bool(c.get("single_instance", True)))
        f.addRow("", self.single)
        tabs.addTab(general, _t("General"))

        backup = QWidget()
        fb = QFormLayout(backup)
        self.backup_on = QCheckBox(_t("Keep a backup copy every time a file is saved"))
        self.backup_on.setChecked(bool(c.get("backup.enabled", True)))
        fb.addRow(self.backup_on)
        self.backup_keep = QSpinBox()
        self.backup_keep.setRange(1, 200)
        self.backup_keep.setValue(int(c.get("backup.keep", 10)))
        fb.addRow(_t("Copies to keep per file:"), self.backup_keep)
        row = QHBoxLayout()
        self.backup_dir = QLineEdit(c.get("backup.dir", ""))
        self.backup_dir.setPlaceholderText(_t("Default location"))
        b = QPushButton(_t("Browse..."))
        b.clicked.connect(lambda: self._pick_dir(self.backup_dir))
        row.addWidget(self.backup_dir)
        row.addWidget(b)
        fb.addRow(_t("Backup folder:"), row)
        tabs.addTab(backup, _t("Backup"))

        net = QWidget()
        fn = QFormLayout(net)
        self.update_url = QLineEdit(c.get("update.url", ""))
        self.update_url.setPlaceholderText("https://example.org/myoffice/version.json")
        fn.addRow(_t("Update feed URL:"), self.update_url)
        self.update_startup = QCheckBox(_t("Check for updates on startup"))
        self.update_startup.setChecked(bool(c.get("update.check_on_startup", False)))
        fn.addRow("", self.update_startup)
        self.templates_url = QLineEdit(c.get("templates.catalog_url", ""))
        self.templates_url.setPlaceholderText("https://example.org/templates/templates.json")
        fn.addRow(_t("Online templates catalog:"), self.templates_url)
        self.collab_url = QLineEdit(c.get("collab.server_url", ""))
        fn.addRow(_t("Collaboration server:"), self.collab_url)
        tabs.addTab(net, _t("Network"))

        ocr = QWidget()
        fo = QFormLayout(ocr)
        row2 = QHBoxLayout()
        self.tess = QLineEdit(c.get("ocr.tesseract_cmd", ""))
        self.tess.setPlaceholderText(_t("Auto-detect"))
        b2 = QPushButton(_t("Browse..."))
        b2.clicked.connect(self._pick_tesseract)
        row2.addWidget(self.tess)
        row2.addWidget(b2)
        fo.addRow(_t("Tesseract executable:"), row2)
        self.ocr_langs = QLineEdit(c.get("ocr.languages", "urd+eng"))
        fo.addRow(_t("OCR languages:"), self.ocr_langs)
        tabs.addTab(ocr, _t("OCR"))

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(_t("OK"))
        bb.button(QDialogButtonBox.Cancel).setText(_t("Cancel"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.language_changed = False
        self.theme_changed = False

    def _pick_dir(self, edit: QLineEdit):
        d = get_directory(self, _t("Choose folder"))
        if d:
            edit.setText(d)

    def _pick_tesseract(self):
        p, _ = QFileDialog.getOpenFileName(self, _t("Locate tesseract"), "", "tesseract*")
        if p:
            self.tess.setText(p)

    def accept(self):
        c = cfg()
        new_lang = self.lang.currentData()
        self.language_changed = new_lang != c.get("lang")
        self.theme_changed = self.theme.currentData() != c.get("theme")
        c.set("lang", new_lang)
        c.set("theme", self.theme.currentData())
        c.set("author", self.author.text().strip() or c.get("author"))
        c.set("autosave_minutes", self.autosave.value())
        c.set("single_instance", self.single.isChecked())
        c.set("backup.enabled", self.backup_on.isChecked())
        c.set("backup.keep", self.backup_keep.value())
        c.set("backup.dir", self.backup_dir.text().strip())
        c.set("update.url", self.update_url.text().strip())
        c.set("update.check_on_startup", self.update_startup.isChecked())
        c.set("templates.catalog_url", self.templates_url.text().strip())
        c.set("collab.server_url", self.collab_url.text().strip())
        c.set("ocr.tesseract_cmd", self.tess.text().strip())
        c.set("ocr.languages", self.ocr_langs.text().strip() or "eng")
        c.save()
        super().accept()


THIRD_PARTY = [
    ("Qt 6 / PySide6", "LGPL-3.0"), ("python-docx", "MIT"), ("openpyxl", "MIT"), ("python-pptx", "MIT"),
    ("PyMuPDF / MuPDF", "AGPL-3.0 (or commercial licence)"), ("pypdf", "BSD-3-Clause"), ("reportlab", "BSD"),
    ("pandas", "BSD-3-Clause"), ("NumPy", "BSD-3-Clause"), ("matplotlib", "PSF-based"),
    ("fontTools", "MIT"), ("Pillow", "MIT-CMU"), ("pytesseract", "Apache-2.0"), ("Tesseract OCR", "Apache-2.0"),
    ("pyHanko", "MIT"), ("cryptography", "Apache-2.0 / BSD"), ("pycrdt (Yjs/Yrs)", "MIT"), ("websockets", "BSD-3-Clause"),
    ("watchdog", "Apache-2.0"), ("boto3", "Apache-2.0"), ("jsonschema", "MIT"), ("lxml", "BSD"), ("xlrd", "BSD"),
    ("Lucide icons", "ISC"), ("Noto Nastaliq Urdu / Noto Sans Devanagari", "SIL OFL 1.1"),
]


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_t("About MyOffice"))
        self.resize(520, 460)
        lay = QVBoxLayout(self)
        title = QLabel(f"<h2>{APP_NAME} {APP_VERSION}</h2>")
        lay.addWidget(title)
        lay.addWidget(QLabel(_t("A lightweight open-source office suite: Writer, Spreadsheets, Presentation and PDF tools.")))
        tb = QTextBrowser()
        rows = "".join(f"<tr><td>{n}</td><td>{l}</td></tr>" for n, l in THIRD_PARTY)
        tb.setHtml(f"<b>{_t('Third-party components')}</b><table cellpadding=3>{rows}</table>")
        lay.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
