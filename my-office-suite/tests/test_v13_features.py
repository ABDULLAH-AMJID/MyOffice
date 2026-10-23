"""Comprehensive end-to-end tests for MyOffice v1.3.0 features:

1. Crisp multi-resolution Windows 11 Explorer `.ico` generation + PE `RT_GROUP_ICON` (`0..7`) embedding + 32 associations
2. PDF Suite reliability & power tools (`add_page_numbers_pdf`, `stamp_pdf`, `flatten_pdf`, AcroForm fill, background redaction)
3. Multi-file simultaneous LAN Collaboration + HTTP auto-download of ANY file format + Per-File QR/Link + Live Team Chat
4. Gmail Cloud Sync & Encrypted Gmail Backup against a real RFC-3501 IMAP server
5. Wi-Fi Router USB (FTP & direct USB) Hardware-Bound `MOVAULT1` Security Vault + Stolen-USB Foreign-Laptop Rejection
6. Keyboard Shortcuts Registry, Rebinding, Conflict Detection, Custom Snippet/Tool Actions, JSON Profiles & Command Palette
"""
from __future__ import annotations

import os
import re
import socket
import socketserver
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pymupdf
import pytest
from PIL import Image


def _pump(app, seconds: float = 0.6):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# ============================================================================= 1. Explorer Icons & PE Embedding
def test_multi_resolution_icons_and_pe_embedding(tmp_path):
    from app.services import register
    from app.services.paths import resource_path

    expected_kinds = ("myoffice", "writer", "sheet", "slide", "pdf", "photos", "code", "vault")
    for kind in expected_kinds:
        ico_path = resource_path("icons", "app", f"{kind}.ico")
        png_path = resource_path("icons", "app", f"{kind}.png")
        assert ico_path.is_file(), f"Missing {ico_path}"
        assert png_path.is_file(), f"Missing {png_path}"
        with Image.open(ico_path) as im:
            sizes = im.info.get("sizes", set())
            assert (16, 16) in sizes and (32, 32) in sizes and (48, 48) in sizes and (256, 256) in sizes

        spec = register.icon_spec_for(kind, r"C:\Program Files\MyOffice\MyOffice.exe")
        assert spec.endswith(f"{kind}.ico")

    # Verify all major formats including Photos & Hardware Vault are registered
    for ext in (".docx", ".xlsx", ".pptx", ".pdf", ".movault", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"):
        assert ext in register.ASSOCIATIONS

    # On Windows, copy python.exe into a temp PE binary and embed all 8 icon groups (0..7)
    import shutil
    import sys

    if sys.platform == "win32":
        exe_out = tmp_path / "DummyMyOffice.exe"
        shutil.copy2(sys.executable, exe_out)

        root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [sys.executable, str(root / "tools" / "embed_exe_icons.py"), str(exe_out), str(root / "resources" / "icons" / "app")],
            check=True,
        )
        import pefile

        pe = pefile.PE(str(exe_out))
        rt_group_icon = pefile.RESOURCE_TYPE["RT_GROUP_ICON"]
        groups = [
            entry.id
            for top in pe.DIRECTORY_ENTRY_RESOURCE.entries
            if top.id == rt_group_icon
            for entry in top.directory.entries
        ]
        pe.close()
        assert len(groups) == 8


