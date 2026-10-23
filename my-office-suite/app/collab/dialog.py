"""Collaboration Hub Dialog — Multi-File LAN Sharing, QR Codes, Per-File Links & Universal Join."""
from __future__ import annotations

import getpass
import os
import re
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.collab.binding import CollabSession, download_remote_room_file, parse_join_target
from app.collab.chat_panel import ShareQrPopup, ensure_tab_collab_panel
from app.collab.qr_tools import make_qr_pixmap
from app.collab.server import ensure_shared_server, lan_addresses, primary_lan_ip
from app.services import firewall
from app.services.config import cfg
from app.services.i18n import _t


def _make_room_slug(doc_widget) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", doc_widget.display_name()).strip("-").lower() or "document"
    return f"{base[:36]}-{uuid.uuid4().hex[:6]}"


def attach_session_to_doc(main, doc_widget, ws_base: str, room: str, user_name: str,
                          http_base: str = "", seed_snapshot: bool = False) -> CollabSession:
    """Attach a live `CollabSession` + docked `CollabSidePanel` chat box to `doc_widget` (supports multiple files!)."""
    existing: CollabSession | None = getattr(doc_widget, "_collab_session", None)
    if existing is not None:
        existing.stop()

    sess = CollabSession(doc_widget, ws_base, room, user=user_name, http_base=http_base)
    if seed_snapshot:
        sess.publish_initial_file_snapshot()

    def _stop_session():
        sess.stop()
        panel = getattr(doc_widget, "_collab_panel", None)
        if panel is not None:
            panel.hide()
        doc_widget._collab_session = None

    ensure_tab_collab_panel(doc_widget, sess, on_stop=_stop_session)
    sess.start()
    if hasattr(main, "collab_sessions") and isinstance(main.collab_sessions, dict):
        main.collab_sessions[doc_widget.doc_id] = sess
    if hasattr(main, "focus_page"):
        main.focus_page(doc_widget)
    return sess


def join_shared_target(main, raw_link: str, user_name: str = "") -> CollabSession:
    """Join ANY shared LAN file (`myoffice://...`, `http://ip:port/f/room`, `ws://ip:port/room`) in 1 click!"""
    target = parse_join_target(raw_link)
    user = user_name.strip() or cfg().get("collab.user", "") or getpass.getuser() or "Collaborator"
    cfg().set("collab.user", user, save=True)

    # Automatically fetch ANY file format from the host if available so the joiner has the exact file immediately!
    downloaded = download_remote_room_file(target["http_base"], target["room"])
    doc = None
    if downloaded and os.path.isfile(downloaded):
        ext = os.path.splitext(downloaded)[1].lower()
        try:
            doc = main.open_path(downloaded)
        except Exception:
            doc = None
        if doc is None:
            # Universal fallback: open text/code or any binary file wrapper inside MyOffice so EVERY format works!
            from app.collab.anyfile import UniversalFileDocument

            doc = UniversalFileDocument(downloaded, parent=main)
            main._register_document(doc, doc.display_name())
    if doc is None:
        doc = main.current_document() or main.new_document("writer")

    return attach_session_to_doc(
        main,
        doc,
        target["ws_base"],
        target["room"],
        user_name=user,
        http_base=target["http_base"],
        seed_snapshot=False,
    )


