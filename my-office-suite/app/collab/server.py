"""Multi-room LAN Real-Time Collaboration & Universal File-Share Server (y-websocket + HTTP on one port).

* Supports UNLIMITED simultaneous shared files (`ws://<lan_ip>:<port>/<room>`), each with its own Yjs CRDT state,
  awareness presence, live Team Chat (`Y.Array("chat")`), and embedded file snapshot (`Y.Map("file")`).
* Serves HTTP endpoints on the EXACT SAME PORT via `process_request`:
  - `GET /health` -> JSON server health & active room count
  - `GET /api/rooms` -> JSON list of all currently shared rooms/files
  - `GET /api/rooms/<room>/meta` -> JSON file metadata (`filename`, `ext`, `kind`, `size`, `peers`)
  - `GET /api/rooms/<room>/file` -> Direct binary download of ANY shared file format (`.docx`, `.xlsx`, `.pptx`,
    `.pdf`, `.png`, `.jpg`, `.zip`, `.csv`, `.py`, etc.) so LAN joiners don't need a pre-existing copy!
  - `GET /f/<room>` -> Modern HTML landing page with 1-click "Open in MyOffice" (`myoffice://...`) + browser download!
"""
from __future__ import annotations

import asyncio
import base64
import html
import http
import json
import logging
import os
import socket
import threading
import time
import urllib.parse
from pathlib import Path

import pycrdt
from pycrdt import Array, Doc, Map, Text

from app.services.paths import user_subdir

log = logging.getLogger(__name__)
SYNC, AWARENESS = 0, 1
STEP1, STEP2, UPDATE = 0, 1, 2


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:80] or "room"


