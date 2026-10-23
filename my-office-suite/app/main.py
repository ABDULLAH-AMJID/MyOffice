"""MyOffice entry point.

    python app/main.py [files...] [--new writer|sheet|slide|pdf] [-setlng ur_PK] ...

Run ``python app/main.py --help`` for all command-line switches.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow "python app/main.py" without installing the package
    sys.path.insert(0, str(ROOT))

try:
    from app.services.ota_runtime import bootstrap_ota_runtime

    bootstrap_ota_runtime(ROOT / "app")
except Exception:
    pass

_MUTEX_HANDLES: list[int] = []  # Windows named mutexes (APP_MUTEX), held for the lifetime of the process


def _quiet_font_warnings() -> None:
    rules = os.environ.get("QT_LOGGING_RULES", "")
    if "qt.text.font.db" not in rules:
        os.environ["QT_LOGGING_RULES"] = ";".join(r for r in (rules, "qt.text.font.db=false") if r)


def _message_filter(mode, context, message):  # pragma: no cover - Qt callback
    if "OpenType support missing" in message or "propagateSizeHints" in message:
        return
    import logging

    logging.getLogger("qt").log(30 if int(mode.value if hasattr(mode, "value") else mode) >= 1 else 10, message)


def apply_ui_font(app, lang: str) -> None:
    from PySide6.QtGui import QFont

    from app.services.config import cfg

    f = QFont(app.font())
    family = cfg().get("ui.font_family", "")
    size = int(cfg().get("ui.font_size", 0) or 0)
    fams = [family] if family else [f.family()]
    if lang == "ur_PK":
        fams.append("Noto Nastaliq Urdu")
    elif lang == "hi_IN":
        fams.append("Noto Sans Devanagari")
    f.setFamilies(fams)
    if size:
        f.setPointSize(size)
    app.setFont(f)


def main(argv: list[str] | None = None) -> int:
    from app.services.cli import parse, run_headless_actions
    from app.services.log import install_excepthook, setup_logging

    args = parse(argv)
    setup_logging()
    rc = run_headless_actions(args)
    if rc is not None:
        return rc

    _quiet_font_warnings()
    from PySide6.QtCore import QCoreApplication, Qt, QTimer, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication

    from app.services import fonts
    from app.services.config import cfg
    from app.services.i18n import install_translator
    from app.version import APP_DOMAIN, APP_ID, APP_MUTEX, APP_NAME, APP_ORG, APP_VERSION

    if sys.platform == "win32":  # taskbar grouping + icon; mutex lets Setup/Uninstall see us running
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
            for name in (APP_MUTEX, "Global\\" + APP_MUTEX):
                handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
                if handle:
                    _MUTEX_HANDLES.append(handle)  # released by Windows when the process exits
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QCoreApplication.setOrganizationName(APP_ORG)
    QCoreApplication.setOrganizationDomain(APP_DOMAIN)
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(APP_VERSION)
    qInstallMessageHandler(_message_filter)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    install_excepthook()

    files = [f if "://" in f else os.path.abspath(f) for f in args.files]
    server = None
    if cfg().get("single_instance", True) and not args.no_single_instance:
        from app.shell.instance import InstanceServer, send_to_running

        if send_to_running({"files": files, "new": args.new, "uninstall": bool(args.uninstall)}):
            return 0
        server = InstanceServer()
        server.listen()

    app.setStyle("Fusion")
    fonts.load_bundled_fonts()
    fonts.install_substitutions()
    lang = cfg().get("lang", "en_US")
    install_translator(app, lang)
    apply_ui_font(app, lang)

    from app.shell.icons import DARK_COLOR, DEFAULT_COLOR, app_icon, set_theme_color
    from app.shell.main_window import MainWindow
    from app.shell.theme import stylesheet

    theme = cfg().get("theme", "light")
    set_theme_color(DARK_COLOR if theme == "dark" else DEFAULT_COLOR)
    app.setStyleSheet(stylesheet(theme))
    app.setWindowIcon(app_icon())

    win = MainWindow(safe_mode=args.safe_mode)
    if server is not None:
        server.setParent(win)
        server.messageReceived.connect(win.handle_message)
    win.show()
    try:
        from app.services.ota_runtime import mark_ota_boot_healthy

        mark_ota_boot_healthy()
    except Exception:
        pass
    autoquit = os.environ.get("MYOFFICE_AUTOQUIT_MS")
    if autoquit:  # smoke tests / CI - armed before any file is opened
        _arm_autoquit(app, win, int(autoquit))
    for f in files:
        win.open_path(f)
    if args.new == "pdf":
        QTimer.singleShot(0, win.open_pdf)
    elif args.new == "photos":
        QTimer.singleShot(0, win.open_photos)
    elif args.new:
        win.new_document(args.new)

    def first_run():
        from app.services import firewall

        if firewall.consent() is None and not os.environ.get("MYOFFICE_NO_FIRST_RUN"):
            firewall.ensure_consent(win)

    if args.uninstall:
        QTimer.singleShot(300, win.uninstall_app)
    QTimer.singleShot(250, win.check_recovery)
    QTimer.singleShot(600, win.start_services)
    QTimer.singleShot(1200, first_run)
    return app.exec()


def _arm_autoquit(app, win, ms: int) -> None:
    """Headless smoke test: after ``ms`` save an optional screenshot (MYOFFICE_SCREENSHOT) and quit.

    Exit code 0 = everything opened, 4 = a document failed to open, 3 = the app hung (watchdog).
    """
    import logging
    import threading

    from PySide6.QtCore import QTimer

    def watchdog():
        logging.getLogger("myoffice").error("Smoke test: still running %d ms after autoquit - hung", ms + 20000)
        logging.shutdown()
        os._exit(3)

    timer = threading.Timer((ms + 20000) / 1000, watchdog)
    timer.daemon = True
    timer.start()

    def quit_now():
        shot = os.environ.get("MYOFFICE_SCREENSHOT")
        if shot:
            win.grab().save(shot)
        for d in win.documents():
            d.set_modified(False)
        win.close()
        app.exit(4 if win.open_failures else 0)

    QTimer.singleShot(ms, quit_now)


if __name__ == "__main__":
    sys.exit(main())
