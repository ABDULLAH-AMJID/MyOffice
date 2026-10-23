"""PC <-> Android Mobile App Sync & QR Pairing Dialog (`Ctrl+Alt+M`)."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from app.collab.qr_tools import make_qr_pixmap
from app.collab.server import lan_addresses
from app.services import firewall, vault
from app.services.cloud_sync import SyncEngine, default_local_dir
from app.services.config import cfg
from app.services.i18n import _t
from app.services.mobile_sync import (
    MobilePeerBackend, discover_lan_peers, ensure_global_mobile_hub, parse_pair_uri,
)
from app.shell.tasks import run_task


class MobileSyncDialog(QDialog):
    """Two-way PC <-> Android App Sync Hub with 1-Scan QR Pairing, 6-Digit PIN, and Vault Key Sharing."""

    def __init__(self, main_window=None):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle(_t("Sync with Android Mobile App (PC \u2194 Mobile)"))
        self.resize(680, 610)

        firewall.ensure_firewall_rule_silent()
        self.hub = ensure_global_mobile_hub(collab_rooms_provider=self._active_collab_rooms)

        root_v = QVBoxLayout(self)
        root_v.setSpacing(10)

        banner = QFrame()
        banner.setStyleSheet(
            "QFrame { background: rgba(37, 99, 235, 0.10); border: 1px solid rgba(37, 99, 235, 0.30); "
            "border-radius: 10px; padding: 8px 12px; }"
        )
        bv = QVBoxLayout(banner)
        bv.setContentsMargins(8, 6, 8, 6)
        b_lbl = QLabel(_t(
            "<b>Instant Two-Way Sync with MyOffice for Android</b> — Works offline over Wi-Fi, Ethernet, or "
            "Phone Hotspot (no internet needed), or anywhere worldwide via <b>Gmail Cloud Sync</b>."
        ))
        b_lbl.setWordWrap(True)
        bv.addWidget(b_lbl)
        root_v.addWidget(banner)

        tabs = QTabWidget()
        tabs.addTab(self._build_host_tab(), _t("1. Scan QR from Android Phone (PC Host)"))
        tabs.addTab(self._build_peer_tab(), _t("2. Connect to Android Host (Phone Hotspot)"))
        root_v.addWidget(tabs, 1)

        bottom = QHBoxLayout()
        btn_cloud = QPushButton(_t("Configure Internet / Gmail Auto-Sync\u2026"))
        btn_cloud.clicked.connect(self._open_cloud_sync)
        bottom.addWidget(btn_cloud)
        bottom.addStretch(1)
        btn_close = QPushButton(_t("Close"))
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root_v.addLayout(bottom)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_activity)
        self._poll_timer.start(1500)
        self._refresh_qr()
        self._refresh_activity()

    def _active_collab_rooms(self) -> list[dict]:
        from app.collab import server as collab_srv

        out = []
        srv = getattr(collab_srv, "_SHARED_SERVER", None) or getattr(self.main_window, "_collab_server", None)
        if srv and srv.running:
            ip = self.ip_combo.currentText().strip() if hasattr(self, "ip_combo") else ""
            host_addr = ip or lan_addresses()[0]
            for r in srv.rooms.values():
                m = r.metadata()
                m["ws_url"] = f"ws://{host_addr}:{srv.port}/{m['room']}?client=mobile"
                m["http_url"] = f"http://{host_addr}:{srv.port}"
                out.append(m)
        return out

    def _build_host_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(_t("Synced PC Folder:")))
        self.folder_edit = QLineEdit(str(self.hub.root))
        folder_row.addWidget(self.folder_edit, 1)
        b_browse = QPushButton(_t("Change\u2026"))
        b_browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(b_browse)
        b_add_cur = QPushButton(_t("+ Add Open Tab"))
        b_add_cur.setToolTip(_t("Save/copy the currently open document into the synced folder immediately"))
        b_add_cur.clicked.connect(self._add_current_doc)
        folder_row.addWidget(b_add_cur)
        v.addLayout(folder_row)

        mid = QHBoxLayout()
        qr_box = QVBoxLayout()
        self.qr_lbl = QLabel()
        self.qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_lbl.setMinimumSize(215, 215)
        self.qr_lbl.setStyleSheet("background: #ffffff; border: 1px solid #cbd5e1; border-radius: 12px; padding: 8px;")
        qr_box.addWidget(self.qr_lbl)
        mid.addLayout(qr_box)

        info_box = QVBoxLayout()
        form = QFormLayout()
        self.ip_combo = QComboBox()
        for ip in lan_addresses():
            self.ip_combo.addItem(ip, ip)
        self.ip_combo.setEditable(True)
        self.ip_combo.currentTextChanged.connect(self._refresh_qr)
        form.addRow(_t("PC Wi-Fi / LAN IP:"), self.ip_combo)

        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        form.addRow(_t("Server Address:"), self.url_edit)

        self.pin_lbl = QLabel()
        self.pin_lbl.setStyleSheet("font-size: 22px; font-weight: 800; letter-spacing: 5px; color: #2563EB;")
        form.addRow(_t("6-Digit Pairing PIN:"), self.pin_lbl)
        info_box.addLayout(form)

        self.chk_vault = QCheckBox(_t(
            "Authorize paired phone to unlock this PC's Hardware Vault (.movault) backups"
        ))
        self.chk_vault.setChecked(bool(self.hub.share_vault_key))
        self.chk_vault.toggled.connect(self._toggle_vault_share)
        info_box.addWidget(self.chk_vault)

        btns = QHBoxLayout()
        b_copy = QPushButton(_t("Copy Pairing URI"))
        b_copy.setProperty("accent", True)
        b_copy.clicked.connect(self._copy_pair_uri)
        btns.addWidget(b_copy)
        b_regen = QPushButton(_t("New PIN / Token"))
        b_regen.clicked.connect(self._regen_pin)
        btns.addWidget(b_regen)
        info_box.addLayout(btns)
        info_box.addStretch(1)
        mid.addLayout(info_box, 1)
        v.addLayout(mid)

        v.addWidget(QLabel(_t("Connected Mobile Devices & Live Sync Activity:")))
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(135)
        v.addWidget(self.log_box)
        return w

    def _build_peer_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        s = cfg().get("sync", {}) or {}
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(_t("Discovered Android Phones / Peers on Wi-Fi or Hotspot:")))
        top_row.addStretch(1)
        b_scan = QPushButton(_t("Scan Wi-Fi Network"))
        b_scan.clicked.connect(self._scan_lan)
        top_row.addWidget(b_scan)
        v.addLayout(top_row)

        self.peer_list = QListWidget()
        self.peer_list.setMaximumHeight(110)
        self.peer_list.itemClicked.connect(self._select_discovered_peer)
        v.addWidget(self.peer_list)

        form = QFormLayout()
        self.peer_url_edit = QLineEdit(s.get("mobile_url", ""))
        self.peer_url_edit.setPlaceholderText("http://192.168.43.1:41880  or  myoffice://pair?...")
        form.addRow(_t("Phone URL or Pairing Link:"), self.peer_url_edit)

        self.peer_pin_edit = QLineEdit(vault.unseal_secret(s.get("mobile_pin", "")))
        self.peer_pin_edit.setPlaceholderText(_t("6-digit PIN shown on Android phone screen"))
        form.addRow(_t("6-Digit PIN:"), self.peer_pin_edit)

        self.peer_local_edit = QLineEdit(s.get("local_dir") or default_local_dir())
        form.addRow(_t("Local PC Folder:"), self.peer_local_edit)
        v.addLayout(form)

        row_btn = QHBoxLayout()
        b_pair_sync = QPushButton(_t("Pair & Two-Way Sync Now"))
        b_pair_sync.setProperty("accent", True)
        b_pair_sync.clicked.connect(self._pair_and_sync_peer)
        row_btn.addWidget(b_pair_sync)

        b_save_bg = QPushButton(_t("Save as Automatic Background Sync"))
        b_save_bg.clicked.connect(self._save_peer_as_background_sync)
        row_btn.addWidget(b_save_bg)
        row_btn.addStretch(1)
        v.addLayout(row_btn)

        self.peer_status = QLabel(_t(
            "Tip: If you have no Wi-Fi router, turn ON Mobile Hotspot on your Android phone, tap "
            "'Host on Phone' inside MyOffice Android, and click 'Scan Wi-Fi Network' above!"
        ))
        self.peer_status.setWordWrap(True)
        v.addWidget(self.peer_status)
        v.addStretch(1)
        return w

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, _t("Select Synced PC Folder"), str(self.hub.root))
        if not d:
            return
        p = Path(d).resolve()
        p.mkdir(parents=True, exist_ok=True)
        self.hub.root = p
        self.folder_edit.setText(str(p))
        ms = dict(cfg().get("mobile_sync", {}) or {})
        ms["local_dir"] = str(p)
        cfg().set("mobile_sync", ms)
        self.hub._log_event(f"Changed synced folder -> {p}")
        self._refresh_activity()

    def _add_current_doc(self):
        mw = self.main_window
        doc = mw.current_document() if mw and hasattr(mw, "current_document") else None
        if doc is None:
            QMessageBox.information(self, _t("No Open Tab"), _t("Open a document tab first, or place files inside the Synced Folder."))
            return
        if getattr(doc, "path", None) and Path(doc.path).is_file():
            src = Path(doc.path)
            dst = self.hub.root / src.name
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            self.hub._log_event(f"Added open document '{dst.name}' to synced folder")
        else:
            name = doc.display_name()
            if "." not in name:
                ext_map = {"writer": ".docx", "sheet": ".xlsx", "slide": ".pptx", "pdf": ".pdf"}
                name += ext_map.get(getattr(doc, "kind", ""), ".docx")
            dst = self.hub.root / name
            doc.save_file(str(dst))
            self.hub._log_event(f"Saved '{dst.name}' into synced folder")
        self._refresh_activity()
        QMessageBox.information(self, _t("Ready to Sync"), _t("'{0}' is now ready to sync to your Android phone.").format(dst.name))

    def _toggle_vault_share(self, checked: bool):
        self.hub.share_vault_key = bool(checked)
        ms = dict(cfg().get("mobile_sync", {}) or {})
        ms["share_vault_key"] = bool(checked)
        cfg().set("mobile_sync", ms)
        self._refresh_qr()

    def _copy_pair_uri(self):
        QGuiApplication.clipboard().setText(self.hub.pair_uri(self.ip_combo.currentText().strip() or None))

    def _regen_pin(self):
        tok, pin = self.hub.regenerate_credentials()
        ms = dict(cfg().get("mobile_sync", {}) or {})
        ms["token"] = vault.seal_secret(tok)
        ms["pin"] = vault.seal_secret(pin)
        cfg().set("mobile_sync", ms)
        self._refresh_qr()
        self._refresh_activity()

    def _refresh_qr(self):
        ip = self.ip_combo.currentText().strip() or None
        self.url_edit.setText(self.hub.base_url(ip))
        self.pin_lbl.setText(self.hub.pin)
        self.qr_lbl.setPixmap(make_qr_pixmap(self.hub.pair_uri(ip), 205))

    def _refresh_activity(self):
        lines = []
        if self.hub.paired_devices:
            devs = ", ".join(f"{k} ({v['ip']})" for k, v in self.hub.paired_devices.items())
            lines.append(f"Paired devices ({len(self.hub.paired_devices)}): {devs}")
        lines.extend(self.hub.activity_log[-40:])
        txt = "\n".join(lines)
        if self.log_box.toPlainText() != txt:
            self.log_box.setPlainText(txt)
            sb = self.log_box.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _scan_lan(self):
        self.peer_list.clear()
        peers = run_task(self, _t("Scanning Wi-Fi for MyOffice devices\u2026"), discover_lan_peers) or []
        for p in peers:
            item = QListWidgetItem(f"{p.device} [{p.platform.upper()}]  —  {p.url}  (v{p.version})")
            item.setData(Qt.ItemDataRole.UserRole, p.url)
            self.peer_list.addItem(item)
        if not peers:
            self.peer_status.setText(_t("No peer broadcast detected yet — enter the URL/PIN shown on your phone directly."))

    def _select_discovered_peer(self, item: QListWidgetItem):
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self.peer_url_edit.setText(str(url))

    def _pair_and_sync_peer(self):
        raw_url = self.peer_url_edit.text().strip()
        pin = self.peer_pin_edit.text().strip()
        local_dir = self.peer_local_edit.text().strip() or default_local_dir()
        if not raw_url:
            QMessageBox.warning(self, _t("Missing Address"), _t("Enter or scan the phone's Sync URL first."))
            return

        def job():
            parsed = parse_pair_uri(raw_url)
            backend = MobilePeerBackend(parsed["url"] or raw_url, token=parsed["token"], pin=pin or parsed["pin"])
            if not backend.token:
                backend.pair(pin=pin or parsed["pin"])
            eng = SyncEngine(local_dir, backend)
            rep = eng.sync_once()
            return backend, rep

        try:
            res = run_task(self, _t("Pairing & syncing with Android device\u2026"), job)
            if not res:
                return
            backend, rep = res
            s = dict(cfg().get("sync", {}) or {})
            s.update({
                "mobile_url": backend.base_url,
                "mobile_token": vault.seal_secret(backend.token),
                "mobile_pin": vault.seal_secret(pin),
                "local_dir": local_dir,
            })
            cfg().set("sync", s)
            self.peer_status.setText(f"\u2713 {rep.summary()}")
        except Exception as exc:
            QMessageBox.critical(self, _t("Sync Failed"), str(exc))

    def _save_peer_as_background_sync(self):
        raw_url = self.peer_url_edit.text().strip()
        parsed = parse_pair_uri(raw_url)
        s = dict(cfg().get("sync", {}) or {})
        s.update({
            "kind": "mobile_peer",
            "enabled": True,
            "mobile_url": parsed["url"] or raw_url,
            "mobile_pin": vault.seal_secret(self.peer_pin_edit.text().strip() or parsed["pin"]),
            "local_dir": self.peer_local_edit.text().strip() or default_local_dir(),
        })
        if parsed["token"]:
            s["mobile_token"] = vault.seal_secret(parsed["token"])
        cfg().set("sync", s)
        if self.main_window and hasattr(self.main_window, "restart_sync"):
            self.main_window.restart_sync()
        QMessageBox.information(self, _t("Automatic Mobile Sync Active"),
                                _t("Background automatic sync with your Android device is now active."))

    def _open_cloud_sync(self):
        from app.tools.cloud_sync_dialog import open_dialog as open_cloud
        open_cloud(self.main_window)


def open_dialog(main_window=None):
    dlg = MobileSyncDialog(main_window)
    dlg.exec()
