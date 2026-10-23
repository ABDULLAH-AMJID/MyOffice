"""Direct PC <-> Android Mobile App Two-Way Sync Hub, QR Pairing, and LAN Auto-Discovery.

Enables zero-cloud, zero-subscription, encrypted-token two-way workspace synchronization between:
  * **MyOffice Desktop (Windows / Linux)** and **MyOffice Android (`MyOffice_Android.apk`)** over Wi-Fi, Ethernet,
    or a phone's Mobile Hotspot (no internet connection required!).
  * Works symmetrically in BOTH directions:
      1. **PC as Sync Hub (`MobileSyncServer`):** PC hosts the workspace over HTTP REST + UDP beacon (`41889`), displays
         a 1-scan QR code (`myoffice://pair?...`) + 6-digit PIN, optionally shares the PC's Hardware Vault key so the
         paired phone can seamlessly open Router-USB & Gmail `.movault` backups, and exposes active Live Collaboration
         rooms for 1-tap mobile join.
      2. **Android Phone as Sync Hub (`MobilePeerBackend`):** When on the go with Mobile Hotspot, Android hosts the
         exact same `/api/v1/*` protocol and Desktop syncs against it via `SyncEngine` (`kind = "mobile_peer"`).
"""
from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import logging
import os
import secrets
import shutil
import socket
import socketserver
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.collab.server import primary_lan_ip
from app.services import vault
from app.services.cloud_sync import RemoteEntry, SyncBackend, default_local_dir, file_hash, ignored
from app.services.config import cfg
from app.version import APP_VERSION

log = logging.getLogger(__name__)

DISCOVERY_PORT = 41889
DEFAULT_SYNC_PORT = 41880
DISCOVERY_MAGIC = b"MYOFFICE_DISCOVER_V1"


def parse_pair_uri(uri_or_url: str) -> dict[str, str]:
    """Parse either a `myoffice://pair?...` QR payload or direct `http://ip:port` string."""
    raw = (uri_or_url or "").strip()
    if raw.lower().startswith("myoffice://pair"):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(raw).query)
        return {
            "url": (q.get("url") or [""])[0].rstrip("/"),
            "token": (q.get("token") or [""])[0],
            "pin": (q.get("pin") or [""])[0],
            "device": (q.get("device") or [""])[0],
            "vault_key_hex": (q.get("vk") or [""])[0],
        }
    if raw.startswith(("http://", "https://")):
        return {"url": raw.rstrip("/"), "token": "", "pin": "", "device": "", "vault_key_hex": ""}
    if ":" in raw and not raw.startswith("/"):
        return {"url": f"http://{raw}".rstrip("/"), "token": "", "pin": "", "device": "", "vault_key_hex": ""}
    return {"url": "", "token": "", "pin": "", "device": "", "vault_key_hex": ""}


def build_pair_uri(base_url: str, token: str, pin: str, device_name: str, vault_key_hex: str = "") -> str:
    params = {
        "url": base_url.rstrip("/"),
        "token": token,
        "pin": pin,
        "device": device_name,
    }
    if vault_key_hex:
        params["vk"] = vault_key_hex
    return "myoffice://pair?" + urllib.parse.urlencode(params)


@dataclass
class DiscoveredPeer:
    device: str
    platform: str
    url: str
    ip: str
    port: int
    version: str


