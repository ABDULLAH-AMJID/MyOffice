"""Thread-safe Qt background helper (`run_in_thread`) for non-blocking UI dialogs.

Why this module exists (v1.6.5 crash fix)
----------------------------------------
The previous implementation connected the worker signals straight to the caller's Python lambdas:

    worker.succeeded.connect(on_done)      # on_done is a plain lambda
    worker.failed.connect(on_error)

PySide only marshals a signal to the receiver's thread when the receiver is a **QObject slot**. A plain
Python callable is invoked *in the thread that emits the signal* — i.e. inside the background worker. So
every dialog callback (`self.status_lbl.setText(...)`, `QMessageBox.warning(...)`) was touching Qt widgets
from a non-GUI thread. Qt is not thread-safe there: the window painted blank/white, Windows marked the app
"Not Responding", and the process died shortly after — exactly the "Scan Error (Not Responding)" window
users saw when the Wi-Fi router scan failed.

Now every callback is bounced through a `_Relay` QObject that lives in the GUI thread, so `on_done` and
`on_error` always run on the GUI thread, the worker is never leaked, and a callback that raises can no
longer take the application down.
"""
from __future__ import annotations

import logging
import traceback
import weakref

from PySide6.QtCore import QObject, QThread, Signal, Slot

log = logging.getLogger(__name__)

# Threads / workers / relays of tasks that are still running. Dialogs can be closed (and destroyed) while
# their job is in flight; these references keep the QObjects alive until `finished` is delivered.
_LIVE: set[QObject] = set()


def _qt_alive(obj) -> bool:
    """True when `obj` still exists on the C++ side (it may have been deleted with its dialog)."""
    if obj is None:
        return True
    try:
        from shiboken6 import isValid

        return bool(isValid(obj))
    except Exception:  # pragma: no cover - shiboken always ships with PySide6
        return True


class _FnWorker(QObject):
    """Runs the blocking callable on the worker thread and emits its outcome."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - the dialog must receive every failure, never a crash
            message = f"{exc}\n{traceback.format_exc(limit=4)}"
            log.debug("background task failed: %s", exc)
            self._emit(self.failed, message)
            return
        self._emit(self.succeeded, result)

    def _emit(self, signal, payload) -> None:
        try:
            signal.emit(payload)
        except RuntimeError:  # pragma: no cover - receiver already destroyed
            pass


class _Relay(QObject):
    """Lives in the GUI thread; receives the worker outcome through a queued connection."""

    def __init__(self, context, on_done, on_error):
        super().__init__()
        self._context = weakref.ref(context) if context is not None else None
        self._on_done = on_done
        self._on_error = on_error
        self._thread: QThread | None = None
        self._worker: QObject | None = None

    # ---------------------------------------------------------------- wiring
    def bind(self, thread: QThread, worker: QObject) -> None:
        self._thread = thread
        self._worker = worker

    def _context_alive(self) -> bool:
        if self._context is None:
            return True
        target = self._context()
        if target is None:
            return False
        return _qt_alive(target)

    # ---------------------------------------------------------------- delivery (GUI thread)
    @Slot(object)
    def deliver(self, result) -> None:
        if self._on_done is None or not self._context_alive():
            return
        try:
            self._on_done(result)
        except Exception:  # noqa: BLE001 - a broken handler must not kill the UI thread
            log.exception("background task result handler failed")

    @Slot(str)
    def deliver_error(self, message: str) -> None:
        if self._on_error is None or not self._context_alive():
            return
        try:
            self._on_error(message)
        except Exception:  # noqa: BLE001
            log.exception("background task error handler failed")

    @Slot()
    def on_thread_finished(self) -> None:
        worker, thread = self._worker, self._thread
        self._worker = self._thread = None
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:  # pragma: no cover
                pass
            _LIVE.discard(worker)
        if thread is not None:
            try:
                thread.deleteLater()
            except RuntimeError:  # pragma: no cover
                pass
            _LIVE.discard(thread)
        _LIVE.discard(self)
        try:
            self.deleteLater()
        except RuntimeError:  # pragma: no cover
            pass


def run_in_thread(parent, fn, on_done=None, on_error=None) -> QThread:
    """Run ``fn()`` on a background ``QThread``.

    ``on_done(result)`` and ``on_error(message)`` are always invoked on the **GUI thread**, even though the
    call executes elsewhere, and they are skipped when ``parent`` (usually the dialog) has been destroyed in
    the meantime. The returned ``QThread`` is not parented to the widget, so closing a dialog while its job
    is still running can never trigger Qt's "QThread: Destroyed while thread is still running" abort.
    """
    thread = QThread()
    worker = _FnWorker(fn)
    relay = _Relay(parent, on_done, on_error)
    relay.bind(thread, worker)
    worker.moveToThread(thread)

    # Worker (background thread) -> relay (GUI thread): Qt queues these automatically because the receiver
    # is a QObject with GUI-thread affinity.
    worker.succeeded.connect(relay.deliver)
    worker.failed.connect(relay.deliver_error)

    # The event loop must stop only *after* the outcome has been queued for delivery.
    worker.succeeded.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(relay.on_thread_finished)

    thread.started.connect(worker.run)
    _LIVE.update((thread, worker, relay))
    thread.start()
    return thread


def wait_for_thread(thread: QThread, timeout_ms: int = 15000) -> bool:
    """Block until a task started by :func:`run_in_thread` has fully finished (used by tests/shutdown)."""
    if thread is None:
        return True
    try:
        return bool(thread.wait(timeout_ms))
    except RuntimeError:  # pragma: no cover - already deleted
        return True
