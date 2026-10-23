"""Logging and crash handling.

Uncaught exceptions are logged to ``<user_dir>/logs/myoffice.log`` and shown in a
friendly dialog instead of terminating the application.
"""
from __future__ import annotations

import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

from app.services.paths import user_subdir

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    log_dir = user_subdir("logs")
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(log_dir / "myoffice.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(logging.WARNING)
    root.addHandler(sh)
    _configured = True


_in_hook = threading.local()


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        logging.getLogger("crash").error("Unhandled exception:\n%s", details)
        if getattr(_in_hook, "active", False):
            return
        _in_hook.active = True
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance()
            if app is not None and threading.current_thread() is threading.main_thread():
                from app.services.i18n import _t

                box = QMessageBox(QMessageBox.Critical, _t("Unexpected error"),
                                  _t("An unexpected error occurred. Your work has not been lost; "
                                     "please save your documents.") + "\n\n" + f"{exc_type.__name__}: {exc}")
                box.setDetailedText(details)
                box.exec()
        except Exception:
            pass
        finally:
            _in_hook.active = False

    sys.excepthook = hook

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = thread_hook
