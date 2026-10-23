"""Run long operations in a worker thread with a modal progress dialog.

``run_task(parent, title, fn, *args, **kwargs)`` blocks (while keeping the UI
responsive) and returns the function result or re-raises its exception in the
GUI thread.  If ``fn`` accepts a ``progress`` keyword it receives a callback
``progress(percent:int, message:str)``.
"""
from __future__ import annotations

import inspect
import traceback
from contextlib import contextmanager

from PySide6.QtCore import QEventLoop, QObject, QThread, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QProgressDialog

from app.services.i18n import _t


class TaskError(Exception):
    def __init__(self, exc: BaseException, tb: str):
        super().__init__(str(exc))
        self.original = exc
        self.tb = tb


class _Worker(QObject):
    done = Signal(object)
    failed = Signal(object, str)
    progress = Signal(int, str)

    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.cancelled = False

    def run(self):
        try:
            kwargs = dict(self.kwargs)
            try:
                params = inspect.signature(self.fn).parameters
            except (TypeError, ValueError):
                params = {}
            if "progress" in params:
                kwargs["progress"] = lambda pct, msg="": self.progress.emit(int(pct), str(msg))
            if "is_cancelled" in params:
                kwargs["is_cancelled"] = lambda: self.cancelled
            res = self.fn(*self.args, **kwargs)
            self.done.emit(res)
        except BaseException as exc:  # noqa: BLE001 - forwarded to GUI thread
            self.failed.emit(exc, traceback.format_exc())


class _Relay(QObject):
    """Receives the worker's signals in the GUI thread.

    It is created in the GUI thread, so queued signals connected to its slots run there.  (A plain Python
    function connected to a signal of the worker - an object moved into the worker thread - would be called
    in the *worker* thread by PySide6, and touching the progress dialog from there crashes Qt.)"""

    def __init__(self, on_progress, on_done, on_failed):
        super().__init__()
        self._progress, self._done, self._failed = on_progress, on_done, on_failed

    @Slot(int, str)
    def progress(self, pct, msg):
        self._progress(pct, msg)

    @Slot(object)
    def done(self, res):
        self._done(res)

    @Slot(object, str)
    def failed(self, exc, tb):
        self._failed(exc, tb)


SHOW_AFTER_MS = 400  # quick tasks finish without a dialog flashing up
_ACTIVE: list = []  # progress dialogs of the run_task() calls in progress (GUI thread only)


class _TaskDialog(QProgressDialog):
    """A progress dialog that other_tasks_paused() can hold back.  QProgressDialog also shows itself from its own
    timers (a 4-second force-show timer that setMinimumDuration() does not restart for busy dialogs, and the
    progress estimate), so the check sits in setVisible(), where every way of showing it ends."""

    def __init__(self, *args):
        super().__init__(*args)
        self.paused = False
        self.due = False

    def setVisible(self, visible: bool) -> None:  # noqa: N802 (Qt override)
        if visible and self.paused:
            self.due = True  # appears when the other flow has finished
            return
        super().setVisible(visible)


def _show_when_due(dlg) -> None:
    dlg.show()


@contextmanager
def other_tasks_paused():
    """A running task opens another flow (for example "Converting document..." -> Legacy Formats Pack): only that
    flow's dialogs stay on screen.  The progress dialogs of the tasks already running - visible ones and those
    still waiting to appear - are hidden until the flow has finished; dialogs the flow opens itself are shown."""
    paused = [dlg for dlg in _ACTIVE if not dlg.paused]
    for dlg in paused:
        dlg.due = dlg.isVisible()
        dlg.hide()
        dlg.paused = True
    try:
        yield
    finally:
        for dlg in paused:
            if dlg not in _ACTIVE:  # that task has ended in the meantime
                continue
            dlg.paused = False
            if dlg.due:
                dlg.due = False
                dlg.show()


def run_task(parent, title: str, fn, *args, cancellable: bool = False, **kwargs):
    app = QApplication.instance()
    if app is None:
        return fn(*args, **kwargs)
    thread = QThread()
    worker = _Worker(fn, args, kwargs)
    worker.moveToThread(thread)
    loop = QEventLoop()
    result: dict = {}

    dlg = _TaskDialog(title, _t("Cancel") if cancellable else "", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModal)
    # shown by our own timer instead of QProgressDialog's built-in one, so that other_tasks_paused() can hold it back
    dlg.setMinimumDuration(2**31 - 1)
    show_timer = QTimer(dlg)
    show_timer.setSingleShot(True)
    show_timer.timeout.connect(lambda: _show_when_due(dlg))
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    if not cancellable:
        dlg.setCancelButton(None)
    else:
        dlg.canceled.connect(lambda: setattr(worker, "cancelled", True))

    def on_progress(pct, msg):
        if dlg.maximum() == 0:
            dlg.setRange(0, 100)
        dlg.setValue(max(0, min(100, pct)))
        if msg:
            dlg.setLabelText(msg)

    def on_done(res):
        result["value"] = res
        loop.quit()

    def on_failed(exc, tb):
        result["error"] = (exc, tb)
        loop.quit()

    relay = _Relay(on_progress, on_done, on_failed)
    worker.progress.connect(relay.progress, Qt.QueuedConnection)
    worker.done.connect(relay.done, Qt.QueuedConnection)
    worker.failed.connect(relay.failed, Qt.QueuedConnection)
    thread.started.connect(worker.run)
    _ACTIVE.append(dlg)
    show_timer.start(SHOW_AFTER_MS)
    thread.start()
    try:
        loop.exec()
    finally:
        _ACTIVE.remove(dlg)
        show_timer.stop()
    thread.quit()
    thread.wait()
    dlg.close()
    dlg.deleteLater()
    if "error" in result:
        exc, tb = result["error"]
        raise TaskError(exc, tb)
    return result.get("value")
