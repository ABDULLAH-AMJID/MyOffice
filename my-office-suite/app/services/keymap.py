"""Centralized Keyboard Shortcut Registry, User Customization Engine, Custom Macro/Snippet Actions & JSON Profiles."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

from app.services.config import cfg


@dataclass
class ShortcutEntry:
    cmd_id: str
    category: str
    label: str
    default_seq: str
    description: str
    handler: Callable[[object], None] | None = None
    is_custom: bool = False
    custom_spec: dict | None = None


def _dispatch_editor_method(mw, method_names: tuple[str, ...]) -> None:
    cur = mw.current_document()
    if cur is None:
        return
    for name in method_names:
        fn = getattr(cur, name, None)
        if callable(fn):
            fn()
            return
        # Check child `.editor` / `.view`
        for sub_attr in ("editor", "view"):
            sub = getattr(cur, sub_attr, None)
            sfn = getattr(sub, name, None) if sub is not None else None
            if callable(sfn):
                sfn()
                return


def insert_text_into_active_widget(mw, text: str) -> bool:
    """Insert a user snippet into whichever editor / cell / input currently holds focus."""
    w = QApplication.focusWidget()
    if isinstance(w, (QTextEdit, QPlainTextEdit)):
        w.insertPlainText(text)
        return True
    if hasattr(w, "textCursor") and callable(w.textCursor):
        w.textCursor().insertText(text)
        return True
    if isinstance(w, QLineEdit):
        w.insert(text)
        return True
    cur = mw.current_document()
    if cur is not None:
        ed = getattr(cur, "editor", None)
        if isinstance(ed, (QTextEdit, QPlainTextEdit)):
            ed.insertPlainText(text)
            return True
        if ed is not None and hasattr(ed, "textCursor") and callable(ed.textCursor):
            ed.textCursor().insertText(text)
            return True
    return False


class KeymapManager:
    """Manages all built-in + custom shortcuts on ``MainWindow``, binds live ``QShortcut``s, and persists overrides."""

    def __init__(self, mw):
        self.mw = mw
        self._entries: dict[str, ShortcutEntry] = {}
        self._qshortcuts: list[QShortcut] = []
        self._closed_tabs_history: list[str] = []
        self._register_builtins()
        self._load_custom_entries()

    # ------------------------------------------------------------------ Built-in Professional Shortcut Catalog
    def _register_builtins(self) -> None:
        mw = self.mw

        def add(cid: str, cat: str, label: str, seq: str, desc: str, fn: Callable[[object], None]):
            self._entries[cid] = ShortcutEntry(cid, cat, label, seq, desc, fn, False, None)

        # 1. File & Document Creation
        add("file.new_writer", "File & Tabs", "New Document (Same Type / Writer)", "Ctrl+N",
            "Create a new document matching the active tab (or Writer document)", lambda w: w._new_same_kind())
        add("file.new_sheet", "File & Tabs", "New Spreadsheet Workbook", "Ctrl+Shift+N",
            "Create a blank Excel (.xlsx) spreadsheet workbook", lambda w: w.new_sheet())
        add("file.new_slide", "File & Tabs", "New Presentation Deck", "Ctrl+Alt+M",
            "Create a blank PowerPoint (.pptx) presentation", lambda w: w.new_slide())
        add("file.new_note", "File & Tabs", "New Text / Code File", "Ctrl+Alt+N",
            "Create a plain text / code scratchpad file for editing & LAN sharing", lambda w: w.new_shared_note())
        add("file.open", "File & Tabs", "Open File\u2026", "Ctrl+O",
            "Open any Office, PDF, Photo, Vault (.movault), or Text/Binary file", lambda w: w.open_dialog())
        add("file.save", "File & Tabs", "Save", "Ctrl+S",
            "Save changes to the active document", lambda w: w.save_current())
        add("file.save_as", "File & Tabs", "Save As\u2026", "Ctrl+Shift+S",
            "Save a copy of the active document under a new filename or format", lambda w: w.save_current_as())
        add("file.save_all", "File & Tabs", "Save All Open Tabs", "Ctrl+Alt+S",
            "Save every modified open tab in one step", lambda w: w.save_all_tabs())
        add("file.export_pdf", "File & Tabs", "Export Active Tab as PDF\u2026", "Ctrl+Alt+E",
            "Export the active Writer, Sheet, Slide, Photo, or Note tab to PDF", lambda w: w.export_current_pdf())
        add("file.print", "File & Tabs", "Print\u2026", "Ctrl+P",
            "Print the active document", lambda w: w.print_current())
        add("file.close_tab", "File & Tabs", "Close Active Tab", "Ctrl+W",
            "Close the currently open document tab", lambda w: w.close_current_tab())
        add("file.reopen_closed_tab", "File & Tabs", "Reopen Last Closed File", "Ctrl+Shift+T",
            "Reopen the most recently closed document tab", lambda w: w.reopen_last_closed_tab())

        # 2. Navigation, Workspace & View
        add("nav.command_palette", "Navigation & View", "Command Palette / Quick Action Search", "Ctrl+Shift+P",
            "Search and launch any command, tool, or recent document instantly", lambda w: w.open_command_palette())
        add("nav.quick_search", "Navigation & View", "Tell Me / Quick Search", "Alt+Q",
            "Open the instant MyOffice command & tool launcher", lambda w: w.open_command_palette())
        add("nav.shortcuts", "Navigation & View", "Keyboard Shortcuts Manager\u2026", "Ctrl+K",
            "View, customize, import/export, or create custom keyboard shortcuts", lambda w: w.open_shortcuts_dialog())
        add("nav.home_tab", "Navigation & View", "Go to Home Tab", "Alt+H",
            "Switch directly to the MyOffice Home start screen", lambda w: w.tabs.setCurrentIndex(0))
        add("nav.next_tab", "Navigation & View", "Next Open Tab", "Ctrl+Tab",
            "Switch forward to the next open tab",
            lambda w: w.tabs.setCurrentIndex((w.tabs.currentIndex() + 1) % max(1, w.tabs.count())))
        add("nav.prev_tab", "Navigation & View", "Previous Open Tab", "Ctrl+Shift+Tab",
            "Switch backward to the previous open tab",
            lambda w: w.tabs.setCurrentIndex((w.tabs.currentIndex() - 1) % max(1, w.tabs.count())))
        add("nav.toggle_ribbon", "Navigation & View", "Collapse / Expand Ribbon", "Ctrl+F1",
            "Show or hide the formatting ribbon bar for extra screen space", lambda w: w.toggle_ribbon_visibility())
        add("nav.toggle_dark", "Navigation & View", "Toggle Dark / Light Theme", "Ctrl+Shift+D",
            "Switch instantly between Light and Dark UI themes", lambda w: w.toggle_theme_mode())
        add("nav.fullscreen", "Navigation & View", "Toggle Full Screen Mode", "F11",
            "Toggle distraction-free full-screen window mode",
            lambda w: w.showNormal() if w.isFullScreen() else w.showFullScreen())
        add("nav.settings", "Navigation & View", "Settings & File Associations\u2026", "Ctrl+,",
            "Open MyOffice preferences, language, and Windows Explorer icon settings", lambda w: w.open_settings())

        # 3. Live Collaboration, Gmail Sync & Hardware Vault
        add("collab.share", "Collaboration & Cloud", "Share File Live on LAN / Show QR Code\u2026", "Ctrl+Shift+L",
            "Start multi-file real-time LAN collaboration, copy share link, or scan QR", lambda w: w.open_tool("collaborate"))
        add("collab.quick_copy_link", "Collaboration & Cloud", "Quick Share Active File & Copy LAN Link", "Ctrl+Alt+L",
            "Instantly host the active file on LAN and copy its link to clipboard", lambda w: w.quick_share_active_file())
        add("collab.toggle_chat", "Collaboration & Cloud", "Toggle Live Team Chat Panel", "Ctrl+Shift+M",
            "Focus or toggle the per-file Team Chat sidebar in the active tab", lambda w: w.focus_or_toggle_collab_chat())
        add("sync.cloud", "Collaboration & Cloud", "Cloud Sync (Gmail / Drive / Router / S3)\u2026", "Ctrl+Shift+Y",
            "Configure or run two-way folder sync with Gmail, Google Drive, Router USB, or S3",
            lambda w: w.open_tool("cloud_sync"))
        add("sync.mobile", "Collaboration & Cloud", "Sync & Pair with Android Mobile App\u2026", "Ctrl+Alt+Y",
            "Two-way Wi-Fi / Hotspot sync, 1-scan QR pairing, and Vault key sharing with MyOffice Android",
            lambda w: w.open_tool("mobile_sync"))
        add("vault.center", "Collaboration & Cloud", "Backup & Hardware Security Vault\u2026", "Ctrl+Shift+B",
            "Open Local Revisions, Router USB Hardware-Encrypted Vault, and Gmail Cloud Backup",
            lambda w: w.open_tool("backup_center"))
        add("vault.quick_encrypt", "Collaboration & Cloud", "Quick-Encrypt Active File to Router/USB Vault", "Ctrl+Alt+B",
            "Encrypt and back up the active file immediately to your configured Hardware Vault",
            lambda w: w.quick_vault_backup_active())

        # 4. PDF Suite, AI, OCR & Productivity Tools
        add("tools.all", "PDF & Suite Tools", "All Tools Hub\u2026", "Ctrl+Shift+A",
            "Browse all 45+ PDF, Office, Photo, Recording, Security, and AI tools", lambda w: w.open_all_tools())
        add("pdf.toolbox", "PDF & Suite Tools", "PDF Toolbox (Merge / Split / Stamp / Compress)\u2026", "Ctrl+Alt+P",
            "Open the multi-tool PDF processing suite", lambda w: w.open_tool("pdf_merge"))
        add("pdf.ocr", "PDF & Suite Tools", "OCR Scanned PDF to Searchable PDF\u2026", "Ctrl+Shift+O",
            "Run offline Tesseract OCR (English / Urdu / Hindi) on any scanned PDF", lambda w: w.open_tool("pdf_ocr"))
        add("tools.pic_to_text", "PDF & Suite Tools", "Picture to Text / Word / Excel (OCR)\u2026", "Ctrl+Alt+O",
            "Extract editable text or tables from any screenshot or image", lambda w: w.open_tool("picture_to_text"))
        add("tools.screenshot", "PDF & Suite Tools", "Capture Region Screenshot\u2026", "Ctrl+Alt+X",
            "Capture a screen region with annotations or instant OCR", lambda w: w.open_tool("screen_capture"))
        add("tools.recorder", "PDF & Suite Tools", "Screen & Audio Recorder\u2026", "Ctrl+Alt+R",
            "Record screen video and audio to MP4", lambda w: w.open_tool("screen_recorder"))
        add("tools.ai", "PDF & Suite Tools", "AI Assistant Settings (Ollama / OpenAI / Anthropic)\u2026", "Ctrl+Shift+I",
            "Configure local offline Ollama/LM Studio or cloud AI provider", lambda w: w.open_tool("ai_settings"))
        add("tools.repair", "PDF & Suite Tools", "Repair Damaged Office Document\u2026", "Ctrl+Alt+F",
            "Recover corrupted .docx, .xlsx, or .pptx archives", lambda w: w.open_tool("file_repair"))
        add("tools.rename", "PDF & Suite Tools", "Batch File Rename\u2026", "F2" if False else "Ctrl+Shift+R",
            "Batch rename multiple files with live preview", lambda w: w.open_tool("batch_rename"))

        # 5. Active Editor Actions (non-conflicting global fallbacks alongside per-editor Ctrl+F / Ctrl++ / Ctrl+-)
        add("edit.find_in_doc", "Editing & Zoom", "Find / Search in Active Document", "Alt+F",
            "Open Find bar or focus search in Writer, Sheet, Slide, PDF, or Code tab",
            lambda w: _dispatch_editor_method(w, ("find_replace", "show_find", "focus_search", "do_search")))
        add("edit.zoom_in", "Editing & Zoom", "Zoom In", "Ctrl+Alt+=",
            "Increase zoom level in the active document viewer/editor",
            lambda w: w.adjust_active_zoom(+10))
        add("edit.zoom_out", "Editing & Zoom", "Zoom Out", "Ctrl+Alt+-",
            "Decrease zoom level in the active document viewer/editor",
            lambda w: w.adjust_active_zoom(-10))
        add("edit.zoom_reset", "Editing & Zoom", "Reset Zoom to 100%", "Ctrl+Alt+0",
            "Reset zoom level to 100% in the active document",
            lambda w: w.adjust_active_zoom(0, reset=True))

    # ------------------------------------------------------------------ Custom User Actions
    def _load_custom_entries(self) -> None:
        # Remove previous custom entries first
        for cid in [k for k, v in self._entries.items() if v.is_custom]:
            del self._entries[cid]

        for spec in (cfg.get("shortcuts.custom") or []):
            cid = spec.get("id") or f"custom.{secrets.token_hex(4)}"
            kind = spec.get("action_type", "snippet")
            label = spec.get("label") or "Custom Action"
            seq = spec.get("seq", "")
            target = spec.get("target", "")
            desc = f"Custom {kind}: {target[:60]}"
            self._entries[cid] = ShortcutEntry(
                cid, "Custom Actions", label, seq, desc,
                handler=self._make_custom_handler(kind, target),
                is_custom=True,
                custom_spec=dict(spec, id=cid),
            )

    def _make_custom_handler(self, kind: str, target: str) -> Callable[[object], None]:
        if kind == "snippet":
            return lambda w: insert_text_into_active_widget(w, target)
        if kind == "open_target":
            def _open_it(w):
                if target.startswith(("http://", "https://")):
                    QDesktopServices.openUrl(QUrl(target))
                elif os.path.isdir(target):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(target))
                elif os.path.exists(target):
                    w.open_path(target)
            return _open_it
        if kind == "tool":
            return lambda w: w.open_tool(target)
        return lambda _w: None

    # ------------------------------------------------------------------ Query & Mutation API
    def entries(self) -> list[ShortcutEntry]:
        return list(self._entries.values())

    def overrides(self) -> dict[str, str]:
        return dict(cfg.get("shortcuts.overrides") or {})

    def sequence_for(self, cmd_id: str) -> str:
        ov = self.overrides()
        if cmd_id in ov:
            return ov[cmd_id]
        ent = self._entries.get(cmd_id)
        return ent.default_seq if ent else ""

    def find_conflict(self, seq: str, exclude_id: str = "") -> ShortcutEntry | None:
        norm = QKeySequence(seq).toString(QKeySequence.PortableText)
        if not norm:
            return None
        for ent in self._entries.values():
            if ent.cmd_id == exclude_id:
                continue
            cur = QKeySequence(self.sequence_for(ent.cmd_id)).toString(QKeySequence.PortableText)
            if cur and cur == norm:
                return ent
        return None

    def set_sequence(self, cmd_id: str, new_seq: str, clear_conflicts: bool = True) -> None:
        norm = QKeySequence(new_seq).toString(QKeySequence.PortableText) if new_seq else ""
        ov = self.overrides()
        if clear_conflicts and norm:
            clash = self.find_conflict(norm, exclude_id=cmd_id)
            while clash is not None:
                ov[clash.cmd_id] = ""
                cfg.set("shortcuts.overrides", ov)
                clash = self.find_conflict(norm, exclude_id=cmd_id)

        ent = self._entries.get(cmd_id)
        if ent and ent.is_custom and ent.custom_spec is not None:
            ent.default_seq = norm
            ent.custom_spec["seq"] = norm
            customs = [e.custom_spec for e in self._entries.values() if e.is_custom and e.custom_spec]
            cfg.set("shortcuts.custom", customs)
            ov.pop(cmd_id, None)
        elif ent and norm == ent.default_seq:
            ov.pop(cmd_id, None)
        else:
            ov[cmd_id] = norm
        cfg.set("shortcuts.overrides", ov)
        self.apply_to_window()

    def add_custom_shortcut(self, label: str, seq: str, action_type: str, target: str) -> ShortcutEntry:
        cid = f"custom.{secrets.token_hex(4)}"
        norm = QKeySequence(seq).toString(QKeySequence.PortableText)
        spec = {"id": cid, "label": label.strip(), "seq": norm, "action_type": action_type, "target": target}
        customs = list(cfg.get("shortcuts.custom") or [])
        customs.append(spec)
        cfg.set("shortcuts.custom", customs)
        self._load_custom_entries()
        if norm:
            self.set_sequence(cid, norm, clear_conflicts=True)
        else:
            self.apply_to_window()
        return self._entries[cid]

    def remove_custom_shortcut(self, cmd_id: str) -> None:
        customs = [s for s in (cfg.get("shortcuts.custom") or []) if s.get("id") != cmd_id]
        cfg.set("shortcuts.custom", customs)
        self._load_custom_entries()
        self.apply_to_window()

    def reset_one(self, cmd_id: str) -> None:
        ov = self.overrides()
        ov.pop(cmd_id, None)
        cfg.set("shortcuts.overrides", ov)
        self.apply_to_window()

    def reset_all(self) -> None:
        cfg.set("shortcuts.overrides", {})
        self.apply_to_window()

    def export_profile(self, path: str | os.PathLike) -> None:
        payload = {
            "app": "MyOffice",
            "version": 1,
            "overrides": self.overrides(),
            "custom": list(cfg.get("shortcuts.custom") or []),
        }
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def import_profile(self, path: str | os.PathLike) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        ov = data.get("overrides") or {}
        custom = data.get("custom") or []
        cfg.set("shortcuts.overrides", ov)
        cfg.set("shortcuts.custom", custom)
        self._load_custom_entries()
        self.apply_to_window()
        return len(ov) + len(custom)

    def trigger(self, cmd_id: str) -> bool:
        ent = self._entries.get(cmd_id)
        if ent and ent.handler:
            ent.handler(self.mw)
            return True
        return False

    # ------------------------------------------------------------------ Live Qt Shortcut Binding
    def apply_to_window(self) -> None:
        """Bind active key sequences to ``MainWindow`` cleanly without breaking child editor focus shortcuts."""
        for sc in self._qshortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._qshortcuts.clear()

        # Skip duplicating sequences already held as native QActions on MainWindow unless user customized them
        ov = self.overrides()
        for ent in self._entries.values():
            seq = self.sequence_for(ent.cmd_id)
            if not seq or not ent.handler:
                continue
            # Keep Ctrl+F non-intrusive when a sub-editor already handles its own Ctrl+F QAction seamlessly
            sc = QShortcut(QKeySequence(seq), self.mw)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(lambda e=ent: e.handler(self.mw))
            self._qshortcuts.append(sc)
        # Keep MainWindow File menu labels synchronized with user's custom keybindings!
        if hasattr(self.mw, "_sync_menu_shortcut_labels"):
            self.mw._sync_menu_shortcut_labels(ov)