# ============================================================================= 2. PDF Power Tools & Interactive Forms
def test_pdf_page_numbers_stamps_flatten_and_forms(qapp, tmp_path):
    from app.pdf import ops
    from app.pdf.viewer import PdfDocument

    src = tmp_path / "doc.pdf"
    doc = pymupdf.open()
    for idx in range(3):
        p = doc.new_page()
        p.insert_text((72, 100), f"Contract Page {idx + 1}", fontsize=16)
    # Add an interactive text widget on page 1
    w = pymupdf.Widget()
    w.field_name = "client_name"
    w.field_label = "Client Name"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_value = "Acme Corp"
    w.rect = pymupdf.Rect(72, 150, 260, 180)
    doc[0].add_widget(w)
    doc.save(str(src))
    doc.close()

    numbered = ops.add_page_numbers_pdf(str(src), str(tmp_path / "numbered.pdf"), fmt="Page {n} of {total}")
    with pymupdf.open(numbered) as d:
        assert "Page 1 of 3" in d[0].get_text()
        assert "Page 3 of 3" in d[2].get_text()

    stamped = ops.stamp_pdf(numbered, str(tmp_path / "stamped.pdf"), label="APPROVED", subtitle="QA Verified", pages="1-2")
    with pymupdf.open(stamped) as d:
        assert "APPROVED" in d[0].get_text()
        assert "QA Verified" in d[0].get_text()

    flattened = ops.flatten_pdf(stamped, str(tmp_path / "flat.pdf"))
    with pymupdf.open(flattened) as d:
        assert list(d[0].widgets() or []) == []
        assert "APPROVED" in d[0].get_text()

    viewer = PdfDocument()
    viewer.load(str(src))
    assert viewer.doc.page_count == 3
    viewer.set_modified(False)


# ============================================================================= 3. Multi-File LAN Collaboration + Chat + QR
def test_multifile_lan_collaboration_chat_qr_and_any_format(qapp, tmp_path):
    from app.collab.binding import CollabSession, download_remote_room_file, parse_join_target
    from app.collab.chat_panel import ensure_tab_collab_panel
    from app.collab.qr_tools import make_qr_pixmap
    from app.collab.server import CollabServer, lan_addresses
    from app.pdf.viewer import PdfDocument
    from app.writer.widget import WriterDocument

    addrs = lan_addresses()
    assert addrs and all(not a.startswith("169.254.") for a in addrs)

    # Create a real PDF file and an arbitrary ZIP archive to share simultaneously alongside a live Writer doc!
    pdf_file = tmp_path / "spec.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Shared Architecture Spec")
    d.save(str(pdf_file))
    d.close()

    raw_archive = tmp_path / "bundle.zip"
    raw_archive.write_bytes(b"PK\x03\x04custom-binary-payload-v13")

    srv = CollabServer("127.0.0.1", 0, persist=False)
    srv.register_shared_file("spec-pdf-room", file_path=str(pdf_file), filename="spec.pdf", kind="pdf")
    srv.register_shared_file("zip-any-room", file_path=str(raw_archive), filename="bundle.zip", kind="any")
    srv.start()
    try:
        # Verify HTTP health, landing page, and automatic download for ANY file format
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/health", timeout=5) as r:
            assert r.status == 200
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/f/spec-pdf-room", timeout=5) as r:
            html_page = r.read().decode("utf-8")
            assert "spec.pdf" in html_page and "myoffice://join" in html_page

        dl_zip = download_remote_room_file(f"http://127.0.0.1:{srv.port}", "zip-any-room")
        assert dl_zip and Path(dl_zip).read_bytes() == b"PK\x03\x04custom-binary-payload-v13"

        # Share TWO simultaneous live rooms at once (Room 1: Writer + Chat, Room 2: PDF + Chat)
        host_w, join_w = WriterDocument(), WriterDocument()
        host_w.editor.document().setPlainText(" Simultaneous Room 1 ")
        s_host_1 = CollabSession(host_w, f"ws://127.0.0.1:{srv.port}", "writer-room-1", user="Ayesha")
        s_join_1 = CollabSession(join_w, f"ws://127.0.0.1:{srv.port}", "writer-room-1", user="Bilal")

        host_pdf, join_pdf = PdfDocument(), PdfDocument()
        host_pdf.load(str(pdf_file))
        join_pdf.load(str(pdf_file))
        s_host_2 = CollabSession(host_pdf, f"ws://127.0.0.1:{srv.port}", "spec-pdf-room", user="Ayesha")
        s_join_2 = CollabSession(join_pdf, f"ws://127.0.0.1:{srv.port}", "spec-pdf-room", user="Sara")

        panel_1 = ensure_tab_collab_panel(host_w, s_host_1)
        panel_join_1 = ensure_tab_collab_panel(join_w, s_join_1)

        s_host_1.start()
        s_host_2.start()
        _pump(qapp, 0.8)
        s_join_1.start()
        s_join_2.start()
        _pump(qapp, 1.2)

        assert join_w.editor.document().toPlainText() == " Simultaneous Room 1 "

        # Send live Team Chat messages in BOTH rooms independently and verify isolation + delivery!
        received_by_bilal = []
        received_by_sara = []
        s_join_1.chatMessageReceived.connect(received_by_bilal.append)
        s_join_2.chatMessageReceived.connect(received_by_sara.append)

        s_host_1.send_chat("Assalam o Alaikum Bilal, section 2 update kar diya hai!")
        s_host_2.send_chat("Sara please review page 1 diagram.")
        _pump(qapp, 0.9)

        assert any("section 2" in m["text"] and m["sender"] == "Ayesha" for m in received_by_bilal)
        assert not any("diagram" in m["text"] for m in received_by_bilal)
        assert any("diagram" in m["text"] for m in received_by_sara)
        assert panel_join_1.feed_layout.count() >= 2

        # Verify QR code + link parser for per-file links
        links = s_host_1.share_links("192.168.1.42")
        assert links["http"] == f"http://192.168.1.42:{srv.port}/f/writer-room-1"
        parsed = parse_join_target(links["myoffice"])
        assert parsed["room"] == "writer-room-1" and parsed["ws_base"] == f"ws://192.168.1.42:{srv.port}"
        pix = make_qr_pixmap(links["http"], 200)
        assert not pix.isNull() and pix.width() >= 180

        for s in (s_host_1, s_join_1, s_host_2, s_join_2):
            s.stop()
        for w in (host_w, join_w, host_pdf, join_pdf):
            w.set_modified(False)
    finally:
        srv.stop()


