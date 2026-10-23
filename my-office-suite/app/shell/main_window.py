"""MyOffice main window: document tabs (WPS-style), per-document ribbons and tool dispatch."""
from __future__ import annotations

import importlib
import logging
import os
import sys
import zipfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QProcess, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QStackedWidget,
                               QTabBar, QToolButton, QVBoxLayout, QWidget)

from app.services import backup
from app.services.config import cfg
from app.services.i18n import LANGUAGES, _t
from app.shell.dialogs import (AboutDialog, SettingsDialog, confirm, get_open_files, handle_exception,
                               restore_window, show_error, show_info)
from app.shell.document import DocumentError, DocumentWidget, OpenCancelled
from app.shell.home import KIND_OF_EXT, HomePage
from app.shell.icons import app_icon, badge, icon
from app.shell.tools_registry import TOOLS_BY_ID, resolve
from app.version import APP_NAME

log = logging.getLogger(__name__)

DOC_CLASSES = {
    "writer": ("app.writer.widget", "WriterDocument"),
    "sheet": ("app.sheet.widget", "SheetDocument"),
    "slide": ("app.slide.widget", "SlideDocument"),
    "pdf": ("app.pdf.viewer", "PdfDocument"),
    "photos": ("app.photos.widget", "PhotosDocument"),
}
OPEN_FILTER = ("All documents (*.docx *.docm *.dotx *.doc *.wpt *.rtf *.odt *.wps *.txt *.md *.html *.htm "
               "*.xlsx *.xlsm *.xls *.csv *.tsv *.ett *.ods *.pptx *.ppt *.pps *.ppsx *.dpt *.odp *.pdf *.movault);;"
               "Writer (*.docx *.docm *.dotx *.doc *.wpt *.rtf *.odt *.wps *.txt *.md *.html *.htm);;"
               "Spreadsheets (*.xlsx *.xlsm *.xls *.csv *.tsv *.ett *.ods);;"
               "Presentations (*.pptx *.ppt *.pps *.ppsx *.dpt *.odp);;PDF (*.pdf);;"
               "Pictures (*.png *.jpg *.jpeg *.jfif *.webp *.bmp *.tif *.tiff *.gif *.ico *.heic);;"
               "Hardware Vault (*.movault);;All files (*)")
REPAIRABLE = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".wpt", ".ett", ".dpt", ".pdf")


def doc_class(kind: str):
    mod, cls = DOC_CLASSES[kind]
    return getattr(importlib.import_module(mod), cls)