def discover_lan_peers(timeout: float = 0.85, discovery_port: int = DISCOVERY_PORT) -> list[DiscoveredPeer]:
    """Broadcast UDP discovery probe and collect all active MyOffice PC / Android Sync Hubs on the LAN."""
    found: dict[str, DiscoveredPeer] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
        targets = {("255.255.255.255", discovery_port), ("127.0.0.1", discovery_port)}
        lan = primary_lan_ip()
        parts = lan.split(".")
        if len(parts) == 4 and parts[0] != "127":
            targets.add((f"{parts[0]}.{parts[1]}.{parts[2]}.255", discovery_port))
        for addr in targets:
            try:
                sock.sendto(DISCOVERY_MAGIC, addr)
            except OSError:
                pass
        deadline = time.monotonic() + max(0.2, timeout)
        while time.monotonic() < deadline:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                data, (sender_ip, _) = sock.recvfrom(4096)
            except (TimeoutError, OSError):
                break
            try:
                obj = json.loads(data.decode("utf-8"))
                if obj.get("service") != "myoffice-sync-v1":
                    continue
                port = int(obj.get("port") or DEFAULT_SYNC_PORT)
                url = f"http://{sender_ip}:{port}"
                found[url] = DiscoveredPeer(
                    device=str(obj.get("device") or sender_ip),
                    platform=str(obj.get("platform") or "unknown"),
                    url=url,
                    ip=sender_ip,
                    port=port,
                    version=str(obj.get("version") or ""),
                )
            except Exception:
                continue
    finally:
        sock.close()
    return sorted(found.values(), key=lambda p: (p.ip == "127.0.0.1", p.device.lower()))


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class MobileSyncServer:
    """Embedded HTTP REST + UDP Beacon Sync Hub allowing Android phones & tablets (or other PCs) to sync two-way."""

    def __init__(
        self,
        sync_dir: str | Path | None = None,
        host: str = "0.0.0.0",
        port: int = DEFAULT_SYNC_PORT,
        token: str = "",
        pin: str = "",
        device_name: str = "",
        share_vault_key: bool = True,
        enable_udp_discovery: bool = True,
        collab_rooms_provider=None,
        keystore: vault.DeviceKeyStore | None = None,
    ):
        self.root = Path(sync_dir or default_local_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.requested_port = int(port)
        self.port = int(port)
        self.token = token or secrets.token_urlsafe(24)
        self.pin = pin or f"{secrets.randbelow(900000) + 100000:06d}"
        self.device_name = device_name or socket.gethostname() or "MyOffice-PC"
        self.share_vault_key = share_vault_key
        self.enable_udp_discovery = enable_udp_discovery
        self.collab_rooms_provider = collab_rooms_provider
        self.keystore = keystore or vault.keystore()
        self.paired_devices: dict[str, dict] = {}
        self.activity_log: list[str] = []
        self._httpd: _ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._udp_sock: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()

    def _log_event(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.activity_log.append(f"[{stamp}] {msg}")
            if len(self.activity_log) > 200:
                self.activity_log = self.activity_log[-200:]

    def regenerate_credentials(self) -> tuple[str, str]:
        with self._lock:
            self.token = secrets.token_urlsafe(24)
            self.pin = f"{secrets.randbelow(900000) + 100000:06d}"
        self._log_event("Generated new QR token & 6-digit PIN")
        return self.token, self.pin

    def base_url(self, ip: str | None = None) -> str:
        addr = ip or primary_lan_ip()
        return f"http://{addr}:{self.port}"

    def pair_uri(self, ip: str | None = None) -> str:
        vk = self.keystore.master_key.hex() if self.share_vault_key else ""
        return build_pair_uri(self.base_url(ip), self.token, self.pin, self.device_name, vk)

    def _safe_target(self, rel: str) -> Path:
        clean = (rel or "").replace("\\", "/").lstrip("/")
        if not clean or any(seg in ("", ".", "..") for seg in clean.split("/")):
            raise ValueError(f"Unsafe relative path: {rel!r}")
        full = (self.root / clean).resolve()
        if full == self.root or not full.is_relative_to(self.root):
            raise ValueError(f"Path escapes sync folder: {rel!r}")
        return full

    def _scan_manifest(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root).as_posix()
            if ignored(rel):
                continue
            try:
                st = p.stat()
                out[rel] = {
                    "size": st.st_size,
                    "mtime": round(st.st_mtime, 3),
                    "sha256": file_hash(str(p)),
                }
            except OSError:
                continue
        return out

    def start(self) -> int:
        if self.running:
            return self.port
        self._stop_evt.clear()
        srv = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # quiet stdlib console spam
                return

            def _send_json(self, payload: dict, code: int = 200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Device-Name")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                auth = self.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    tok = auth[7:].strip()
                    return hmac.compare_digest(tok, srv.token)
                return False

            def do_OPTIONS(self):
                self._send_json({"ok": True})

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                qs = urllib.parse.parse_qs(parsed.query)

                if path == "/api/v1/info":
                    file_count = sum(1 for p in srv.root.rglob("*") if p.is_file() and not ignored(p.name))
                    return self._send_json({
                        "ok": True,
                        "service": "myoffice-sync-v1",
                        "device": srv.device_name,
                        "platform": "pc",
                        "version": APP_VERSION,
                        "file_count": file_count,
                        "requires_auth": True,
                    })

                if not self._authorized():
                    return self._send_json({"ok": False, "error": "Unauthorized — scan QR or enter 6-digit PIN"}, 401)

                dev = self.headers.get("X-Device-Name", self.client_address[0])
                srv.paired_devices[dev] = {"ip": self.client_address[0], "last_seen": time.time()}

                if path == "/api/v1/ping":
                    return self._send_json({
                        "ok": True,
                        "device": srv.device_name,
                        "platform": "pc",
                        "version": APP_VERSION,
                        "sync_folder": str(srv.root),
                        "update_repo_url": cfg().get("update.url", "") or cfg().get("servers.update", ""),
                    })

                if path == "/api/v1/files":
                    return self._send_json({
                        "ok": True,
                        "device": srv.device_name,
                        "files": srv._scan_manifest(),
                    })

                if path == "/api/v1/collab-rooms":
                    rooms = srv.collab_rooms_provider() if callable(srv.collab_rooms_provider) else []
                    return self._send_json({"ok": True, "rooms": rooms})

                if path == "/api/v1/file":
                    rel = (qs.get("path") or [""])[0]
                    try:
                        target = srv._safe_target(rel)
                    except ValueError as exc:
                        return self._send_json({"ok": False, "error": str(exc)}, 400)
                    if not target.is_file():
                        return self._send_json({"ok": False, "error": "File not found"}, 404)
                    data = target.read_bytes()
                    st = target.stat()
                    sha = hashlib.sha256(data).hexdigest()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-SHA256", sha)
                    self.send_header("X-MTime", f"{st.st_mtime:.3f}")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    srv._log_event(f"Sent '{rel}' ({len(data):,} B) -> {dev}")
                    return

                return self._send_json({"ok": False, "error": "Unknown endpoint"}, 404)

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    body = {}

                if parsed.path == "/api/v1/pair":
                    pin = str(body.get("pin") or "").strip()
                    tok = str(body.get("token") or "").strip()
                    pin_ok = bool(pin) and hmac.compare_digest(pin, srv.pin)
                    tok_ok = bool(tok) and hmac.compare_digest(tok, srv.token)
                    if not (pin_ok or tok_ok):
                        srv._log_event(f"Rejected invalid PIN attempt from {self.client_address[0]}")
                        return self._send_json({"ok": False, "error": "Invalid 6-digit pairing PIN"}, 403)
                    peer_name = str(body.get("device_name") or self.client_address[0])
                    peer_vk = str(body.get("vault_key_hex") or "").strip()
                    if srv.share_vault_key and len(peer_vk) == 64:
                        try:
                            raw_k = bytes.fromhex(peer_vk)
                            kid = vault.compute_key_id(raw_k)
                            if kid != srv.keystore.key_id:
                                srv.keystore._extra_keys[kid] = raw_k
                                srv.keystore._save()
                                srv._log_event(f"Authorized Vault Key ({kid}) from '{peer_name}'")
                        except Exception as exc:
                            log.debug("Vault key import skipped: %s", exc)
                    srv.paired_devices[peer_name] = {"ip": self.client_address[0], "last_seen": time.time()}
                    srv._log_event(f"Paired device '{peer_name}' ({self.client_address[0]})")
                    resp = {
                        "ok": True,
                        "token": srv.token,
                        "device": srv.device_name,
                        "platform": "pc",
                        "version": APP_VERSION,
                    }
                    if srv.share_vault_key:
                        resp["vault_key_id"] = srv.keystore.key_id
                        resp["vault_key_hex"] = srv.keystore.master_key.hex()
                    return self._send_json(resp)

                return self._send_json({"ok": False, "error": "Unknown POST endpoint"}, 404)

            def do_PUT(self):
                if not self._authorized():
                    return self._send_json({"ok": False, "error": "Unauthorized"}, 401)
                parsed = urllib.parse.urlsplit(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if parsed.path != "/api/v1/file":
                    return self._send_json({"ok": False, "error": "Unknown PUT endpoint"}, 404)
                rel = (qs.get("path") or [""])[0]
                try:
                    target = srv._safe_target(rel)
                except ValueError as exc:
                    return self._send_json({"ok": False, "error": str(exc)}, 400)
                length = int(self.headers.get("Content-Length") or "0")
                data = self.rfile.read(length) if length > 0 else b""
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".sync_")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp_name, target)
                finally:
                    if os.path.exists(tmp_name):
                        try:
                            os.unlink(tmp_name)
                        except OSError:
                            pass
                mtime_str = (qs.get("mtime") or [""])[0]
                if mtime_str:
                    try:
                        mt = float(mtime_str)
                        os.utime(target, (mt, mt))
                    except ValueError:
                        pass
                st = target.stat()
                sha = hashlib.sha256(data).hexdigest()
                dev = self.headers.get("X-Device-Name", self.client_address[0])
                srv._log_event(f"Received '{rel}' ({len(data):,} B) <- {dev}")
                return self._send_json({
                    "ok": True,
                    "size": st.st_size,
                    "mtime": round(st.st_mtime, 3),
                    "sha256": sha,
                })

            def do_DELETE(self):
                if not self._authorized():
                    return self._send_json({"ok": False, "error": "Unauthorized"}, 401)
                parsed = urllib.parse.urlsplit(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                rel = (qs.get("path") or [""])[0]
                try:
                    target = srv._safe_target(rel)
                except ValueError as exc:
                    return self._send_json({"ok": False, "error": str(exc)}, 400)
                if target.is_file():
                    target.unlink()
                    dev = self.headers.get("X-Device-Name", self.client_address[0])
                    srv._log_event(f"Deleted '{rel}' <- {dev}")
                return self._send_json({"ok": True})

        try:
            self._httpd = _ThreadingHTTPServer((self.host, self.requested_port), Handler)
        except OSError:
            self._httpd = _ThreadingHTTPServer((self.host, 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self._http_thread = threading.Thread(target=self._httpd.serve_forever, name="MyOfficeMobileSyncHTTP", daemon=True)
        self._http_thread.start()

        if self.enable_udp_discovery:
            try:
                usock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                usock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                usock.bind(("0.0.0.0", DISCOVERY_PORT))
                usock.settimeout(0.5)
                self._udp_sock = usock
                self._udp_thread = threading.Thread(target=self._udp_loop, name="MyOfficeMobileSyncUDP", daemon=True)
                self._udp_thread.start()
            except OSError as exc:
                log.debug("UDP discovery port %d busy (%s); QR/IP sync still active", DISCOVERY_PORT, exc)

        self._log_event(f"Mobile Sync Hub listening on port {self.port} ({self.root})")
        return self.port

    def _udp_loop(self) -> None:
        assert self._udp_sock is not None
        while not self._stop_evt.is_set():
            try:
                data, addr = self._udp_sock.recvfrom(1024)
            except (TimeoutError, OSError):
                continue
            if data.strip() == DISCOVERY_MAGIC:
                reply = json.dumps({
                    "service": "myoffice-sync-v1",
                    "device": self.device_name,
                    "platform": "pc",
                    "port": self.port,
                    "version": APP_VERSION,
                }).encode("utf-8")
                try:
                    self._udp_sock.sendto(reply, addr)
                except OSError:
                    pass

    @property
    def running(self) -> bool:
        return self._httpd is not None and self._http_thread is not None and self._http_thread.is_alive()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=3)
            self._http_thread = None
        if self._udp_sock is not None:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None
        if self._udp_thread is not None:
            self._udp_thread.join(timeout=2)
            self._udp_thread = None


class MobilePeerBackend(SyncBackend):
    """Client `SyncBackend` talking to an Android phone OR another PC running `myoffice-sync-v1`."""

    name = "mobile_peer"

    def __init__(self, base_url: str, token: str = "", pin: str = "", device_name: str = ""):
        parsed = parse_pair_uri(base_url)
        self.base_url = (parsed["url"] or base_url).rstrip("/")
        if not self.base_url:
            raise ValueError("Enter or scan a Mobile/PC Sync Hub URL.")
        self.token = token or parsed["token"]
        self.pin = pin or parsed["pin"]
        self.device_name = device_name or socket.gethostname() or "MyOffice-PC"
        if not self.token and self.pin:
            self.pair(self.pin)

    def _headers(self, extra: dict | None = None) -> dict[str, str]:
        h = {
            "X-Device-Name": self.device_name,
            "User-Agent": f"MyOffice/{APP_VERSION}",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def pair(self, pin: str = "", token: str = "", share_vault_key: bool = True,
             keystore: vault.DeviceKeyStore | None = None) -> dict:
        ks = keystore or vault.keystore()
        payload = {
            "pin": pin or self.pin,
            "token": token or self.token,
            "device_name": self.device_name,
            "platform": "pc",
        }
        if share_vault_key:
            payload["vault_key_hex"] = ks.master_key.hex()
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/pair",
            data=data,
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Pairing rejected ({exc.code}): {detail}") from exc
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "Pairing failed")
        self.token = str(out["token"])
        peer_vk = str(out.get("vault_key_hex") or "")
        if share_vault_key and len(peer_vk) == 64:
            raw_k = bytes.fromhex(peer_vk)
            kid = vault.compute_key_id(raw_k)
            if kid != ks.key_id:
                ks._extra_keys[kid] = raw_k
                ks._save()
        return out

    def list(self) -> dict[str, RemoteEntry]:
        req = urllib.request.Request(f"{self.base_url}/api/v1/files", headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        out: dict[str, RemoteEntry] = {}
        for rel, info in (obj.get("files") or {}).items():
            if not ignored(rel):
                out[rel] = RemoteEntry(
                    size=int(info.get("size") or 0),
                    mtime=float(info.get("mtime") or 0.0),
                    etag=str(info.get("sha256") or ""),
                )
        return out

    def download(self, rel: str, local: str) -> None:
        q = urllib.parse.urlencode({"path": rel})
        req = urllib.request.Request(f"{self.base_url}/api/v1/file?{q}", headers=self._headers(), method="GET")
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(Path(local).parent))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, os.fdopen(fd, "wb") as fh:
                shutil.copyfileobj(resp, fh)
            os.replace(tmp, local)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def upload(self, local: str, rel: str) -> RemoteEntry:
        st = os.stat(local)
        q = urllib.parse.urlencode({"path": rel, "mtime": f"{st.st_mtime:.3f}"})
        data = Path(local).read_bytes()
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/file?{q}",
            data=data,
            headers=self._headers({"Content-Type": "application/octet-stream"}),
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        return RemoteEntry(
            size=int(info.get("size") or st.st_size),
            mtime=float(info.get("mtime") or st.st_mtime),
            etag=str(info.get("sha256") or file_hash(local)),
        )

    def delete(self, rel: str) -> None:
        q = urllib.parse.urlencode({"path": rel})
        req = urllib.request.Request(f"{self.base_url}/api/v1/file?{q}", headers=self._headers(), method="DELETE")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    def remote_collab_rooms(self) -> list[dict]:
        req = urllib.request.Request(f"{self.base_url}/api/v1/collab-rooms", headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return list((json.loads(resp.read().decode("utf-8"))).get("rooms") or [])


_global_hub: MobileSyncServer | None = None


def ensure_global_mobile_hub(collab_rooms_provider=None) -> MobileSyncServer:
    """Start or return the singleton Desktop Mobile Sync Hub persisted in `config.json`."""
    global _global_hub
    if _global_hub is not None and _global_hub.running:
        if collab_rooms_provider is not None:
            _global_hub.collab_rooms_provider = collab_rooms_provider
        return _global_hub
    ms = cfg().get("mobile_sync", {}) or {}
    tok = vault.unseal_secret(ms.get("token", ""))
    pin = vault.unseal_secret(ms.get("pin", ""))
    port = int(ms.get("port") or DEFAULT_SYNC_PORT)
    sync_dir = ms.get("local_dir") or default_local_dir()
    srv = MobileSyncServer(
        sync_dir=sync_dir,
        port=port,
        token=tok,
        pin=pin,
        share_vault_key=bool(ms.get("share_vault_key", True)),
        collab_rooms_provider=collab_rooms_provider,
    )
    srv.start()
    ms_out = dict(ms)
    ms_out["token"] = vault.seal_secret(srv.token)
    ms_out["pin"] = vault.seal_secret(srv.pin)
    ms_out["port"] = srv.port
    ms_out["local_dir"] = str(srv.root)
    ms_out["share_vault_key"] = srv.share_vault_key
    cfg().set("mobile_sync", ms_out)
    _global_hub = srv
    return srv


def stop_global_mobile_hub() -> None:
    global _global_hub
    if _global_hub is not None:
        _global_hub.stop()
        _global_hub = None
