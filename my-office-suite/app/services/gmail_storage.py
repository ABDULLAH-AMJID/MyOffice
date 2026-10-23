"""Gmail & Google Drive storage backends for Cloud Sync and Encrypted Cloud Backup.

Two first-class ways any ``@gmail.com`` / Google Workspace user can sync & back up their documents:

1. **Gmail Direct Storage (`GmailBackend` over IMAP4-SSL `imap.gmail.com:993`)** — *Recommended, zero Cloud Console*:
   * Uses only the user's ``@gmail.com`` address + a 16-character Google App Password
     (``https://myaccount.google.com/apppasswords``).
   * Stores files inside dedicated Gmail labels (``MyOffice/Sync`` and ``MyOffice/Backups``) via ``IMAP APPEND`` with
     the ``\\Seen`` flag — **never touches the Inbox, never sends emails to anyone, and never triggers SMTP limits**.
   * Supports large files seamlessly by splitting payloads > 18 MB into numbered chunks and verifying full SHA-256 on
     reassembly.
   * Lists hundreds of files in a single header-only ``UID FETCH`` without downloading attachment bodies.

2. **Google Drive REST API v3 (`GoogleDriveBackend` with OAuth 2.0 PKCE Loopback Flow)**:
   * Authorizes in the user's default browser via ``127.0.0.1`` loopback callback and syncs directly with a dedicated
     folder on Google Drive using automatic token refresh.
"""
from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import http.server
import imaplib
import json
import logging
import os
import re
import secrets
import ssl
import threading
import time
import urllib.parse
import webbrowser
from email.message import EmailMessage
from pathlib import Path

import requests

from app.services.cloud_sync import RemoteEntry, SyncBackend

log = logging.getLogger(__name__)

GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993
DEFAULT_CHUNK_BYTES = 18 * 1024 * 1024  # well under Gmail's 25-35 MB per-message MIME cap


def normalize_app_password(raw: str) -> str:
    """Google displays 16-char App Passwords grouped as ``abcd efgh ijkl mnop`` — strip whitespace cleanly."""
    return "".join((raw or "").split())


def friendly_gmail_error(exc: BaseException) -> str:
    msg = str(exc)
    low = msg.lower()
    if "application-specific password" in low or "authenticationfailed" in low or "invalid credentials" in low:
        return ("Gmail rejected the password. Google requires a 16-character Gmail App Password instead of your "
                "regular login password: enable 2-Step Verification on your Google Account, open "
                "https://myaccount.google.com/apppasswords, generate an App Password for 'MyOffice', and paste it here.")
    if "timed out" in low or "name or service not known" in low or "getaddrinfo" in low or "unreachable" in low:
        return "Could not reach imap.gmail.com — check your internet connection."
    return msg