class RibbonStack(QStackedWidget):
    """One ribbon per tab, exactly as tall as the ribbon on screen.  A plain QStackedWidget is as tall as its tallest
    page, which left an empty band above the Home page (it has no ribbon) and under a collapsed ribbon while other
    documents were open."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(self._current_changed)

    def _current_changed(self, _index: int) -> None:
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        w = self.currentWidget()
        if w is None:
            return QSize(0, 0)
        s = w.sizeHint().expandedTo(w.minimumSizeHint()).boundedTo(w.maximumSize()).expandedTo(w.minimumSize())
        return s.expandedTo(QSize(0, 0))

    def minimumSizeHint(self) -> QSize:
        w = self.currentWidget()
        if w is None:
            return QSize(0, 0)
        return w.minimumSizeHint().boundedTo(w.maximumSize()).expandedTo(w.minimumSize()).expandedTo(QSize(0, 0))


class RecoveryDialog(QDialog):
    def __init__(self, parent, entries: list[dict]):
        super().__init__(parent)
        self.setWindowTitle(_t("Document Recovery"))
        self.resize(560, 360)
        v = QVBoxLayout(self)
        lab = QLabel(_t("MyOffice closed unexpectedly. The following documents were recovered. "
                        "Select the ones you want to restore."))
        lab.setWordWrap(True)
        v.addWidget(lab)
        self.list = QListWidget()
        for e in entries:
            it = QListWidgetItem(badge(e.get("kind", "writer")), f"{e.get('name')}   \u2022   {e.get('time', '')}\n"
                                 f"{e.get('original') or _t('(never saved)')}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, e)
            self.list.addItem(it)
        v.addWidget(self.list, 1)
        bb = QDialogButtonBox()
        bb.addButton(_t("Restore Selected"), QDialogButtonBox.AcceptRole)
        bb.addButton(_t("Discard All"), QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def selected(self) -> list[dict]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class MainWindow(QMainWindow):
    def __init__(self, safe_mode: bool = False):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1360, 860)
        self.safe_mode = safe_mode
        self.recovery = backup.RecoveryManager()
        self.addins = None
        self.sync_service = None
        self.collab_sessions: dict = {}
        self._pages: list[QWidget] = []
        self.open_failures: list[str] = []   # paths that could not be opened (smoke tests check this)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QWidget()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(4, 0, 6, 0)
        tl.setSpacing(2)
        self.app_button = QToolButton()
        self.app_button.setObjectName("AppMenuButton")
        self.app_button.setText(APP_NAME)
        self.app_button.setIcon(app_icon())
        self.app_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.app_button.setPopupMode(QToolButton.InstantPopup)
        tl.addWidget(self.app_button)
        self.tabs = QTabBar()
        self.tabs.setObjectName("DocTabBar")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabMoved.connect(self._tab_moved)
        tl.addWidget(self.tabs, 0)
        self.new_tab_btn = QToolButton()
        self.new_tab_btn.setObjectName("NewTabButton")
        self.new_tab_btn.setText("+")
        self.new_tab_btn.setToolTip(_t("New"))
        self.new_tab_btn.setPopupMode(QToolButton.InstantPopup)
        nm = QMenu(self)
        nm.addAction(badge("writer"), _t("Writer Document"), self.new_writer)
        nm.addAction(badge("sheet"), _t("Spreadsheet"), self.new_sheet)
        nm.addAction(badge("slide"), _t("Presentation"), self.new_slide)
        nm.addAction(icon("templates"), _t("From Template\u2026"), self.show_templates)
        self.new_tab_btn.setMenu(nm)
        tl.addWidget(self.new_tab_btn)
        tl.addStretch(1)
        for ic, tip, fn in (("search", _t("Command Palette (Ctrl+Shift+P / Alt+Q)"), self.open_command_palette),
                            ("collab", _t("Live Collaborate, QR & Chat (Ctrl+Shift+L)"), self.collaborate),
                            ("keyboard", _t("Keyboard Shortcuts (Ctrl+K)"), self.open_shortcuts_dialog),
                            ("save", _t("Save (Ctrl+S)"), self.save_current),
                            ("undo", _t("Undo"), lambda: self._doc_call("undo")),
                            ("redo", _t("Redo"), lambda: self._doc_call("redo")),
                            ("print", _t("Print (Ctrl+P)"), self.print_current)):
            b = QToolButton()
            b.setIcon(icon(ic))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            tl.addWidget(b)
        lay.addWidget(top)
        self.ribbon_stack = RibbonStack()
        self.ribbon_stack.setSizePolicy(self.ribbon_stack.sizePolicy().horizontalPolicy(),
                                        self.ribbon_stack.sizePolicy().verticalPolicy().Maximum)
        lay.addWidget(self.ribbon_stack)
        self.content = QStackedWidget()
        lay.addWidget(self.content, 1)
        self.setCentralWidget(central)

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.lang_label = QLabel(LANGUAGES.get(cfg().get("lang", "en_US"), {}).get("native", ""))
        self.statusBar().addPermanentWidget(self.lang_label)
        self.sync_label = QLabel("")
        self.statusBar().addPermanentWidget(self.sync_label)

        self.home = HomePage(self)
        self._add_page(self.home, _t("Home"), icon("home"), closable=False)
        self._build_menu()
        self._build_shortcuts()
        # opening/converting a .doc, .ppt, .odt ... without LibreOffice offers the Legacy Formats Pack
        from app.services import office_convert
        from app.shell.legacy_pack_dialog import missing_handler_for

        office_convert.set_missing_handler(missing_handler_for(self))

        self.autosave = QTimer(self)
        self.autosave.timeout.connect(self.autosave_all)
        self.autosave.start(max(1, int(cfg().get("autosave_minutes", 3))) * 60_000)
        geo = cfg().get("ui.window_geometry", "")
        restored = False
        if geo:
            try:
                restored = bool(self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii"))))
            except Exception:
                restored = False
        if not restored:
            self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """First start: a comfortable size on large screens, maximized on laptop-size screens."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        if avail.width() < 1440 or avail.height() < 900:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
            return
        w, h = min(1360, int(avail.width() * 0.9)), min(860, int(avail.height() * 0.9))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    # ================================================================== pages / tabs
    def _add_page(self, widget: QWidget, title: str, ic, closable: bool = True) -> int:
        ribbon = widget.ribbon_widget() if isinstance(widget, DocumentWidget) else None
        holder = ribbon if ribbon is not None else QWidget()
        if ribbon is None:
            holder.setFixedHeight(0)
        widget.setProperty("_ribbon", holder)
        self.ribbon_stack.addWidget(holder)
        self.content.addWidget(widget)
        self._pages.append(widget)
        idx = self.tabs.addTab(ic, title)
        if not closable:
            for side in (QTabBar.RightSide, QTabBar.LeftSide):
                btn = self.tabs.tabButton(idx, side)
                if btn is not None:
                    btn.resize(0, 0)
                    btn.hide()
        self.tabs.setCurrentIndex(idx)
        return idx

    def _tab_moved(self, frm: int, to: int):
        w = self._pages.pop(frm)
        self._pages.insert(to, w)

    def _tab_changed(self, idx: int):
        if not 0 <= idx < len(self._pages):
            return
        w = self._pages[idx]
        self.content.setCurrentWidget(w)
        self.ribbon_stack.setCurrentWidget(w.property("_ribbon"))
        self.status_label.setText("")
        if isinstance(w, DocumentWidget):
            self.setWindowTitle(f"{w.tab_title()} - {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)
            self.status_label.setText("")
            if w is self.home:
                self.home.refresh()

    def documents(self) -> list[DocumentWidget]:
        return [w for w in self._pages if isinstance(w, DocumentWidget)]

    def current_page(self) -> QWidget | None:
        i = self.tabs.currentIndex()
        return self._pages[i] if 0 <= i < len(self._pages) else None

    def current_document(self) -> DocumentWidget | None:
        w = self.current_page()
        return w if isinstance(w, DocumentWidget) else None

    def current_of_kind(self, kind: str):
        w = self.current_document()
        if w is not None and w.kind == kind:
            return w
        for d in reversed(self.documents()):
            if d.kind == kind:
                return d
        return None

    def focus_page(self, w: QWidget):
        if w in self._pages:
            self.tabs.setCurrentIndex(self._pages.index(w))

    def add_document(self, doc: DocumentWidget) -> DocumentWidget:
        doc.titleChanged.connect(lambda d=doc: self._update_tab(d))
        doc.modifiedChanged.connect(lambda _m, d=doc: self._update_tab(d))
        doc.statusChanged.connect(lambda msg, d=doc: self._doc_status(d, msg))
        doc.openRequested.connect(self.add_document)
        doc.openPathRequested.connect(self.open_path)
        self._add_page(doc, doc.tab_title(), badge(doc.kind))
        self._update_tab(doc)
        if self.addins is not None:
            self.addins.decorate(doc)
        return doc

    def _update_tab(self, doc: DocumentWidget):
        if doc not in self._pages:
            return
        i = self._pages.index(doc)
        self.tabs.setTabText(i, doc.tab_title())
        self.tabs.setTabToolTip(i, str(doc.path) if doc.path else doc.display_name())
        if self.current_page() is doc:
            self.setWindowTitle(f"{doc.tab_title()} - {APP_NAME}")

    def _doc_status(self, doc, msg: str):
        if self.current_page() is doc:
            self.statusBar().showMessage(msg, 6000)

    def close_tab(self, idx: int) -> bool:
        if not 0 <= idx < len(self._pages):
            return False
        w = self._pages[idx]
        if w is self.home:
            return False
        if isinstance(w, DocumentWidget):
            if not w.close_request():
                return False
            if w.path and w.path.exists():
                self.keymap._closed_tabs_history.append(str(w.path))
            try:
                self.recovery.discard(w.doc_id)
            except Exception:
                pass
            for attr in ("collab_session", "_collab_session"):
                sess = getattr(w, attr, None)
                if sess is not None:
                    try:
                        sess.stop()
                    except Exception:
                        pass
            self.collab_sessions.pop(w.doc_id, None)
        elif hasattr(w, "close_request") and not w.close_request():
            return False
        self._pages.pop(idx)
        self.tabs.removeTab(idx)
        rb = w.property("_ribbon")
        self.ribbon_stack.removeWidget(rb)
        self.content.removeWidget(w)
        rb.deleteLater()
        w.deleteLater()
        return True

    # ================================================================== new / open
    def new_document(self, kind: str) -> DocumentWidget | None:
        try:
            doc = doc_class(kind)()
        except Exception as exc:
            handle_exception(self, _t("New document"), exc)
            return None
        return self.add_document(doc)

    def new_writer(self):
        return self.new_document("writer")

    def new_sheet(self):
        return self.new_document("sheet")

    def new_slide(self):
        return self.new_document("slide")

    def open_dialog(self):
        for p in get_open_files(self, _t("Open"), OPEN_FILTER):
            self.open_path(p)

    def open_pdf(self):
        for p in get_open_files(self, _t("Open PDF"), "PDF (*.pdf)"):
            self.open_path(p)

    def edit_pdf(self):
        doc = self.current_of_kind("pdf")
        if doc is None:
            files = get_open_files(self, _t("Edit PDF"), "PDF (*.pdf)")
            if not files:
                return
            doc = self.open_path(files[0])
        if doc is not None:
            self.focus_page(doc)
            doc.enter_edit_mode()

    @staticmethod
    def kind_for(path: str) -> str | None:
        ext = Path(path).suffix.lower()
        if ext in KIND_OF_EXT:
            return KIND_OF_EXT[ext]
        if ext in (".dotm", ".fodt", ".sxw"):
            return "writer"
        if ext in (".et", ".xltm", ".fods"):
            return "sheet"
        if ext in (".dps", ".pptm", ".potm", ".fodp"):
            return "slide"
        return None

    def find_open(self, path: str) -> DocumentWidget | None:
        try:
            rp = Path(path).resolve()
        except OSError:
            return None
        for d in self.documents():
            if d.path is not None and d.path.resolve() == rp:
                return d
        return None

    def open_path(self, path: str, as_copy: bool = False, display: str | None = None) -> DocumentWidget | None:
        if isinstance(path, str) and path.lower().startswith("myoffice://"):
            from app.collab.dialog import join_shared_target

            try:
                sess = join_shared_target(self, path)
                return sess.doc_widget
            except Exception as exc:
                show_error(self, _t("Live Collaboration"), str(exc))
                return None

        path = os.path.abspath(path)
        if not as_copy:
            existing = self.find_open(path)
            if existing is not None:
                self.focus_page(existing)
                return existing
        if not os.path.exists(path):
            show_error(self, _t("Open"), _t("The file does not exist:\n{0}").format(path))
            return None
        if Path(path).suffix.lower() == ".movault":
            return self._open_vault_file(path)
        kind = self.kind_for(path)
        if kind is None:
            show_error(self, _t("Open"), _t("MyOffice cannot open files of this type:\n{0}").format(path))
            return None
        password = None
        load_from = path
        from app.services import ooxml_crypto

        if ooxml_crypto.is_encrypted(path):
            password, load_from = self._ask_password_and_decrypt(path)
            if password is None:
                return None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            doc = doc_class(kind)()
            doc.load(load_from)
            if password is not None:
                doc.path = Path(path)
                doc.password = password
                doc.read_only = Path(path).suffix.lower() not in doc.writable_exts
                os.unlink(load_from)
        except OpenCancelled:
            QApplication.restoreOverrideCursor()
            return None
        except DocumentError as exc:
            QApplication.restoreOverrideCursor()
            self._open_failed(path, exc)
            return None
        except MemoryError:
            QApplication.restoreOverrideCursor()
            show_error(self, _t("Open"), _t("Not enough memory to open this file:\n{0}").format(path))
            return None
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self._open_failed(path, exc)
            return None
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        if as_copy:
            doc.path = None
            doc.title_override = display
            doc.set_modified(False)
        else:
            cfg().add_recent(path)
        self.add_document(doc)
        self.home.refresh()
        return doc

    def _ask_password_and_decrypt(self, path: str):
        """Password-protected DOCX/XLSX/PPTX: ask (three tries) and decrypt into a temporary file."""
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        from app.services import ooxml_crypto

        for attempt in range(3):
            label = _t("\"{0}\" is protected. Enter the password to open it:").format(Path(path).name)
            if attempt:
                label = _t("The password is not correct. Try again:") + "\n" + label
            pw, ok = QInputDialog.getText(self, _t("Password"), label, QLineEdit.Password)
            if not ok:
                return None, None
            try:
                return pw, ooxml_crypto.decrypt_to_temp(path, pw)
            except ooxml_crypto.WrongPassword:
                continue
            except Exception as exc:
                show_error(self, _t("Open"), _t("The file could not be decrypted: {0}").format(exc))
                return None, None
        show_error(self, _t("Open"), _t("The password is not correct."))
        return None, None

    # ------------------------------------------------------------------ new features (Photos, AI, versions...)
    def new_photo(self, img, title: str):
        from app.photos.widget import PhotosDocument

        doc = PhotosDocument()
        doc.new_from_image(img, title)
        return self.add_document(doc)

    def open_photos(self):
        from app.photos.widget import open_photos

        open_photos(self)

    def version_history(self):
        from app.shell.version_dialog import VersionHistoryDialog

        doc = self.current_document()
        if doc is None or doc.path is None:
            show_info(self, _t("Version History"), _t("Save the document first - every save then keeps a version."))
            return
        VersionHistoryDialog(self, doc).exec()

    def protect_with_password(self):
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        from app.services import ooxml_crypto

        doc = self.current_document()
        if doc is None or doc.kind not in ("writer", "sheet", "slide"):
            show_info(self, _t("Protect with Password"), _t("Open a Writer document, spreadsheet or presentation "
                                                            "first. (PDF files: PDF \u25b8 Protect.)"))
            return
        if doc.password:
            if confirm(self, _t("Protect with Password"), _t("Remove the password from this document?")):
                doc.password = None
                doc.save()
            return
        pw, ok = QInputDialog.getText(self, _t("Protect with Password"),
                                      _t("Password needed to open this document:"), QLineEdit.Password)
        if not ok or not pw:
            return
        pw2, ok = QInputDialog.getText(self, _t("Protect with Password"), _t("Repeat the password:"),
                                       QLineEdit.Password)
        if not ok:
            return
        if pw != pw2:
            show_error(self, _t("Protect with Password"), _t("The passwords do not match."))
            return
        doc.password = pw
        if doc.path is not None and doc.path.suffix.lower() not in ooxml_crypto.OOXML_EXTS:
            show_info(self, _t("Protect with Password"), _t("Passwords work with .docx, .xlsx and .pptx files. Please "
                                                            "choose one of these formats."))
            doc.save_as()
        else:
            doc.save()
        if doc.path is not None and ooxml_crypto.is_encrypted(doc.path):
            show_info(self, _t("Protect with Password"), _t("Saved with a password. Word, Excel, PowerPoint and "
                                                            "MyOffice will ask for it when the file is opened.\n"
                                                            "Keep the password safe - it cannot be recovered."))

    def chat_pdf(self):
        doc = self.current_of_kind("pdf")
        if doc is None:
            files = get_open_files(self, _t("Chat with PDF"), "PDF (*.pdf)")
            if not files:
                return
            doc = self.open_path(files[0])
        if doc is not None:
            self.focus_page(doc)
            doc.a_chat.setChecked(True)
            doc.toggle_chat(True)

    def parallel_translate(self):
        doc = self.current_document()
        if doc is not None and doc.kind == "writer":
            from app.writer.features import ai_translate
            ai_translate(doc)
        elif doc is not None and doc.kind == "slide":
            from app.slide.features import ai_translate
            ai_translate(doc)
        elif doc is not None and doc.kind == "pdf":
            doc.translate_pdf()
        else:
            files = get_open_files(self, _t("Parallel Translate"), "Documents (*.pdf *.docx *.pptx)")
            if files:
                doc = self.open_path(files[0])
                if doc is not None:
                    self.parallel_translate()

    def ai_settings(self):
        from app.shell.ai_dialogs import AISettingsDialog

        AISettingsDialog(self).exec()

    def open_failed_box(self, path: str, exc: Exception):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(_t("Cannot open document"))
        box.setText(_t("MyOffice could not open \u201c{0}\u201d.").format(os.path.basename(path)))
        box.setInformativeText(str(exc) or exc.__class__.__name__)
        repair_btn = None
        if Path(path).suffix.lower() in REPAIRABLE:
            repair_btn = box.addButton(_t("Repair File\u2026"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        return box, repair_btn

    def _open_failed(self, path: str, exc: Exception):
        log.warning("Open failed for %s: %s", path, exc)
        self.open_failures.append(path)
        if os.environ.get("MYOFFICE_AUTOQUIT_MS"):  # headless smoke test: report via exit code, never block on a box
            return
        box, repair_btn = self.open_failed_box(path, exc)
        box.exec()
        if repair_btn is not None and box.clickedButton() is repair_btn:
            self.repair_and_open(path)

    def repair_and_open(self, path: str):
        from app.services.repair import repair_file
        from app.shell.tasks import TaskError, run_task

        try:
            rep = run_task(self, _t("Repairing\u2026"), repair_file, path, None)
        except TaskError as exc:
            show_error(self, _t("File Repair"), _t("The file could not be repaired."), str(exc.original))
            return
        if not rep.ok:
            show_error(self, _t("File Repair"), _t("The file could not be repaired."), rep.summary())
            return
        show_info(self, _t("File Repair"), rep.summary())
        self.open_path(rep.output)

    def new_from_template(self, t):
        from app.services.templates import instantiate

        try:
            tmp = instantiate(t)
        except Exception as exc:
            handle_exception(self, _t("Templates"), exc)
            return None
        doc = self.open_path(tmp, as_copy=True, display=t.name)
        if tmp != t.path:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return doc

    # ================================================================== save / print
    def save_current(self):
        d = self.current_document()
        if d is not None:
            d.save()

    def save_as_current(self):
        d = self.current_document()
        if d is not None:
            d.save_as()

    def save_all(self):
        for d in self.documents():
            if d.is_modified():
                self.focus_page(d)
                if not d.save():
                    break

    def export_pdf_current(self):
        d = self.current_document()
        if d is not None:
            d.export_pdf()

    def print_current(self):
        d = self.current_document()
        if d is not None:
            d.print_document()

    def _doc_call(self, name: str):
        d = self.current_document()
        if d is None:
            return
        fn = getattr(d, name, None) or getattr(getattr(d, "editor", None), name, None)
        if callable(fn):
            fn()

    # ================================================================== menu & shortcuts
    def _build_menu(self):
        m = QMenu(self)
        new = m.addMenu(icon("new"), _t("New"))
        new.addAction(badge("writer"), _t("Writer Document"), self.new_writer)
        new.addAction(badge("sheet"), _t("Spreadsheet"), self.new_sheet)
        new.addAction(badge("slide"), _t("Presentation"), self.new_slide)
        new.addAction(badge("photos"), _t("Photos (open a picture)\u2026"), self.open_photos)
        new.addAction(icon("templates"), _t("From Template\u2026"), self.show_templates)
        m.addAction(icon("open"), _t("Open\u2026"), self.open_dialog)
        self.recent_menu = m.addMenu(icon("restore"), _t("Recent"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        m.addAction(icon("save"), _t("Save"), self.save_current)
        m.addAction(icon("save_as"), _t("Save As\u2026"), self.save_as_current)
        m.addAction(icon("save"), _t("Save All"), self.save_all)
        m.addAction(icon("export_pdf"), _t("Export to PDF\u2026"), self.export_pdf_current)
        m.addAction(icon("print"), _t("Print\u2026"), self.print_current)
        m.addAction(icon("close"), _t("Close Tab"), lambda: self.close_tab(self.tabs.currentIndex()))
        m.addAction(icon("versions"), _t("Version History\u2026"), self.version_history)
        m.addAction(icon("password"), _t("Protect with Password\u2026"), self.protect_with_password)
        m.addSeparator()
        m.addAction(icon("pdf"), _t("PDF Toolbox"), self.open_toolbox)
        tools = m.addMenu(icon("addins"), _t("Tools"))
        for tid in ("collab", "cloud_sync", "backup", "vault", "shortcuts", "command_palette",
                    "addins", "fonts", "repair", "update", "capture", "screen_recorder",
                    "scan_pdf", "picture_text", "batch_rename", "resume", "macros", "forms", "ai_settings"):
            t = TOOLS_BY_ID[tid]
            tools.addAction(icon(t.icon), _t(t.title), lambda x=tid: self.run_tool(x))
        tools.addSeparator()
        tools.addAction(icon("link"), _t("Register File Types"), self.register_file_types)
        tools.addAction(icon("share"), _t("Create Desktop Shortcuts"), self.create_shortcuts)
        tools.addAction(icon("install"), _t("Legacy Formats Pack\u2026"), self.show_legacy_pack)
        if sys.platform == "win32":
            tools.addAction(icon("pin"), _t("Pin to Taskbar"), self.pin_to_taskbar)
        m.addSeparator()
        lang = m.addMenu(icon("language"), _t("Language"))
        for code, info in LANGUAGES.items():
            a = lang.addAction(f"{info['native']} ({info['name']})", lambda c=code: self.change_language(c))
            a.setCheckable(True)
            a.setChecked(cfg().get("lang", "en_US") == code)
        theme = m.addMenu(icon("theme"), _t("Theme"))
        theme.addAction(icon("sun"), _t("Light"), lambda: self.set_theme("light"))
        theme.addAction(icon("moon"), _t("Dark"), lambda: self.set_theme("dark"))
        m.addAction(icon("settings"), _t("Settings\u2026"), self.open_settings)
        m.addAction(icon("info"), _t("About MyOffice"), self.show_about)
        m.addAction(icon("uninstall"), _t("Uninstall MyOffice\u2026"), self.uninstall_app)
        m.addSeparator()
        m.addAction(icon("close"), _t("Exit"), self.close)
        self.app_button.setMenu(m)

    def _fill_recent(self):
        self.recent_menu.clear()
        files = cfg().recent_files()
        for p in files:
            kind = self.kind_for(p) or "writer"
            self.recent_menu.addAction(badge(kind), os.path.basename(p), lambda x=p: self.open_path(x)).setToolTip(p)
        if not files:
            self.recent_menu.addAction(_t("(empty)")).setEnabled(False)

    def _build_shortcuts(self):
        from app.services.keymap import KeymapManager

        self.keymap = KeymapManager(self)
        self.keymap.apply_to_window()

        def sc(seq, fn):
            a = QAction(self)
            a.setShortcut(QKeySequence(seq))
            a.setShortcutContext(Qt.WindowShortcut)
            a.triggered.connect(fn)
            self.addAction(a)
            return a

        for idx in range(1, 10):
            sc(f"Ctrl+{idx}", lambda _c=False, k=idx - 1: self.tabs.setCurrentIndex(min(k, self.tabs.count() - 1)))
        sc("F12", self.save_as_current)

    def _open_vault_file(self, vault_path: str) -> DocumentWidget | None:
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        from app.services import vault
        from app.services.paths import user_subdir

        passphrase = vault.unseal_secret((cfg().get("backup_vault") or {}).get("passphrase", ""))
        for attempt in range(2):
            try:
                plain, meta = vault.decrypt_bytes(Path(vault_path).read_bytes(), passphrase=passphrase)
                out_dir = user_subdir("vault_opened")
                dest = out_dir / meta["original_name"]
                dest.write_bytes(plain)
                return self.open_path(str(dest))
            except vault.VaultAccessDeniedError as exc:
                if attempt == 0 and "passphrase" in str(exc).lower():
                    pw, ok = QInputDialog.getText(self, _t("Vault Passphrase"), _t("Enter vault passphrase:"), QLineEdit.Password)
                    if ok:
                        passphrase = pw
                        continue
                show_error(self, _t("Hardware Security Vault"), str(exc))
                return None
            except Exception as exc:
                show_error(self, _t("Hardware Security Vault"), str(exc))
                return None
        return None

    # -- v1.3 Shortcut & Command Palette Actions ------------------------------------------------------------------
    def _register_document(self, doc: DocumentWidget, _name: str = "") -> DocumentWidget:
        return self.add_document(doc)

    def new_shared_note(self):
        from app.collab.anyfile import UniversalFileDocument
        from app.services.paths import user_subdir

        note_path = user_subdir("notes") / f"Note_{len(self.documents()) + 1}.md"
        if not note_path.exists():
            note_path.write_text("# Shared Note\n\n", encoding="utf-8")
        doc = UniversalFileDocument(str(note_path), parent=self)
        return self.add_document(doc)

    save_current_as = save_as_current
    save_all_tabs = save_all
    export_current_pdf = export_pdf_current

    def toggle_ribbon_visibility(self):
        self.toggle_ribbon()

    def close_current_tab(self):
        self.close_tab(self.tabs.currentIndex())

    def reopen_last_closed_tab(self):
        while self.keymap._closed_tabs_history:
            last = self.keymap._closed_tabs_history.pop()
            if os.path.exists(last):
                self.open_path(last)
                return

    def open_command_palette(self):
        from app.shell.command_palette import CommandPaletteDialog

        CommandPaletteDialog(self).exec()

    def open_shortcuts_dialog(self):
        from app.shell.shortcuts_dialog import ShortcutsDialog

        ShortcutsDialog(self).exec()

    def open_hardware_vault(self):
        from app.tools.backup_dialog import open_dialog

        open_dialog(self, initial_tab=1)

    def toggle_theme_mode(self):
        self.set_theme("light" if cfg().get("theme", "light") == "dark" else "dark")

    def open_all_tools(self):
        self.focus_page(self.home)
        self.home.show_page("tools")

    def open_tool(self, tool_id: str):
        aliases = {
            "collaborate": "collab",
            "backup_center": "backup",
            "picture_to_text": "picture_text",
            "screen_capture": "capture",
            "file_repair": "repair",
        }
        resolved = aliases.get(tool_id, tool_id)
        if resolved in TOOLS_BY_ID:
            self.run_tool(resolved)

    def quick_share_active_file(self):
        from PySide6.QtGui import QGuiApplication
        from app.collab.binding import extract_doc_snapshot_bytes
        from app.collab.dialog import attach_session_to_doc, _make_room_slug
        from app.collab.server import ensure_shared_server, primary_lan_ip

        doc = self.current_document() or self.new_writer()
        sess = getattr(doc, "_collab_session", None)
        if sess is None:
            srv = ensure_shared_server()
            room = _make_room_slug(doc)
            path_str = str(doc.path) if doc.path else ""
            raw_bytes, fname, kind = extract_doc_snapshot_bytes(doc)
            srv.register_shared_file(
                room,
                file_path=path_str if path_str and os.path.isfile(path_str) else None,
                filename=fname,
                kind=kind,
                raw_bytes=raw_bytes or None,
            )
            sess = attach_session_to_doc(
                self, doc, f"ws://127.0.0.1:{srv.port}", room,
                user_name=cfg().get("collab.user", "") or "Host",
                http_base=f"http://{primary_lan_ip()}:{srv.port}",
                seed_snapshot=True,
            )
        link = sess.share_links(primary_lan_ip())["http"]
        QGuiApplication.clipboard().setText(link)
        self.statusBar().showMessage(_t("Live share active — link copied: {0}").format(link), 8000)

    def focus_or_toggle_collab_chat(self):
        doc = self.current_document()
        panel = getattr(doc, "_collab_panel", None) if doc else None
        if panel is not None:
            panel.setVisible(not panel.isVisible())
            if panel.isVisible():
                panel.msg_edit.setFocus()
            return
        self.collaborate()

    def quick_vault_backup_active(self):
        doc = self.current_document()
        vcfg = cfg().get("backup_vault") or {}
        if doc is not None and doc.path and doc.path.is_file() and vcfg.get("location"):
            try:
                out = backup.backup_to_vault_target(str(doc.path), vcfg)
                self.statusBar().showMessage(_t("Encrypted to Hardware Vault: {0}").format(out), 8000)
                return
            except Exception as exc:
                show_error(self, _t("Hardware Security Vault"), str(exc))
                return
        self.open_hardware_vault()

    def adjust_active_zoom(self, delta: int, reset: bool = False):
        doc = self.current_document()
        if doc is None:
            return
        if hasattr(doc, "view") and hasattr(doc.view, "set_zoom") and hasattr(doc.view, "zoom"):
            doc.view.set_zoom(1.0 if reset else doc.view.zoom * (1.12 if delta > 0 else 1 / 1.12))
        elif hasattr(doc, "zoom_slider"):
            z = 1.0 if reset else max(0.25, min(5.0, doc.zoom_slider.zoom() + delta / 100.0))
            doc.zoom_slider.set_zoom(z)

    def _new_same_kind(self):
        d = self.current_document()
        self.new_document(d.kind if d is not None and d.kind != "pdf" else "writer")

    def toggle_full_screen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def toggle_ribbon(self):
        rb = self.ribbon_stack.currentWidget()
        if hasattr(rb, "toggle_collapsed"):
            rb.toggle_collapsed()

    # ================================================================== tools
    def run_tool(self, tool_id: str):
        t = TOOLS_BY_ID[tool_id]
        try:
            resolve(t.handler, self)()
        except Exception as exc:
            handle_exception(self, _t(t.title), exc)

    def show_templates(self):
        self.focus_page(self.home)
        self.home.show_page("templates")

    def open_toolbox(self):
        from app.pdf.toolbox import PdfToolbox

        for w in self._pages:
            if isinstance(w, PdfToolbox):
                self.focus_page(w)
                return w
        tb = PdfToolbox(self)
        self._add_page(tb, _t("PDF Toolbox"), badge("pdf", "T"))
        return tb

    def _writer_for_tool(self):
        d = self.current_of_kind("writer")
        if d is None:
            d = self.new_writer()
        else:
            self.focus_page(d)
        return d

    def _sheet_for_tool(self):
        d = self.current_of_kind("sheet")
        if d is None:
            files = get_open_files(self, _t("Open a spreadsheet with your data"),
                                   "Spreadsheets (*.xlsx *.xlsm *.xls *.csv *.tsv *.ods);;All files (*)")
            d = self.open_path(files[0]) if files else None
        else:
            self.focus_page(d)
        return d

    def mail_merge(self):
        d = self._writer_for_tool()
        if d is not None:
            d.mail_merge()

    def citations(self):
        d = self._writer_for_tool()
        if d is not None:
            d.ribbon.select_page(_t("References"))
            d.manage_sources()

    def track_changes(self):
        d = self._writer_for_tool()
        if d is not None:
            d.ribbon.select_page(_t("Review"))
            d.a_track.setChecked(True)
            d.toggle_track(True)

    def compare_documents(self):
        d = self.current_of_kind("writer")
        if d is not None:
            self.focus_page(d)
            d.compare_documents()
            return
        from app.writer.widget import WriterDocument

        tmp = WriterDocument()
        tmp.openRequested.connect(self.add_document)
        tmp.compare_documents()
        tmp.deleteLater()

    def pivot_table(self):
        d = self._sheet_for_tool()
        if d is not None:
            d.pivot_dialog()

    def data_analysis(self):
        d = self._sheet_for_tool()
        if d is not None:
            d.analysis_dialog()

    def charts(self):
        d = self.current_of_kind("sheet") or self.current_of_kind("slide")
        if d is None:
            d = self._sheet_for_tool()
        if d is not None:
            self.focus_page(d)
            d.insert_chart()

    def macros(self):
        from app.macros.manager import MacroDialog

        MacroDialog(self).exec()

    def collaborate(self):
        from app.collab.dialog import open_dialog

        open_dialog(self)

    def screen_capture(self):
        from app.services.capture import capture_dialog, save_capture
        from app.shell.dialogs import get_save_file

        img = capture_dialog(self)
        if img is None:
            return
        d = self.current_document()
        box = QMessageBox(self)
        box.setWindowTitle(_t("Screen Capture"))
        box.setText(_t("The capture ({0} \u00d7 {1}) was copied to the clipboard.").format(img.width(), img.height()))
        ins = box.addButton(_t("Insert into Document"), QMessageBox.ActionRole) if d is not None and hasattr(d, "insert_image") or (d is not None and d.kind == "writer") else None
        save = box.addButton(_t("Save As\u2026"), QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if ins is not None and box.clickedButton() is ins:
            if d.kind == "writer":
                d.editor.insert_qimage(img)
            else:
                d.insert_image(img)
        elif box.clickedButton() is save:
            path = get_save_file(self, _t("Save Capture"), "PNG (*.png);;JPEG (*.jpg)", "capture.png", "png")
            if path:
                img.save(path)
            else:
                save_capture(img)

    def register_file_types(self):
        from app.services import register

        try:
            done = register.register_associations()
            show_info(self, _t("Register File Types"),
                      _t("MyOffice is registered for: {0}\n\nTo make it the default app, choose it in "
                         "Settings \u2192 Default apps.").format(" ".join(done)))
            if sys.platform == "win32" and confirm(self, _t("Register File Types"), _t("Open Default apps settings now?")):
                register.open_default_apps_settings()
        except Exception as exc:
            handle_exception(self, _t("Register File Types"), exc)

    def create_shortcuts(self):
        from app.services import shortcuts

        try:
            made = shortcuts.create_shortcuts()
            show_info(self, _t("Create Desktop Shortcuts"), _t("Created {0} shortcut(s).").format(len(made)))
        except Exception as exc:
            handle_exception(self, _t("Create Desktop Shortcuts"), exc)

    # ================================================================== legacy formats, taskbar, uninstall
    def show_legacy_pack(self):
        """Tools > Legacy Formats Pack...: status, or download + install LibreOffice on request."""
        from app.services import legacy_pack
        from app.shell.legacy_pack_dialog import ensure_libreoffice

        st = legacy_pack.status()
        if st["installed"]:
            show_info(self, _t("Legacy Formats Pack"),
                      _t("The Legacy Formats Pack is installed: MyOffice opens .doc, .xls, .ppt and OpenDocument "
                         "files with LibreOffice.\n\n{0}").format(st["path"]))
            return
        if ensure_libreoffice(self):
            self.statusBar().showMessage(_t("Legacy Formats Pack installed."), 8000)

    def pin_to_taskbar(self):
        """Ask Windows to pin MyOffice (explicit user action, app in the foreground - Microsoft's rules)."""
        from app.services import taskbar

        if getattr(self, "_pin_request", None) is not None:
            return
        self._pin_request = taskbar.PinRequest(self)
        self._pin_request.finished.connect(self._pin_finished)
        self._pin_request.start()

    def _pin_finished(self, pinned: bool, method: str):
        req, self._pin_request = getattr(self, "_pin_request", None), None
        if req is not None:
            req.deleteLater()
        cfg().set("taskbar.pin_prompt", "done", save=True)
        self.home.pin_banner.hide()
        if pinned:
            self.statusBar().showMessage(_t("MyOffice is pinned to the taskbar."), 8000)
        elif method == "declined":
            self.statusBar().showMessage(_t("MyOffice was not pinned to the taskbar."), 8000)
        else:
            show_info(self, _t("Pin to Taskbar"),
                      _t("On this version of Windows only you can pin programs to the taskbar:\n\n"
                         "1. Keep MyOffice open.\n2. Right-click the MyOffice icon on the taskbar.\n"
                         "3. Choose \u201cPin to taskbar\u201d."))

    def uninstall_app(self):
        """MyOffice menu -> Uninstall MyOffice...: confirm, close documents (asking to save), then remove."""
        from app.services import uninstall
        from app.shell.uninstall_dialog import UninstallDialog

        try:
            plan = uninstall.make_plan()
        except Exception as exc:
            handle_exception(self, _t("Uninstall MyOffice"), exc)
            return
        if UninstallDialog(plan, self).exec() != QDialog.Accepted:
            return
        if not self._close_all():  # the user cancelled a "save changes?" question
            return
        if plan.installed:
            try:
                uninstall.launch_uninstaller(plan.uninstaller)
            except OSError as exc:
                handle_exception(self, _t("Uninstall MyOffice"), exc)
                return
            self._shutdown()  # the uninstaller waits until this process has exited
            QApplication.instance().quit()
            return
        self._shutdown()
        removed = uninstall.remove_portable_traces(plan)
        show_info(self, _t("Uninstall MyOffice"),
                  _t("MyOffice's settings, data, registrations and shortcuts were removed ({0} items).\n\n"
                     "To finish, delete the program folder after MyOffice has closed:\n{1}")
                  .format(len(removed), plan.program_dir))
        QApplication.instance().quit()

    # ================================================================== settings
    def open_settings(self):
        old_lang, old_theme = cfg().get("lang"), cfg().get("theme")
        d = SettingsDialog(self)
        if d.exec():
            if cfg().get("theme") != old_theme:
                self.set_theme(cfg().get("theme"))
            if cfg().get("lang") != old_lang:
                self._offer_restart()
            self.autosave.start(max(1, int(cfg().get("autosave_minutes", 3))) * 60_000)

    def show_about(self):
        AboutDialog(self).exec()

    def set_theme(self, theme: str):
        from app.shell.icons import DARK_COLOR, DEFAULT_COLOR, set_theme_color
        from app.shell.theme import stylesheet

        cfg().set("theme", theme, save=True)
        set_theme_color(DARK_COLOR if theme == "dark" else DEFAULT_COLOR)
        QApplication.instance().setStyleSheet(stylesheet(theme))

    def change_language(self, code: str):
        from app.services.i18n import set_language

        if code == cfg().get("lang"):
            return
        set_language(code)
        self._offer_restart()

    def _offer_restart(self):
        if confirm(self, _t("Language"), _t("The new language is applied after a restart. Restart MyOffice now?")):
            self.restart()

    def restart(self):
        if not self._close_all():
            return
        from app.services.paths import executable_command

        cmd = executable_command()
        QProcess.startDetached(cmd[0], cmd[1:] + ["--no-single-instance"])
        self._shutdown()
        QApplication.instance().quit()

    # ================================================================== recovery
    def autosave_all(self):
        for d in self.documents():
            if d.is_modified():
                try:
                    self.recovery.snapshot(d.doc_id, d.display_name(), d.kind, str(d.path) if d.path else None,
                                           d.recovery_ext(), d.write_recovery)
                except Exception as exc:
                    log.warning("Auto-recovery snapshot failed for %s: %s", d.display_name(), exc)

    def check_recovery(self):
        try:
            entries = self.recovery.orphaned_sessions()
        except Exception as exc:
            log.warning("Recovery scan failed: %s", exc)
            return
        if not entries:
            return
        d = RecoveryDialog(self, entries)
        restore = d.selected() if d.exec() else []
        for e in restore:
            doc = self.open_path(e["path"], as_copy=True, display=e.get("name"))
            if doc is not None:
                if e.get("original"):
                    doc.path = Path(e["original"])
                doc.title_override = None
                doc.set_modified(True)
                doc.titleChanged.emit()
        for s in {e["session"] for e in entries}:
            self.recovery.purge_session(s)

    # ================================================================== addins / services
    def start_services(self):
        try:
            from app.services import register

            register.repair_stale_icon_registrations()
        except Exception as exc:
            log.debug("Startup icon check skipped: %s", exc)
        if not self.safe_mode:
            try:
                from app.services.addinmgr import AddinManager

                self.addins = AddinManager(self)
                self.addins.load_all()
            except Exception as exc:
                log.error("Add-in manager failed: %s", exc)
        if cfg().get("sync.enabled", False):
            try:
                from app.services.cloud_sync import start_background_sync

                self.sync_service = start_background_sync(self)
            except Exception as exc:
                log.error("Cloud sync failed to start: %s", exc)
        if cfg().get("update.check_on_startup", False):
            QTimer.singleShot(4000, self._startup_update_check)

    def _startup_update_check(self):
        from app.services import firewall

        if firewall.consent() is False:
            return
        try:
            from app.tools.update_dialog import background_check

            background_check(self)
        except Exception as exc:
            log.info("Update check skipped: %s", exc)

    # ================================================================== drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())

    def handle_message(self, msg: dict):
        """Message from a second instance (files to open)."""
        for f in msg.get("files", []):
            self.open_path(f)
        if msg.get("new"):
            self.new_document(msg["new"])
        restore_window(self)  # keeps a maximized window maximized
        if msg.get("uninstall"):
            QTimer.singleShot(0, self.uninstall_app)

    # ================================================================== close
    def _close_all(self) -> bool:
        for d in list(self.documents()):
            self.focus_page(d)
            if not d.close_request():
                return False
        return True

    def _shutdown(self):
        if getattr(self, "_shut_down", False):  # run once (restart / uninstall call it before quit())
            return
        self._shut_down = True
        from app.services import office_convert

        office_convert.set_missing_handler(None)
        cfg().set("ui.window_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))
        cfg().save()
        for d in self.documents():
            for attr in ("collab_session", "_collab_session"):
                sess = getattr(d, attr, None)
                if sess is not None:
                    try:
                        sess.stop()
                    except Exception:
                        pass
        try:
            from app.collab.server import _SHARED_SERVER

            if _SHARED_SERVER is not None and _SHARED_SERVER.running:
                _SHARED_SERVER.stop()
        except Exception:
            pass
        try:
            from app.services.mobile_sync import stop_global_mobile_hub

            stop_global_mobile_hub()
        except Exception:
            pass
        if self.sync_service is not None:
            try:
                self.sync_service.stop()
            except Exception:
                pass
        if self.addins is not None:
            try:
                self.addins.unload_all()
            except Exception:
                pass
        self.recovery.clean_shutdown()

    def closeEvent(self, e):
        if getattr(self, "_shut_down", False):
            e.accept()
            return
        if not self._close_all():
            e.ignore()
            return
        self._shutdown()
        e.accept()


def is_zip(path: str) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False