class Room:
    def __init__(self, name: str, persist_dir: Path | None):
        self.name = name
        self.doc = Doc()
        self.clients: set = set()
        self.mobile_clients: set = set()
        self.mobile_peers: dict = {}
        self.awareness: dict = {}
        self.shared_file_path: str | None = None
        self.shared_filename: str = ""
        self.shared_kind: str = ""
        self._broadcasted_chat_ids: set[str] = set()
        self.persist = (persist_dir / f"{_safe(name)}.ydoc") if persist_dir else None
        self.dirty = False
        if self.persist and self.persist.exists():
            try:
                self.doc.apply_update(self.persist.read_bytes())
            except Exception as exc:
                log.warning("Could not restore room %s: %s", name, exc)

    def chat_list(self) -> list[dict]:
        out: list[dict] = []
        try:
            arr = self.doc.get("chat", type=Array)
            for item in arr:
                if isinstance(item, dict):
                    out.append(dict(item))
                elif isinstance(item, str):
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, dict):
                            out.append(parsed)
                    except Exception:
                        continue
        except Exception:
            pass
        return out

    def writer_text(self) -> str:
        try:
            return str(self.doc.get("content", type=Text))
        except Exception:
            return ""

    def sheets_snapshot(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        try:
            # 1. Include PC SheetBinding CRDT map ("Sheet1!A1" -> value)
            croot = self.doc.get("cells", type=Map)
            for full_key in list(croot.keys()):
                val = croot.get(full_key)
                sname, sep, ref = str(full_key).rpartition("!")
                sheet_key = sname if sep and sname else "Sheet1"
                cell_key = ref if sep else str(full_key)
                if cell_key:
                    out.setdefault(sheet_key, {})[cell_key] = "" if val is None else str(val)
        except Exception:
            pass
        try:
            # 2. Include nested mobile map ("sheets" -> sname -> A1 -> value)
            sroot = self.doc.get("sheets", type=Map)
            for sname in list(sroot.keys()):
                sub = sroot.get(sname)
                if sub is not None and hasattr(sub, "items"):
                    bucket = out.setdefault(str(sname), {})
                    for k, v in sub.items():
                        bucket[str(k)] = str(v)
        except Exception:
            pass
        return out

    @staticmethod
    def _ensure_ext(fname: str, kind: str) -> str:
        ext_map = {"writer": ".docx", "sheet": ".xlsx", "slide": ".pptx", "pdf": ".pdf", "photos": ".png"}
        clean = (fname or "").strip() or "Shared_Document"
        if not os.path.splitext(clean)[1] and kind in ext_map:
            return clean + ext_map[kind]
        return clean

    def full_mobile_state(self) -> dict:
        return {
            "type": "state",
            "meta": self.metadata(),
            "writer_text": self.writer_text(),
            "sheets": self.sheets_snapshot(),
            "chat": self.chat_list(),
            "mobile_peers": list(self.mobile_peers.values()),
        }

    def metadata(self) -> dict:
        fname = self.shared_filename
        kind = self.shared_kind
        size = 0
        try:
            fmap = self.doc.get("file", type=Map)
            kind = str(fmap.get("kind") or kind or "writer")
            fname = str(fmap.get("name") or fname or self.name)
            b64 = fmap.get("b64") or ""
            if b64:
                size = (len(b64) * 3) // 4
        except Exception:
            pass
        if not size and self.shared_file_path and os.path.isfile(self.shared_file_path):
            try:
                size = os.path.getsize(self.shared_file_path)
                fname = fname or os.path.basename(self.shared_file_path)
            except OSError:
                pass
        fname = self._ensure_ext(fname or self.name, kind or "any")
        return {
            "room": self.name,
            "filename": fname,
            "ext": os.path.splitext(fname)[1].lower(),
            "kind": kind or "any",
            "size": size,
            "peers": len(self.clients),
        }

    def raw_file_bytes(self) -> tuple[bytes, str]:
        kind = self.shared_kind or "any"
        try:
            fmap = self.doc.get("file", type=Map)
            b64 = fmap.get("b64")
            kind = str(fmap.get("kind") or kind)
            fname = self._ensure_ext(str(fmap.get("name") or self.shared_filename or self.name), kind)
            if b64:
                return base64.b64decode(b64.encode("ascii")), fname
        except Exception:
            pass
        if self.shared_file_path and os.path.isfile(self.shared_file_path):
            fn = self._ensure_ext(self.shared_filename or os.path.basename(self.shared_file_path), kind)
            return Path(self.shared_file_path).read_bytes(), fn
        return b"", self._ensure_ext(self.shared_filename or self.name, kind)

    def save(self):
        if self.persist and self.dirty:
            tmp = self.persist.with_suffix(".tmp")
            tmp.write_bytes(self.doc.get_update())
            tmp.replace(self.persist)
            self.dirty = False


class CollabServer:
    def __init__(self, host: str | int = "0.0.0.0", port: int = 8765, persist: bool = True):
        if isinstance(host, int):
            host, port = "0.0.0.0", host
        self.host: str = host
        self.port: int = int(port)
        self.rooms: dict[str, Room] = {}
        self.persist_dir = user_subdir("collab") if persist else None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._server = None
        self._stop: asyncio.Event | None = None
        self.started = threading.Event()
        self.error: str = ""

    def register_shared_file(self, room_name: str, file_path: str | None = None,
                             filename: str = "", kind: str = "", raw_bytes: bytes | None = None) -> Room:
        room = self.rooms.get(room_name)
        if room is None:
            room = self.rooms[room_name] = Room(room_name, self.persist_dir)
        if file_path:
            room.shared_file_path = file_path
            room.shared_filename = filename or os.path.basename(file_path)
        elif filename:
            room.shared_filename = filename
        if kind:
            room.shared_kind = kind
        if raw_bytes is not None:
            fmap = room.doc.get("file", type=Map)
            fmap["name"] = room.shared_filename or f"{room_name}.bin"
            fmap["kind"] = room.shared_kind or "any"
            fmap["b64"] = base64.b64encode(raw_bytes).decode("ascii")
            fmap["ts"] = time.time()
            room.dirty = True
        return room

    # -- HTTP handler on the same TCP port for QR scan / browser join / auto file download
    async def _process_http(self, connection, request):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        upgrade = request.headers.get("Upgrade", "").lower()
        if upgrade == "websocket":
            return None

        parsed = urllib.parse.urlsplit(request.path or "/")
        path = parsed.path or "/"
        host_hdr = request.headers.get("Host") or f"{primary_lan_ip()}:{self.port}"

        def _json_resp(payload: object, status=http.HTTPStatus.OK) -> Response:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            hdrs = Headers([
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ])
            return Response(status.value, status.phrase, hdrs, body)

        if path == "/health":
            return _json_resp({"status": "ok", "port": self.port, "rooms": [r.metadata() for r in self.rooms.values()]})

        if path == "/api/rooms":
            return _json_resp({"rooms": [r.metadata() for r in self.rooms.values()]})

        if path.startswith("/api/rooms/"):
            rest = path[len("/api/rooms/"):].strip("/")
            room_part, _, action = rest.partition("/")
            room_name = urllib.parse.unquote(room_part)
            room = self.rooms.get(room_name)
            if room is None:
                return _json_resp({"error": f"Room '{room_name}' not found"}, http.HTTPStatus.NOT_FOUND)
            if action in ("", "meta"):
                return _json_resp(room.metadata())
            if action == "state":
                return _json_resp(room.full_mobile_state())
            if action == "chat":
                return _json_resp({"room": room.name, "chat": room.chat_list()})
            if action == "file":
                data, fname = room.raw_file_bytes()
                safe_disp = fname.replace('"', "_")
                hdrs = Headers([
                    ("Content-Type", "application/octet-stream"),
                    ("Content-Length", str(len(data))),
                    ("Content-Disposition", f'attachment; filename="{safe_disp}"'),
                    ("Cache-Control", "no-store"),
                ])
                return Response(200, "OK", hdrs, data)

        if path.startswith("/f/") or path == "/":
            room_name = urllib.parse.unquote(path[3:].strip("/")) if path.startswith("/f/") else ""
            if not room_name and self.rooms:
                room_name = next(iter(self.rooms.keys()))
            room = self.rooms.get(room_name)
            meta = room.metadata() if room else {"room": room_name or "default", "filename": room_name or "Shared Document", "peers": 0}
            q_room = urllib.parse.quote(meta["room"])
            q_name = urllib.parse.quote(meta["filename"])
            app_uri = f"myoffice://join?ws={urllib.parse.quote(f'ws://{host_hdr}/{q_room}')}&http={urllib.parse.quote(f'http://{host_hdr}')}&room={q_room}&name={q_name}"
            dl_url = f"http://{host_hdr}/api/rooms/{q_room}/file"
            esc_name = html.escape(meta["filename"])
            esc_room = html.escape(meta["room"])
            esc_uri = html.escape(app_uri)
            page = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>MyOffice Live — {esc_name}</title>"
                "<style>body{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#f8fafc;"
                "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:16px}"
                ".card{background:#1e293b;border:1px solid #334155;border-radius:16px;max-width:480px;width:100%;padding:28px;"
                "box-shadow:0 20px 50px rgba(0,0,0,.45)}h1{font-size:20px;margin:0 0 8px}"
                ".muted{color:#94a3b8;font-size:13px;margin-bottom:20px}"
                ".btn{display:block;text-align:center;padding:13px 18px;border-radius:10px;text-decoration:none;"
                "font-weight:600;margin-bottom:10px;font-size:14px}"
                ".pri{background:#2563eb;color:#fff}.sec{background:#334155;color:#e2e8f0}"
                "code{display:block;background:#0f172a;padding:10px;border-radius:8px;font-size:12px;"
                "word-break:break-all;color:#93c5fd;margin-top:12px}</style></head><body>"
                f"<div class='card'><h1>{esc_name}</h1>"
                f"<div class='muted'>Live LAN Room: <b>{esc_room}</b> &bull; {meta['peers']} active collaborator(s)</div>"
                f"<a class='btn pri' href='{esc_uri}'>Open Live in MyOffice App (with Team Chat)</a>"
                f"<a class='btn sec' href='{html.escape(dl_url)}'>Download Latest File Snapshot</a>"
                f"<div class='muted' style='margin:14px 0 4px'>Or paste this link inside <b>MyOffice &rarr; Collaborate &rarr; Join</b>:</div>"
                f"<code>http://{html.escape(host_hdr)}/f/{q_room}</code></div></body></html>"
            ).encode("utf-8")
            hdrs = Headers([
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(page))),
            ])
            return Response(200, "OK", hdrs, page)

        return _json_resp({"error": "Not found"}, http.HTTPStatus.NOT_FOUND)

    # -- y-websocket + Mobile JSON bridge handler on the exact same port
    async def handler(self, ws):
        raw_path = getattr(getattr(ws, "request", None), "path", "/") or "/"
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_path).query)
        is_mobile = (qs.get("client") or [""])[0].lower() == "mobile"
        name = urllib.parse.unquote(raw_path.strip("/").split("?")[0]) or "default"
        room = self.rooms.get(name)
        if room is None:
            room = self.rooms[name] = Room(name, self.persist_dir)
        room.clients.add(ws)
        if is_mobile:
            room.mobile_clients.add(ws)
        log.info("Client joined room %s (%d connected, mobile=%s)", name, len(room.clients), is_mobile)
        try:
            if is_mobile:
                await ws.send(json.dumps(room.full_mobile_state(), ensure_ascii=False))
            else:
                await ws.send(pycrdt.create_sync_message(room.doc))
                for other, msg in list(room.awareness.items()):
                    if other is not ws:
                        await ws.send(msg)
            async for message in ws:
                if isinstance(message, str):
                    room.mobile_clients.add(ws)
                    await self.on_mobile_json(room, ws, message)
                else:
                    await self.on_message(room, ws, bytes(message))
        except Exception as exc:
            log.debug("Connection closed: %s", exc)
        finally:
            room.clients.discard(ws)
            room.mobile_clients.discard(ws)
            room.mobile_peers.pop(ws, None)
            room.awareness.pop(ws, None)
            room.save()
            log.info("Client left room %s (%d connected)", name, len(room.clients))

    async def on_mobile_json(self, room: Room, ws, raw_json: str):
        try:
            msg = json.loads(raw_json)
        except Exception:
            return
        mtype = msg.get("type")
        if mtype == "mobile_hello":
            room.mobile_peers[ws] = {
                "sender": str(msg.get("sender") or "Mobile User"),
                "color": str(msg.get("color") or "#2563EB"),
                "platform": "android",
            }
            await ws.send(json.dumps(room.full_mobile_state(), ensure_ascii=False))
            await self.broadcast_mobile(room, {"type": "peers", "mobile_peers": list(room.mobile_peers.values())}, exclude=ws)
            return

        before = room.doc.get_state()
        changed = False
        mobile_event: dict | None = None

        if mtype == "chat_send":
            text_body = str(msg.get("text") or "").strip()
            if not text_body:
                return
            entry = {
                "id": str(msg.get("id") or f"mob-{int(time.time()*1000)}"),
                "sender": str(msg.get("sender") or "Mobile User"),
                "sender_id": str(msg.get("sender_id") or f"mob-{id(ws)}"),
                "color": str(msg.get("color") or "#10B981"),
                "text": text_body,
                "ts": float(msg.get("ts") or time.time()),
            }
            arr = room.doc.get("chat", type=Array)
            with room.doc.transaction():
                arr.append(entry)
            changed = True
            mobile_event = {"type": "chat_push", "message": entry}

        elif mtype == "writer_update":
            new_text = str(msg.get("text") or "")
            ytext = room.doc.get("content", type=Text)
            old_text = str(ytext)
            if new_text != old_text:
                old_b = old_text.encode("utf-8")
                new_b = new_text.encode("utf-8")
                # UTF-8 character-boundary safe replace on common prefix/suffix
                p = 0
                lim = min(len(old_text), len(new_text))
                while p < lim and old_text[p] == new_text[p]:
                    p += 1
                s = 0
                while (s < (len(old_text) - p) and s < (len(new_text) - p)
                       and old_text[len(old_text) - 1 - s] == new_text[len(new_text) - 1 - s]):
                    s += 1
                start_b = len(old_text[:p].encode("utf-8"))
                del_b = len(old_text[p:len(old_text) - s].encode("utf-8"))
                ins_str = new_text[p:len(new_text) - s]
                with room.doc.transaction():
                    if del_b > 0:
                        del ytext[start_b:start_b + del_b]
                    if ins_str:
                        ytext.insert(start_b, ins_str)
                changed = True
                mobile_event = {"type": "writer_push", "writer_text": new_text, "sender": msg.get("sender", "")}

        elif mtype == "sheet_cell":
            sname = str(msg.get("sheet") or "Sheet1")
            cell_ref = str(msg.get("cell") or "")
            val = str(msg.get("value") if msg.get("value") is not None else "")
            if cell_ref:
                sroot = room.doc.get("sheets", type=Map)
                croot = room.doc.get("cells", type=Map)
                full_key = f"{sname}!{cell_ref}"
                with room.doc.transaction():
                    sub = sroot.get(sname)
                    if sub is None:
                        sub = Map()
                        sroot[sname] = sub
                    if val == "":
                        try:
                            del sub[cell_ref]
                        except KeyError:
                            pass
                        if full_key in croot:
                            del croot[full_key]
                    else:
                        sub[cell_ref] = val
                        croot[full_key] = val
                changed = True
                mobile_event = {"type": "sheet_push", "sheet": sname, "cell": cell_ref, "value": val}

        elif mtype == "file_push":
            b64 = str(msg.get("b64") or "")
            fname = str(msg.get("filename") or room.shared_filename or f"{room.name}.bin")
            kind_str = str(msg.get("kind") or room.shared_kind or "any")
            if b64:
                fmap = room.doc.get("file", type=Map)
                with room.doc.transaction():
                    fmap["name"] = fname
                    fmap["kind"] = kind_str
                    fmap["b64"] = b64
                    fmap["ts"] = time.time()
                room.shared_filename = fname
                room.shared_kind = kind_str
                changed = True
                mobile_event = {"type": "file_push_notify", "meta": room.metadata()}

        if changed:
            room.dirty = True
            diff = room.doc.get_update(before)
            await self.broadcast(room, pycrdt.create_update_message(diff), exclude=ws)
            if mobile_event:
                await self.broadcast_mobile(room, mobile_event, exclude=ws if mtype != "chat_send" else None)

    async def on_message(self, room: Room, ws, message: bytes):
        if not message:
            return
        kind = message[0]
        if kind == SYNC:
            prev_chats = {m.get("id") for m in room.chat_list() if isinstance(m, dict) and m.get("id")}
            prev_sha = ""
            try:
                prev_sha = str(room.doc.get("file", type=Map).get("sha") or "")
            except Exception:
                pass
            sub = message[1] if len(data := message) > 1 else -1
            reply = pycrdt.handle_sync_message(message[1:], room.doc)
            if reply is not None:
                await ws.send(reply)
            if sub in (STEP2, UPDATE):
                room.dirty = True
                fwd = message if sub == UPDATE else bytes([SYNC, UPDATE]) + message[2:]
                await self.broadcast(room, fwd, exclude=ws)
                if room.mobile_clients:
                    for m in room.chat_list():
                        mid = m.get("id")
                        if mid and mid not in prev_chats:
                            room._broadcasted_chat_ids.add(mid)
                            await self.broadcast_mobile(room, {"type": "chat_push", "message": m})
                    try:
                        new_sha = str(room.doc.get("file", type=Map).get("sha") or "")
                        if new_sha and new_sha != prev_sha:
                            await self.broadcast_mobile(room, {"type": "file_push_notify", "meta": room.metadata()})
                    except Exception:
                        pass
                    await self.broadcast_mobile(room, room.full_mobile_state())
        elif kind == AWARENESS:
            room.awareness[ws] = message
            await self.broadcast(room, message, exclude=ws)

    async def broadcast(self, room: Room, message: bytes, exclude=None):
        for c in list(room.clients):
            if c is exclude or c in room.mobile_clients:
                continue
            try:
                await c.send(message)
            except Exception:
                room.clients.discard(c)

    async def broadcast_mobile(self, room: Room, payload: dict, exclude=None):
        encoded = json.dumps(payload, ensure_ascii=False)
        for c in list(room.mobile_clients):
            if c is exclude:
                continue
            try:
                await c.send(encoded)
            except Exception:
                room.mobile_clients.discard(c)
                room.clients.discard(c)

    # -- server lifecycle
    async def _main(self):
        from websockets.asyncio.server import serve

        self._stop = asyncio.Event()
        try:
            async with serve(
                self.handler,
                self.host,
                self.port,
                max_size=128 * 1024 * 1024,
                process_request=self._process_http,
            ) as server:
                self._server = server
                if server.sockets:
                    self.port = int(server.sockets[0].getsockname()[1])
                self.started.set()
                saver = asyncio.create_task(self._autosave())
                await self._stop.wait()
                saver.cancel()
        except OSError as exc:
            self.error = str(exc)
            self.started.set()
        finally:
            for room in self.rooms.values():
                room.save()

    async def _autosave(self):
        while True:
            await asyncio.sleep(10)
            for room in self.rooms.values():
                room.save()

    def start(self, timeout: float = 5.0) -> None:
        if self.running:
            return
        self.error = ""
        self.started.clear()

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._main())
            self.loop.close()

        self.thread = threading.Thread(target=run, name="MyOfficeCollabServer", daemon=True)
        self.thread.start()
        self.started.wait(timeout)
        if self.error:
            raise OSError(self.error)

    def stop(self) -> None:
        if self.loop is not None and self._stop is not None:
            try:
                self.loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self.error


