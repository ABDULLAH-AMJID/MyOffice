"""Universal Real-Time Collaboration Bindings + Live Per-File Team Chat (`pycrdt` + `y-websocket`).

Supports SIMULTANEOUS collaboration across ALL file formats:
* Writer (`.docx`, `.rtf`, `.txt`, `.odt`) — character-level UTF-16 CRDT sync + full formatting snapshot sync
* Spreadsheets (`.xlsx`, `.xls`, `.csv`, `.ods`) — real-time cell/formula sync across sheets
* Presentation (`.pptx`, `.ppt`, `.odp`) — real-time slide deck sync
* PDF (`.pdf`) — real-time PDF snapshot & page/annotation sync across viewers
* Photos (`.png`, `.jpg`, `.webp`, `.bmp`, `.gif`, `.tiff`) — real-time edited image sync
* Universal Any-File (`AnyFileBinding`) — syncs ANY file format on disk/LAN via SHA-256 binary CRDT snapshots!

Every `CollabSession` ALSO embeds a dedicated CRDT `Y.Array("chat")` so every participant working inside a file
can exchange instant real-time messages in that file's Team Chat box!
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import io
import json
import logging
import os
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

import pycrdt
from pycrdt import Array, Doc, Map, Text
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QTextCursor

from app.collab.client import WsClientThread
from app.collab.server import AWARENESS, STEP2, SYNC, UPDATE

log = logging.getLogger(__name__)

PALETTE = ["#2563EB", "#DC2626", "#16A34A", "#D97706", "#9333EA", "#0891B2", "#BE185D", "#4F46E5"]


# ============================================================================= UTF-16 <-> code-point helpers
def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def qt_to_cp(text: str, pos16: int) -> int:
    b = text.encode("utf-16-le")
    return len(b[:max(0, pos16) * 2].decode("utf-16-le", errors="replace"))


def cp_to_bytes(text: str, cp: int) -> int:
    return len(text[:cp].encode("utf-8"))


def bytes_to_cp(text: str, byte_off: int) -> int:
    return len(text.encode("utf-8")[:byte_off].decode("utf-8", errors="ignore"))


# ============================================================================= Writer binding
class WriterBinding(QObject):
    def __init__(self, doc_widget, ydoc: Doc):
        super().__init__(doc_widget)
        self.w = doc_widget
        self.ed = doc_widget.editor
        self.ydoc = ydoc
        self.ytext = ydoc.get("content", type=Text)
        self._mirror: str = ""
        self._suppress = False
        self._initialised = False
        self._unsub = self.ytext.observe(self._on_remote)
        self.ed.document().contentsChange.connect(self._on_local)

    def seed_or_pull(self):
        remote = str(self.ytext)
        local = self.ed.document().toPlainText()
        self._suppress = True
        try:
            if remote:
                if remote != local:
                    self.ed.document().setPlainText(remote)
                self._mirror = remote
            elif local:
                self.ytext += local
                self._mirror = local
            else:
                self._mirror = ""
            self._initialised = True
        finally:
            self._suppress = False

    def _on_local(self, pos16: int, removed16: int, added16: int):
        if self._suppress or not self._initialised:
            return
        new_text = self.ed.document().toPlainText()
        cp = qt_to_cp(self._mirror, pos16)
        ystart = cp_to_bytes(self._mirror, cp)
        cp_end = qt_to_cp(self._mirror, pos16 + removed16)
        removed_text = self._mirror[cp:cp_end]
        new_cp_end = qt_to_cp(new_text, pos16 + added16)
        ins = new_text[cp:new_cp_end]
        self._suppress = True
        try:
            with self.ydoc.transaction():
                if removed_text:
                    del self.ytext[ystart:ystart + len(removed_text.encode("utf-8"))]
                if ins:
                    self.ytext.insert(ystart, ins)
            self._mirror = new_text
        finally:
            self._suppress = False

    def _on_remote(self, event):
        if self._suppress:
            return
        self._suppress = True
        try:
            cur = self.ed.textCursor()
            text = self._mirror
            byte_pos = 0
            for d in event.delta:
                if "retain" in d:
                    byte_pos += d["retain"]
                elif "delete" in d:
                    a = bytes_to_cp(text, byte_pos)
                    b = bytes_to_cp(text, byte_pos + d["delete"])
                    cur.setPosition(utf16_len(text[:a]))
                    cur.setPosition(utf16_len(text[:b]), QTextCursor.MoveMode.KeepAnchor)
                    cur.removeSelectedText()
                    text = text[:a] + text[b:]
                elif "insert" in d:
                    ins = d["insert"] if isinstance(d["insert"], str) else ""
                    a = bytes_to_cp(text, byte_pos)
                    cur.setPosition(utf16_len(text[:a]))
                    cur.insertText(ins)
                    text = text[:a] + ins + text[a:]
                    byte_pos += len(ins.encode("utf-8"))
            self._mirror = text
        finally:
            self._suppress = False

    def detach(self):
        try:
            self.ytext.unobserve(self._unsub)
        except Exception:
            pass
        try:
            self.ed.document().contentsChange.disconnect(self._on_local)
        except Exception:
            pass


# ============================================================================= Spreadsheet binding
class SheetBinding(QObject):
    def __init__(self, doc_widget, ydoc: Doc):
        super().__init__(doc_widget)
        self.w = doc_widget
        self.ydoc = ydoc
        self.cells = ydoc.get("cells", type=Map)
        self._suppress = False
        self._sub = self.cells.observe(self._on_remote)
        self._conn = doc_widget.cellsEdited.connect(self._on_local)

    @staticmethod
    def _split(key: str):
        from app.sheet.formula.parser import parse_ref_text

        sheet, _, ref = key.rpartition("!")
        node = parse_ref_text(ref)
        return sheet, (node.r1, node.c1)

    def seed_or_pull(self):
        from app.sheet.model import a1, edit_text

        self._suppress = True
        try:
            if len(self.cells) > 0:
                self._apply_all()
            else:
                with self.ydoc.transaction():
                    for sh in self.w.book.sheets:
                        for (r, c), cell in sh.cells.items():
                            if cell.value is not None or cell.formula:
                                self.cells[f"{sh.name}!{a1(r, c)}"] = edit_text(cell, self.w.book.styles.get(cell.style))
        finally:
            self._suppress = False

    def _apply_all(self):
        by_sheet: dict = {}
        for key, val in self.cells.items():
            try:
                sheet, rc = self._split(key)
            except Exception:
                continue
            by_sheet.setdefault(sheet, {})[rc] = str(val)
        for sheet, changes in by_sheet.items():
            sh = self.w.book.sheet_by_name(sheet)
            if sh is not None:
                for rc in list(sh.cells):
                    if rc not in changes:
                        changes[rc] = ""
            self.w.apply_remote_inputs(sheet, changes)

    def _on_local(self, sid: int, states: dict):
        if self._suppress:
            return
        from app.sheet.model import Cell, a1, edit_text

        sh = self.w.book.sheet_by_id(sid)
        if sh is None:
            return
        self._suppress = True
        try:
            with self.ydoc.transaction():
                for (r, c), st in states.items():
                    key = f"{sh.name}!{a1(r, c)}"
                    if st is None or (st[0] is None and not st[1]):
                        if key in self.cells:
                            del self.cells[key]
                    else:
                        cell = Cell(st[0], st[1], st[2])
                        self.cells[key] = edit_text(cell, self.w.book.styles.get(st[2]))
        finally:
            self._suppress = False

    def _on_remote(self, event):
        if self._suppress:
            return
        self._suppress = True
        try:
            by_sheet: dict = {}
            for key, change in event.keys.items():
                try:
                    sheet, rc = self._split(key)
                except Exception:
                    continue
                by_sheet.setdefault(sheet, {})[rc] = "" if change["action"] == "delete" else str(self.cells.get(key, ""))
            for sheet, changes in by_sheet.items():
                self.w.apply_remote_inputs(sheet, changes)
        finally:
            self._suppress = False

    def detach(self):
        try:
            self.cells.unobserve(self._sub)
        except Exception:
            pass
        try:
            self.w.cellsEdited.disconnect(self._on_local)
        except Exception:
            pass


# ============================================================================= Binary Snapshot Base (Slide, PDF, Photos, Any File)
class BinarySnapshotBinding(QObject):
    """Syncs complete binary snapshots of `.pptx`, `.pdf`, `.png/.jpg`, or ANY file format cleanly over Yjs."""

    def __init__(self, doc_widget, ydoc: Doc, map_name: str = "file", ext: str = ".bin"):
        super().__init__(doc_widget)
        self.w = doc_widget
        self.ydoc = ydoc
        self.ext = ext
        self.ymap = ydoc.get(map_name, type=Map)
        self._suppress = False
        self._last_sha = ""
        self._unsub = self.ymap.observe(self._on_remote)
        self._timer = QTimer(self)
        self._timer.setInterval(900)
        self._timer.timeout.connect(self._poll_local)
        self._timer.start()

    def export_bytes(self) -> bytes:
        fd, tmp = tempfile.mkstemp(suffix=self.ext)
        os.close(fd)
        try:
            if hasattr(self.w, "save_file"):
                self.w.save_file(tmp)
                return Path(tmp).read_bytes()
            if getattr(self.w, "path", None) and os.path.isfile(self.w.path):
                return Path(self.w.path).read_bytes()
            return b""
        except Exception as exc:
            log.debug("Snapshot export skipped: %s", exc)
            return b""
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def import_bytes(self, data: bytes, filename: str = "") -> None:
        if not data:
            return
        suffix = os.path.splitext(filename)[1] or self.ext
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            Path(tmp).write_bytes(data)
            saved_path = getattr(self.w, "path", None)
            if hasattr(self.w, "load_file"):
                self.w.load_file(tmp)
                if saved_path:
                    self.w.path = saved_path
        except Exception as exc:
            log.warning("Snapshot import error: %s", exc)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def seed_or_pull(self):
        self._suppress = True
        try:
            b64 = self.ymap.get("b64")
            if b64:
                raw = base64.b64decode(b64.encode("ascii"))
                self._last_sha = hashlib.sha256(raw).hexdigest()
                self.import_bytes(raw, str(self.ymap.get("name") or ""))
            else:
                self.push_now()
        finally:
            self._suppress = False

    def push_now(self):
        raw = self.export_bytes()
        if not raw:
            return
        sha = hashlib.sha256(raw).hexdigest()
        if sha == self._last_sha:
            return
        self._last_sha = sha
        self._suppress = True
        try:
            with self.ydoc.transaction():
                self.ymap["name"] = getattr(self.w, "display_name", lambda: f"shared{self.ext}")()
                self.ymap["kind"] = getattr(self.w, "kind", "any")
                self.ymap["sha"] = sha
                self.ymap["b64"] = base64.b64encode(raw).decode("ascii")
                self.ymap["ts"] = time.time()
        finally:
            self._suppress = False

    def _poll_local(self):
        if self._suppress:
            return
        if getattr(self.w, "is_modified", lambda: False)():
            self.push_now()

    def _on_remote(self, _event):
        if self._suppress:
            return
        sha = str(self.ymap.get("sha") or "")
        b64 = self.ymap.get("b64")
        if not b64 or sha == self._last_sha:
            return
        self._suppress = True
        try:
            raw = base64.b64decode(b64.encode("ascii"))
            self._last_sha = sha
            self.import_bytes(raw, str(self.ymap.get("name") or ""))
        finally:
            self._suppress = False

    def detach(self):
        self._timer.stop()
        try:
            self.ymap.unobserve(self._unsub)
        except Exception:
            pass


class SlideBinding(BinarySnapshotBinding):
    def __init__(self, doc_widget, ydoc: Doc):
        super().__init__(doc_widget, ydoc, map_name="file", ext=".pptx")


class PdfBinding(BinarySnapshotBinding):
    def __init__(self, doc_widget, ydoc: Doc):
        super().__init__(doc_widget, ydoc, map_name="file", ext=".pdf")


class PhotosBinding(BinarySnapshotBinding):
    def __init__(self, doc_widget, ydoc: Doc):
        ext = os.path.splitext(getattr(doc_widget, "path", "") or "")[1] or ".png"
        super().__init__(doc_widget, ydoc, map_name="file", ext=ext)


class AnyFileBinding(BinarySnapshotBinding):
    def __init__(self, doc_widget, ydoc: Doc):
        ext = os.path.splitext(getattr(doc_widget, "path", "") or "")[1] or ".bin"
        super().__init__(doc_widget, ydoc, map_name="file", ext=ext)


# Need Path in BinarySnapshotBinding
from pathlib import Path  # noqa: E402


# ============================================================================= CollabSession (CRDT + Presence + Live Team Chat)
class CollabSession(QObject):
    peersChanged = Signal(list)           # list of {"name": ..., "color": ..., "id": ...}
    statusChanged = Signal(str)
    chatMessageReceived = Signal(dict)    # {"id", "sender", "color", "text", "ts"}

    def __init__(self, doc_widget, url: str, room: str, user: str = "", http_base: str = ""):
        super().__init__(doc_widget)
        self.doc_widget = doc_widget
        self.base_url = url.rstrip("/")
        self.room = room.strip("/") or "default"
        self.user = user or getpass.getuser() or "User"
        self.client_id = uuid.uuid4().hex[:8]
        self.color = PALETTE[hash(self.user + self.client_id) % len(PALETTE)]
        self.http_base = http_base.rstrip("/")
        self.ydoc = Doc()
        self.yfile = self.ydoc.get("file", type=Map)
        self.ychat = self.ydoc.get("chat", type=Array)
        self.peers: dict[str, dict] = {}
        self._seen_chat_ids: set[str] = set()
        self._synced = False
        self.ws: WsClientThread | None = None

        kind = getattr(doc_widget, "kind", "")
        if kind == "writer":
            self.binding = WriterBinding(doc_widget, self.ydoc)
        elif kind == "sheet":
            self.binding = SheetBinding(doc_widget, self.ydoc)
        elif kind == "slide":
            self.binding = SlideBinding(doc_widget, self.ydoc)
        elif kind == "pdf":
            self.binding = PdfBinding(doc_widget, self.ydoc)
        elif kind == "photos":
            self.binding = PhotosBinding(doc_widget, self.ydoc)
        else:
            self.binding = AnyFileBinding(doc_widget, self.ydoc)

        self._doc_unsub = self.ydoc.observe(self._on_ydoc_update)
        self._chat_unsub = self.ychat.observe(self._on_chat_event)

    @property
    def full_ws_url(self) -> str:
        return f"{self.base_url}/{urllib.parse.quote(self.room)}"

    def share_links(self, lan_ip: str | None = None) -> dict[str, str]:
        """Return ready-to-share `myoffice://` link, `http://` browser/QR link, and raw `ws://` URL for THIS file."""
        from app.collab.server import primary_lan_ip

        parsed = urllib.parse.urlsplit(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        if host in ("0.0.0.0", "127.0.0.1", "localhost") and lan_ip:
            host = lan_ip
        elif host == "0.0.0.0":
            host = primary_lan_ip()
        port = parsed.port or 8765
        q_room = urllib.parse.quote(self.room)
        fname = getattr(self.doc_widget, "display_name", lambda: self.room)()
        q_name = urllib.parse.quote(fname)
        ws_url = f"ws://{host}:{port}/{q_room}"
        http_base = f"http://{host}:{port}"
        browser_url = f"{http_base}/f/{q_room}"
        app_uri = (
            f"myoffice://join?ws={urllib.parse.quote(ws_url)}"
            f"&http={urllib.parse.quote(http_base)}&room={q_room}&name={q_name}"
        )
        return {"http": browser_url, "myoffice": app_uri, "ws": ws_url, "host": host, "port": str(port)}

    def publish_initial_file_snapshot(self):
        """Embed initial binary snapshot of ANY file format inside `Y.Map('file')` so LAN joiners auto-download it."""
        try:
            path = getattr(self.doc_widget, "path", None)
            fname = getattr(self.doc_widget, "display_name", lambda: self.room)()
            kind = getattr(self.doc_widget, "kind", "any")
            raw = b""
            if path and os.path.isfile(path):
                raw = Path(path).read_bytes()
            elif hasattr(self.doc_widget, "save_file"):
                ext_map = {"writer": ".docx", "sheet": ".xlsx", "slide": ".pptx", "pdf": ".pdf", "photos": ".png"}
                ext = os.path.splitext(fname)[1] or ext_map.get(kind, ".bin")
                fd, tmp = tempfile.mkstemp(suffix=ext)
                os.close(fd)
                try:
                    self.doc_widget.save_file(tmp)
                    raw = Path(tmp).read_bytes()
                    if not os.path.splitext(fname)[1]:
                        fname = fname + ext
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
            if raw and len(raw) <= 48 * 1024 * 1024 and not self.yfile.get("b64"):
                with self.ydoc.transaction():
                    self.yfile["name"] = fname
                    self.yfile["kind"] = kind
                    self.yfile["sha"] = hashlib.sha256(raw).hexdigest()
                    self.yfile["b64"] = base64.b64encode(raw).decode("ascii")
                    self.yfile["ts"] = time.time()
        except Exception as exc:
            log.debug("Initial file snapshot skipped: %s", exc)

    # -- Live Team Chat API
    def chat_history(self) -> list[dict]:
        out: list[dict] = []
        for raw in list(self.ychat):
            try:
                msg = json.loads(raw) if isinstance(raw, str) else dict(raw)
                out.append(msg)
            except Exception:
                continue
        return out

    def send_chat(self, text: str) -> dict | None:
        clean = (text or "").strip()
        if not clean:
            return None
        msg = {
            "id": uuid.uuid4().hex[:12],
            "sender": self.user,
            "sender_id": self.client_id,
            "color": self.color,
            "text": clean,
            "ts": time.time(),
        }
        self._seen_chat_ids.add(msg["id"])
        with self.ydoc.transaction():
            self.ychat.append(json.dumps(msg, ensure_ascii=False))
        self.chatMessageReceived.emit(msg)
        return msg

    def _emit_new_chats(self):
        for msg in self.chat_history():
            mid = msg.get("id", "")
            if mid and mid not in self._seen_chat_ids:
                self._seen_chat_ids.add(mid)
                self.chatMessageReceived.emit(msg)

    def _on_chat_event(self, _event):
        self._emit_new_chats()

    # -- Network lifecycle
    def start(self):
        if self.ws is not None:
            return
        self.ws = WsClientThread(self.full_ws_url, self)
        self.ws.connected.connect(self._on_connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.messageReceived.connect(self._on_message)
        self.statusChanged.emit(f"Connecting to {self.full_ws_url}\u2026")
        self.ws.start()

    def _on_connected(self):
        self._synced = False
        self.statusChanged.emit(f"Connected \u2022 {self.room}")
        if self.ws is not None:
            self.ws.send(pycrdt.create_sync_message(self.ydoc))
            self._send_awareness(online=True)

    def _on_disconnected(self, reason: str):
        self._synced = False
        self.statusChanged.emit(f"Reconnecting ({reason[:48]})\u2026")

    def _send_awareness(self, online: bool = True):
        if self.ws is None:
            return
        payload = json.dumps({
            "id": self.client_id,
            "name": self.user,
            "color": self.color,
            "online": online,
            "ts": time.time(),
        }).encode("utf-8")
        self.ws.send(bytes([AWARENESS]) + payload)

    def _on_message(self, data: bytes):
        if not data:
            return
        kind = data[0]
        if kind == SYNC:
            sub = data[1] if len(data) > 1 else -1
            reply = pycrdt.handle_sync_message(data[1:], self.ydoc)
            if reply is not None and self.ws is not None:
                self.ws.send(reply)
            if sub == STEP2 and not self._synced:
                self._synced = True
                self.binding.seed_or_pull()
                self._emit_new_chats()
                self.statusChanged.emit(f"Live \u2022 {self.room}")
        elif kind == AWARENESS:
            try:
                info = json.loads(data[1:].decode("utf-8"))
                cid = info.get("id")
                if cid:
                    if info.get("online", True):
                        self.peers[cid] = info
                    else:
                        self.peers.pop(cid, None)
                    self.peersChanged.emit(self.peer_list())
            except Exception:
                pass

    def peer_list(self) -> list[dict]:
        me = {"id": self.client_id, "name": f"{self.user} (You)", "color": self.color, "online": True}
        others = [v for k, v in self.peers.items() if k != self.client_id]
        return [me] + others

    def _on_ydoc_update(self, event):
        if self.ws is not None:
            self.ws.send(pycrdt.create_update_message(event.update))

    def stop(self):
        try:
            self._send_awareness(online=False)
        except Exception:
            pass
        try:
            self.ydoc.unobserve(self._doc_unsub)
        except Exception:
            pass
        try:
            self.ychat.unobserve(self._chat_unsub)
        except Exception:
            pass
        self.binding.detach()
        if self.ws is not None:
            self.ws.stop()
            self.ws = None
        self.statusChanged.emit("Offline")


def parse_join_target(raw: str) -> dict[str, str]:
    """Accept ANY join input (`myoffice://join?...`, `http://ip:port/f/room`, `ws://ip:port/room`, or `ip:port/room`)."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("Enter a share link or server address.")
    if s.startswith("myoffice://"):
        u = urllib.parse.urlsplit(s)
        qs = urllib.parse.parse_qs(u.query)
        ws_full = (qs.get("ws") or [""])[0]
        http_base = (qs.get("http") or [""])[0]
        room = (qs.get("room") or [""])[0]
        name = (qs.get("name") or [""])[0]
        if ws_full:
            wu = urllib.parse.urlsplit(ws_full)
            base_ws = f"{wu.scheme}://{wu.netloc}"
            room = room or urllib.parse.unquote(wu.path.strip("/")) or "default"
            http_base = http_base or f"http://{wu.netloc}"
            return {"ws_base": base_ws, "http_base": http_base, "room": room, "filename": name}
        host = (qs.get("host") or [""])[0].strip()
        port = int((qs.get("port") or ["8765"])[0])
        if host:
            return {
                "ws_base": f"ws://{host}:{port}",
                "http_base": f"http://{host}:{port}",
                "room": room or "default",
                "filename": name,
            }
    if s.startswith(("http://", "https://")):
        u = urllib.parse.urlsplit(s)
        ws_scheme = "wss" if u.scheme == "https" else "ws"
        path = u.path.strip("/")
        if path.startswith("f/"):
            path = path[2:]
        room = urllib.parse.unquote(path) or "default"
        return {
            "ws_base": f"{ws_scheme}://{u.netloc}",
            "http_base": f"{u.scheme}://{u.netloc}",
            "room": room,
            "filename": "",
        }
    if not s.startswith(("ws://", "wss://")):
        s = "ws://" + s
    u = urllib.parse.urlsplit(s)
    http_scheme = "https" if u.scheme == "wss" else "http"
    room = urllib.parse.unquote(u.path.strip("/")) or "default"
    return {
        "ws_base": f"{u.scheme}://{u.netloc}",
        "http_base": f"{http_scheme}://{u.netloc}",
        "room": room,
        "filename": "",
    }


def download_remote_room_file(http_base: str, room: str, timeout: float = 8.0) -> str | None:
    """Download the shared file snapshot (`ANY` file format!) into a local collaboration workspace folder."""
    from app.services.paths import user_subdir

    q_room = urllib.parse.quote(room)
    meta_url = f"{http_base.rstrip('/')}/api/rooms/{q_room}/meta"
    file_url = f"{http_base.rstrip('/')}/api/rooms/{q_room}/file"
    try:
        with urllib.request.urlopen(meta_url, timeout=timeout) as resp:  # noqa: S310
            meta = json.loads(resp.read().decode("utf-8"))
        fname = os.path.basename(meta.get("filename") or f"{room}.bin")
        with urllib.request.urlopen(file_url, timeout=max(timeout, 20.0)) as fresp:  # noqa: S310
            data = fresp.read()
        if not data:
            return None
        dest_dir = user_subdir("collab_files")
        dest = dest_dir / fname
        dest.write_bytes(data)
        return str(dest)
    except Exception as exc:
        log.debug("Direct HTTP file download skipped (%s) — falling back to CRDT sync", exc)
        return None