# ============================================================================= 1. Gmail IMAP4_SSL Backend
class GmailBackend(SyncBackend):
    """Stores synced files or versioned backups in a dedicated Gmail label using IMAP4 over TLS."""
    name = "gmail"

    def __init__(self, email_address: str, app_password: str, label: str = "MyOffice/Sync",
                 imap_host: str = GMAIL_IMAP_HOST, imap_port: int = GMAIL_IMAP_PORT,
                 use_ssl: bool = True, ssl_context: ssl.SSLContext | None = None,
                 chunk_size: int = DEFAULT_CHUNK_BYTES):
        addr = (email_address or "").strip()
        pw = normalize_app_password(app_password)
        if not addr or "@" not in addr:
            raise ValueError("Enter your full Gmail address (for example: yourname@gmail.com).")
        if not pw:
            raise ValueError("Enter your 16-character Gmail App Password (click 'Get App Password' for steps).")
        self.email = addr
        self.password = pw
        self.label = label.strip("/") or "MyOffice/Sync"
        self.host = imap_host or GMAIL_IMAP_HOST
        self.port = int(imap_port or GMAIL_IMAP_PORT)
        self.use_ssl = use_ssl
        self.ssl_context = ssl_context
        self.chunk_size = max(65536, int(chunk_size))

    def _connect(self) -> imaplib.IMAP4:
        try:
            if self.use_ssl:
                ctx = self.ssl_context or ssl.create_default_context()
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx, timeout=25)
            else:
                conn = imaplib.IMAP4(self.host, self.port, timeout=25)
            conn.login(self.email, self.password)
            conn.create(f'"{self.label}"')
            typ, _ = conn.select(f'"{self.label}"')
            if typ != "OK":
                # Fallback without quotes for strict non-Gmail test servers
                typ, _ = conn.select(self.label)
                if typ != "OK":
                    conn.create(self.label)
                    typ, _ = conn.select(self.label)
                    if typ != "OK":
                        raise RuntimeError(f"Could not open Gmail folder {self.label!r}")
            return conn
        except Exception as exc:
            raise RuntimeError(friendly_gmail_error(exc)) from exc

    @staticmethod
    def _encode_rel(rel: str) -> str:
        return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii")

    @staticmethod
    def _decode_rel(val: str) -> str:
        raw = (val or "").strip()
        if raw.startswith("b64:"):
            return base64.urlsafe_b64decode(raw[4:].encode("ascii")).decode("utf-8")
        return raw

    def _scan_messages(self, conn: imaplib.IMAP4) -> dict[str, dict]:
        """Return ``rel -> {size, mtime, hash, total, parts: {idx: uid}, stale_uids: [...]}``."""
        typ, data = conn.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0].strip():
            return {}
        uids = data[0].decode("ascii", errors="ignore").split()
        uid_set = ",".join(uids)
        typ, fetched = conn.uid(
            "FETCH", uid_set,
            "(BODY.PEEK[HEADER.FIELDS (X-MyOffice-Rel X-MyOffice-Size X-MyOffice-Mtime X-MyOffice-Hash X-MyOffice-Part X-MyOffice-Rev)])"
        )
        if typ != "OK" or not fetched:
            return {}

        grouped: dict[str, dict[str, dict]] = {}  # rel -> rev -> info
        for item in fetched:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            meta_line = item[0].decode("ascii", errors="ignore")
            m = re.search(r"UID\s+(\d+)", meta_line)
            if not m:
                continue
            uid = m.group(1)
            msg = email.message_from_bytes(item[1], policy=email.policy.default)
            rel_hdr = msg.get("X-MyOffice-Rel", "")
            if not rel_hdr:
                continue
            try:
                rel = self._decode_rel(rel_hdr)
                size = int(msg.get("X-MyOffice-Size", "0"))
                mtime = float(msg.get("X-MyOffice-Mtime", "0"))
                sha = (msg.get("X-MyOffice-Hash", "") or "").strip()
                part_str = (msg.get("X-MyOffice-Part", "1/1") or "1/1").strip()
                p_idx, p_tot = [int(x) for x in part_str.split("/", 1)]
                rev = (msg.get("X-MyOffice-Rev", "") or f"{mtime}-{sha[:8]}").strip()
            except Exception:
                continue
            bucket = grouped.setdefault(rel, {}).setdefault(rev, {
                "size": size, "mtime": mtime, "hash": sha, "total": p_tot, "parts": {},
            })
            bucket["parts"][p_idx] = uid

        resolved: dict[str, dict] = {}
        for rel, revs in grouped.items():
            # Keep newest complete revision; mark older/incomplete revisions stale
            complete = [(rev, info) for rev, info in revs.items() if len(info["parts"]) == info["total"]]
            if not complete:
                continue
            complete.sort(key=lambda kv: kv[1]["mtime"], reverse=True)
            best_rev, best = complete[0]
            stale = [u for r, inf in revs.items() if r != best_rev for u in inf["parts"].values()]
            best["stale_uids"] = stale
            resolved[rel] = best
        return resolved

    @staticmethod
    def _delete_uids(conn: imaplib.IMAP4, uids: list[str]) -> None:
        if not uids:
            return
        uid_set = ",".join(uids)
        conn.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")
        conn.expunge()

    def list(self) -> dict[str, RemoteEntry]:
        with self._connect() as conn:
            records = self._scan_messages(conn)
        return {
            rel: RemoteEntry(info["size"], info["mtime"], info["hash"] or f"{info['size']}-{int(info['mtime'])}")
            for rel, info in records.items()
        }

    def upload(self, local: str, rel: str) -> RemoteEntry:
        raw = Path(local).read_bytes()
        size = len(raw)
        mtime = os.path.getmtime(local)
        sha = hashlib.sha256(raw).hexdigest()
        rev = f"{time.time():.6f}-{secrets.token_hex(4)}"
        chunks = [raw[i:i + self.chunk_size] for i in range(0, max(1, size), self.chunk_size)]
        total = len(chunks)

        with self._connect() as conn:
            existing = self._scan_messages(conn).get(rel)
            for idx, chunk in enumerate(chunks, start=1):
                msg = EmailMessage()
                msg["From"] = self.email
                msg["To"] = self.email
                msg["Subject"] = f"[MyOffice] {rel}" + (f" (part {idx}/{total})" if total > 1 else "")
                msg["X-MyOffice-Rel"] = "b64:" + self._encode_rel(rel)
                msg["X-MyOffice-Size"] = str(size)
                msg["X-MyOffice-Mtime"] = f"{mtime:.6f}"
                msg["X-MyOffice-Hash"] = sha
                msg["X-MyOffice-Part"] = f"{idx}/{total}"
                msg["X-MyOffice-Rev"] = rev
                msg.set_content(f"MyOffice synchronized file: {rel}\nSHA-256: {sha}\nSize: {size} bytes\n")
                msg.add_attachment(chunk, maintype="application", subtype="octet-stream",
                                   filename=os.path.basename(rel) + (f".part{idx}" if total > 1 else ""))
                raw_msg = msg.as_bytes()
                typ, _ = conn.append(f'"{self.label}"', r"(\Seen)", imaplib.Time2Internaldate(time.time()), raw_msg)
                if typ != "OK":
                    typ2, _ = conn.append(self.label, r"(\Seen)", imaplib.Time2Internaldate(time.time()), raw_msg)
                    if typ2 != "OK":
                        raise RuntimeError(f"Gmail IMAP APPEND failed for {rel}")
            if existing:
                old_uids = list(existing["parts"].values()) + existing.get("stale_uids", [])
                self._delete_uids(conn, old_uids)
        return RemoteEntry(size, mtime, sha)

    def download(self, rel: str, local: str) -> None:
        with self._connect() as conn:
            info = self._scan_messages(conn).get(rel)
            if not info:
                raise FileNotFoundError(f"{rel} not found in Gmail label {self.label}")
            buf = bytearray()
            for idx in range(1, info["total"] + 1):
                uid = info["parts"][idx]
                typ, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if typ != "OK" or not fetched:
                    raise RuntimeError(f"Could not download part {idx} of {rel} from Gmail")
                raw_email = next((it[1] for it in fetched if isinstance(it, tuple) and len(it) >= 2), b"")
                parsed = email.message_from_bytes(raw_email, policy=email.policy.default)
                payload = None
                for part in parsed.iter_attachments():
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        break
                if payload is None:
                    raise RuntimeError(f"Attachment missing in Gmail storage message for {rel}")
                buf.extend(payload)
        if info["hash"] and hashlib.sha256(buf).hexdigest() != info["hash"]:
            raise RuntimeError(f"Integrity verification failed when downloading {rel} from Gmail.")
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        tmp = local + ".partial"
        Path(tmp).write_bytes(bytes(buf))
        os.replace(tmp, local)

    def delete(self, rel: str) -> None:
        with self._connect() as conn:
            info = self._scan_messages(conn).get(rel)
            if info:
                self._delete_uids(conn, list(info["parts"].values()) + info.get("stale_uids", []))

    def test(self) -> str:
        n = len(self.list())
        return f"Connected to Gmail ({self.email}) \u2022 folder \"{self.label}\" ready ({n} file(s))."


