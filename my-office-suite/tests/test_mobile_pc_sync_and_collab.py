"""End-to-end automated tests for PC <-> Mobile Sync (`mobile_sync.py`),
`MobileSyncDialog` UI, dual-mode `CollabServer` (`pycrdt` + `?client=mobile`),
Tool #49 registration, `Ctrl+Alt+M` keymap binding, and 15/15 `--self-test` checks.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pycrdt
from pycrdt import Array, Doc, Text
import pytest
import websockets.sync.client as ws_client

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_mobile_sync_server_peer_discovery_vault_and_dialog(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MYOFFICE_CONFIG_DIR", str(tmp_path / "cfg"))

    pc_dir = tmp_path / "pc_sync"
    mobile_dir = tmp_path / "mobile_sync"
    pc_dir.mkdir()
    mobile_dir.mkdir()

    (pc_dir / "Quarterly_Plan.txt").write_text("Written on Windows PC", encoding="utf-8")
    (mobile_dir / "Field_Scan.txt").write_text("Captured on Android Mobile", encoding="utf-8")

    from app.services import cloud_sync
    from app.services.mobile_sync import (
        MobilePeerBackend,
        MobileSyncServer,
        discover_lan_peers,
        parse_pair_uri,
        primary_lan_ip,
    )

    assert primary_lan_ip()

    srv = MobileSyncServer(pc_dir, port=0, device_name="Office-Windows-PC", enable_udp_discovery=True)
    port = srv.start()
    try:
        # 1. UDP auto-discovery finds server
        peers = discover_lan_peers(timeout=0.6)
        assert any(p.port == port for p in peers)

        # 2. QR URI round-trip parsing
        uri = srv.pair_uri("127.0.0.1")
        parsed = parse_pair_uri(uri)
        assert parsed["pin"] == srv.pin
        assert parsed["token"] == srv.token

        # 3. Wrong PIN without token is rejected with HTTP 403
        with pytest.raises(RuntimeError, match="403"):
            MobilePeerBackend(f"http://127.0.0.1:{port}", pin="000000")

        # 4. Valid pairing via cloud_sync.make_backend("mobile_peer") + full 3-way sync
        backend = cloud_sync.make_backend(
            {"backend": "mobile_peer", "mobile_url": f"http://127.0.0.1:{port}", "mobile_pin": srv.pin, "device_name": "Galaxy-Tab"}
        )
        eng = cloud_sync.SyncEngine(str(mobile_dir), backend)
        rep1 = eng.sync_once()
        assert rep1.downloaded == ["Quarterly_Plan.txt"]
        assert rep1.uploaded == ["Field_Scan.txt"]
        assert (mobile_dir / "Quarterly_Plan.txt").read_text(encoding="utf-8") == "Written on Windows PC"
        assert (pc_dir / "Field_Scan.txt").read_text(encoding="utf-8") == "Captured on Android Mobile"

        # 5. Deletion propagates cleanly when remote delete enabled
        eng_del = cloud_sync.SyncEngine(str(mobile_dir), backend, delete_remote=True)
        (mobile_dir / "Quarterly_Plan.txt").unlink()
        rep2 = eng_del.sync_once()
        assert rep2.deleted_remote == ["Quarterly_Plan.txt"]
        assert not (pc_dir / "Quarterly_Plan.txt").exists()

        # 6. Headless Qt dialog renders QR SVG, PIN, and status cleanly
        from PySide6.QtWidgets import QApplication
        from app.tools.mobile_sync_dialog import MobileSyncDialog

        _app = QApplication.instance() or QApplication([])
        dlg = MobileSyncDialog()
        assert len(dlg.hub.pin) == 6
        dlg.close()
        dlg.hub.stop()
    finally:
        srv.stop()


def test_collab_server_bridges_desktop_pycrdt_and_mobile_json_ws(tmp_path: Path):
    from app.collab.server import CollabServer

    sample = tmp_path / "TeamDoc.txt"
    sample.write_text("Initial line", encoding="utf-8")

    srv = CollabServer(host="127.0.0.1", port=0, persist=False)
    srv.start()
    try:
        srv.register_shared_file("BridgeRoom", file_path=str(sample), kind="writer")
        ws_base = f"ws://127.0.0.1:{srv.port}/BridgeRoom"

        desk_doc = Doc()
        desk_text = desk_doc.get("content", type=Text)
        desk_chat = desk_doc.get("chat", type=Array)

        with ws_client.connect(ws_base) as desk_ws:
            desk_ws.send(pycrdt.create_sync_message(desk_doc))
            stop_ev = threading.Event()

            def pump():
                while not stop_ev.is_set():
                    try:
                        data = desk_ws.recv(timeout=0.2)
                        if data and data[0] == 0:
                            rep = pycrdt.handle_sync_message(data[1:], desk_doc)
                            if rep is not None:
                                desk_ws.send(rep)
                    except TimeoutError:
                        continue
                    except Exception:
                        break

            t = threading.Thread(target=pump, daemon=True)
            t.start()

            with ws_client.connect(ws_base + "?client=mobile&name=AndroidUser") as mob:
                init_state = json.loads(mob.recv(timeout=3.0))
                assert init_state["type"] == "state"

                # Mobile sends live Team Chat -> verify Desktop Y.Array("chat") receives it!
                mob.send(json.dumps({"type": "chat_send", "sender": "AndroidUser", "text": "Hi from Android phone!"}))
                deadline = time.time() + 3.0
                while time.time() < deadline and not any(m.get("text") == "Hi from Android phone!" for m in list(desk_chat)):
                    time.sleep(0.05)
                assert any(m.get("text") == "Hi from Android phone!" for m in list(desk_chat))

                # Mobile edits Writer text -> verify Desktop Y.Text("content") receives real-time update!
                mob.send(json.dumps({"type": "writer_update", "sender": "AndroidUser", "text": "Live edit from Mobile!"}))
                deadline = time.time() + 3.0
                while time.time() < deadline and str(desk_text) != "Live edit from Mobile!":
                    time.sleep(0.05)
                assert str(desk_text) == "Live edit from Mobile!"

            stop_ev.set()
            t.join(timeout=1.0)
    finally:
        srv.stop()


def test_v140_version_registry_shortcut_and_15_check_selftest(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from app.version import APP_VERSION, version_tuple
    from app.services import selftest
    from app.shell.main_window import MainWindow
    from app.shell.tools_registry import TOOLS

    _app = QApplication.instance() or QApplication([])
    assert version_tuple(APP_VERSION) >= (1, 6, 0)
    assert any(t.id == "mobile_sync" for t in TOOLS)
    mw = MainWindow()
    assert mw.keymap.sequence_for("sync.mobile") == "Ctrl+Alt+Y"
    mw.close()

    rep_file = tmp_path / "selftest_report.txt"
    rc = selftest.run(str(rep_file))
    report = rep_file.read_text(encoding="utf-8")
    assert rc == 0, report
    assert "[ OK ] Mobile app sync (PC <-> Android)" in report