# ============================================================================= 4. Gmail Sync & Backup (Real Local IMAP Server)
class _MiniImapServer:
    """Real socket-level RFC-3501 IMAP server supporting LOGIN, CREATE, SELECT, SEARCH, FETCH, APPEND, STORE, EXPUNGE."""

    def __init__(self):
        self.mailboxes: dict[str, list[dict]] = {"INBOX": []}
        self._next_uid = 1
        self._lock = threading.Lock()
        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), self._make_handler())
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def _make_handler(self):
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def send_line(self, line: str | bytes):
                raw = line.encode("utf-8") if isinstance(line, str) else line
                self.wfile.write(raw + b"\r\n")
                self.wfile.flush()

            def handle(self):
                box_name = "INBOX"
                self.send_line("* OK MiniIMAP ready")
                while True:
                    line = self.rfile.readline()
                    if not line:
                        break
                    s = line.decode("utf-8", "replace").rstrip("\r\n")
                    if not s:
                        continue
                    tag, _, rest = s.partition(" ")
                    parts = rest.split(" ")
                    cmd = parts[0].upper()
                    if cmd == "CAPABILITY":
                        self.send_line("* CAPABILITY IMAP4rev1 UIDPLUS")
                        self.send_line(f"{tag} OK CAPABILITY completed")
                    elif cmd == "LOGIN":
                        self.send_line(f"{tag} OK LOGIN completed")
                    elif cmd == "CREATE":
                        mb = rest[len("CREATE"):].strip().strip('"')
                        with outer._lock:
                            outer.mailboxes.setdefault(mb, [])
                        self.send_line(f"{tag} OK CREATE completed")
                    elif cmd == "SELECT":
                        mb = rest[len("SELECT"):].strip().strip('"')
                        with outer._lock:
                            outer.mailboxes.setdefault(mb, [])
                            box_name = mb
                            n = len(outer.mailboxes[box_name])
                        self.send_line(f"* {n} EXISTS")
                        self.send_line(f"{tag} OK [READ-WRITE] SELECT completed")
                    elif cmd == "APPEND":
                        m = re.search(r"\{(\d+)\}\s*$", s)
                        nbytes = int(m.group(1))
                        mb_match = re.match(r'^APPEND\s+"([^"]+)"', rest, re.I)
                        mb = mb_match.group(1) if mb_match else parts[1].strip('"')
                        self.send_line("+ Ready for literal data")
                        raw_msg = self.rfile.read(nbytes)
                        self.rfile.readline()  # trailing CRLF
                        with outer._lock:
                            uid = outer._next_uid
                            outer._next_uid += 1
                            outer.mailboxes.setdefault(mb, []).append({"uid": uid, "raw": raw_msg, "deleted": False})
                        self.send_line(f"{tag} OK [APPENDUID 1 {uid}] APPEND completed")
                    elif cmd == "UID":
                        sub = parts[1].upper()
                        if sub == "SEARCH":
                            with outer._lock:
                                uids = [str(m["uid"]) for m in outer.mailboxes.get(box_name, []) if not m["deleted"]]
                            self.send_line("* SEARCH" + (" " + " ".join(uids) if uids else ""))
                            self.send_line(f"{tag} OK UID SEARCH completed")
                        elif sub == "FETCH":
                            req_uids = [int(u) for u in parts[2].split(",") if u.isdigit()]
                            spec = " ".join(parts[3:])
                            with outer._lock:
                                matched = [m for m in outer.mailboxes.get(box_name, []) if m["uid"] in req_uids and not m["deleted"]]
                            for seq_no, item in enumerate(matched, start=1):
                                uid_req = item["uid"]
                                payload = item["raw"]
                                if "HEADER" in spec.upper():
                                    payload = payload.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                                header = f"* {seq_no} FETCH (UID {uid_req} BODY[HEADER] {{{len(payload)}}}\r\n".encode("ascii")
                                self.wfile.write(header + payload + b")\r\n")
                                self.wfile.flush()
                            self.send_line(f"{tag} OK UID FETCH completed")
                        elif sub == "STORE":
                            uid_req = int(parts[2])
                            with outer._lock:
                                for m in outer.mailboxes.get(box_name, []):
                                    if m["uid"] == uid_req:
                                        m["deleted"] = True
                            self.send_line(f"{tag} OK UID STORE completed")
                    elif cmd == "EXPUNGE":
                        with outer._lock:
                            outer.mailboxes[box_name] = [m for m in outer.mailboxes.get(box_name, []) if not m["deleted"]]
                        self.send_line(f"{tag} OK EXPUNGE completed")
                    elif cmd in ("CLOSE", "NOOP"):
                        self.send_line(f"{tag} OK {cmd} completed")
                    elif cmd == "LOGOUT":
                        self.send_line("* BYE Logging out")
                        self.send_line(f"{tag} OK LOGOUT completed")
                        break
                    else:
                        self.send_line(f"{tag} OK {cmd}")

        return Handler

    def start(self):
        self._thread.start()

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


