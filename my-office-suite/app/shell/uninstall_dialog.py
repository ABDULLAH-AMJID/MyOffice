"""'Uninstall MyOffice' confirmation dialog (MyOffice menu -> Uninstall MyOffice...)."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.services.i18n import _t
from app.services.uninstall import UninstallPlan
from app.shell.icons import badge_pixmap
from app.version import APP_NAME, APP_VERSION

RED = "#c5221f"
GREEN = "#188038"
BLUE = "#1a73e8"


def _libreoffice_by_myoffice() -> bool:
    try:
        from app.services.legacy_pack import installed_by_myoffice

        return installed_by_myoffice()
    except Exception:
        return False


def _row(mark: str, color: str, text: str) -> QLabel:
    lab = QLabel(f"<table cellspacing=0 cellpadding=0><tr><td width=22 valign=top>"
                 f"<b style='color:{color}'>{mark}</b></td><td>{text}</td></tr></table>")
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    return lab


def removal_items(plan: UninstallPlan) -> list[str]:
    """Human-readable list of what will be deleted (HTML, translated)."""
    esc = html.escape
    items = []
    if plan.installed:
        items.append(_t("The program and all of its files, including the OCR engine and Ghostscript that came with "
                        "it") + f"<br><span style='color:#5f6368'>{esc(str(plan.program_dir))}</span>")
        items.append(_t("Settings, add-ins, macros, form designs and templates saved in MyOffice - for every "
                        "user of this computer"))
    else:
        items.append(_t("Settings, add-ins, macros, form designs and templates saved in MyOffice"))
    items.append(_t("MyOffice backups, recovery copies, screen captures, recordings and logs") + f"<br><span style='color:#5f6368'>"
                 f"{esc(str(plan.data_dir))}</span>")
    if plan.installed or plan.registered:
        items.append(_t("File associations (.docx, .xlsx, .pptx, .pdf and others) and \u201cOpen with\u201d entries"))
    if plan.installed:
        items.append(_t("Desktop, Start-menu and taskbar shortcuts"))
        items.append(_t("The firewall rule for real-time collaboration and MyOffice's temporary files"))
    else:
        if plan.shortcuts:
            items.append(_t("Shortcuts created by MyOffice ({0})").format(len(plan.shortcuts)))
        items.append(_t("MyOffice's temporary files"))
    return items


class UninstallDialog(QDialog):
    def __init__(self, plan: UninstallPlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setObjectName("UninstallDialog")
        self.setWindowTitle(_t("Uninstall MyOffice"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(badge_pixmap("app", 52, "M"))
        logo.setAlignment(Qt.AlignTop)
        head.addWidget(logo)
        title = QLabel(f"<span style='font-size:15pt; font-weight:600'>{_t('Remove MyOffice from this computer')}"
                       f"</span><br><span style='color:#5f6368'>{APP_NAME} {APP_VERSION}</span>")
        title.setTextFormat(Qt.RichText)
        head.addWidget(title, 1)
        lay.addLayout(head)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: rgba(128,128,128,0.35)")
        lay.addWidget(line)

        lay.addWidget(QLabel(f"<b>{_t('The following will be deleted:')}</b>"))
        for item in removal_items(plan):
            lay.addWidget(_row("\u2715", RED, item))
        if plan.installed and _libreoffice_by_myoffice():
            lay.addWidget(_row("?", BLUE, _t("LibreOffice (Legacy Formats Pack) was installed by MyOffice. The "
                                             "uninstaller asks whether to remove it as well.")))
        lay.addSpacing(4)
        lay.addWidget(_row("\u2713", GREEN, "<b>" + _t("Not deleted:") + "</b> " +
                           _t("your own documents (.docx, .xlsx, .pptx, .pdf ...) stay where you saved them.")))
        if not plan.installed:
            note = QLabel(_t("This copy of MyOffice was not installed with MyOffice Setup (portable or source "
                             "copy). After MyOffice closes, delete its folder yourself:") +
                          f"<br><b>{html.escape(str(plan.program_dir))}</b>")
            note.setWordWrap(True)
            note.setTextFormat(Qt.RichText)
            note.setObjectName("UninstallNote")
            note.setStyleSheet("#UninstallNote { background: rgba(251,188,4,0.16); border: 1px solid "
                               "rgba(251,188,4,0.55); border-radius: 6px; padding: 8px; }")
            lay.addWidget(note)
        lay.addSpacing(6)

        hint = QLabel(_t("Windows may ask for permission to make changes.") if plan.installed else "")
        hint.setStyleSheet("color:#5f6368")
        buttons = QHBoxLayout()
        self.open_data = QPushButton(_t("Open Data Folder"))
        self.open_data.setToolTip(_t("Copy anything you want to keep (e.g. backups, captures or recordings) before uninstalling"))
        self.open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(plan.data_dir))))
        buttons.addWidget(self.open_data)
        buttons.addStretch(1)
        self.cancel_btn = QPushButton(_t("Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        self.uninstall_btn = QPushButton(_t("Uninstall"))
        self.uninstall_btn.setObjectName("DangerButton")
        self.uninstall_btn.setStyleSheet(f"#DangerButton {{ background: {RED}; color: white; border: none; "
                                         "border-radius: 4px; padding: 6px 18px; font-weight: 600; }"
                                         "#DangerButton:hover { background: #a50e0e; }")
        self.uninstall_btn.clicked.connect(self.accept)
        self.cancel_btn.setDefault(True)  # the safe choice is the default button
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.uninstall_btn)
        lay.addWidget(hint)
        lay.addLayout(buttons)
