"""Cloud Sync (Gmail / Google Drive / Wi-Fi Router USB FTP & SMB / S3 / Supabase / Shared Folder + Hardware Vault).

* Three-way sync using a state file (last synced size/mtime/hash per file) so local edits, remote edits, deletions,
  and conflicts are handled cleanly without data loss.
* Conflicts never overwrite: the local copy is preserved alongside as ``name (conflict <host> <date>).ext``.
* ``watchdog`` triggers background sync on local edits; ``start_background_sync`` syncs periodically.
* Optional ``VaultSyncBackend`` encrypts all remote filenames & contents with hardware-locked AES-256-GCM.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.services.config import cfg
from app.services.paths import user_subdir

log = logging.getLogger(__name__)
IGNORE = ["~$*", "*.tmp", ".~lock.*", "*.swp", ".DS_Store", "Thumbs.db", "desktop.ini", ".myoffice_sync*", "*.partial"]


def file_hash(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ignored(rel: str) -> bool:
    name = os.path.basename(rel)
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE)


@dataclass
class RemoteEntry:
    size: int
    mtime: float
    etag: str = ""


@dataclass
class SyncReport:
    uploaded: list = field(default_factory=list)
    downloaded: list = field(default_factory=list)
    deleted_local: list = field(default_factory=list)
    deleted_remote: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def summary(self) -> str:
        return (f"\u2191 {len(self.uploaded)} uploaded, \u2193 {len(self.downloaded)} downloaded, "
                f"{len(self.deleted_local) + len(self.deleted_remote)} deleted, {len(self.conflicts)} conflict(s), "
                f"{len(self.errors)} error(s)")

    @property
    def changed(self) -> bool:
        return bool(self.uploaded or self.downloaded or self.deleted_local or self.deleted_remote or self.conflicts)


# ============================================================================= backends
class SyncBackend:
    name = "base"

    def list(self) -> dict[str, RemoteEntry]:  # pragma: no cover - abstract
        raise NotImplementedError

    def upload(self, local: str, rel: str) -> RemoteEntry:  # pragma: no cover - abstract
        raise NotImplementedError

    def download(self, rel: str, local: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def delete(self, rel: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def test(self) -> str:
        n = len(self.list())
        return f"Connected: {n} file(s) on the remote."


class FolderBackend(SyncBackend):
    name = "folder"

    def __init__(self, target: str):
        if not target:
            raise ValueError("No target folder configured")
        self.root = Path(target)
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self):
        out = {}
        for p in self.root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.root).as_posix()
                if not ignored(rel):
                    st = p.stat()
                    out[rel] = RemoteEntry(st.st_size, st.st_mtime, f"{st.st_size}-{int(st.st_mtime)}")
        return out

    def upload(self, local, rel):
        dst = self.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".partial")
        shutil.copy2(local, tmp)
        os.replace(tmp, dst)
        st = dst.stat()
        return RemoteEntry(st.st_size, st.st_mtime, f"{st.st_size}-{int(st.st_mtime)}")

    def download(self, rel, local):
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        tmp = local + ".partial"
        shutil.copy2(self.root / rel, tmp)
        os.replace(tmp, local)

    def delete(self, rel):
        p = self.root / rel
        if p.exists():
            p.unlink()


class S3Backend(SyncBackend):
    """S3-compatible object storage (AWS, MinIO, R2, Supabase Storage S3 endpoint)."""
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "MyOffice/", endpoint: str = "", region: str = "",
                 access_key: str = "", secret_key: str = "", client=None):
        import boto3
        from botocore.config import Config

        if not bucket:
            raise ValueError("No bucket configured")
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        if client is None:
            kw = dict(config=Config(retries={"max_attempts": 3}, connect_timeout=10, read_timeout=60,
                                    s3={"addressing_style": "path"}))
            if endpoint:
                kw["endpoint_url"] = endpoint
            if region:
                kw["region_name"] = region
            if access_key:
                kw["aws_access_key_id"] = access_key
                kw["aws_secret_access_key"] = secret_key
            client = boto3.client("s3", **kw)
        self.s3 = client

    def list(self):
        out = {}
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(self.prefix):]
                if rel and not rel.endswith("/") and not ignored(rel):
                    out[rel] = RemoteEntry(int(obj["Size"]), obj["LastModified"].timestamp(), obj.get("ETag", "").strip('"'))
        return out

    def upload(self, local, rel):
        key = self.prefix + rel
        self.s3.upload_file(local, self.bucket, key, ExtraArgs={"Metadata": {"mtime": str(os.path.getmtime(local))}})
        head = self.s3.head_object(Bucket=self.bucket, Key=key)
        return RemoteEntry(int(head["ContentLength"]), head["LastModified"].timestamp(), head.get("ETag", "").strip('"'))

    def download(self, rel, local):
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        tmp = local + ".partial"
        self.s3.download_file(self.bucket, self.prefix + rel, tmp)
        os.replace(tmp, local)

    def delete(self, rel):
        self.s3.delete_object(Bucket=self.bucket, Key=self.prefix + rel)


def make_backend(settings: dict | None = None) -> SyncBackend:
    from app.services import vault
    from app.services.gmail_storage import GmailBackend, GoogleDriveBackend
    from app.services.net_storage import FtpStorageBackend, VaultSyncBackend, connect_smb_share

    s = settings if settings is not None else cfg().get("sync", {})
    kind = s.get("backend", "folder")
    if kind == "gmail":
        base: SyncBackend = GmailBackend(
            s.get("gmail_email", ""),
            vault.unseal_secret(s.get("gmail_app_password", "")),
            label=s.get("gmail_label", "") or "MyOffice/Sync",
            imap_host=s.get("gmail_imap_host", "") or "imap.gmail.com",
            imap_port=int(s.get("gmail_imap_port") or 993),
            use_ssl=bool(s.get("gmail_use_ssl", True)),
        )
    elif kind == "gdrive":
        base = GoogleDriveBackend(
            s.get("gdrive_client_id", ""),
            vault.unseal_secret(s.get("gdrive_client_secret", "")),
            refresh_token=vault.unseal_secret(s.get("gdrive_refresh_token", "")),
            access_token=vault.unseal_secret(s.get("gdrive_access_token", "")),
            folder_name=s.get("gdrive_folder", "") or "MyOffice Sync",
        )
    elif kind == "mobile_peer":
        from app.services.mobile_sync import MobilePeerBackend

        base = MobilePeerBackend(
            s.get("mobile_url") or s.get("url") or "",
            token=vault.unseal_secret(s.get("mobile_token") or s.get("token") or ""),
            pin=vault.unseal_secret(s.get("mobile_pin") or s.get("pin") or ""),
            device_name=s.get("device_name") or "",
        )
    elif kind == "ftp":
        base = FtpStorageBackend(
            s.get("ftp_url", ""),
            s.get("ftp_user", "") or "anonymous",
            vault.unseal_secret(s.get("ftp_password", "")),
        )
    elif kind == "folder":
        target = s.get("folder_target") or s.get("target") or ""
        if target.startswith("\\\\") and s.get("smb_user"):
            ok, msg = connect_smb_share(target, s.get("smb_user", ""), vault.unseal_secret(s.get("smb_password", "")))
            if not ok:
                raise RuntimeError(f"Could not authenticate router/network share: {msg}")
        base = FolderBackend(target)
    elif kind in ("s3", "supabase"):
        s3 = s.get("s3") or s
        endpoint = s3.get("endpoint") or s.get("url") or ""
        if kind == "supabase" and endpoint and not endpoint.rstrip("/").endswith("/storage/v1/s3"):
            endpoint = endpoint.rstrip("/") + "/storage/v1/s3"
        base = S3Backend(
            s3.get("bucket", ""), s3.get("prefix", "MyOffice/"), endpoint,
            s3.get("region", ""), s3.get("access_key", ""),
            vault.unseal_secret(s3.get("secret_key", "") or s3.get("api_key", "")),
        )
    else:
        raise ValueError(f"Unknown sync backend {kind}")

    if s.get("encrypt"):
        return VaultSyncBackend(base, passphrase=vault.unseal_secret(s.get("vault_passphrase", "")))
    return base


build_backend = make_backend


# ============================================================================= engine
class SyncEngine:
    def __init__(self, local_dir: str, backend: SyncBackend, state_path: str | None = None,
                 delete_remote: bool = False):
        if not local_dir:
            raise ValueError("Choose a local folder to sync.")
        self.local = Path(local_dir)
        self.local.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.delete_remote = delete_remote
        self.state_path = Path(state_path) if state_path else self.local / ".myoffice_sync.json"
        self.lock = threading.Lock()
        self.state = self._load_state()

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"files": {}}

    def _save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def scan_local(self) -> dict[str, tuple[int, float]]:
        out = {}
        for p in self.local.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.local).as_posix()
                if not ignored(rel):
                    st = p.stat()
                    out[rel] = (st.st_size, st.st_mtime)
        return out

    def _local_changed(self, rel: str, info, known: dict | None) -> bool:
        if known is None:
            return True
        if info[0] == known.get("size") and abs(info[1] - known.get("mtime", 0)) < 1.0:
            return False
        return file_hash(str(self.local / rel)) != known.get("hash")

    @staticmethod
    def _remote_changed(entry: RemoteEntry, known: dict | None) -> bool:
        if known is None:
            return True
        if entry.etag and known.get("etag"):
            return entry.etag != known["etag"]
        return entry.size != known.get("rsize") or abs(entry.mtime - known.get("rmtime", 0)) > 1.0

    def _record(self, rel: str, remote: RemoteEntry):
        p = self.local / rel
        st = p.stat()
        self.state["files"][rel] = {"size": st.st_size, "mtime": st.st_mtime, "hash": file_hash(str(p)),
                                    "etag": remote.etag, "rsize": remote.size, "rmtime": remote.mtime}

    def conflict_name(self, rel: str) -> str:
        p = Path(rel)
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        return str(p.with_name(f"{p.stem} (conflict {socket.gethostname()} {stamp}){p.suffix}").as_posix())

    def sync_once(self, progress=None) -> SyncReport:
        with self.lock:
            rep = SyncReport()
            remote = self.backend.list()
            local = self.scan_local()
            known_all = self.state.setdefault("files", {})
            names = sorted(set(remote) | set(local) | set(known_all))
            for k, rel in enumerate(names):
                if progress:
                    progress(int(100 * k / max(1, len(names))), rel)
                try:
                    self._sync_file(rel, local.get(rel), remote.get(rel), known_all.get(rel), rep)
                except Exception as exc:
                    log.warning("Sync error on %s: %s", rel, exc)
                    rep.errors.append(f"{rel}: {exc}")
            self.state["last_sync"] = time.time()
            self._save_state()
            return rep

    sync = sync_once

    def _sync_file(self, rel, linfo, rentry: RemoteEntry | None, known, rep: SyncReport):
        lp = str(self.local / rel)
        if linfo and rentry:
            lch = self._local_changed(rel, linfo, known)
            rch = self._remote_changed(rentry, known)
            if known is None:
                if rentry.size == linfo[0] and self._same_content(rel):
                    self._record(rel, rentry)
                    return
                lch = rch = True
            if lch and rch:
                cname = self.conflict_name(rel)
                os.replace(lp, str(self.local / cname))
                self.backend.download(rel, lp)
                self._record(rel, rentry)
                up = self.backend.upload(str(self.local / cname), cname)
                self._record(cname, up)
                rep.conflicts.append(rel)
            elif lch:
                self._record(rel, self.backend.upload(lp, rel))
                rep.uploaded.append(rel)
            elif rch:
                self.backend.download(rel, lp)
                self._record(rel, rentry)
                rep.downloaded.append(rel)
        elif linfo and not rentry:
            if known is not None and not self._local_changed(rel, linfo, known):
                os.unlink(lp)
                self.state["files"].pop(rel, None)
                rep.deleted_local.append(rel)
            else:
                self._record(rel, self.backend.upload(lp, rel))
                rep.uploaded.append(rel)
        elif rentry and not linfo:
            if known is not None and not self._remote_changed(rentry, known):
                if self.delete_remote:
                    self.backend.delete(rel)
                    rep.deleted_remote.append(rel)
                    self.state["files"].pop(rel, None)
                else:
                    self.backend.download(rel, lp)
                    self._record(rel, rentry)
                    rep.downloaded.append(rel)
            else:
                self.backend.download(rel, lp)
                self._record(rel, rentry)
                rep.downloaded.append(rel)
        else:
            self.state["files"].pop(rel, None)

    def _same_content(self, rel: str) -> bool:
        fd, tmp = tempfile.mkstemp()
        os.close(fd)
        try:
            self.backend.download(rel, tmp)
            return file_hash(tmp) == file_hash(str(self.local / rel))
        except Exception:
            return False
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def default_local_dir() -> str:
    d = cfg().get("sync.local_dir") or cfg().get("sync.local") or ""
    if d:
        return d
    p = Path.home() / "MyOffice Cloud"
    return str(p)


def engine_from_config() -> SyncEngine:
    s = cfg().get("sync", {})
    local = s.get("local_dir") or s.get("local") or default_local_dir()
    state = str(user_subdir("sync") / (hashlib.sha1(local.encode()).hexdigest()[:12] + ".json"))
    return SyncEngine(local, make_backend(s), state, bool(s.get("delete_remote", False)))


# ============================================================================= background service (Qt)
def start_background_sync(main_window):
    from PySide6.QtCore import QObject, QThread, QTimer, Signal
    from app.services import firewall

    if firewall.consent() is not True:
        log.info("Cloud sync not started: no network consent")
        return None

    class _Worker(QObject):
        finished = Signal(object)

        def __init__(self, engine):
            super().__init__()
            self.engine = engine

        def run(self):
            try:
                rep = self.engine.sync_once()
            except Exception as exc:
                rep = SyncReport(errors=[str(exc)])
            self.finished.emit(rep)

    class SyncService(QObject):
        status = Signal(str)

        def __init__(self, parent):
            super().__init__(parent)
            self.engine = engine_from_config()
            self.thread = None
            self.pending = False
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.request)
            self.timer.start(max(1, int(cfg().get("sync.interval_minutes", 10))) * 60_000)
            self.debounce = QTimer(self, singleShot=True, interval=5000, timeout=self.request)
            self.observer = None
            try:
                from watchdog.events import FileSystemEventHandler
                from watchdog.observers import Observer

                svc = self

                class Handler(FileSystemEventHandler):
                    def on_any_event(self, event):
                        if not event.is_directory and not ignored(os.path.basename(getattr(event, "src_path", ""))):
                            from PySide6.QtCore import QMetaObject, Qt

                            QMetaObject.invokeMethod(svc.debounce, "start", Qt.QueuedConnection)

                self.observer = Observer()
                self.observer.schedule(Handler(), str(self.engine.local), recursive=True)
                self.observer.daemon = True
                self.observer.start()
            except Exception as exc:
                log.warning("File watching unavailable: %s", exc)
            QTimer.singleShot(1500, self.request)

        def request(self):
            if self.thread is not None:
                self.pending = True
                return
            self.status.emit("Syncing\u2026")
            self.thread = QThread()
            self.worker = _Worker(self.engine)
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.finished.connect(self._done)
            self.thread.start()

        def _done(self, rep: SyncReport):
            self.thread.quit()
            self.thread.wait()
            self.thread = None
            if rep.errors and not rep.changed:
                self.status.emit("Sync error: " + rep.errors[0][:60])
            else:
                self.status.emit(f"Synced {datetime.now().strftime('%H:%M')}")
            if self.pending:
                self.pending = False
                self.request()

        def stop(self):
            self.timer.stop()
            if self.observer is not None:
                self.observer.stop()
                self.observer.join(timeout=3)
            if self.thread is not None:
                self.thread.quit()
                self.thread.wait(5000)

    svc = SyncService(main_window)
    if hasattr(main_window, "sync_label"):
        svc.status.connect(lambda s: main_window.sync_label.setText("\u2601 " + s))
    return svc
