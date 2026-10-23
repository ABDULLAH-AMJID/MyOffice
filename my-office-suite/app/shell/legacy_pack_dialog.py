"""Legacy Formats Pack: the offer shown when a file needs LibreOffice, and the download + install flow.

``ensure_libreoffice(parent, src)`` is registered with office_convert.set_missing_handler by the main
window, so opening a .doc/.ppt/.odt file (or converting one in the PDF Toolbox) asks once, downloads with a
progress bar, installs, and then simply continues with the original operation.
"""
from __future__ import annotations

import html
import logging
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.services import legacy_pack
from app.services.i18n import _t
from app.shell.icons import icon

log = logging.getLogger(__name__)
GREEN = "#188038"


def _row(text: str) -> QLabel:
    lab = QLabel(f"<table cellspacing=0 cellpadding=0><tr><td width=22 valign=top>"
                 f"<b style='color:{GREEN}'>\u2713</b></td><td>{text}</td></tr></table>")
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    return lab


class LegacyPackDialog(QDialog):
    """Explains what the pack is and asks before anything is downloaded."""

    def __init__(self, parent=None, filename: str = ""):
        super().__init__(parent)
        self.setObjectName("LegacyPackDialog")
        self.setWindowTitle(_t("Legacy Formats Pack"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icon("install", "#1a73e8").pixmap(44, 44))
        logo.setAlignment(Qt.AlignTop)
        head.addWidget(logo)
        title = QLabel(f"<span style='font-size:15pt; font-weight:600'>"
                       f"{_t('Open old .doc, .xls and .ppt files')}</span><br>"
                       f"<span style='color:#5f6368'>{_t('Legacy Formats Pack')} \u2022 LibreOffice "
                       f"{legacy_pack.LO_VERSION}</span>")
        title.setTextFormat(Qt.RichText)
        head.addWidget(title, 1)
        lay.addLayout(head)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: rgba(128,128,128,0.35)")
        lay.addWidget(line)
        if filename:
            why = _t("\u201c{0}\u201d is saved in an older or OpenDocument format. MyOffice reads these formats with "
                     "the free LibreOffice engine, which is not installed yet.").format(html.escape(filename))
        else:
            why = _t("Old Word, Excel and PowerPoint formats (.doc, .xls, .ppt) and OpenDocument files (.odt, .ods, "
                     ".odp, .rtf) are read with the free LibreOffice engine.")
        intro = QLabel(why)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        lay.addWidget(intro)
        lay.addWidget(QLabel(f"<b>{_t('MyOffice can add it for you now:')}</b>"))
        lay.addWidget(_row(_t("Download: {0} MB from libreoffice.org, checked with SHA-256 \u2014 an interrupted "
                              "download continues where it stopped").format(round(legacy_pack.LO_SIZE / 1e6))))
        lay.addWidget(_row(_t("Installs in a few minutes; Windows asks for permission once")))
        lay.addWidget(_row(_t("Free software (MPL-2.0) by The Document Foundation; MyOffice's uninstaller offers to "
                              "remove it again")))
        note = QLabel(_t("Word, Excel and PowerPoint files (.docx, .xlsx, .pptx) and PDFs never need this pack."))
        note.setWordWrap(True)
        note.setStyleSheet("color:#5f6368")
        lay.addWidget(note)
        lay.addSpacing(6)
        buttons = QHBoxLayout()
        self.local_msi = ""
        self.file_btn = QPushButton(_t("Use Installer File\u2026"))
        self.file_btn.setToolTip(_t("No internet? Choose LibreOffice_{0}_Win_x86-64.msi copied from another computer "
                                    "or a USB stick.").format(legacy_pack.LO_VERSION))
        self.file_btn.clicked.connect(self._choose_file)
        buttons.addWidget(self.file_btn)
        buttons.addStretch(1)
        self.later_btn = QPushButton(_t("Not Now"))
        self.later_btn.clicked.connect(self.reject)
        self.install_btn = QPushButton(_t("Download and Install"))
        self.install_btn.setObjectName("Primary")
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self.accept)
        buttons.addWidget(self.later_btn)
        buttons.addWidget(self.install_btn)
        lay.addLayout(buttons)

    def _choose_file(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, _t("Choose the LibreOffice installer"), "",
                                              "Windows Installer (*.msi);;All files (*)")
        if path:
            self.local_msi = path
            self.accept()


