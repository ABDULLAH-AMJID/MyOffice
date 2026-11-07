"""The presentation script editor: list, edit, run, save, import/export.

Everything here is UI only — running is delegated to
:mod:`app.slide.script.runner`, storage to :mod:`app.slide.script.store`.

Run/stop uses a cooperative scheme: the interpreter runs on the UI thread and
the engine pumps the Qt event loop every 50 ms, so the Stop button stays
clickable while a long script works.  The dialog is application-modal, so the
pumped events cannot reach the rest of the window.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from app.macros.errors import MacroError
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_file, get_save_file, show_error, show_info
from app.slide.script import bundle as bundle_mod
from app.slide.script import store as store_mod
from app.slide.script.store import Script, ScriptStore


class ScriptDialog(QDialog):
    """Scripts for one presentation.  ``changed`` is True when the deck was modified."""

    def __init__(self, parent, doc):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle(_t("Scripts — {0}").format(doc.display_name()))
        self.resize(1000, 680)
        self.setModal(True)
        self.changed = False
        self._stop_requested = False
        self._running = False
        self._tab = "deck"

        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        # ---------------------------------------------------------- deck tab
        deck = QWidget()
        dv = QVBoxLayout(deck)
        dv.setContentsMargins(6, 6, 6, 6)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._load)
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.b_new = QPushButton(_t("New"))
        self.b_new.clicked.connect(self.new)
        self.b_del = QPushButton(_t("Delete"))
        self.b_del.clicked.connect(self.delete)
        row.addWidget(self.b_new)
        row.addWidget(self.b_del)
        lv.addLayout(row)
        self.b_import = QPushButton(_t("Import…"))
        self.b_import.clicked.connect(self.import_script)
        lv.addWidget(self.b_import)
        self.b_bundle = QPushButton(_t("Export package…"))
        self.b_bundle.setToolTip(_t("One .mopkg file containing the presentation and its scripts"))
        self.b_bundle.clicked.connect(self.export_bundle)
        lv.addWidget(self.b_bundle)
        left.setMaximumWidth(280)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("")
        self.title.setObjectName("H2")
        rv.addWidget(self.title)
        self.editor = QPlainTextEdit()
        self.editor.setTabStopDistance(32)
        self.editor.setPlaceholderText(_t("Write Python here.  Try: p = app.presentation"))
        try:
            from app.macros.manager import PythonHighlighter

            self.highlighter = PythonHighlighter(self.editor.document())
        except Exception:      # highlighting is a nicety, never a hard dependency
            self.highlighter = None
        rv.addWidget(self.editor, 3)

        bar = QHBoxLayout()
        self.b_run = QPushButton(_t("▶  Run"))
        self.b_run.setDefault(True)
        self.b_run.clicked.connect(self.run)
        self.b_stop = QPushButton(_t("■  Stop"))
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self.stop)
        self.b_save = QPushButton(_t("Save"))
        self.b_save.clicked.connect(self.save)
        self.b_export = QPushButton(_t("Export…"))
        self.b_export.clicked.connect(self.export)
        self.b_api = QPushButton(_t("API"))
        self.b_api.setToolTip(_t("Reference for every object a script can use"))
        self.b_api.clicked.connect(self.show_api)
        for b in (self.b_run, self.b_stop, self.b_save, self.b_export, self.b_api):
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("Muted")
        bar.addWidget(self.lbl_info)
        rv.addLayout(bar)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(170)
        self.output.setPlaceholderText(_t("Output appears here."))
        rv.addWidget(self.output, 1)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        dv.addWidget(split, 1)
        self.tabs.addTab(deck, _t("This presentation"))

        # ---------------------------------------------------------- library tab
        lib = QWidget()
        libv = QVBoxLayout(lib)
        self.lib_list = QListWidget()
        self.lib_list.currentItemChanged.connect(self._load_library)
        libv.addWidget(self.lib_list, 1)
        libbar = QHBoxLayout()
        self.b_lib_save = QPushButton(_t("Save to My scripts"))
        self.b_lib_save.clicked.connect(self.save_to_library)
        b_lib_del = QPushButton(_t("Delete"))
        b_lib_del.clicked.connect(self.delete_library)
        libbar.addWidget(self.b_lib_save)
        libbar.addWidget(b_lib_del)
        libbar.addStretch(1)
        libv.addLayout(libbar)
        libv.addWidget(QLabel(_t("Scripts in {0} — available from every presentation.").format(
            store_mod.scripts_dir())))
        self.tabs.addTab(lib, _t("My scripts"))

        # ---------------------------------------------------------- examples tab
        ex = QWidget()
        exv = QVBoxLayout(ex)
        self.ex_list = QListWidget()
        self.ex_list.currentItemChanged.connect(self._load_example)
        exv.addWidget(self.ex_list, 1)
        self.ex_text = QPlainTextEdit()
        self.ex_text.setReadOnly(True)
        exv.addWidget(self.ex_text, 2)
        exbar = QHBoxLayout()
        b_ex_use = QPushButton(_t("Copy into this presentation"))
        b_ex_use.clicked.connect(self.copy_example)
        exbar.addWidget(b_ex_use)
        exbar.addStretch(1)
        exv.addLayout(exbar)
        self.tabs.addTab(ex, _t("Examples"))

        self.tabs.currentChanged.connect(self._tab_changed)

        foot = QLabel(_t("Scripts run with the same rights as MyOffice itself. Only run scripts you trust."))
        foot.setObjectName("Muted")
        foot.setWordWrap(True)
        v.addWidget(foot)

        self._reload_all()
        self.editor.textChanged.connect(self._editor_changed)

    # ================================================================== data loading
    def _store(self) -> ScriptStore:
        return self.doc.script_store

    def _reload_all(self):
        self._reload_deck()
        self._reload_library()
        self._reload_examples()

    def _reload_deck(self):
        self.list.blockSignals(True)
        self.list.clear()
        for s in self._store().scripts:
            item = QListWidgetItem(s.name)
            item.setData(Qt.UserRole, s.name)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.title.setText(_t("No scripts in this presentation"))
            self.editor.setPlainText("")
        if self._store().path:
            self.lbl_info.setText(self._store().path.name + ("  ⚠" if store_mod.needs_consent(self._store().path) else ""))
        else:
            self.lbl_info.setText(_t("not saved yet"))

    def _reload_library(self):
        self.lib_list.blockSignals(True)
        self.lib_list.clear()
        try:
            self.lib_store = ScriptStore.library()
        except Exception:
            self.lib_store = ScriptStore()
        for s in self.lib_store.scripts:
            item = QListWidgetItem(s.name)
            item.setData(Qt.UserRole, s.name)
            self.lib_list.addItem(item)
        self.lib_list.blockSignals(False)
        if self.lib_list.count():
            self.lib_list.setCurrentRow(0)

    def _reload_examples(self):
        self.ex_list.clear()
        for title, path in store_mod.list_examples():
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, str(path))
            self.ex_list.addItem(item)
        if self.ex_list.count():
            self.ex_list.setCurrentRow(0)

    # ================================================================== selection
    def _current_deck_script(self) -> Script | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return self._store().get(item.data(Qt.UserRole))

    def _load(self, item, _prev=None):
        if item is None:
            return
        script = self._store().get(item.data(Qt.UserRole))
        if script is None:
            return
        self.title.setText(script.name)
        self.editor.blockSignals(True)
        self.editor.setPlainText(script.code)
        self.editor.blockSignals(False)
        self._loaded_name = script.name
        if self._store().path and store_mod.needs_consent(self._store().path):
            self.lbl_info.setText(_t("{0}  ⚠ not trusted on this device").format(self._store().path.name))

    def _load_library(self, item, _prev=None):
        if item is None:
            return
        script = self.lib_store.get(item.data(Qt.UserRole))
        if script is None:
            return
        self.title.setText(script.name)
        self.editor.blockSignals(True)
        self.editor.setPlainText(script.code)
        self.editor.blockSignals(False)
        self._loaded_name = None          # library scripts are edited only via "Copy…"

    def _load_example(self, item, _prev=None):
        if item is None:
            return
        try:
            self.ex_text.setPlainText(Path(item.data(Qt.UserRole)).read_text(encoding="utf-8"))
        except OSError as exc:
            self.ex_text.setPlainText(str(exc))

    def _tab_changed(self, index: int):
        self._tab = {0: "deck", 1: "library", 2: "examples"}.get(index, "deck")
        editing = self._tab != "examples"
        self.editor.setReadOnly(not editing)
        self.b_run.setEnabled(editing and not self._running)
        self.b_save.setEnabled(editing and not self._running)

    def _editor_changed(self):
        if self._running:
            return
        self._dirty = True

    # ================================================================== editing ops
    def new(self):
        name, ok = QInputDialog.getText(self, _t("New Script"), _t("Name:"))
        if not ok or not str(name).strip():
            return
        script = self._store().add(str(name).strip(), DEFAULT_CODE)
        self._store().dirty = True
        self._reload_deck()
        self._select_deck_item(script.name)
        self.doc.set_modified(True)
        self.changed = True

    def _select_deck_item(self, name: str):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == name:
                self.list.setCurrentRow(i)
                return

    def delete(self):
        script = self._current_deck_script()
        if script is None:
            return
        if not confirm(self, _t("Delete Script"), _t("Delete “{0}” from this presentation?").format(script.name)):
            return
        self._store().remove(script.name)
        self.doc.set_modified(True)
        self.changed = True
        self._reload_deck()

    def save(self):
        if self._tab == "library":
            self.save_to_library()
            return
        script = self._current_deck_script()
        code = self.editor.toPlainText()
        if script is None:
            name, ok = QInputDialog.getText(self, _t("Save Script"), _t("Name:"))
            if not ok or not str(name).strip():
                return
            script = self._store().add(str(name).strip(), code)
            self._select_deck_item(script.name)
        else:
            script.code = code
            script.touch()
        self._store().dirty = True
        self._store().self_authored = True
        self.doc.set_modified(True)
        self.changed = True
        if self.doc.path is not None:
            try:
                self._store().save_for_document(self.doc.path)
                self.output.appendPlainText(_t("Saved {0} script(s) to {1}").format(
                    len(self._store().scripts), self._store().path.name if self._store().path else ""))
            except OSError as exc:
                show_error(self, _t("Save Script"), _t("Could not write the script file: {0}").format(exc))
        else:
            self.output.appendPlainText(_t("Saved in memory — save the presentation to keep the scripts."))

    def save_to_library(self):
        code = self.editor.toPlainText()
        name = getattr(self, "_loaded_name", None) or self.title.text() or _t("Script")
        if not code.strip():
            return
        name, ok = QInputDialog.getText(self, _t("Save to My scripts"), _t("Name:"), text=str(name))
        if not ok or not str(name).strip():
            return
        script = Script(str(name).strip(), code)
        try:
            path = ScriptStore().save_library_script(script)
        except OSError as exc:
            show_error(self, _t("Save to My scripts"), str(exc))
            return
        self._reload_library()
        self.output.appendPlainText(_t("Saved {0}").format(path))

    def delete_library(self):
        item = self.lib_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.UserRole)
        if not confirm(self, _t("Delete Script"), _t("Delete “{0}” from My scripts?").format(name)):
            return
        ScriptStore().delete_library_script(name)
        self._reload_library()

    def import_script(self):
        path = get_open_file(self, _t("Import Script"), _t("Scripts (*.py *.moscript);;All files (*)"))
        if not path:
            return
        if not confirm(self, _t("Import Script"),
                       _t("Scripts can change documents and read files. Only import scripts from people you trust.\n\n"
                          "Import “{0}” into this presentation?").format(Path(path).name)):
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            show_error(self, _t("Import Script"), str(exc))
            return
        if Path(path).suffix.lower() == store_mod.SUFFIX:
            try:
                imported = ScriptStore.load_file(path)
                for s in imported:
                    self._store().scripts.append(self._store().normalise(s))
            except Exception as exc:
                show_error(self, _t("Import Script"), _t("This file could not be read: {0}").format(exc))
                return
        else:
            self._store().add(Path(path).stem.replace("_", " "), text)
        self._store().dirty = True
        self.doc.set_modified(True)
        self.changed = True
        self._reload_deck()

    def export(self):
        script = self._current_deck_script()
        suggested = (script.name if script else "script") + ".py"
        out = get_save_file(self, _t("Export Script"), _t("Python (*.py)"), suggested, "py")
        if not out:
            return
        try:
            Path(out).write_text(self.editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            show_error(self, _t("Export Script"), str(exc))

    def export_bundle(self):
        """Write a .mopkg with the deck and its scripts (deck must be saved first)."""
        if self.doc.path is None:
            show_info(self, _t("Export package"),
                      _t("Save the presentation first — the package carries the saved file."))
            return
        suggested = Path(self.doc.path).stem + bundle_mod.SUFFIX
        out = get_save_file(self, _t("Export package"), _t("MyOffice package (*.mopkg)"), suggested, "mopkg")
        if not out:
            return
        try:
            path = bundle_mod.export_bundle(self.doc, out)
        except (bundle_mod.BundleError, OSError) as exc:
            show_error(self, _t("Export package"), str(exc))
            return
        self.output.appendPlainText(_t("Package written: {0}").format(path))

    def copy_example(self):
        item = self.ex_list.currentItem()
        if item is None:
            return
        text = self.ex_text.toPlainText()
        self._store().add(item.text(), text, _t("Bundled example"))
        self._store().dirty = True
        self.doc.set_modified(True)
        self.changed = True
        self._reload_deck()
        self.tabs.setCurrentIndex(0)
        self.output.appendPlainText(_t("Copied “{0}” into this presentation.").format(item.text()))

    # ================================================================== running
    def run(self):
        if self._running:
            return
        if not store_mod.enabled():
            show_info(self, _t("Scripts are turned off"),
                      _t("Scripts are disabled in Settings. Turn them on to run this script."))
            return
        code = self.editor.toPlainText()
        if not code.strip():
            return
        if not self._consent():
            return
        name = self._current_deck_script().name if self._tab == "deck" and self._current_deck_script() else _t("script")
        self.output.appendPlainText(f"▶ {name}")
        self._set_running(True)
        try:
            from app.slide.script.runner import run_script
            from app.slide.script.store import timeout_seconds

            out = run_script(code, self.doc.window(), name=name, doc=self.doc, timeout=timeout_seconds(),
                             stop=lambda: self._stop_requested,
                             pump=self._pump, ask_file=self._ask_file)
            if out:
                self.output.appendPlainText(out.rstrip())
            self.output.appendPlainText(_t("✔ finished"))
        except MacroError as exc:
            self.output.appendPlainText(f"✖ {exc}")
        except Exception as exc:                     # never let a script crash the editor
            self.output.appendPlainText(f"✖ {exc.__class__.__name__}: {exc}")
        finally:
            self._set_running(False)
            self._stop_requested = False
            self.changed = True
            self._refresh_after_run()

    def stop(self):
        self._stop_requested = True
        self.b_stop.setEnabled(False)
        self.output.appendPlainText(_t("Stopping…"))

    def _pump(self):
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

    def _ask_file(self, path: str, purpose: str) -> bool:
        return bool(confirm(self, _t("Script wants to read a file"),
                            _t("{0}:\n\n{1}\n\nAllow this script to read it?").format(purpose, path)))

    def _consent(self) -> bool:
        """Ask once per file content before running scripts from a file we do not trust."""
        store = self._store()
        path = store.path
        if path is None or store.self_authored or not store_mod.needs_consent(path):
            return True
        names = "\n".join("   • " + s.name for s in self._store().scripts[:8]) or "   • (script being written)"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(_t("Scripts in this presentation"))
        box.setText(_t("{0}\n\nThis file contains scripts and was not created on this device:\n{1}\n\n"
                       "Scripts can read and change your files, just like MyOffice itself. "
                       "Only continue if you trust this file.").format(path.name, names))
        trust = box.addButton(_t("Trust this file and run"), QMessageBox.AcceptRole)
        box.addButton(_t("Don’t run"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not trust:
            self.output.appendPlainText(_t("✖ not run: the script file is not trusted"))
            return False
        store_mod.approve(path)
        self._reload_deck()
        return True

    def _set_running(self, running: bool):
        self._running = running
        for b in (self.b_run, self.b_save, self.b_new, self.b_del, self.b_import, self.b_export,
                  self.b_lib_save, self.b_bundle):
            b.setEnabled(not running)
        self.b_stop.setEnabled(running)
        self.tabs.setTabEnabled(1, not running)
        self.tabs.setTabEnabled(2, not running)

    def _refresh_after_run(self):
        if self._store().path and store_mod.needs_consent(self._store().path):
            self.lbl_info.setText(_t("{0}  ⚠ not trusted on this device").format(self._store().path.name))

    # ================================================================== API help
    def show_api(self):
        d = QDialog(self)
        d.setWindowTitle(_t("Script API reference"))
        d.resize(760, 620)
        v = QVBoxLayout(d)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setPlainText(api_reference())
        v.addWidget(browser, 1)
        close = QPushButton(_t("Close"))
        close.clicked.connect(d.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        d.exec()

    # ================================================================== closing
    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        script = self._current_deck_script()
        if self._tab == "deck" and script is not None and self.editor.toPlainText() != script.code:
            if confirm(self, _t("Unsaved script"),
                       _t("Save changes to “{0}” before closing?").format(script.name)):
                self.save()
        super().closeEvent(event)


# =========================================================================== helpers
DEFAULT_CODE = """p = app.presentation