def _ip_priority(ip: str) -> tuple[int, str]:
    """Rank real Wi-Fi / LAN addresses ahead of virtual Hyper-V/WSL bridges, and banish APIPA `169.254.*`."""
    if ip.startswith("192.168."):
        return (0, ip)
    if ip.startswith("10."):
        return (1, ip)
    if ip.startswith("172."):
        parts = ip.split(".")
        second = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        # 172.16..31 private range: deprioritize `.1` virtual host adapters behind real DHCP leases
        if 16 <= second <= 31:
            return (3 if ip.endswith(".1") else 2, ip)
    return (4, ip)


def lan_addresses() -> list[str]:
    """Return usable IPv4 LAN addresses, ranked so real Wi-Fi / Ethernet router IPs always come FIRST."""
    addrs: set[str] = set()
    primary_route = ""
    for target in (("192.168.1.1", 80), ("10.255.255.255", 1), ("8.8.8.8", 80)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(target)
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith(("127.", "169.254.")):
                addrs.add(ip)
                if not primary_route:
                    primary_route = ip
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith(("127.", "169.254.")):
                addrs.add(ip)
    except OSError:
        pass
    ranked = sorted(addrs, key=lambda ip: (-1 if ip == primary_route else _ip_priority(ip)[0], ip))
    return ranked or ["127.0.0.1"]


def primary_lan_ip() -> str:
    return lan_addresses()[0]


_SHARED_SERVER: CollabServer | None = None
_SERVER_LOCK = threading.Lock()


def ensure_shared_server(port: int | None = None) -> CollabServer:
    """Return the singleton multi-room `CollabServer` for this MyOffice process (hosts unlimited shared files at once!)."""
    global _SHARED_SERVER
    with _SERVER_LOCK:
        if _SHARED_SERVER is not None and _SHARED_SERVER.running:
            return _SHARED_SERVER
        from app.services.config import cfg

        desired_port = int(port if port is not None else cfg().get("collab.port", 8765))
        try:
            srv = CollabServer("0.0.0.0", desired_port, persist=True)
            srv.start()
        except OSError:
            srv = CollabServer("0.0.0.0", 0, persist=True)
            srv.start()
        _SHARED_SERVER = srv
        return srv


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(description="MyOffice multi-room LAN collaboration & file server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-persist", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    srv = CollabServer(a.host, a.port, not a.no_persist)
    srv.start()
    print(f"Collaboration server listening on ws://{a.host}:{srv.port}/<room>  (Ctrl+C to stop)")
    try:
        while srv.running:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