# ============================================================================= 2. Google Drive OAuth 2.0 Backend
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def authorize_google_oauth_pkce(client_id: str, client_secret: str = "", timeout: float = 180.0) -> dict:
    """Open Google sign-in in the default browser with PKCE loopback callback and exchange the auth code for tokens."""
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("Enter a Google OAuth 2.0 Desktop Client ID (or choose 'Gmail (Email + App Password)' above "
                         "to connect in 60 seconds with zero Google Cloud setup!).")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(16)
    result: dict = {}
    done = threading.Event()

    class _Callback(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = (q.get("code") or [""])[0]
            result["state"] = (q.get("state") or [""])[0]
            result["error"] = (q.get("error") or [""])[0]
            ok = bool(result["code"]) and result["state"] == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            body = ("<h2>MyOffice connected to Google Account!</h2><p>You may close this tab and return to MyOffice.</p>"
                    if ok else f"<h2>Authorization cancelled</h2><p>{result['error']}</p>")
            self.wfile.write(f"<html><body style='font-family:Segoe UI,sans-serif;padding:40px'>{body}</body></html>".encode())
            done.set()

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
    port = httpd.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
    thread = threading.Thread(target=httpd.handle_request, daemon=True)
    thread.start()

    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": DRIVE_SCOPE + " email",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    webbrowser.open(GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params))
    finished = done.wait(timeout=timeout)
    httpd.server_close()
    if not finished:
        raise TimeoutError("Google sign-in timed out before completing in the browser.")
    if result.get("error") or not result.get("code") or result.get("state") != state:
        raise RuntimeError(f"Google sign-in failed: {result.get('error') or 'invalid state'}")

    payload = {
        "client_id": cid,
        "code": result["code"],
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        payload["client_secret"] = client_secret.strip()
    resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=25)
    resp.raise_for_status()
    tok = resp.json()
    tok["obtained_at"] = time.time()
    return tok


class GoogleDriveBackend(SyncBackend):
    """Google Drive v3 REST API backend using OAuth 2.0 refresh/access tokens."""
    name = "gdrive"

    def __init__(self, client_id: str, client_secret: str = "", refresh_token: str = "",
                 access_token: str = "", folder_name: str = "MyOffice Sync", session=None):
        if not refresh_token and not access_token:
            raise ValueError("Google Account is not authorized yet — click 'Sign in with Google' first.")
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.refresh_token = (refresh_token or "").strip()
        self.access_token = (access_token or "").strip()
        self.folder_name = folder_name or "MyOffice Sync"
        self.http = session or requests.Session()
        self._folder_id: str | None = None

    def _headers(self) -> dict[str, str]:
        if not self.access_token and self.refresh_token:
            self._refresh()
        return {"Authorization": f"Bearer {self.access_token}"}

    def _refresh(self) -> None:
        data = {"client_id": self.client_id, "refresh_token": self.refresh_token, "grant_type": "refresh_token"}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = self.http.post(GOOGLE_TOKEN_URL, data=data, timeout=25)
        r.raise_for_status()
        self.access_token = r.json()["access_token"]

    def _req(self, method: str, url: str, **kw):
        r = self.http.request(method, url, headers={**self._headers(), **kw.pop("headers", {})}, timeout=45, **kw)
        if r.status_code == 401 and self.refresh_token:
            self._refresh()
            r = self.http.request(method, url, headers={**self._headers(), **kw.pop("headers", {})}, timeout=45, **kw)
        r.raise_for_status()
        return r

    def _root_id(self) -> str:
        if self._folder_id:
            return self._folder_id
        q = f"mimeType='application/vnd.google-apps.folder' and name='{self.folder_name}' and trashed=false"
        r = self._req("GET", f"{DRIVE_API}/files", params={"q": q, "fields": "files(id,name)"})
        files = r.json().get("files", [])
        if files:
            self._folder_id = files[0]["id"]
            return self._folder_id
        cr = self._req("POST", f"{DRIVE_API}/files",
                       json={"name": self.folder_name, "mimeType": "application/vnd.google-apps.folder"})
        self._folder_id = cr.json()["id"]
        return self._folder_id

    def _files_map(self) -> dict[str, dict]:
        fid = self._root_id()
        out: dict[str, dict] = {}
        token = None
        while True:
            params = {
                "q": f"'{fid}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'",
                "fields": "nextPageToken,files(id,name,size,md5Checksum,modifiedTime,appProperties)",
                "pageSize": 200,
            }
            if token:
                params["pageToken"] = token
            data = self._req("GET", f"{DRIVE_API}/files", params=params).json()
            for f in data.get("files", []):
                rel = (f.get("appProperties") or {}).get("rel") or f["name"]
                out[rel] = f
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    def list(self) -> dict[str, RemoteEntry]:
        out: dict[str, RemoteEntry] = {}
        for rel, f in self._files_map().items():
            sz = int(f.get("size", 0) or 0)
            mt_raw = (f.get("modifiedTime") or "").replace("Z", "+00:00")
            try:
                from datetime import datetime

                mt = datetime.fromisoformat(mt_raw).timestamp()
            except ValueError:
                mt = time.time()
            out[rel] = RemoteEntry(sz, mt, f.get("md5Checksum") or f"{sz}-{int(mt)}")
        return out

    def upload(self, local: str, rel: str) -> RemoteEntry:
        fid = self._root_id()
        existing = self._files_map().get(rel)
        raw = Path(local).read_bytes()
        meta = {"name": rel.replace("/", "__"), "appProperties": {"rel": rel}}
        boundary = "===============myoffice_" + secrets.token_hex(8)
        if not existing:
            meta["parents"] = [fid]
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
        if existing:
            r = self._req("PATCH", f"{DRIVE_UPLOAD_API}/files/{existing['id']}?uploadType=multipart&fields=id,size,md5Checksum",
                          data=body, headers=headers)
        else:
            r = self._req("POST", f"{DRIVE_UPLOAD_API}/files?uploadType=multipart&fields=id,size,md5Checksum",
                          data=body, headers=headers)
        info = r.json()
        mtime = os.path.getmtime(local)
        return RemoteEntry(len(raw), mtime, info.get("md5Checksum") or f"{len(raw)}-{int(mtime)}")

    def download(self, rel: str, local: str) -> None:
        f = self._files_map().get(rel)
        if not f:
            raise FileNotFoundError(rel)
        r = self._req("GET", f"{DRIVE_API}/files/{f['id']}", params={"alt": "media"})
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        tmp = local + ".partial"
        Path(tmp).write_bytes(r.content)
        os.replace(tmp, local)

    def delete(self, rel: str) -> None:
        f = self._files_map().get(rel)
        if f:
            self._req("DELETE", f"{DRIVE_API}/files/{f['id']}")

    def test(self) -> str:
        n = len(self.list())
        return f"Connected to Google Drive folder \"{self.folder_name}\" ({n} file(s))."
