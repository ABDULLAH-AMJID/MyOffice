"""Dual-Tier GitHub / OTA Update Center Dialog & Automatic Startup Checker."""
from __future__ import annotations

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import firewall
from app.services.config import cfg
from app.services.i18n import _t
from app.services.updater import (
    DEFAULT_GITHUB_REPO,
    UpdateError,
    check_for_update,
    download_update,
    install_update,
    rollback_updates,
    update_url,
)
from app.shell.dialogs import confirm, show_error, show_info
from app.version import BINARY_VERSION, effective_version


class _CheckWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, parent, url: str | None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            info = check_for_update(self._url or None, force=True)
            self.finished_ok.emit(info)
        except Exception as exc:
            self.failed.emit(str(exc))


class _DownloadWorker(QThread):
    progressed = Signal(int, str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, parent, info):
        super().__init__(parent)
        self._info = info
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            out = download_update(
                self._info,
                progress=lambda pct, detail: self.progressed.emit(int(pct), str(detail)),
                is_cancelled=lambda: self._cancelled,
            )
            self.finished_ok.emit(out)
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    def __init__(self, main, preloaded_info=None):
        super().__init__(main)
        self.main = main
        self.info = preloaded_info
        self._dl_worker: _DownloadWorker | None = None
        self._chk_worker: _CheckWorker | None = None

        self.setWindowTitle(_t("MyOffice Auto-Update & GitHub OTA"))
        self.resize(620, 490)
        v = QVBoxLayout(self)

        cur = effective_version()
        ver_label = f"<b>{cur}</b>" if cur == BINARY_VERSION else f"<b>{cur}</b> (Base binary {BINARY_VERSION})"
        f = QFormLayout()
        f.addRow(_t("Active version:"), QLabel(ver_label))

        self.url = QLineEdit(update_url() or DEFAULT_GITHUB_REPO)
        self.url.setPlaceholderText("https://github.com/ABDULLAH-AMJID/MyOffice")
        f.addRow(_t("GitHub Repo / Update URL:"), self.url)

        self.token = QLineEdit(cfg().get("update.github_token", ""))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText(_t("Optional — leave blank for Public GitHub repos"))
        f.addRow(_t("Private Repo Read Token:"), self.token)

        self.auto = QCheckBox(_t("Automatically check for updates when MyOffice starts"))
        self.auto.setChecked(bool(cfg().get("update.check_on_startup", True)))
        f.addRow(self.auto)
        v.addLayout(f)

        self.status = QLabel(_t("Checking signed GitHub feed…"))
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-weight: 600; padding: 4px 0;")
        v.addWidget(self.status)

        # Embedded real-time progress bar + speed/ETA readout + Cancel button!
        self.progress_wrap = QWidget(self)
        pl = QVBoxLayout(self.progress_wrap)
        pl.setContentsMargins(0, 4, 0, 6)
        pl.setSpacing(4)
        self.progress_bar = QProgressBar(self.progress_wrap)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_detail = QLabel("")
        self.progress_detail.setStyleSheet("font-size: 12px;")
        pl.addWidget(self.progress_bar)
        pl.addWidget(self.progress_detail)
        self.progress_wrap.setVisible(False)
        v.addWidget(self.progress_wrap)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        v.addWidget(self.notes, 1)

        btn_row = QHBoxLayout()
        self.check_btn = QPushButton(_t("Check Now"))
        self.check_btn.clicked.connect(self.check)
        self.install_btn = QPushButton(_t("Download & Apply Update"))
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.install)
        self.cancel_btn = QPushButton(_t("Cancel Download"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.reset_btn = QPushButton(_t("Reset to Factory Version"))
        self.reset_btn.clicked.connect(self.reset_factory)
        btn_row.addWidget(self.check_btn)
        btn_row.addWidget(self.install_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.reset_btn)
        v.addLayout(btn_row)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self._close)
        v.addWidget(bb)

        if self.info is not None:
            self._apply_check_result(self.info)
        else:
            QTimer.singleShot(120, self.check)

    def _save_settings(self):
        val = self.url.text().strip() or DEFAULT_GITHUB_REPO
        cfg().set("update.url", val)
        cfg().set("servers.update", val)
        cfg().set("update.github_token", self.token.text().strip())
        cfg().set("update.check_on_startup", self.auto.isChecked(), save=True)

    def _close(self):
        if self._dl_worker and self._dl_worker.isRunning():
            self._dl_worker.cancel()
        self._save_settings()
        self.reject()

    def _apply_check_result(self, info):
        self.info = info
        self.status.setText(info.message().split("\n")[0])
        self.notes.setPlainText(info.notes or "")
        self.install_btn.setEnabled(info.available and bool(info.url))
        if info.available:
            self.install_btn.setText(_t("Download & Install v{0}").format(info.latest))
        else:
            self.install_btn.setText(_t("Up to Date"))
        if hasattr(self.main, "show_update_available_indicator"):
            self.main.show_update_available_indicator(info if info.available else None)

    def check(self):
        self._save_settings()
        url = self.url.text().strip()
        if url.startswith("http") and not firewall.ensure_consent(self, "update"):
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText(_t("Checking…"))
        self.status.setText(_t("Connecting to GitHub OTA feed…"))
        w = _CheckWorker(self, url)
        self._chk_worker = w

        def on_ok(info):
            self.check_btn.setEnabled(True)
            self.check_btn.setText(_t("Check Now"))
            self._apply_check_result(info)

        def on_err(msg):
            self.check_btn.setEnabled(True)
            self.check_btn.setText(_t("Check Now"))
            self.status.setText(f"Error: {msg}")

        w.finished_ok.connect(on_ok)
        w.failed.connect(on_err)
        w.finished.connect(w.deleteLater)
        w.start()

    def cancel_download(self):
        if self._dl_worker and self._dl_worker.isRunning():
            self.cancel_btn.setEnabled(False)
            self.progress_detail.setText(_t("Cancelling download…"))
            self._dl_worker.cancel()

    def install(self):
        if self.info is None:
            return
        # Instant 0ms UI state feedback!
        self.check_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self.install_btn.setText(_t("Downloading…"))
        self.reset_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.progress_wrap.setVisible(True)
        self.progress_bar.setValue(1)
        self.progress_detail.setText(_t("Starting high-speed 6x parallel download…"))
        self.status.setText(_t("Downloading & verifying SHA-256 + ECDSA signature for v{0}…").format(self.info.latest))

        w = _DownloadWorker(self, self.info)
        self._dl_worker = w

        def on_progress(pct: int, detail: str):
            self.progress_bar.setValue(max(1, min(100, pct)))
            self.progress_detail.setText(detail)

        def on_done(path: str):
            self.cancel_btn.setVisible(False)
            self.check_btn.setEnabled(True)
            self.reset_btn.setEnabled(True)
            self.progress_bar.setValue(100)
            self.progress_detail.setText(_t("100% — Cryptographic verification passed!"))
            prompt_msg = (
                _t("Signed delta update verified (<1s apply). Restart MyOffice now to activate {0}?").format(self.info.latest)
                if self.info.install_mode == "hotpatch"
                else _t("The installer was downloaded and verified. Close MyOffice and install {0} now?").format(self.info.latest)
            )
            if confirm(self, _t("Update Ready"), prompt_msg):
                install_update(path, info=self.info, restart=True)
                self.main.close()
            else:
                self.install_btn.setEnabled(True)
                self.install_btn.setText(_t("Download & Install v{0}").format(self.info.latest))

        def on_err(err_msg: str):
            self.cancel_btn.setVisible(False)
            self.check_btn.setEnabled(True)
            self.reset_btn.setEnabled(True)
            self.install_btn.setEnabled(True)
            self.install_btn.setText(_t("Retry Download"))
            self.progress_detail.setText(f"Stopped: {err_msg}")
            if "cancelled" not in err_msg.lower():
                show_error(self, _t("Update"), err_msg)

        w.progressed.connect(on_progress)
        w.finished_ok.connect(on_done)
        w.failed.connect(on_err)
        w.finished.connect(w.deleteLater)
        w.start()

    def reset_factory(self):
        if confirm(self, _t("Reset Updates"), _t("Remove downloaded OTA hot-patches and revert to the factory binary?")):
            rollback_updates()
            show_info(self, _t("Reset Complete"), _t("Reverted to factory build. Restart MyOffice to load factory modules."))


def background_check(main):
    """Startup check: enabled automatically whenever a GitHub update repo URL is configured."""
    if not update_url() or not bool(cfg().get("update.check_on_startup", True)):
        return

    class _T(QThread):
        found = Signal(object)

        def run(self):
            try:
                info = check_for_update(force=True)
                if info.available:
                    self.found.emit(info)
            except (UpdateError, Exception):
                pass

    t = _T(main)

    def notify(info):
        if hasattr(main, "show_update_available_indicator"):
            main.show_update_available_indicator(info)
        main.statusBar().showMessage(
            _t("MyOffice v{0} update available — click 'Update v{0}' in the top bar to apply!").format(info.latest),
            30000,
        )

    t.found.connect(notify)
    t.finished.connect(t.deleteLater)
    t.start()
    main._update_thread = t


def open_dialog(main, preloaded_info=None):
    UpdateDialog(main, preloaded_info=preloaded_info).exec()