def ensure_libreoffice(parent=None, src: str = "") -> bool:
    """GUI thread.  True if LibreOffice is (now) available; offers, downloads and installs it otherwise."""
    from app.services import office_convert

    if office_convert.available():
        return True
    # opened from inside another task ("Converting document..."): keep only this flow's dialogs on screen
    from app.shell.tasks import other_tasks_paused

    with other_tasks_paused():
        return _offer_and_install(parent, src)


def _offer_and_install(parent, src: str) -> bool:
    from app.services import office_convert
    from app.shell.dialogs import show_error, show_info
    from app.shell.tasks import TaskError, run_task

    title = _t("Legacy Formats Pack")
    if sys.platform != "win32":
        show_info(parent, title, _t("This file format needs LibreOffice. Install it with your system's package "
                                    "manager (for example: sudo apt install libreoffice) and try again."))
        return False
    dlg = LegacyPackDialog(parent, os.path.basename(src) if src else "")
    if dlg.exec() != QDialog.Accepted:
        return False
    short = legacy_pack.disk_space_shortfall(0 if dlg.local_msi else legacy_pack.pending_download_bytes())
    if short:
        needed, free, drive = short
        show_error(parent, title, _t("There is not enough free disk space for LibreOffice. It needs about {0} GB on "
                                     "{1}, but only {2} GB are free. Please free up some space and try again.")
                   .format(f"{needed / 1e9:.1f}", drive, f"{free / 1e9:.1f}"))
        return False
    try:
        if dlg.local_msi:  # offline: the user's own copy of the official MSI, SHA-256 checked
            msi = run_task(parent, _t("Checking the installer file\u2026"), legacy_pack.verify_installer, dlg.local_msi)
        else:
            msi = run_task(parent, _t("Downloading the Legacy Formats Pack\u2026"), legacy_pack.download,
                           cancellable=True)
    except TaskError as exc:
        if dlg.local_msi:
            show_error(parent, title, str(exc.original), exc.tb)
        elif isinstance(exc.original, legacy_pack.DownloadCancelled):
            show_info(parent, title, _t("The download was paused. Next time it continues where it stopped."))
        else:
            show_error(parent, title, _t("The Legacy Formats Pack could not be downloaded. Please check your "
                                         "internet connection and try again.") + "\n\n" + str(exc.original),
                       exc.tb)
        return False
    hwnd = 0
    try:
        win = parent.window() if parent is not None else None
        hwnd = int(win.winId()) if win is not None else 0
    except Exception:
        pass
    try:
        restart = run_task(parent, _t("Installing LibreOffice\u2026 this takes a few minutes."), legacy_pack.install,
                           msi, hwnd)
    except TaskError as exc:
        orig = exc.original
        if isinstance(orig, legacy_pack.InstallDeclined):
            show_info(parent, title, _t("LibreOffice was not installed because Windows did not get permission. "
                                        "You can try again at any time."))
        else:
            show_error(parent, title, _t("LibreOffice could not be installed.") + "\n\n" + str(orig), exc.tb)
        return False
    if not office_convert.available():
        show_error(parent, title, _t("LibreOffice was installed, but MyOffice cannot find soffice.exe. Set its path "
                                     "in Settings \u2192 Servers."))
        return False
    legacy_pack.record_installed()
    legacy_pack.remove_download(msi)
    log.info("Legacy Formats Pack installed (restart needed: %s)", restart)
    if restart:
        show_info(parent, title, _t("LibreOffice is installed. Windows asks to be restarted to finish the "
                                    "installation, but you can keep working now."))
    return True


def missing_handler_for(parent):
    """office_convert missing-LibreOffice handler bound to ``parent`` (safe to call from worker threads)."""
    from app.services.builtin_pdf import call_in_gui_thread

    def handler(src: str) -> bool:
        return bool(call_in_gui_thread(lambda: ensure_libreoffice(parent, src)))

    return handler


__all__ = ["LegacyPackDialog", "ensure_libreoffice", "missing_handler_for"]
