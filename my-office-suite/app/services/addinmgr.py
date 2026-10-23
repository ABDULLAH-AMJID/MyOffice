"""Add-in Manager (re-implementation of the ksomisc ``addinmgr`` logic).

Add-in kinds
============
* **Python add-ins**: a folder containing ``manifest.json`` and a module with ``register(api)``
  (and optionally ``unregister(api)``).  They can add ribbon buttons to Writer/Sheet/Slide/PDF tabs,
  register spreadsheet functions and react to documents being opened.
* **Native Qt plugins**: shared libraries (``.dll``/``.so``/``.dylib``) loaded with
  :class:`QPluginLoader`; their JSON metadata is shown and, if the plugin object has an
  ``initialize(QObject)`` slot, it is invoked with the main window.

Add-ins live in ``<install>/plugins`` (bundled) and ``<user dir>/addins`` (installed by the user).
Disabled add-ins are stored in ``config.json`` under ``plugins.disabled``.  A failing add-in is
isolated: its error is logged and shown in the manager, the application keeps running.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.config import cfg
from app.services.paths import bundled_plugins_dir, user_subdir
from app.version import APP_VERSION, version_tuple

log = logging.getLogger(__name__)
NATIVE_EXTS = (".dll", ".so", ".dylib")


@dataclass
class AddinInfo:
    id: str
    name: str
    version: str
    path: Path
    kind: str = "python"            # python | native
    author: str = ""
    description: str = ""
    entry: str = "plugin.py"
    min_app_version: str = "1.0.0"
    builtin: bool = False
    enabled: bool = True
    loaded: bool = False
    error: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def compatible(self) -> bool:
        return version_tuple(APP_VERSION) >= version_tuple(self.min_app_version or "0")


def user_addins_dir() -> Path:
    return user_subdir("addins")


def read_manifest(folder: Path) -> dict:
    with open(folder / "manifest.json", encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("id", "name", "version"):
        if not data.get(key):
            raise ValueError(f"manifest.json is missing '{key}'")
    return data


class AddinAPI:
    """The interface add-ins receive in ``register(api)``."""

    def __init__(self, manager: "AddinManager", addin: AddinInfo):
        self._m = manager
        self.addin = addin

    @property
    def main_window(self):
        return self._m.main

    @property
    def app_version(self) -> str:
        return APP_VERSION

    def add_ribbon_action(self, kind: str, text: str, callback, icon: str = "addins", tooltip: str = "",
                          group: str | None = None):
        """Add a button to the "Add-ins" ribbon tab of documents of ``kind`` (writer/sheet/slide/pdf)."""
        self._m.ribbon_actions.append(dict(kind=kind, text=text, callback=callback, icon=icon, tooltip=tooltip,
                                           group=group or self.addin.name, addin=self.addin.id))

    def register_sheet_function(self, name: str, func, min_args: int = 0, max_args: int | None = None,
                                category: str = "Add-in", syntax: str = "", help: str = ""):
        from app.sheet.formula.functions import FUNCTIONS, FuncSpec

        name = name.upper()
        if name in FUNCTIONS and FUNCTIONS[name].category != "Add-in":
            raise ValueError(f"Cannot override the built-in function {name}")
        FUNCTIONS[name] = FuncSpec(func, min_args, max_args, category=category, syntax=syntax or f"{name}()", help=help)
        self._m.functions.setdefault(self.addin.id, []).append(name)

    def on_document_opened(self, callback):
        self._m.open_hooks.append((self.addin.id, callback))

    def active_document(self):
        return self._m.main.current_document() if self._m.main is not None else None

    def show_message(self, title: str, text: str):
        from app.shell.dialogs import show_info

        show_info(self._m.main, title, text)

    def log(self, msg: str):
        log.info("[addin %s] %s", self.addin.id, msg)


class AddinManager:
    def __init__(self, main=None):
        self.main = main
        self.addins: dict[str, AddinInfo] = {}
        self.modules: dict[str, object] = {}
        self.apis: dict[str, AddinAPI] = {}
        self.native: dict[str, object] = {}
        self.ribbon_actions: list[dict] = []
        self.functions: dict[str, list[str]] = {}
        self.open_hooks: list = []

    # -- discovery
    def discover(self) -> list[AddinInfo]:
        disabled = set(cfg().get("plugins.disabled", []))
        found: dict[str, AddinInfo] = {}
        for root, builtin in ((bundled_plugins_dir(), True), (user_addins_dir(), False)):
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if folder.is_dir() and (folder / "manifest.json").exists():
                    try:
                        m = read_manifest(folder)
                        info = AddinInfo(m["id"], m["name"], m["version"], folder, "python", m.get("author", ""),
                                         m.get("description", ""), m.get("entry", "plugin.py"),
                                         m.get("min_app_version", "1.0.0"), builtin, m["id"] not in disabled,
                                         metadata=m)
                    except Exception as exc:
                        info = AddinInfo(folder.name, folder.name, "?", folder, error=f"Invalid manifest: {exc}",
                                         builtin=builtin, enabled=False)
                    if info.id in found and builtin:
                        continue  # a user-installed copy overrides the bundled one
                    found[info.id] = info
                elif folder.is_file() and folder.suffix.lower() in NATIVE_EXTS:
                    info = self._native_info(folder, builtin, disabled)
                    if info is not None:
                        found[info.id] = info
        for k, v in found.items():
            old = self.addins.get(k)
            if old is not None:
                v.loaded, v.error = old.loaded, old.error or v.error
        self.addins = found
        return list(found.values())

    def _native_info(self, path: Path, builtin: bool, disabled: set) -> AddinInfo | None:
        try:
            from PySide6.QtCore import QPluginLoader

            loader = QPluginLoader(str(path))
            meta = loader.metaData()
            data = dict(meta) if meta else {}
            md = data.get("MetaData", {}) or {}
            pid = md.get("id") or path.stem
            return AddinInfo(pid, md.get("name", path.stem), str(md.get("version", "?")), path, "native",
                             md.get("author", ""), md.get("description", data.get("IID", "")),
                             builtin=builtin, enabled=pid not in disabled, metadata=data,
                             error="" if data else f"Not a Qt plugin: {loader.errorString()}")
        except Exception as exc:
            return AddinInfo(path.stem, path.stem, "?", path, "native", error=str(exc), builtin=builtin, enabled=False)

    # -- loading
    def load_all(self) -> None:
        self.discover()
        for info in self.addins.values():
            if info.enabled and not info.loaded and not info.error:
                self.load(info)

    def load(self, info: AddinInfo) -> bool:
        if not info.compatible:
            info.error = f"Requires MyOffice {info.min_app_version} or newer"
            return False
        try:
            if info.kind == "native":
                from PySide6.QtCore import QMetaObject, QPluginLoader, Qt, Q_ARG, QObject

                loader = QPluginLoader(str(info.path))
                if not loader.load():
                    raise RuntimeError(loader.errorString())
                inst = loader.instance()
                if inst is not None and self.main is not None:
                    mo = inst.metaObject()
                    if mo.indexOfMethod("initialize(QObject*)") >= 0:
                        QMetaObject.invokeMethod(inst, "initialize", Qt.DirectConnection, Q_ARG(QObject, self.main))
                self.native[info.id] = loader
            else:
                entry = info.path / info.entry
                mod_name = f"myoffice_addin_{info.id.replace('.', '_').replace('-', '_')}"
                spec = importlib.util.spec_from_file_location(mod_name, entry)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Cannot import {entry}")
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                api = AddinAPI(self, info)
                if not hasattr(mod, "register"):
                    raise AttributeError("The add-in has no register(api) function")
                mod.register(api)
                self.modules[info.id] = mod
                self.apis[info.id] = api
            info.loaded = True
            info.error = ""
            log.info("Loaded add-in %s %s", info.id, info.version)
            if self.main is not None:
                for doc in self.main.documents():
                    self.decorate(doc)
            return True
        except Exception as exc:
            info.loaded = False
            info.error = f"{exc.__class__.__name__}: {exc}"
            log.error("Add-in %s failed to load:\n%s", info.id, traceback.format_exc())
            self._cleanup(info.id)
            return False

    def _cleanup(self, addin_id: str):
        from app.sheet.formula.functions import FUNCTIONS

        for name in self.functions.pop(addin_id, []):
            FUNCTIONS.pop(name, None)
        self.ribbon_actions = [a for a in self.ribbon_actions if a["addin"] != addin_id]
        self.open_hooks = [h for h in self.open_hooks if h[0] != addin_id]

    def unload(self, info: AddinInfo) -> None:
        mod = self.modules.pop(info.id, None)
        api = self.apis.pop(info.id, None)
        if mod is not None and hasattr(mod, "unregister"):
            try:
                mod.unregister(api)
            except Exception as exc:
                log.warning("unregister() of %s failed: %s", info.id, exc)
        loader = self.native.pop(info.id, None)
        if loader is not None:
            try:
                loader.unload()
            except Exception:
                pass
        self._cleanup(info.id)
        info.loaded = False
        if self.main is not None:
            for doc in self.main.documents():
                self.undecorate(doc, info.id)

    def unload_all(self):
        for info in list(self.addins.values()):
            if info.loaded:
                self.unload(info)

    # -- enable / disable / install
    def set_enabled(self, addin_id: str, enabled: bool) -> None:
        disabled = set(cfg().get("plugins.disabled", []))
        (disabled.discard if enabled else disabled.add)(addin_id)
        cfg().set("plugins.disabled", sorted(disabled), save=True)
        info = self.addins.get(addin_id)
        if info is None:
            return
        info.enabled = enabled
        if enabled and not info.loaded:
            self.load(info)
        elif not enabled and info.loaded:
            self.unload(info)

    def install_zip(self, zip_path: str) -> AddinInfo:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            manifest_name = next((n for n in names if n.endswith("manifest.json") and n.count("/") <= 1), None)
            if manifest_name is None:
                raise ValueError("The ZIP file does not contain a manifest.json")
            m = json.loads(z.read(manifest_name).decode("utf-8"))
            for key in ("id", "name", "version"):
                if not m.get(key):
                    raise ValueError(f"manifest.json is missing '{key}'")
            prefix = manifest_name[: -len("manifest.json")]
            target = user_addins_dir() / m["id"]
            if target.exists():
                old = self.addins.get(m["id"])
                if old is not None and old.loaded:
                    self.unload(old)
                shutil.rmtree(target)
            target.mkdir(parents=True)
            for n in names:
                if not n.startswith(prefix) or n.endswith("/"):
                    continue
                rel = n[len(prefix):]
                dest = (target / rel).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    raise ValueError("Unsafe path inside the add-in archive")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(n))
        self.discover()
        info = self.addins[m["id"]]
        if info.enabled:
            self.load(info)
        return info

    def uninstall(self, addin_id: str) -> None:
        info = self.addins.get(addin_id)
        if info is None:
            return
        if info.builtin:
            raise PermissionError("Bundled add-ins cannot be uninstalled; disable them instead.")
        if info.loaded:
            self.unload(info)
        if info.path.is_dir():
            shutil.rmtree(info.path)
        elif info.path.is_file():
            info.path.unlink()
        self.addins.pop(addin_id, None)

    # -- integration with documents
    def decorate(self, doc) -> None:
        """Add the "Add-ins" ribbon tab with buttons registered for this document kind."""
        actions = [a for a in self.ribbon_actions if a["kind"] in (doc.kind, "all")]
        rb = doc.ribbon_widget() if hasattr(doc, "ribbon_widget") else None
        if rb is None:
            return
        old = getattr(doc, "_addin_page", None)
        if old is not None:
            idx = rb.tabs.indexOf(old.parentWidget().parentWidget()) if old.parentWidget() else -1
            if idx >= 0:
                rb.tabs.removeTab(idx)
            doc._addin_page = None
        if not actions:
            return
        from PySide6.QtGui import QAction

        from app.shell.icons import icon

        page = rb.page("Add-ins")
        doc._addin_page = page
        groups = {}
        for a in actions:
            g = groups.get(a["group"]) or page.group(a["group"])
            groups[a["group"]] = g
            act = QAction(icon(a["icon"]), a["text"], doc)
            act.setToolTip(a["tooltip"] or a["text"])
            act.triggered.connect(lambda _=False, cb=a["callback"], d=doc: self._safe_call(cb, d))
            g.large(act)
        for addin_id, hook in self.open_hooks:
            self._safe_call(hook, doc)

    def undecorate(self, doc, addin_id: str) -> None:
        self.decorate(doc)

    def _safe_call(self, cb, doc):
        try:
            cb(doc)
        except Exception as exc:
            log.error("Add-in callback failed: %s", traceback.format_exc())
            if self.main is not None:
                from app.shell.dialogs import show_error

                show_error(self.main, "Add-in error", str(exc), traceback.format_exc())