class CollabDialog(QDialog):
    """Multi-File LAN Collaboration Hub: Share Any File(s) with QR + Link, Manage Active Rooms, or Join via Link."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(_t("Live LAN Collaboration, QR Share & Team Chat"))
        self.resize(820, 560)

        root = QVBoxLayout(self)
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(_t("Your display name in Team Chat & Cursors:")))
        self.name_edit = QLineEdit(cfg().get("collab.user", "") or getpass.getuser() or "User")
        top_bar.addWidget(self.name_edit, 1)

        top_bar.addWidget(QLabel(_t("Wi-Fi / LAN IP:")))
        self.ip_combo = QComboBox()
        for ip in lan_addresses():
            self.ip_combo.addItem(ip, ip)
        self.ip_combo.setEditable(True)
        self.ip_combo.currentTextChanged.connect(self._update_selected_preview)
        top_bar.addWidget(self.ip_combo)

        btn_fw = QPushButton(_t("Allow Windows Wi-Fi Firewall"))
        btn_fw.setToolTip(_t("Ensure inbound LAN connections work across Public & Private Wi-Fi networks"))
        btn_fw.clicked.connect(self._allow_firewall)
        top_bar.addWidget(btn_fw)
        root.addLayout(top_bar)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self._build_share_tab()
        self._build_join_tab()
        self._refresh_active_list()

    def _allow_firewall(self):
        port = int(self.port_spin.value())
        ok, msg = firewall.add_firewall_rule(port)
        QMessageBox.information(self, _t("Windows Firewall"), msg or (_t("Firewall updated.") if ok else _t("Done.")))

    def _build_share_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel(_t("LAN Server Port:")))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(cfg().get("collab.port", 8765)))
        ctrl.addWidget(self.port_spin)

        btn_share_cur = QPushButton(_t("Share Active Tab Now"))
        btn_share_cur.setProperty("accent", True)
        btn_share_cur.clicked.connect(self._share_current_tab)
        ctrl.addWidget(btn_share_cur)

        btn_share_file = QPushButton(_t("Share Any File from Disk\u2026"))
        btn_share_file.clicked.connect(self._share_disk_file)
        ctrl.addWidget(btn_share_file)

        btn_share_all = QPushButton(_t("Share All Open Tabs"))
        btn_share_all.clicked.connect(self._share_all_tabs)
        ctrl.addWidget(btn_share_all)
        ctrl.addStretch(1)
        v.addLayout(ctrl)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel(_t("<b>Simultaneously Shared Files on This Network</b> (select any file for its QR & Link):")))
        self.rooms_tree = QTreeWidget()
        self.rooms_tree.setHeaderLabels([_t("File"), _t("Format"), _t("Room ID"), _t("Share URL")])
        self.rooms_tree.setColumnWidth(0, 190)
        self.rooms_tree.setColumnWidth(1, 70)
        self.rooms_tree.setColumnWidth(2, 150)
        self.rooms_tree.setRootIsDecorated(False)
        self.rooms_tree.itemSelectionChanged.connect(self._update_selected_preview)
        left.addWidget(self.rooms_tree, 1)
        body.addLayout(left, 3)

        right = QGroupBox(_t("Selected File QR Code & Per-File Link"))
        rv = QVBoxLayout(right)
        self.qr_label = QLabel(_t("Share a file to generate its live QR code & link"))
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(210, 210)
        rv.addWidget(self.qr_label)

        self.link_edit = QLineEdit()
        self.link_edit.setReadOnly(True)
        self.link_edit.setPlaceholderText("http://<wifi-ip>:8765/f/<room>")
        rv.addWidget(self.link_edit)

        copy_row = QHBoxLayout()
        btn_copy_http = QPushButton(_t("Copy Browser / Join Link"))
        btn_copy_http.setProperty("accent", True)
        btn_copy_http.clicked.connect(lambda: self._copy_text(self.link_edit.text(), btn_copy_http))
        copy_row.addWidget(btn_copy_http)

        btn_copy_app = QPushButton(_t("Copy App Link"))
        btn_copy_app.clicked.connect(self._copy_app_link)
        copy_row.addWidget(btn_copy_app)
        rv.addLayout(copy_row)
        body.addWidget(right, 2)

        v.addLayout(body, 1)
        self.tabs.addTab(page, _t("Share Files & QR Codes"))

    def _build_join_tab(self):
        page = QWidget()
        f = QFormLayout(page)
        help_lbl = QLabel(
            _t("Paste ANY link shared by a teammate (<code>http://192.168.x.x:8765/f/...</code>, "
               "<code>myoffice://...</code>, or <code>ws://...</code>). MyOffice will automatically download the "
               "file (any format!), open it in a new tab, sync live edits, and connect you to that file's Team Chat!")
        )
        help_lbl.setWordWrap(True)
        f.addRow(help_lbl)

        self.join_url_edit = QLineEdit()
        self.join_url_edit.setPlaceholderText("http://192.168.1.25:8765/f/project-plan-a1b2c3")
        f.addRow(_t("Share Link / Server URL:"), self.join_url_edit)

        btn_join = QPushButton(_t("Download File, Join Live Room & Open Team Chat"))
        btn_join.setProperty("accent", True)
        btn_join.clicked.connect(self._join_now)
        f.addRow("", btn_join)
        self.tabs.addTab(page, _t("Join Shared File"))

    def _copy_text(self, txt: str, btn: QPushButton):
        if not txt:
            return
        QGuiApplication.clipboard().setText(txt)
        old = btn.text()
        btn.setText(_t("Copied \u2713"))
        QTimer.singleShot(1400, lambda: btn.setText(old))

    def _copy_app_link(self):
        cur = self.rooms_tree.currentItem()
        if not cur:
            return
        sess: CollabSession = cur.data(0, Qt.ItemDataRole.UserRole)
        links = sess.share_links(self.ip_combo.currentText().strip() or None)
        QGuiApplication.clipboard().setText(links["myoffice"])

    def _ensure_server_and_user(self):
        user = self.name_edit.text().strip() or getpass.getuser() or "User"
        port = int(self.port_spin.value())
        cfg().set("collab.user", user, save=False)
        cfg().set("collab.port", port, save=True)
        srv = ensure_shared_server(port)
        return srv, user

    def _share_doc_widget(self, doc) -> CollabSession:
        from app.collab.binding import extract_doc_snapshot_bytes

        srv, user = self._ensure_server_and_user()
        existing: CollabSession | None = getattr(doc, "_collab_session", None)
        if existing is not None:
            return existing
        room_slug = _make_room_slug(doc)
        path = getattr(doc, "path", None)
        path_str = str(path) if path else ""
        raw_bytes, fname, kind = extract_doc_snapshot_bytes(doc)
        srv.register_shared_file(
            room_slug,
            file_path=path_str if path_str and os.path.isfile(path_str) else None,
            filename=fname,
            kind=kind,
            raw_bytes=raw_bytes or None,
        )
        sess = attach_session_to_doc(
            self.main,
            doc,
            f"ws://127.0.0.1:{srv.port}",
            room_slug,
            user_name=user,
            http_base=f"http://{primary_lan_ip()}:{srv.port}",
            seed_snapshot=True,
        )
        return sess

    def _share_current_tab(self):
        doc = self.main.current_document()
        if doc is None:
            doc = self.main.new_document("writer")
        self._share_doc_widget(doc)
        self._refresh_active_list()

    def _share_all_tabs(self):
        docs = self.main.documents()
        if not docs:
            docs = [self.main.new_document("writer")]
        for d in docs:
            self._share_doc_widget(d)
        self._refresh_active_list()

    def _share_disk_file(self):
        path, _ = QFileDialog.getOpenFileName(self, _t("Select ANY File to Share & Collaborate on LAN"), "", "All files (*.*)")
        if not path:
            return
        doc = None
        try:
            doc = self.main.open_path(path)
        except Exception:
            doc = None
        if doc is None:
            from app.collab.anyfile import UniversalFileDocument

            doc = UniversalFileDocument(path, parent=self.main)
            self.main._register_document(doc, doc.display_name())
        self._share_doc_widget(doc)
        self._refresh_active_list()

    def _refresh_active_list(self):
        self.rooms_tree.clear()
        ip = self.ip_combo.currentText().strip() or primary_lan_ip()
        for doc in self.main.documents():
            sess: CollabSession | None = getattr(doc, "_collab_session", None)
            if sess is None:
                continue
            links = sess.share_links(ip)
            row = QTreeWidgetItem([
                doc.display_name(),
                getattr(doc, "kind", "any").upper(),
                sess.room,
                links["http"],
            ])
            row.setData(0, Qt.ItemDataRole.UserRole, sess)
            self.rooms_tree.addTopLevelItem(row)
        if self.rooms_tree.topLevelItemCount() > 0:
            self.rooms_tree.setCurrentItem(self.rooms_tree.topLevelItem(0))
        self._update_selected_preview()

    def _update_selected_preview(self):
        cur = self.rooms_tree.currentItem()
        if cur is None:
            return
        sess: CollabSession = cur.data(0, Qt.ItemDataRole.UserRole)
        ip = self.ip_combo.currentText().strip() or primary_lan_ip()
        links = sess.share_links(ip)
        cur.setText(3, links["http"])
        self.link_edit.setText(links["http"])
        self.qr_label.setPixmap(make_qr_pixmap(links["http"], 204))

    def _join_now(self):
        raw = self.join_url_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, _t("Join Shared File"), _t("Paste a share link or server address first."))
            return
        try:
            join_shared_target(self.main, raw, self.name_edit.text().strip())
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, _t("Could Not Join Room"), str(exc))


def open_dialog(main) -> CollabDialog:
    if not firewall.ensure_consent(main, "collab"):
        return None
    dlg = CollabDialog(main)
    dlg.exec()
    return dlg
