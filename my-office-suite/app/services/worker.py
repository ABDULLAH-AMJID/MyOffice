"""Lightweight Qt QThread helper (`run_in_thread`) for non-blocking UI dialog background tasks."""
from __future__ import annotations

import traceback
from PySide6.QtCore import QObject, QThread, Signal


class _FnWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            res = self._fn()
        except Exception as exc:
            self.failed.emit(f"{exc}\n{traceback.format_exc(limit=3)}")
            return
        self.succeeded.emit(res)


def run_in_thread(parent, fn, on_done=None, on_error=None) -> QThread:
    """Run ``fn()`` in a background ``QThread`` and invoke ``on_done(res)`` / ``on_error(msg)`` on the GUI thread."""
    thread = QThread(parent)
    worker = _FnWorker(fn)
    worker.moveToThread(thread)
    # Keep strong references on `thread` until finished so Python GC cannot collect while running
    thread._worker_ref = worker  # type: ignore[attr-defined]
    active_list = getattr(parent, "_bg_threads", None)
    if active_list is None and parent is not None:
        active_list = []
        parent._bg_threads = active_list  # type: ignore[attr-defined]
    if active_list is not None:
        active_list.append(thread)

    def _cleanup():
        if active_list is not None and thread in active_list:
            active_list.remove(thread)
        worker.deleteLater()
        thread.deleteLater()

    if on_done is not None:
        worker.succeeded.connect(on_done)
    if on_error is not None:
        worker.failed.connect(on_error)
    worker.succeeded.connect(lambda _r: thread.quit())
    worker.failed.connect(lambda _m: thread.quit())
    thread.finished.connect(_cleanup)
    thread.started.connect(worker.run)
    thread.start()
    return thread