# Add a slide
s = p.add_slide(layout="title_content", title="Hello", body="Created by a script")
print("Slides:", p.count)
"""


def api_reference() -> str:
    """Generated from the API classes themselves, so the help cannot drift."""
    from app.slide.script import api as api_mod

    lines = [_t("MyOffice Presentation Script — API reference"), "=" * 60, "",
             _t("Coordinates are EMU. app.emu(pixels) converts; values below 1000 are taken as pixels."),
             _t("Everything is live: writing a property changes the presentation immediately."), ""]
    for cls_name in ("PresentationAPI", "SlideListAPI", "SlideAPI", "ShapeListAPI", "ShapeAPI", "ParagraphAPI",
                     "RunAPI", "ThemeAPI"):
        cls = getattr(api_mod, cls_name, None)
        if cls is None:
            continue
        doc = (inspect.getdoc(cls) or "").split("\n")[0]
        lines.append(f"── {cls_name}  {doc}")
        for name, member in inspect.getmembers(cls):
            if name.startswith("_"):
                continue
            if isinstance(member, property):
                lines.append(f"   {name}    ({_t('property')})")
            elif inspect.isfunction(member):
                try:
                    sig = str(inspect.signature(member)).replace("self, ", "").replace("(self)", "()")
                except (TypeError, ValueError):
                    sig = "(...)"
                first = (inspect.getdoc(member) or "").split("\n")[0]
                lines.append(f"   {name}{sig}" + (f"    — {first}" if first else ""))
        lines.append("")
    lines += [_t("App helpers:"), "   app.presentation / app.slide, app.emu(px), app.px(emu), app.log(text),",
              "   app.message(text), app.confirm(text), app.input(prompt), app.status(text), app.sleep(ms),",
              "   app.new_presentation(), app.open(path)  — open() is permission-gated.", ""]
    return "\n".join(lines)