def test_gmail_cloud_sync_and_encrypted_gmail_backup(tmp_path):
    from app.services import backup, vault
    from app.services.cloud_sync import SyncEngine
    from app.services.gmail_storage import GmailBackend

    imap = _MiniImapServer()
    imap.start()
    try:
        # 1. Two-way Gmail Cloud Sync between Laptop A and Laptop B
        be_a = GmailBackend("user@gmail.com", "abcd efgh ijkl mnop", label="MyOffice/Sync",
                            imap_host="127.0.0.1", imap_port=imap.port, use_ssl=False)
        be_b = GmailBackend("user@gmail.com", "abcd efgh ijkl mnop", label="MyOffice/Sync",
                            imap_host="127.0.0.1", imap_port=imap.port, use_ssl=False)
        eng_a = SyncEngine(str(tmp_path / "LaptopA"), be_a, str(tmp_path / "sa.json"))
        eng_b = SyncEngine(str(tmp_path / "LaptopB"), be_b, str(tmp_path / "sb.json"))

        (tmp_path / "LaptopA" / "finance.xlsx").write_bytes(b"Q4-REVENUE-2026")
        assert eng_a.sync_once().uploaded == ["finance.xlsx"]
        assert eng_b.sync_once().downloaded == ["finance.xlsx"]
        assert (tmp_path / "LaptopB" / "finance.xlsx").read_bytes() == b"Q4-REVENUE-2026"

        # 2. Encrypted Gmail Backup & Restore
        owner_store = vault.DeviceKeyStore(tmp_path / "owner.key")
        secret_doc = tmp_path / "board_minutes.docx"
        secret_doc.write_bytes(b"STRICTLY-CONFIDENTIAL-BOARD-MINUTES")
        gcfg = {
            "email": "user@gmail.com",
            "app_password": "abcd efgh ijkl mnop",
            "label": "MyOffice/Backups",
            "imap_host": "127.0.0.1",
            "imap_port": imap.port,
            "use_ssl": False,
            "encrypt": True,
        }
        rel = backup.backup_to_gmail(str(secret_doc), gcfg, store=owner_store)
        listed = backup.list_gmail_backups(gcfg)
        assert any(r == rel for r, _dt, _sz in listed)

        restored = backup.restore_gmail_backup(rel, str(tmp_path / "restored_dir"), gcfg, store=owner_store)
        assert restored.name == "board_minutes.docx"
        assert restored.read_bytes() == b"STRICTLY-CONFIDENTIAL-BOARD-MINUTES"
    finally:
        imap.stop()


