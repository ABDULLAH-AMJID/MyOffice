"""Dual-Tier GitHub / OTA Update Center Dialog & Automatic Startup Checker."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.services import firewall
from app.services.config import cfg
from app.services.i18n import _t
from app.services.updater import (
    UpdateError,
    check_for_update,
    download_update,
    install_update,
    rollback_updates,
    update_url,
)
from app.shell.dialogs import confirm, show_error, show_info
from app.version import BINARY_VERSION, effective_version


class UpdateDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(_t("MyOffice Auto-Update & GitHub OTA"))
        self.resize(590, 460)
        v = QVBoxLayout(self)

        cur = effective_version()
        ver_label = f"<b>{cur}</b>" if cur == BINARY_VERSION else f"<b>{cur}</b> (Base binary {BINARY_VERSION})"
        f = QFormLayout()
        f.addRow(_t("Active version:"), QLabel(ver_label))

        self.url = QLineEdit(update_url())
        self.url.setPlaceholderText("https://github.com/username/myoffice  (or owner/repo)")
        f.addRow(_t("GitHub Repo / Update URL:"), self.url)

        self.token = QLineEdit(cfg().get("update.github_token", ""))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText(_t("Optional — leave blank for Public GitHub repos"))
        f.addRow(_t("Private Repo Read Token:"), self.token)

        self.auto = QCheckBox(_t("Automatically check for updates when MyOffice starts"))
        self.auto.setChecked(bool(cfg().get("update.check_on_startup", True)))
        f.addRow(self.auto)
        v.addLayout(f)

        self.status = QLabel(_t("Enter your GitHub repository URL above and click “Check Now”."))
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        v.addWidget(self.notes, 1)

        btn_row = QHBoxLayout()
        self.check_btn = QPushButton(_t("Check Now"))
        self.check_btn.clicked.connect(self.check)
        self.install_btn = QPushButton(_t("Download & Apply Update"))
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.install)
        self.reset_btn = QPushButton(_t("Reset to Factory Version"))
        self.reset_btn.clicked.connect(self.reset_factory)
        btn_row.addWidget(self.check_btn)
        btn_row.addWidget(self.install_btn)
        btn_row.addWidget(self.reset_btn)
        v.addLayout(btn_row)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self._close)
        v.addWidget(bb)
        self.info = None

    def _save_settings(self):
        val = self.url.text().strip()
        cfg().set("update.url", val)
        cfg().set("servers.update", val)
        cfg().set("update.github_token", self.token.text().strip())
        cfg().set("update.check_on_startup", self.auto.isChecked(), save=True)

    def _close(self):
        self._save_settings()
        self.reject()

    def check(self):
        from app.shell.tasks import TaskError, run_task

        self._save_settings()
        url = self.url.text().strip()
        if url.startswith("http") and not firewall.ensure_consent(self, "update"):
            return
        try:
            info = run_task(self, _t("Checking GitHub OTA feed…"), check_for_update, url or None, True)
        except TaskError as exc:
            self.status.setText(f"Error: {exc.original}")
            return
        self.info = info
        self.status.setText(info.message().split("\n")[0])
        self.notes.setPlainText(info.notes)
        self.install_btn.setEnabled(info.available and bool(info.url))

    def install(self):
        from app.shell.tasks import TaskError, run_task

        if self.info is None:
            return
        try:
            path = run_task(self, _t("Downloading verified update…"), download_update, self.info, cancellable=True)
        except TaskError as exc:
            show_error(self, _t("Update"), str(exc.original))
            return
        prompt_msg = (
            _t("Signed delta update verified (<1s apply). Restart MyOffice now to activate {0}?").format(self.info.latest)
            if self.info.install_mode == "hotpatch"
            else _t("The installer was downloaded and verified. Close MyOffice and install {0} now?").format(self.info.latest)
        )
        if confirm(self, _t("Update Ready"), prompt_msg):
            install_update(path, info=self.info, restart=True)
            self.main.close()

    def reset_factory(self):
        if confirm(self, _t("Reset Updates"), _t("Remove downloaded OTA hot-patches and revert to the factory binary?")):
            rollback_updates()
            show_info(self, _t("Reset Complete"), _t("Reverted to factory build. Restart MyOffice to load factory modules."))


def background_check(main):
    """Startup check: enabled automatically whenever a GitHub update repo URL is configured."""
    if not update_url() or not bool(cfg().get("update.check_on_startup", True)):
        return
    from PySide6.QtCore import QThread, Signal

    class _T(QThread):
        found = Signal(object)

        def run(self):
            try:
                info = check_for_update()
                if info.available:
                    self.found.emit(info)
            except (UpdateError, Exception):
                pass

    t = _T(main)

    def notify(info):
        main.statusBar().showMessage(
            _t("Update {0} ready — Help / Tools → Update Service to apply in 1 click").format(info.latest), 20000
        )

    t.found.connect(notify)
    t.finished.connect(t.deleteLater)
    t.start()
    main._update_thread = t


def open_dialog(main):
    UpdateDialog(main).exec()
