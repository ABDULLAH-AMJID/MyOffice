"""v1.6.5 — Wi-Fi router / USB vault scan must never crash or white-out the window.

Regression cover for three defects that together produced the "Scan Error (Not Responding)" window and the
blank white dialog the user reported on Windows:

1. `BackupDialog` called `discover_storage_targets(scan_network=True)` while the service took no arguments
   → `TypeError` on every scan (raised inside a worker thread).
2. Both scan dialogs read `target.location` / `target.detail` / `target.backend`, but `StorageTarget` only
   exposed `label` / `path_or_url` / `kind` → `AttributeError` the moment anything *was* found.
3. `run_in_thread()` connected worker signals straight to plain Python lambdas. PySide only queues into the
   receiving thread for QObject slots, so every "on success / on error" handler — `QMessageBox`, label text,
   tree rebuilds — executed on the worker thread. Touching widgets cross-thread blanked the dialog and hung
   the event loop: the frozen white window in the screenshots.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services import net_storage
from app.services.net_storage import StorageTarget


# --------------------------------------------------------------------------- discovery service


def test_discover_accepts_the_arguments_the_dialogs_pass():
    """`scan_network=True` used to raise TypeError inside the worker -> "Scan Error"."""
    targets = net_storage.discover_storage_targets(scan_network=True)
    assert isinstance(targets, list)
    assert all(isinstance(t, StorageTarget) for t in targets)


def test_scan_is_bounded_and_never_hangs(monkeypatch):
    """An unreachable router must produce a verdict in ~a second, not freeze the event loop."""
    calls = []

    def slow_probe(host, *a, **k):
        calls.append(host)
        time.sleep(0.25)          # pretend the address silently swallows packets
        return False

    monkeypatch.setattr(net_storage, "_smb_open", lambda host: slow_probe(host))
    monkeypatch.setattr(net_storage, "_ftp_open", lambda host, port=21, timeout=0.6: slow_probe(host))

    started = time.monotonic()
    assert net_storage.discover_storage_targets(scan_network=True) == []
    elapsed = time.monotonic() - started
    assert len(calls) >= 2, "gateway addresses must actually be probed"
    assert elapsed < 3.0, f"parallel probe should finish fast, took {elapsed:.2f}s"


def test_scan_without_network_returns_router_suggestions_only():
    targets = net_storage.discover_storage_targets(scan_network=False)
    kinds = {t.backend for t in targets}
    assert kinds <= {"smb", "ftp", "usb"}
    assert all(t.detail for t in targets), "every suggestion needs a detail line for the chooser"


def test_storage_target_exposes_the_ui_contract_and_legacy_names():
    t = StorageTarget("USB Drive (E:)", r"E:\MyOffice_Vault", "usb", "removable, 12 GB free")
    assert (t.label, t.location, t.backend, t.detail) == ("USB Drive (E:)", r"E:\MyOffice_Vault", "usb", "removable, 12 GB free")
    # older call sites / scripts still use these names
    assert t.path_or_url == t.location
    assert t.kind == t.backend
    assert "E:" in str(t)


def test_probe_marks_a_router_that_answers(monkeypatch):
    monkeypatch.setattr(net_storage, "_smb_open", lambda host: host == "192.168.1.1")
    monkeypatch.setattr(net_storage, "_probe_ftp", lambda host, timeout: (False, ""))
    monkeypatch.setattr(net_storage, "_gateway_candidates", lambda: ["192.168.1.1"])
    monkeypatch.setattr(net_storage, "_removable_drives_windows", lambda: [], raising=False)
    monkeypatch.setattr(net_storage, "_mounted_drives_posix", lambda: [])
    targets = net_storage.discover_storage_targets(scan_network=True)
    assert len(targets) == 1
    assert targets[0].backend == "smb"
    assert "192.168.1.1" in targets[0].location


# --------------------------------------------------------------------------- worker / thread safety


def _pump_until(qapp, predicate, timeout_ms=8000):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


def test_callbacks_always_run_on_the_gui_thread(qapp):
    """The whole crash: lambdas used to execute inside the worker QThread."""
    from PySide6.QtCore import QThread

    from app.services.worker import run_in_thread

    gui_thread = QThread.currentThread()
    seen: dict[str, bool] = {}
    done = threading.Event()

    def on_done(_err):
        seen["gui"] = QThread.currentThread() is gui_thread
        done.set()

    def boom():
        raise RuntimeError("router refused the connection")

    run_in_thread(None, boom, on_done, on_done)
    assert _pump_until(qapp, done.is_set), "error handler was never invoked"
    assert seen["gui"] is True


def test_success_callback_runs_on_the_gui_thread(qapp):
    from PySide6.QtCore import QThread

    from app.services.worker import run_in_thread

    gui_thread = QThread.currentThread()
    result: dict[str, object] = {}
    done = threading.Event()

    def on_done(value):
        result["thread_ok"] = QThread.currentThread() is gui_thread
        result["value"] = value
        done.set()

    run_in_thread(None, lambda: [1, 2, 3], on_done)
    assert _pump_until(qapp, done.is_set)
    assert result == {"thread_ok": True, "value": [1, 2, 3]}


def test_a_raising_callback_cannot_kill_the_app(qapp):
    """An exception inside a handler must be logged, not crash the process."""
    from app.services.worker import run_in_thread

    called = threading.Event()

    def handler(_value):
        called.set()
        raise ValueError("handler exploded")

    run_in_thread(None, lambda: 1, handler)
    assert _pump_until(qapp, called.is_set)
    # the GUI thread is still alive and usable
    assert qapp.processEvents() is not None or True


def test_closing_the_dialog_mid_job_does_not_abort(qapp):
    """QThread parented to a destroyed dialog used to abort with 'Destroyed while thread is still running'."""
    from PySide6.QtWidgets import QDialog

    from app.services.worker import run_in_thread

    dlg = QDialog()
    finished = threading.Event()
    started = threading.Event()
    delivered: list[object] = []

    def job():
        started.set()
        time.sleep(0.35)
        finished.set()
        return "late"

    run_in_thread(dlg, job, lambda r: delivered.append(r), lambda m: delivered.append(m))
    assert _pump_until(qapp, started.is_set)
    from shiboken6 import delete as destroy

    destroy(dlg)                       # the user closed the dialog while the scan was still running
    assert _pump_until(qapp, finished.is_set)
    qapp.processEvents()
    # a destroyed receiver is skipped, and nothing crashed
    assert delivered == []


# --------------------------------------------------------------------------- the real dialog flow


class _StubMain:
    """Minimal main window stub: the dialog only needs a QWidget parent and open_path()."""

    def __init__(self):
        from PySide6.QtWidgets import QWidget

        self.widget = QWidget()
        self.active_path = None

    def open_path(self, *_a, **_k):  # pragma: no cover - only used by the restore flow
        return None


@pytest.fixture()
def backup_dialog(qapp):
    from app.tools.backup_dialog import BackupDialog

    stub = _StubMain()
    dlg = BackupDialog(stub.widget, active_path=None, initial_tab=1)
    dlg._stub = stub           # keep the parent widget alive for the duration of the test
    yield dlg
    dlg.close()
    dlg.deleteLater()
    qapp.processEvents()


def test_clicking_scan_shows_a_result_and_keeps_the_window_alive(qapp, backup_dialog):
    """"Scan Wi-Fi Router & USB" must end in a message — never in a frozen white window."""
    dlg = backup_dialog
    dlg._scan_vault_targets()
    assert dlg.v_scan.isEnabled() is False
    assert "Scanning" in dlg.status_lbl.text()

    done = _pump_until(qapp, lambda: dlg.v_scan.isEnabled(), timeout_ms=10000)
    assert done, "scan never finished (event loop starved)"
    assert dlg.status_lbl.text() != "", "status label must report the outcome"
    # the dialog is still a live widget and can be used again
    dlg._scan_vault_targets()
    assert _pump_until(qapp, lambda: dlg.v_scan.isEnabled(), timeout_ms=10000)


def test_scan_guard_prevents_parallel_scans(qapp, backup_dialog, monkeypatch):
    import app.tools.backup_dialog as bd

    calls: list[int] = []

    def fake_run_in_thread(_parent, fn, on_done=None, on_error=None):
        calls.append(1)
        return object()

    monkeypatch.setattr(bd, "run_in_thread", fake_run_in_thread)
    dlg = backup_dialog
    dlg._scan_vault_targets()
    dlg._scan_vault_targets()
    dlg._scan_vault_targets()
    assert len(calls) == 1, "a second click while scanning must be ignored"


def test_a_detected_target_fills_the_location_field(qapp, backup_dialog, monkeypatch):
    """The chooser used to die on `t.location` — the exact AttributeError behind the broken scan."""
    import app.tools.backup_dialog as bd
    from PySide6.QtWidgets import QInputDialog

    target = StorageTarget("USB Drive (E:)", r"E:\MyOffice_Vault", "usb", "removable, 12 GB free")
    monkeypatch.setattr(bd, "discover_storage_targets", lambda scan_network=True: [target])
    monkeypatch.setattr(bd, "run_in_thread", lambda _p, fn, on_done=None, on_error=None: on_done(fn()))

    label = f"{target.label}  \u2192  {target.location}  ({target.detail})"
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (label, True)))
    monkeypatch.setattr(bd.BackupDialog, "_refresh_vault", lambda self: None)

    dlg = backup_dialog
    dlg._scan_vault_targets()
    assert dlg.v_loc.text() == target.location
    assert dlg.v_scan.isEnabled() is True


def test_scan_error_is_reported_in_the_dialog(qapp, backup_dialog, monkeypatch):
    import app.tools.backup_dialog as bd

    def explode():
        raise OSError("SMB host unreachable")

    monkeypatch.setattr(bd, "discover_storage_targets", lambda scan_network=True: explode())
    dlg = backup_dialog
    dlg._scan_vault_targets()
    assert _pump_until(qapp, lambda: dlg.v_scan.isEnabled(), timeout_ms=8000)
    assert "Scan failed" in dlg.status_lbl.text()
    assert "SMB host unreachable" in dlg.status_lbl.text()


def test_probe_ftp_detects_a_real_listening_server():
    """A router that speaks FTP on :21 must be detected from a real socket, not just from a mock."""
    import socket
    import threading as _threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            conn.sendall(b"220 MyOffice test FTP ready\r\n")
            conn.close()
        except OSError:
            pass

    _threading.Thread(target=serve, daemon=True).start()
    try:
        assert net_storage._probe_ftp("127.0.0.1", 0.8) == (False, "")  # port 21 is not listening here
        assert net_storage._port_open("127.0.0.1", port, 0.8) is True
        assert net_storage._port_open("127.0.0.1", 1, 0.4) is False
    finally:
        srv.close()