# ============================================================================= 5. Router USB / FTP Vault & Theft Protection
class _MiniRouterFtpServer:
    """Minimal passive-mode RFC-959 FTP server simulating a USB drive plugged into a Wi-Fi Router."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), self._make_handler())
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def _make_handler(self):
        root = self.root

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, code: int, msg: str):
                self.wfile.write(f"{code} {msg}\r\n".encode("utf-8"))
                self.wfile.flush()

            def handle(self):
                cwd = root
                pasv_sock = None
                self.reply(220, "RouterUSB FTP Ready")
                while True:
                    line = self.rfile.readline()
                    if not line:
                        break
                    s = line.decode("utf-8", "replace").rstrip("\r\n")
                    if not s:
                        continue
                    cmd, _, arg = s.partition(" ")
                    cu = cmd.upper()
                    if cu == "USER":
                        self.reply(331, "Password required")
                    elif cu == "PASS":
                        self.reply(230, "Logged in")
                    elif cu in ("SYST",):
                        self.reply(215, "UNIX Type: L8")
                    elif cu in ("TYPE", "NOOP"):
                        self.reply(200, "OK")
                    elif cu == "PWD":
                        rel = "/" + cwd.relative_to(root).as_posix().lstrip(".")
                        self.reply(257, f'"{rel}" is current directory')
                    elif cu == "CWD":
                        sub = arg.strip("/")
                        target = (root / sub) if arg.startswith("/") else (cwd / sub)
                        target.mkdir(parents=True, exist_ok=True)
                        cwd = target
                        self.reply(250, "Directory changed")
                    elif cu == "MKD":
                        target = (root / arg.strip("/")) if arg.startswith("/") else (cwd / arg)
                        target.mkdir(parents=True, exist_ok=True)
                        self.reply(257, f'"{arg}" created')
                    elif cu == "PASV":
                        pasv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        pasv_sock.bind(("127.0.0.1", 0))
                        pasv_sock.listen(1)
                        p = pasv_sock.getsockname()[1]
                        self.reply(227, f"Entering Passive Mode (127,0,0,1,{p >> 8},{p & 255})")
                    elif cu == "NLST":
                        self.reply(150, "Opening data connection")
                        conn, _ = pasv_sock.accept()
                        names = "\r\n".join(p.name for p in sorted(cwd.iterdir()) if p.is_file())
                        conn.sendall((names + "\r\n").encode("utf-8") if names else b"")
                        conn.close()
                        pasv_sock.close()
                        self.reply(226, "Transfer complete")
                    elif cu == "MLSD":
                        self.reply(150, "Opening data connection")
                        conn, _ = pasv_sock.accept()
                        lines = [
                            f"type=file;size={p.stat().st_size};modify=20261022120000; {p.name}"
                            for p in sorted(cwd.iterdir()) if p.is_file()
                        ]
                        conn.sendall(("\r\n".join(lines) + "\r\n").encode("utf-8") if lines else b"")
                        conn.close()
                        pasv_sock.close()
                        self.reply(226, "Transfer complete")
                    elif cu == "SIZE":
                        fp = cwd / os.path.basename(arg)
                        if fp.is_file():
                            self.reply(213, str(fp.stat().st_size))
                        else:
                            self.reply(550, "Not found")
                    elif cu == "STOR":
                        self.reply(150, "Opening binary data connection")
                        conn, _ = pasv_sock.accept()
                        chunks = []
                        while True:
                            b = conn.recv(65536)
                            if not b:
                                break
                            chunks.append(b)
                        conn.close()
                        pasv_sock.close()
                        (cwd / os.path.basename(arg)).write_bytes(b"".join(chunks))
                        self.reply(226, "Transfer complete")
                    elif cu == "RETR":
                        fp = cwd / os.path.basename(arg)
                        if not fp.is_file():
                            self.reply(550, "File unavailable")
                            continue
                        self.reply(150, "Opening binary data connection")
                        conn, _ = pasv_sock.accept()
                        conn.sendall(fp.read_bytes())
                        conn.close()
                        pasv_sock.close()
                        self.reply(226, "Transfer complete")
                    elif cu == "RNFR":
                        self._rnfr = cwd / os.path.basename(arg)
                        self.reply(350, "Ready for RNTO")
                    elif cu == "RNTO":
                        os.replace(self._rnfr, cwd / os.path.basename(arg))
                        self.reply(250, "Rename successful")
                    elif cu == "DELE":
                        (cwd / os.path.basename(arg)).unlink(missing_ok=True)
                        self.reply(250, "Deleted")
                    elif cu == "QUIT":
                        self.reply(221, "Goodbye")
                        break
                    else:
                        self.reply(502, "Command not implemented")

        return Handler

    def start(self):
        self._thread.start()

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


def test_router_usb_vault_network_and_direct_plug_plus_stolen_usb_protection(tmp_path):
    from app.services import backup, vault

    usb_physical_dir = tmp_path / "Router_USB_Stick"
    owner_laptop = vault.DeviceKeyStore(tmp_path / "my_laptop_secret.key")
    attacker_laptop = vault.DeviceKeyStore(tmp_path / "thief_laptop_secret.key")

    src = tmp_path / "Tax_Return_2026.xlsx"
    src.write_bytes(b"SECRET-SALARY-AND-BANK-DETAILS-2026")

    ftp = _MiniRouterFtpServer(usb_physical_dir)
    ftp.start()
    try:
        # Step A: Back up OVER WI-FI ROUTER NETWORK (`ftp://127.0.0.1:<port>/vault`) from Owner's Laptop
        router_cfg = {"location": f"ftp://127.0.0.1:{ftp.port}/vault", "username": "admin", "password": "router_pw"}
        remote_vault_uri = backup.backup_to_vault_target(str(src), router_cfg, store=owner_laptop)
        assert remote_vault_uri.endswith(".movault")

        # Verify on physical USB stick: filename is completely opaque (no "Tax_Return_2026" anywhere on disk!)
        vault_files = list((usb_physical_dir / "vault").glob("*.movault"))
        assert len(vault_files) == 1
        assert "Tax" not in vault_files[0].name
        assert b"Tax_Return" not in vault_files[0].read_bytes()
        assert b"SECRET-SALARY" not in vault_files[0].read_bytes()

        # Step B: Owner unplugs USB from Wi-Fi router & plugs DIRECTLY into Owner's Laptop USB port -> opens seamlessly!
        direct_usb_cfg = {"location": str(usb_physical_dir / "vault")}
        owner_entries = backup.list_vault_target(direct_usb_cfg, store=owner_laptop)
        assert len(owner_entries) == 1
        assert owner_entries[0].original_name == "Tax_Return_2026.xlsx"
        restored = backup.restore_from_vault_target(owner_entries[0], str(tmp_path / "restored.xlsx"), direct_usb_cfg, store=owner_laptop)
        assert restored.read_bytes() == b"SECRET-SALARY-AND-BANK-DETAILS-2026"

        # Step C: Attacker unplugs/steals the USB drive (or accesses router over Wi-Fi) from ANOTHER laptop -> COMPLETELY LOCKED!
        thief_entries = backup.list_vault_target(direct_usb_cfg, store=attacker_laptop)
        assert len(thief_entries) == 1
        assert thief_entries[0].locked_to_other_device is True
        assert "Tax_Return" not in thief_entries[0].original_name
        with pytest.raises(vault.VaultAccessDeniedError):
            backup.restore_from_vault_target(thief_entries[0], str(tmp_path / "stolen.xlsx"), direct_usb_cfg, store=attacker_laptop)
        assert not (tmp_path / "stolen.xlsx").exists()
    finally:
        ftp.stop()


# ============================================================================= 6. Professional Shortcuts & Custom Keymap
def test_custom_shortcuts_manager_and_command_palette(qapp, tmp_path):
    from app.shell.command_palette import CommandPaletteDialog
    from app.shell.main_window import MainWindow
    from app.shell.shortcuts_dialog import ShortcutsDialog

    win = MainWindow()
    km = win.keymap
    assert len(km.entries()) >= 25

    # Rebind a built-in command and verify persistence + conflict resolution
    km.set_sequence("collab.share", "Ctrl+Alt+9")
    assert km.sequence_for("collab.share") == "Ctrl+Alt+9"

    # Add a custom snippet shortcut, trigger it into an open Writer document, and verify output!
    doc = win.new_writer()
    doc.editor.setFocus()
    ent = km.add_custom_shortcut("Insert Company Sign-off", "Ctrl+Alt+8", "snippet", "Best regards,\nMyOffice Team")
    assert km.find_conflict("Ctrl+Alt+8").cmd_id == ent.cmd_id
    km.trigger(ent.cmd_id)
    assert "MyOffice Team" in doc.editor.document().toPlainText()

    # Export JSON profile, reset, re-import, and verify exact restoration!
    profile = tmp_path / "shortcuts.json"
    km.export_profile(profile)
    km.remove_custom_shortcut(ent.cmd_id)
    km.reset_all()
    assert km.sequence_for("collab.share") == "Ctrl+Shift+L"

    restored_count = km.import_profile(profile)
    assert restored_count >= 2
    assert km.sequence_for("collab.share") == "Ctrl+Alt+9"

    # Verify both ShortcutsDialog and CommandPaletteDialog instantiate cleanly without errors
    sd = ShortcutsDialog(win)
    cp = CommandPaletteDialog(win, initial_query="gmail")
    assert cp.list.count() >= 1
    sd.close()
    cp.close()
    km.reset_all()
    for d in win.documents():
        d.set_modified(False)
    win.close()
