"""Backup & Security Vault Center: Local Rolling Revisions + Laptop-Locked Router/USB Vault + Gmail Backup."""
from __future__ import annotations

import os
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.services import backup, vault
from app.services.config import cfg
from app.services.i18n import tr
from app.services.net_storage import discover_storage_targets
from app.services.worker import run_in_thread

GMAIL_APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"


class BackupDialog(QDialog):
    def __init__(self, mw, active_path: str | None = None, initial_tab: int = 0):
        super().__init__(mw)
        self.mw = mw
        self.active_path = active_path
        self.setWindowTitle(tr("Backup & Vault"))
        self.resize(820, 560)

        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        self._build_local_tab()
        self._build_vault_tab()
        self._build_gmail_tab()

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        v.addWidget(self.status_lbl)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.accept)
        close_row.addWidget(b_close)
        v.addLayout(close_row)

        self.tabs.setCurrentIndex(max(0, min(initial_tab, 2)))
        self._fill()

    # ========================================================================= Tab 1: Local Version History
    def _build_local_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.only = QCheckBox("Only show versions of the active file")
        self.only.setChecked(bool(self.active_path))
        self.only.setEnabled(bool(self.active_path))
        self.only.toggled.connect(self._fill)
        v.addWidget(self.only)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Original file", "Saved", "Size"])
        self.tree.setColumnWidth(0, 390)
        self.tree.setRootIsDecorated(False)
        v.addWidget(self.tree, 1)

        hb = QHBoxLayout()
        b_now = QPushButton("Back Up Current File Now")
        b_now.clicked.connect(self._now)
        b_now.setEnabled(bool(self.active_path))
        b_rest = QPushButton("Restore & Open\u2026")
        b_rest.setProperty("accent", True)
        b_rest.clicked.connect(self._restore)
        b_folder = QPushButton("Open Backups Folder")
        b_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(backup.backup_dir()))))
        hb.addWidget(b_now)
        hb.addWidget(b_rest)
        hb.addWidget(b_folder)
        hb.addStretch(1)
        v.addLayout(hb)
        self.tabs.addTab(page, "Local Version History")

    def _fill(self):
        self.tree.clear()
        for it in backup.list_backups(self.active_path if self.only.isChecked() else None):
            row = QTreeWidgetItem([it.original, it.when.strftime("%Y-%m-%d %H:%M:%S"), f"{it.size / 1024:.1f} KB"])
            row.setData(0, Qt.UserRole, it)
            self.tree.addTopLevelItem(row)

    def _now(self):
        if self.active_path and backup.snapshot(self.active_path, int(cfg.get("backup.max_versions", 10))):
            self._fill()
            self.status_lbl.setText(f"Saved snapshot of {os.path.basename(self.active_path)}.")

    def _restore(self):
        cur = self.tree.currentItem()
        if not cur:
            return
        it: backup.BackupItem = cur.data(0, Qt.UserRole)
        dest, _ = QFileDialog.getSaveFileName(self, "Restore version to", it.original)
        if not dest:
            return
        backup.restore(it, dest)
        self.mw.open_path(dest)
        self.accept()

    # ========================================================================= Tab 2: Router USB / Hardware Vault
    def _build_vault_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        saved = dict(cfg.get("backup_vault") or {})
        ks = vault.keystore()

        sec_banner = QGroupBox("Laptop Hardware Security Key (AES-256-GCM + DPAPI Hardware Seal)")
        sb_layout = QVBoxLayout(sec_banner)
        badge = QLabel(
            f"<b>Authorized Device:</b> {ks.device_label} &nbsp;&bull;&nbsp; "
            f"<b>Hardware Key Fingerprint:</b> <code>{ks.key_id}</code><br>"
            "Every file backed up here has its <b>contents AND filename</b> encrypted with 256-bit AES-GCM bound to "
            "this laptop. If anyone steals or unplugs the USB drive from your Wi-Fi router, <b>zero filenames or data "
            "can be decrypted on any other PC</b> — while YOUR laptop opens it seamlessly both over Wi-Fi AND if you "
            "plug that USB directly into your laptop's USB port!"
        )
        badge.setWordWrap(True)
        sb_layout.addWidget(badge)
        key_btns = QHBoxLayout()
        b_exp = QPushButton("Export Emergency Recovery Key (.movaultkey)\u2026")
        b_exp.clicked.connect(self._export_recovery_key)
        b_imp = QPushButton("Authorize Backup from Previous Laptop (.movaultkey)\u2026")
        b_imp.clicked.connect(self._import_recovery_key)
        key_btns.addWidget(b_exp)
        key_btns.addWidget(b_imp)
        key_btns.addStretch(1)
        sb_layout.addLayout(key_btns)
        v.addWidget(sec_banner)

        form_box = QGroupBox("Wi-Fi Router USB Share / Network NAS / External USB Target")
        fm = QFormLayout(form_box)
        self.v_loc = QLineEdit(saved.get("location", ""))
        self.v_loc.setPlaceholderText(
            r"\\192.168.1.1\usb1_1\MyOfficeVault  OR  ftp://192.168.1.1:21/MyOfficeVault  OR  E:\MyOfficeVault"
        )
        b_vscan = QPushButton("Scan Wi-Fi Router & USB")
        b_vscan.clicked.connect(self._scan_vault_targets)
        b_vbrowse = QPushButton("Browse\u2026")
        b_vbrowse.clicked.connect(self._browse_vault)
        hl = QHBoxLayout()
        hl.addWidget(self.v_loc, 1)
        hl.addWidget(b_vscan)
        hl.addWidget(b_vbrowse)
        fm.addRow("Storage location", hl)

        cred_row = QHBoxLayout()
        self.v_user = QLineEdit(saved.get("username", ""))
        self.v_user.setPlaceholderText("Router SMB/FTP username (optional)")
        self.v_pass = QLineEdit(vault.unseal_secret(saved.get("password", "")))
        self.v_pass.setEchoMode(QLineEdit.Password)
        self.v_pass.setPlaceholderText("Router password (optional)")
        self.v_passphrase = QLineEdit(vault.unseal_secret(saved.get("passphrase", "")))
        self.v_passphrase.setEchoMode(QLineEdit.Password)
        self.v_passphrase.setPlaceholderText("Optional emergency passphrase")
        cred_row.addWidget(self.v_user, 1)
        cred_row.addWidget(self.v_pass, 1)
        cred_row.addWidget(self.v_passphrase, 1)
        fm.addRow("Credentials", cred_row)

        self.v_auto = QCheckBox("Automatically mirror every saved document into this encrypted Vault")
        self.v_auto.setChecked(bool(saved.get("auto_mirror", False)))
        fm.addRow(self.v_auto)
        v.addWidget(form_box)

        self.v_tree = QTreeWidget()
        self.v_tree.setHeaderLabels(["Decrypted Name", "Saved", "Original Size", "Owner Device", "Vault File"])
        self.v_tree.setColumnWidth(0, 230)
        self.v_tree.setColumnWidth(1, 145)
        self.v_tree.setColumnWidth(3, 150)
        self.v_tree.setRootIsDecorated(False)
        v.addWidget(self.v_tree, 1)

        vb = QHBoxLayout()
        b_vsave = QPushButton("Save & Refresh Vault")
        b_vsave.clicked.connect(self._refresh_vault)
        b_vcur = QPushButton("Encrypt & Back Up Current File")
        b_vcur.setEnabled(bool(self.active_path))
        b_vcur.clicked.connect(self._vault_backup_current)
        b_vany = QPushButton("Encrypt & Back Up Any File(s)\u2026")
        b_vany.setProperty("accent", True)
        b_vany.clicked.connect(self._vault_backup_files)
        b_vrest = QPushButton("Decrypt & Restore\u2026")
        b_vrest.clicked.connect(self._vault_restore)
        vb.addWidget(b_vsave)
        vb.addWidget(b_vcur)
        vb.addWidget(b_vany)
        vb.addWidget(b_vrest)
        vb.addStretch(1)
        v.addLayout(vb)
        self.tabs.addTab(page, "Router USB / Hardware Vault")

    def _save_vault_cfg(self) -> dict:
        d = {
            "location": self.v_loc.text().strip(),
            "username": self.v_user.text().strip(),
            "password": vault.seal_secret(self.v_pass.text()),
            "passphrase": vault.seal_secret(self.v_passphrase.text()),
            "auto_mirror": self.v_auto.isChecked(),
        }
        cfg.set("backup_vault", d)
        return d

    def _browse_vault(self):
        d = QFileDialog.getExistingDirectory(self, "Choose USB Drive or Shared Router Folder", self.v_loc.text())
        if d:
            self.v_loc.setText(d)
            self._refresh_vault()

    def _scan_vault_targets(self):
        self.status_lbl.setText("Scanning USB drives and Wi-Fi router SMB/FTP storage shares\u2026")

        def done(targets):
            if not targets:
                self.status_lbl.setText("No automatic USB/Router target found — browse a drive or paste \\\\router\\share or ftp://router:21.")
                QMessageBox.information(
                    self, "Router & USB Scan",
                    "No open USB drive or router share responded automatically.\n\n"
                    "If your USB drive is plugged into your Wi-Fi router:\n"
                    "1. Open your router admin page (usually http://192.168.1.1)\n"
                    "2. Enable USB Storage Sharing (SMB or FTP Server)\n"
                    "3. Click Scan again or paste e.g. ftp://192.168.1.1:21/MyOfficeVault",
                )
                return
            labels = [f"{t.label}  \u2192  {t.location}  ({t.detail})" for t in targets]
            choice, ok = QInputDialog.getItem(self, "Detected USB & Wi-Fi Router Storage",
                                              "Choose where encrypted backups should live:", labels, 0, False)
            if ok and choice:
                t = targets[labels.index(choice)]
                self.v_loc.setText(t.location)
                self._refresh_vault()

        run_in_thread(self, lambda: discover_storage_targets(scan_network=True), done,
                      lambda m: QMessageBox.warning(self, "Scan Error", m))

    def _refresh_vault(self):
        d = self._save_vault_cfg()
        if not d["location"]:
            return
        self.status_lbl.setText(f"Reading encrypted vault at {d['location']}\u2026")

        def done(entries: list[vault.VaultEntry]):
            self.v_tree.clear()
            for e in entries:
                sz = f"{e.original_size / 1024:.1f} KB" if not e.locked_to_other_device else f"{e.encrypted_size / 1024:.1f} KB (enc)"
                owner = f"{e.device_label} ({e.key_id[:8]})"
                row = QTreeWidgetItem([e.original_name, e.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                                       sz, owner, e.vault_filename])
                row.setData(0, Qt.UserRole, e)
                self.v_tree.addTopLevelItem(row)
            self.status_lbl.setText(f"Vault connected ({len(entries)} encrypted backup item(s)).")

        run_in_thread(self, lambda: backup.list_vault_target(d), done,
                      lambda m: (self.status_lbl.setText("Vault error: " + m.splitlines()[0]),
                                 QMessageBox.warning(self, "Vault Error", m)))

    def _vault_backup_current(self):
        if self.active_path:
            self._run_vault_backup([self.active_path])

    def _vault_backup_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select any file(s) to encrypt into Hardware Vault", "")
        if files:
            self._run_vault_backup(files)

    def _run_vault_backup(self, paths: list[str]):
        d = self._save_vault_cfg()
        if not d["location"]:
            QMessageBox.warning(self, "Vault Target Needed", "Select or enter a USB / Router storage path first.")
            return
        self.status_lbl.setText(f"Encrypting {len(paths)} file(s) with AES-256-GCM hardware lock\u2026")

        def job():
            return [backup.backup_to_vault_target(p, d) for p in paths]

        def done(created):
            self.status_lbl.setText(f"Encrypted & stored {len(created)} file(s) safely.")
            self._refresh_vault()

        run_in_thread(self, job, done, lambda m: QMessageBox.warning(self, "Vault Backup Failed", m))

    def _vault_restore(self):
        cur = self.v_tree.currentItem()
        if not cur:
            return
        entry: vault.VaultEntry = cur.data(0, Qt.UserRole)
        suggested = entry.original_name if not entry.locked_to_other_device else "restored_file"
        dest, _ = QFileDialog.getSaveFileName(self, "Decrypt & Restore File To", suggested)
        if not dest:
            return
        d = self._save_vault_cfg()
        pw = self.v_passphrase.text()

        def done(out_path: Path):
            self.status_lbl.setText(f"Verified SHA-256 & decrypted to {out_path}")
            self.mw.open_path(str(out_path))

        run_in_thread(self, lambda: backup.restore_from_vault_target(entry, dest, d, passphrase=pw),
                      done, lambda m: QMessageBox.critical(self, "Vault Decryption Blocked", m))

    def _export_recovery_key(self):
        pw, ok = QInputDialog.getText(
            self, "Export Emergency Recovery Key",
            "Create a strong password (min 6 chars) to encrypt your .movaultkey recovery file:",
            QLineEdit.Password,
        )
        if not ok or not pw:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save Emergency Recovery Key",
            f"MyOffice_{vault.keystore().device_label}.movaultkey",
            "MyOffice Vault Key (*.movaultkey)",
        )
        if not dest:
            return
        try:
            out = vault.keystore().export_recovery_kit(dest, pw)
            QMessageBox.information(
                self, "Recovery Key Exported",
                f"Saved encrypted recovery key to:\n{out}\n\n"
                "Keep this file in a safe place (NOT on the same router USB stick). If your laptop ever fails, "
                "import this file on your replacement laptop to unlock all your encrypted backups.",
            )
        except Exception as e:
            QMessageBox.warning(self, "Export Failed", str(e))

    def _import_recovery_key(self):
        src, _ = QFileDialog.getOpenFileName(
            self, "Import MyOffice Vault Recovery Key", "", "MyOffice Vault Key (*.movaultkey);;All files (*)"
        )
        if not src:
            return
        pw, ok = QInputDialog.getText(self, "Unlock Recovery Key", "Enter recovery key password:", QLineEdit.Password)
        if not ok or not pw:
            return
        try:
            added = vault.keystore().import_recovery_kit(src, pw)
            QMessageBox.information(
                self, "Device Authorized",
                f"Imported {len(added)} hardware key(s). This computer can now open backups created on that laptop.",
            )
            self._refresh_vault()
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", str(e))

    # ========================================================================= Tab 3: Gmail Cloud Backup
    def _build_gmail_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        saved = dict(cfg.get("backup_gmail") or {})

        box = QGroupBox("Gmail Cloud Backup (Encrypted IMAP4-SSL Storage)")
        fm = QFormLayout(box)
        info = QLabel(
            "Back up important documents straight into your Gmail account under label <b>MyOffice/Backups</b>. "
            "No emails are sent and your Inbox stays completely clean. With <b>Hardware Lock Encryption</b> checked, "
            "even someone with access to your Gmail password cannot read your backed-up files on another computer."
        )
        info.setWordWrap(True)
        fm.addRow(info)

        self.g_email = QLineEdit(saved.get("email", (cfg.get("sync") or {}).get("gmail_email", "")))
        self.g_email.setPlaceholderText("yourname@gmail.com")
        self.g_pass = QLineEdit(vault.unseal_secret(saved.get("app_password", (cfg.get("sync") or {}).get("gmail_app_password", ""))))
        self.g_pass.setEchoMode(QLineEdit.Password)
        self.g_pass.setPlaceholderText("16-character Gmail App Password")
        b_app = QPushButton("Get Gmail App Password\u2026")
        b_app.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GMAIL_APP_PASSWORD_URL)))
        hp = QHBoxLayout()
        hp.addWidget(self.g_pass, 1)
        hp.addWidget(b_app)
        self.g_label = QLineEdit(saved.get("label", "MyOffice/Backups"))
        self.g_enc = QCheckBox("Encrypt files with AES-256-GCM Hardware Lock before uploading to Gmail (Recommended)")
        self.g_enc.setChecked(bool(saved.get("encrypt", True)))
        self.g_auto = QCheckBox("Automatically mirror saved files to Gmail Backup in background")
        self.g_auto.setChecked(bool(saved.get("auto_mirror", False)))

        fm.addRow("Gmail address", self.g_email)
        fm.addRow("App Password", hp)
        fm.addRow("Backup label", self.g_label)
        fm.addRow(self.g_enc)
        fm.addRow(self.g_auto)
        v.addWidget(box)

        self.g_tree = QTreeWidget()
        self.g_tree.setHeaderLabels(["Gmail Backup Item", "Uploaded", "Size"])
        self.g_tree.setColumnWidth(0, 420)
        self.g_tree.setRootIsDecorated(False)
        v.addWidget(self.g_tree, 1)

        gb = QHBoxLayout()
        b_glist = QPushButton("Save & Refresh Gmail Backups")
        b_glist.clicked.connect(self._gmail_refresh)
        b_gcur = QPushButton("Back Up Current File to Gmail")
        b_gcur.setEnabled(bool(self.active_path))
        b_gcur.clicked.connect(lambda: self._gmail_backup([self.active_path]) if self.active_path else None)
        b_gany = QPushButton("Back Up File(s) to Gmail\u2026")
        b_gany.setProperty("accent", True)
        b_gany.clicked.connect(self._gmail_pick_and_backup)
        b_grest = QPushButton("Download & Restore\u2026")
        b_grest.clicked.connect(self._gmail_restore)
        gb.addWidget(b_glist)
        gb.addWidget(b_gcur)
        gb.addWidget(b_gany)
        gb.addWidget(b_grest)
        gb.addStretch(1)
        v.addLayout(gb)
        self.tabs.addTab(page, "Gmail Cloud Backup")

    def _save_gmail_cfg(self) -> dict:
        d = {
            "email": self.g_email.text().strip(),
            "app_password": vault.seal_secret(self.g_pass.text().strip()),
            "label": self.g_label.text().strip() or "MyOffice/Backups",
            "encrypt": self.g_enc.isChecked(),
            "auto_mirror": self.g_auto.isChecked(),
        }
        cfg.set("backup_gmail", d)
        return d

    def _gmail_refresh(self):
        d = self._save_gmail_cfg()
        self.status_lbl.setText(f"Connecting to Gmail ({d['email']})\u2026")

        def done(items):
            self.g_tree.clear()
            for rel, ts, sz in items:
                row = QTreeWidgetItem([rel, ts.strftime("%Y-%m-%d %H:%M:%S"), f"{sz / 1024:.1f} KB"])
                row.setData(0, Qt.UserRole, rel)
                self.g_tree.addTopLevelItem(row)
            self.status_lbl.setText(f"Connected to Gmail \u2022 {len(items)} backup(s) in {d['label']}.")

        run_in_thread(self, lambda: backup.list_gmail_backups(d), done,
                      lambda m: (self.status_lbl.setText("Gmail error: " + m.splitlines()[0]),
                                 QMessageBox.warning(self, "Gmail Backup", m)))

    def _gmail_pick_and_backup(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose file(s) to back up to Gmail", "")
        if files:
            self._gmail_backup(files)

    def _gmail_backup(self, paths: list[str]):
        d = self._save_gmail_cfg()
        self.status_lbl.setText(f"Uploading {len(paths)} backup(s) to Gmail\u2026")

        def job():
            return [backup.backup_to_gmail(p, d) for p in paths]

        def done(created):
            self.status_lbl.setText(f"Uploaded {len(created)} backup(s) to Gmail label '{d['label']}'.")
            self._gmail_refresh()

        run_in_thread(self, job, done, lambda m: QMessageBox.warning(self, "Gmail Backup Failed", m))

    def _gmail_restore(self):
        cur = self.g_tree.currentItem()
        if not cur:
            return
        rel = cur.data(0, Qt.UserRole)
        folder = QFileDialog.getExistingDirectory(self, "Choose folder to restore backup into", "")
        if not folder:
            return
        d = self._save_gmail_cfg()
        self.status_lbl.setText(f"Downloading & verifying {rel} from Gmail\u2026")

        def done(out_path: Path):
            self.status_lbl.setText(f"Restored to {out_path}")
            self.mw.open_path(str(out_path))

        run_in_thread(self, lambda: backup.restore_gmail_backup(rel, folder, d), done,
                      lambda m: QMessageBox.critical(self, "Gmail Restore Failed", m))


def open_dialog(main, initial_tab: int = 0) -> BackupDialog:
    doc = main.current_document() if hasattr(main, "current_document") else None
    active_path = getattr(doc, "path", None) if doc else None
    dlg = BackupDialog(main, active_path=active_path, initial_tab=initial_tab)
    dlg.exec()
    return dlg

