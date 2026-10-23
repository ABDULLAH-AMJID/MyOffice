"""Per-file Live Team Chat & Share/QR side panel embedded inside every collaborated document tab."""
from __future__ import annotations

import datetime as dt
import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.collab.binding import CollabSession
from app.collab.qr_tools import make_qr_pixmap
from app.collab.server import lan_addresses
from app.services.i18n import _t


class ShareQrPopup(QDialog):
    """Crisp QR code + LAN IP switcher + one-click Copy Link popup for THIS specific file room."""

    def __init__(self, session: CollabSession, parent=None):
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(_t("Share File & Scan QR Code"))
        self.setMinimumWidth(430)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        title = QLabel(f"<b>{html.escape(session.doc_widget.display_name())}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel(_t("Wi-Fi / LAN IP:")))
        self.ip_combo = QComboBox()
        for ip in lan_addresses():
            self.ip_combo.addItem(ip, ip)
        self.ip_combo.setEditable(True)
        self.ip_combo.currentTextChanged.connect(self._refresh)
        ip_row.addWidget(self.ip_combo, 1)
        v.addLayout(ip_row)

        self.qr_lbl = QLabel()
        self.qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_lbl.setMinimumHeight(230)
        v.addWidget(self.qr_lbl)

        self.http_edit = QLineEdit()
        self.http_edit.setReadOnly(True)
        v.addWidget(QLabel(_t("Universal Browser & Join Link (opens on Phone or Desktop):")))
        row1 = QHBoxLayout()
        row1.addWidget(self.http_edit, 1)
        b_copy_http = QPushButton(_t("Copy Link"))
        b_copy_http.setProperty("accent", True)
        b_copy_http.clicked.connect(lambda: self._copy(self.http_edit.text(), b_copy_http))
        row1.addWidget(b_copy_http)
        v.addLayout(row1)

        self.app_edit = QLineEdit()
        self.app_edit.setReadOnly(True)
        v.addWidget(QLabel(_t("Direct 1-Click MyOffice App Link (`myoffice://`):")))
        row2 = QHBoxLayout()
        row2.addWidget(self.app_edit, 1)
        b_copy_app = QPushButton(_t("Copy App Link"))
        b_copy_app.clicked.connect(lambda: self._copy(self.app_edit.text(), b_copy_app))
        row2.addWidget(b_copy_app)
        v.addLayout(row2)

        b_close = QPushButton(_t("Done"))
        b_close.clicked.connect(self.accept)
        v.addWidget(b_close, 0, Qt.AlignmentFlag.AlignRight)
        self._refresh()

    def _copy(self, txt: str, btn: QPushButton):
        QGuiApplication.clipboard().setText(txt)
        old = btn.text()
        btn.setText(_t("Copied \u2713"))
        QTimer.singleShot(1500, lambda: btn.setText(old))

    def _refresh(self):
        links = self.session.share_links(self.ip_combo.currentText().strip() or None)
        self.http_edit.setText(links["http"])
        self.app_edit.setText(links["myoffice"])
        self.qr_lbl.setPixmap(make_qr_pixmap(links["http"], 216))


class CollabSidePanel(QFrame):
    """Embedded right-hand live Team Chat + Peers + Share/QR panel inside any collaborated file tab."""

    def __init__(self, session: CollabSession, on_stop=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.on_stop = on_stop
        self.setObjectName("CollabSidePanel")
        self.setMinimumWidth(295)
        self.setMaximumWidth(370)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Top header: live indicator + Share/QR + Disconnect
        hdr = QHBoxLayout()
        self.status_lbl = QLabel(f"<b>\u25cf Live</b> &middot; {html.escape(session.room)}")
        self.status_lbl.setStyleSheet("color: #16a34a;")
        hdr.addWidget(self.status_lbl, 1)

        btn_share = QPushButton(_t("QR / Link"))
        btn_share.setProperty("accent", True)
        btn_share.setToolTip(_t("Show QR code and copyable join link for this file"))
        btn_share.clicked.connect(self.show_share_qr)
        hdr.addWidget(btn_share)

        btn_leave = QPushButton(_t("Leave"))
        btn_leave.setToolTip(_t("Stop collaborating on this file"))
        btn_leave.clicked.connect(self._leave)
        hdr.addWidget(btn_leave)
        root.addLayout(hdr)

        # Connected peers bar
        self.peers_lbl = QLabel("")
        self.peers_lbl.setWordWrap(True)
        self.peers_lbl.setObjectName("Muted")
        root.addWidget(self.peers_lbl)

        # Scrollable live chat messages feed
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.feed_host = QWidget()
        self.feed_layout = QVBoxLayout(self.feed_host)
        self.feed_layout.setContentsMargins(2, 2, 2, 2)
        self.feed_layout.setSpacing(6)
        self.feed_layout.addStretch(1)
        self.scroll.setWidget(self.feed_host)
        root.addWidget(self.scroll, 1)

        # Quick input row
        inp_row = QHBoxLayout()
        self.msg_edit = QLineEdit()
        self.msg_edit.setPlaceholderText(_t("Message everyone in this file\u2026"))
        self.msg_edit.returnPressed.connect(self._send)
        inp_row.addWidget(self.msg_edit, 1)
        btn_send = QPushButton(_t("Send"))
        btn_send.setProperty("accent", True)
        btn_send.clicked.connect(self._send)
        inp_row.addWidget(btn_send)
        root.addLayout(inp_row)

        # Wire session signals
        self._rendered_ids: set[str] = set()
        session.chatMessageReceived.connect(self._append_message)
        session.peersChanged.connect(self._update_peers)
        session.statusChanged.connect(self._update_status)
        self._update_peers(session.peer_list())
        for m in session.chat_history():
            self._append_message(m)

    def show_share_qr(self):
        dlg = ShareQrPopup(self.session, self)
        dlg.exec()

    def _update_status(self, text: str):
        is_live = text.startswith(("Live", "Connected"))
        color = "#16a34a" if is_live else "#d97706"
        self.status_lbl.setStyleSheet(f"color: {color};")
        self.status_lbl.setText(f"<b>\u25cf {html.escape(text)}</b>")

    def _update_peers(self, peers: list[dict]):
        chips = []
        for p in peers:
            c = p.get("color", "#2563EB")
            nm = html.escape(p.get("name", "Peer"))
            chips.append(f"<span style='color:{c};font-weight:600'>\u25cf {nm}</span>")
        self.peers_lbl.setText(_t("Online ({0}): ").format(len(peers)) + " &nbsp; ".join(chips))

    def _append_message(self, msg: dict):
        mid = msg.get("id", "")
        if mid and mid in self._rendered_ids:
            return
        if mid:
            self._rendered_ids.add(mid)
        sender = html.escape(msg.get("sender", "Peer"))
        color = msg.get("color", "#2563EB")
        body = html.escape(msg.get("text", "")).replace("\n", "<br>")
        ts_val = float(msg.get("ts") or 0)
        stamp = dt.datetime.fromtimestamp(ts_val).strftime("%H:%M") if ts_val else ""
        is_me = msg.get("sender_id") == self.session.client_id

        bubble = QFrame()
        bubble.setStyleSheet(
            "QFrame { border-radius: 10px; padding: 6px 8px; "
            + ("background: rgba(37, 99, 235, 0.13); border: 1px solid rgba(37, 99, 235, 0.30); }"
               if is_me else
               "background: rgba(148, 163, 184, 0.14); border: 1px solid rgba(148, 163, 184, 0.28); }")
        )
        bv = QVBoxLayout(bubble)
        bv.setContentsMargins(6, 4, 6, 4)
        bv.setSpacing(2)
        meta_lbl = QLabel(
            f"<span style='color:{color};font-weight:700;font-size:11px'>{sender}</span> "
            f"<span style='color:#64748b;font-size:10px'>{stamp}</span>"
        )
        txt_lbl = QLabel(body)
        txt_lbl.setWordWrap(True)
        txt_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bv.addWidget(meta_lbl)
        bv.addWidget(txt_lbl)

        idx = max(0, self.feed_layout.count() - 1)
        self.feed_layout.insertWidget(idx, bubble)
        QTimer.singleShot(25, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        ))

    def _send(self):
        txt = self.msg_edit.text().strip()
        if not txt:
            return
        self.msg_edit.clear()
        self.session.send_chat(txt)

    def _leave(self):
        if callable(self.on_stop):
            self.on_stop()
        else:
            self.session.stop()
            self.hide()


def ensure_tab_collab_panel(doc_widget, session: CollabSession, on_stop=None) -> CollabSidePanel:
    """Attach (or replace) the right-hand Live Team Chat & Share QR panel inside ANY document tab!"""
    old_panel = getattr(doc_widget, "_collab_panel", None)
    if old_panel is not None:
        old_panel.setParent(None)
        old_panel.deleteLater()

    panel = CollabSidePanel(session, on_stop=on_stop, parent=doc_widget)
    lay = doc_widget.layout()
    if isinstance(lay, QHBoxLayout):
        lay.addWidget(panel)
    elif lay is not None:
        # Wrap existing document layout alongside right-hand chat panel without touching any internal child widget!
        existing_widgets = []
        existing_layouts = []
        while lay.count():
            item = lay.takeAt(0)
            if item.widget() is not None:
                existing_widgets.append(item.widget())
            elif item.layout() is not None:
                existing_layouts.append(item.layout())
        body = QWidget(doc_widget)
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(0)
        for w in existing_widgets:
            body_v.addWidget(w)
        for sub in existing_layouts:
            body_v.addLayout(sub)
        split_row = QHBoxLayout()
        split_row.setContentsMargins(0, 0, 0, 0)
        split_row.setSpacing(0)
        split_row.addWidget(body, 1)
        split_row.addWidget(panel, 0)
        lay.addLayout(split_row)
    doc_widget._collab_panel = panel
    doc_widget._collab_session = session
    return panel
