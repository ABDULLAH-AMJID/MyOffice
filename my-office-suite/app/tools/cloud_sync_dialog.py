"""Cloud Sync configuration & two-way runner dialog (Gmail, Google Drive, Router USB / FTP, Folder, S3, Supabase)."""
from __future__ import annotations

import webbrowser
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from app.services import vault
from app.services.cloud_sync import SyncEngine, build_backend
from app.services.config import cfg
from app.services.i18n import tr
from app.services.net_storage import discover_storage_targets
from app.services.worker import run_in_thread

GMAIL_APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"


class CloudSyncDialog(QDialog):
    BACKENDS = [
        ("gmail", "Gmail Storage (Email + App Password — Recommended)"),
        ("gdrive", "Google Drive (OAuth 2.0 Sign-In)"),
        ("folder", "Local / USB / Wi-Fi Router SMB Folder"),
        ("ftp", "Wi-Fi Router USB / NAS via FTP / FTPS"),
        ("s3", "S3-compatible (AWS / Cloudflare R2 / MinIO)"),
        ("supabase", "Supabase Storage"),
    ]

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle(tr("Cloud Sync"))
        self.resize(650, 620)
        saved = dict(cfg.get("sync") or {})

        v = QVBoxLayout(self)

        # Header intro banner
        intro = QLabel(
            "<b>Two-Way Cloud &amp; Network Sync</b> — keep a local folder synchronized across Gmail, Google Drive, "
            "a USB drive plugged into your Wi-Fi router, or cloud object storage."
        )
        intro.setWordWrap(True)
        v.addWidget(intro)

        # Local folder + backend selector
        top = QFormLayout()
        self.local = QLineEdit(saved.get("local", ""))
        self.local.setPlaceholderText("Choose the local folder whose documents should stay synchronized...")
        b_loc = QPushButton("Browse\u2026")
        b_loc.clicked.connect(lambda: self._pick(self.local))
        hl = QHBoxLayout()
        hl.addWidget(self.local, 1)
        hl.addWidget(b_loc)
        top.addRow("Local folder", hl)

        self.kind = QComboBox()
        for code, label in self.BACKENDS:
            self.kind.addItem(label, code)
        v.addLayout(top)
        top.addRow("Storage provider", self.kind)

        # Stacked provider configuration pages
        self.stack = QStackedWidget()

        # 0: Gmail (Email + App Password)
        p_gmail = QGroupBox("Gmail Cloud Storage Settings")
        fgm = QFormLayout(p_gmail)
        gm_help = QLabel(
            "Uses your own Gmail storage via encrypted TLS (<code>imap.gmail.com:993</code>) inside a private "
            "<b>MyOffice/Sync</b> label — <b>never</b> sends emails or clutters your Inbox.<br>"
            "<i>Setup in 60s:</i> Turn on Google 2-Step Verification &rarr; click <b>Get Gmail App Password</b> &rarr; "
            "paste the 16-letter code below."
        )
        gm_help.setWordWrap(True)
        fgm.addRow(gm_help)
        self.gm_email = QLineEdit(saved.get("gmail_email", ""))
        self.gm_email.setPlaceholderText("yourname@gmail.com")
        self.gm_pass = QLineEdit(vault.unseal_secret(saved.get("gmail_app_password", "")))
        self.gm_pass.setEchoMode(QLineEdit.Password)
        self.gm_pass.setPlaceholderText("16-character Google App Password (spaces OK)")
        btn_app_pw = QPushButton("Get Gmail App Password\u2026")
        btn_app_pw.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GMAIL_APP_PASSWORD_URL)))
        hpw = QHBoxLayout()
        hpw.addWidget(self.gm_pass, 1)
        hpw.addWidget(btn_app_pw)
        self.gm_label = QLineEdit(saved.get("gmail_label", "MyOffice/Sync"))
        fgm.addRow("Gmail address", self.gm_email)
        fgm.addRow("App Password", hpw)
        fgm.addRow("Gmail label", self.gm_label)
        self.stack.addWidget(p_gmail)

        # 1: Google Drive OAuth 2.0
        p_gd = QGroupBox("Google Drive (OAuth 2.0 PKCE)")
        fgd = QFormLayout(p_gd)
        gd_note = QLabel(
            "Sign in with a Google Cloud Desktop OAuth Client ID, or switch above to "
            "<b>Gmail Storage (Email + App Password)</b> if you want instant zero-console setup."
        )
        gd_note.setWordWrap(True)
        fgd.addRow(gd_note)
        self.gd_cid = QLineEdit(saved.get("gdrive_client_id", ""))
        self.gd_cid.setPlaceholderText("xxxx.apps.googleusercontent.com")
        self.gd_csec = QLineEdit(vault.unseal_secret(saved.get("gdrive_client_secret", "")))
        self.gd_csec.setEchoMode(QLineEdit.Password)
        self.gd_csec.setPlaceholderText("(Optional for PKCE desktop clients)")
        self.gd_folder = QLineEdit(saved.get("gdrive_folder", "MyOffice Sync"))
        self.gd_refresh = vault.unseal_secret(saved.get("gdrive_refresh_token", ""))
        self.gd_access = vault.unseal_secret(saved.get("gdrive_access_token", ""))
        self.gd_status = QLabel("Authorized" if self.gd_refresh else "Not signed in yet")
        btn_gd_auth = QPushButton("Sign in with Google\u2026")
        btn_gd_auth.clicked.connect(self._google_oauth)
        hgd = QHBoxLayout()
        hgd.addWidget(btn_gd_auth)
        hgd.addWidget(self.gd_status, 1)
        fgd.addRow("OAuth Client ID", self.gd_cid)
        fgd.addRow("Client Secret", self.gd_csec)
        fgd.addRow("Drive Folder", self.gd_folder)
        fgd.addRow("Account Status", hgd)
        self.stack.addWidget(p_gd)

        # 2: Shared / USB / Router SMB Folder
        p_fold = QGroupBox("Local / USB / Wi-Fi Router SMB Share")
        ff = QFormLayout(p_fold)
        self.target = QLineEdit(saved.get("target", ""))
        self.target.setPlaceholderText(r"e.g. E:\MyOfficeSync  or  \\192.168.1.1\usb1_1\MyOfficeSync")
        b_tar = QPushButton("Browse\u2026")
        b_tar.clicked.connect(lambda: self._pick(self.target))
        b_scan_smb = QPushButton("Scan Router & USB")
        b_scan_smb.clicked.connect(self._scan_targets)
        ht = QHBoxLayout()
        ht.addWidget(self.target, 1)
        ht.addWidget(b_tar)
        ht.addWidget(b_scan_smb)
        self.smb_user = QLineEdit(saved.get("smb_user", ""))
        self.smb_user.setPlaceholderText("Optional router share username")
        self.smb_pass = QLineEdit(vault.unseal_secret(saved.get("smb_password", "")))
        self.smb_pass.setEchoMode(QLineEdit.Password)
        ff.addRow("Target path", ht)
        ff.addRow("Router username", self.smb_user)
        ff.addRow("Router password", self.smb_pass)
        self.stack.addWidget(p_fold)

        # 3: Router FTP / FTPS
        p_ftp = QGroupBox("Wi-Fi Router USB / NAS (FTP or FTPS)")
        fftp = QFormLayout(p_ftp)
        self.ftp_url = QLineEdit(saved.get("ftp_url", "ftp://192.168.1.1:21/MyOfficeSync"))
        b_scan_ftp = QPushButton("Scan Router FTP")
        b_scan_ftp.clicked.connect(self._scan_targets)
        hftp = QHBoxLayout()
        hftp.addWidget(self.ftp_url, 1)
        hftp.addWidget(b_scan_ftp)
        self.ftp_user = QLineEdit(saved.get("ftp_user", "anonymous"))
        self.ftp_pass = QLineEdit(vault.unseal_secret(saved.get("ftp_password", "")))
        self.ftp_pass.setEchoMode(QLineEdit.Password)
        fftp.addRow("Router FTP URL", hftp)
        fftp.addRow("Username", self.ftp_user)
        fftp.addRow("Password", self.ftp_pass)
        self.stack.addWidget(p_ftp)

        # 4: S3
        p_s3 = QGroupBox("S3-Compatible Object Storage")
        fs = QFormLayout(p_s3)
        self.s3_ep = QLineEdit(saved.get("endpoint", ""))
        self.s3_ep.setPlaceholderText("https://<account>.r2.cloudflarestorage.com  or  https://s3.amazonaws.com")
        self.s3_bucket = QLineEdit(saved.get("bucket", ""))
        self.s3_ak = QLineEdit(saved.get("access_key", ""))
        self.s3_sk = QLineEdit(vault.unseal_secret(saved.get("secret_key", "")))
        self.s3_sk.setEchoMode(QLineEdit.Password)
        self.s3_region = QLineEdit(saved.get("region", "auto"))
        self.s3_prefix = QLineEdit(saved.get("prefix", ""))
        fs.addRow("Endpoint URL", self.s3_ep)
        fs.addRow("Bucket", self.s3_bucket)
        fs.addRow("Access key", self.s3_ak)
        fs.addRow("Secret key", self.s3_sk)
        fs.addRow("Region", self.s3_region)
        fs.addRow("Key prefix (optional)", self.s3_prefix)
        self.stack.addWidget(p_s3)

        # 5: Supabase
        p_sb = QGroupBox("Supabase Storage")
        fp = QFormLayout(p_sb)
        self.sb_url = QLineEdit(saved.get("url", ""))
        self.sb_url.setPlaceholderText("https://<project>.supabase.co")
        self.sb_key = QLineEdit(vault.unseal_secret(saved.get("api_key", "")))
        self.sb_key.setEchoMode(QLineEdit.Password)
        self.sb_bucket = QLineEdit(saved.get("bucket", ""))
        self.sb_prefix = QLineEdit(saved.get("prefix", ""))
        fp.addRow("Project URL", self.sb_url)
        fp.addRow("API key", self.sb_key)
        fp.addRow("Bucket", self.sb_bucket)
        fp.addRow("Key prefix (optional)", self.sb_prefix)
        self.stack.addWidget(p_sb)

        v.addWidget(self.stack)
        self.kind.currentIndexChanged.connect(self.stack.setCurrentIndex)
        saved_kind = saved.get("backend", "gmail")
        idx = next((i for i, (code, _) in enumerate(self.BACKENDS) if code == saved_kind), 0)
        self.kind.setCurrentIndex(idx)

        # Hardware-bound Zero-Knowledge Encryption box
        enc_box = QGroupBox("End-to-End Hardware Security (Optional)")
        fe = QFormLayout(enc_box)
        ks = vault.keystore()
        self.chk_encrypt = QCheckBox(
            f"Encrypt all synced filenames & files with AES-256-GCM locked to this PC ({ks.device_label} \u2022 ID {ks.key_id[:8]}\u2026)"
        )
        self.chk_encrypt.setChecked(bool(saved.get("encrypt", False)))
        self.enc_pass = QLineEdit(vault.unseal_secret(saved.get("vault_passphrase", "")))
        self.enc_pass.setEchoMode(QLineEdit.Password)
        self.enc_pass.setPlaceholderText("Optional shared passphrase if syncing between TWO different laptops")
        fe.addRow(self.chk_encrypt)
        fe.addRow("Cross-PC passphrase", self.enc_pass)
        v.addWidget(enc_box)

        # Action buttons
        hb = QHBoxLayout()
        self.btn_test = QPushButton("Test Connection")
        self.btn_test.clicked.connect(self._test)
        self.btn_sync = QPushButton("Sync Now")
        self.btn_sync.setProperty("accent", True)
        self.btn_sync.clicked.connect(self._sync)
        b_save = QPushButton("Save Settings")
        b_save.clicked.connect(self._save_settings)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.accept)
        hb.addWidget(self.btn_test)
        hb.addWidget(self.btn_sync)
        hb.addWidget(b_save)
        hb.addStretch(1)
        hb.addWidget(b_close)
        v.addLayout(hb)

        self.prog = QProgressBar()
        self.prog.setVisible(False)
        v.addWidget(self.prog)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        v.addWidget(self.log, 1)

    def _pick(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if d:
            edit.setText(d)

    def _scan_targets(self):
        self.log.append("Scanning USB ports, local cloud folders, and Wi-Fi router shares\u2026")

        def job():
            return discover_storage_targets(scan_network=True)

        def done(targets):
            if not targets:
                QMessageBox.information(
                    self, "Router & USB Scan",
                    "No open SMB/FTP USB share was detected automatically.\n\n"
                    "Tip: Log into your Wi-Fi router page (usually http://192.168.1.1), open USB Settings, "
                    "and enable either Network Neighborhood (SMB) or FTP Server.",
                )
                return
            labels = [f"{t.label}  \u2192  {t.location}  [{t.detail}]" for t in targets]
            choice, ok = QInputDialog.getItem(self, "Detected USB & Router Shares",
                                              "Select a storage target:", labels, 0, False)
            if ok and choice:
                t = targets[labels.index(choice)]
                if t.backend == "ftp":
                    self.kind.setCurrentIndex(3)
                    self.ftp_url.setText(t.location)
                else:
                    self.kind.setCurrentIndex(2)
                    self.target.setText(t.location)
                self.log.append(f"Selected: {t.location}")

        run_in_thread(self, job, done, lambda m: QMessageBox.warning(self, "Scan failed", m))

    def _google_oauth(self):
        from app.services.gmail_storage import authorize_google_oauth_pkce

        cid = self.gd_cid.text().strip()
        csec = self.gd_csec.text().strip()
        if not cid:
            webbrowser.open("https://console.cloud.google.com/apis/credentials")
            QMessageBox.information(
                self, "Google OAuth Client ID",
                "Paste your Google Cloud Desktop OAuth 2.0 Client ID first — OR select "
                "'Gmail Storage (Email + App Password)' in Storage Provider above to sync with your @gmail.com "
                "account immediately without creating any Google Cloud project!",
            )
            return
        self.log.append("Opening Google sign-in in your browser\u2026")

        def job():
            return authorize_google_oauth_pkce(cid, csec)

        def done(tok):
            self.gd_refresh = tok.get("refresh_token", "") or self.gd_refresh
            self.gd_access = tok.get("access_token", "")
            self.gd_status.setText("Authorized with Google Drive \u2713")
            self._collect()
            self.log.append("Google Drive OAuth 2.0 authorization succeeded.")

        run_in_thread(self, job, done, lambda m: QMessageBox.warning(self, "Google Sign-In", m))

    def _collect(self) -> dict:
        code = self.kind.currentData()
        d: dict = {
            "local": self.local.text().strip(),
            "backend": code,
            "encrypt": self.chk_encrypt.isChecked(),
            "vault_passphrase": vault.seal_secret(self.enc_pass.text().strip()),
            "gmail_email": self.gm_email.text().strip(),
            "gmail_app_password": vault.seal_secret(self.gm_pass.text().strip()),
            "gmail_label": self.gm_label.text().strip() or "MyOffice/Sync",
            "gdrive_client_id": self.gd_cid.text().strip(),
            "gdrive_client_secret": vault.seal_secret(self.gd_csec.text().strip()),
            "gdrive_refresh_token": vault.seal_secret(self.gd_refresh),
            "gdrive_access_token": vault.seal_secret(self.gd_access),
            "gdrive_folder": self.gd_folder.text().strip() or "MyOffice Sync",
            "target": self.target.text().strip(),
            "smb_user": self.smb_user.text().strip(),
            "smb_password": vault.seal_secret(self.smb_pass.text()),
            "ftp_url": self.ftp_url.text().strip(),
            "ftp_user": self.ftp_user.text().strip() or "anonymous",
            "ftp_password": vault.seal_secret(self.ftp_pass.text()),
            "endpoint": self.s3_ep.text().strip(),
            "bucket": (self.sb_bucket if code == "supabase" else self.s3_bucket).text().strip(),
            "access_key": self.s3_ak.text().strip(),
            "secret_key": vault.seal_secret(self.s3_sk.text()),
            "region": self.s3_region.text().strip() or "auto",
            "prefix": (self.sb_prefix if code == "supabase" else self.s3_prefix).text().strip(),
            "url": self.sb_url.text().strip(),
            "api_key": vault.seal_secret(self.sb_key.text()),
        }
        cfg.set("sync", d)
        return d

    def _save_settings(self):
        self._collect()
        self.log.append("Sync configuration saved securely.")

    def _set_busy(self, busy: bool):
        self.btn_sync.setEnabled(not busy)
        self.btn_test.setEnabled(not busy)
        self.prog.setVisible(busy)
        self.prog.setRange(0, 0 if busy else 1)

    def _test(self):
        try:
            b = build_backend(self._collect())
        except Exception as e:
            QMessageBox.warning(self, tr("Cloud Sync"), str(e))
            return
        self._set_busy(True)
        run_in_thread(self, b.test,
                      lambda msg: (self._set_busy(False), self.log.append(msg)),
                      lambda msg: (self._set_busy(False), self.log.append("Error: " + msg),
                                   QMessageBox.warning(self, tr("Cloud Sync"), msg.splitlines()[0])))

    def _sync(self):
        d = self._collect()
        try:
            eng = SyncEngine(d["local"], build_backend(d))
        except Exception as e:
            QMessageBox.warning(self, tr("Cloud Sync"), str(e))
            return
        self._set_busy(True)

        def done(rep):
            self._set_busy(False)
            self.log.append("Sync complete: " + rep.summary())
            for k in ("uploaded", "downloaded", "deleted_local", "deleted_remote", "conflicts", "errors"):
                for item in getattr(rep, k):
                    self.log.append(f"  {k}: {item}")

        run_in_thread(self, eng.sync, done,
                      lambda msg: (self._set_busy(False), self.log.append("Error: " + msg),
                                   QMessageBox.warning(self, tr("Cloud Sync"), msg.splitlines()[0])))


def open_dialog(main) -> CloudSyncDialog:
    dlg = CloudSyncDialog(main)
    dlg.exec()
    return dlg

