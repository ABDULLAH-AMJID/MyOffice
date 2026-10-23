"""Version history: every save keeps a copy of the saved file, per document.

Versions live in ``%APPDATA%/MyOffice/versions/<id>/`` where <id> is derived from the document's full path, so two
files with the same name never mix.  ``index.json`` records the original path; the newest ``versions.keep`` copies
(default 30) are kept.  Restoring first records the current file as a version, so a restore can be undone.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.config import cfg
from app.services.paths import user_subdir

log = logging.getLogger(__name__)


@dataclass
class Version:
    path: Path
    timestamp: datetime
    size: int
    label: str = ""


def _key(original: str | os.PathLike) -> str:
    p = os.path.normcase(os.path.abspath(str(original)))
    return hashlib.sha1(p.encode("utf-8")).hexdigest()[:16]


def folder(original: str | os.PathLike) -> Path:
    return user_subdir("versions", _key(original))


def _index(d: Path) -> dict:
    try:
        return json.loads((d / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(original: str | os.PathLike, label: str = "") -> Path | None:
    """Store a copy of ``original`` (call right after saving it)."""
    src = Path(original)
    if not src.is_file() or not cfg().get("versions.enabled", True):
        return None
    d = folder(src)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = d / f"{stamp}{src.suffix.lower()}"
    versions = list_versions(src)
    if versions and versions[0].size == src.stat().st_size and _same(versions[0].path, src):
        return versions[0].path  # unchanged since the last version
    shutil.copy2(src, dest)
    idx = _index(d)
    idx["original"] = str(src.resolve())
    if label:
        idx.setdefault("labels", {})[dest.name] = label
    (d / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    prune(src, int(cfg().get("versions.keep", 30)))
    return dest


def _same(a: Path, b: Path) -> bool:
    def digest(p):
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.digest()
    try:
        return digest(a) == digest(b)
    except OSError:
        return False


def list_versions(original: str | os.PathLike) -> list[Version]:
    d = folder(original)
    labels = _index(d).get("labels", {})
    out = []
    for p in d.iterdir():
        if p.name == "index.json" or not p.is_file():
            continue
        try:
            ts = datetime.strptime(p.stem[:22], "%Y%m%d_%H%M%S_%f")
        except ValueError:
            continue
        out.append(Version(p, ts, p.stat().st_size, labels.get(p.name, "")))
    out.sort(key=lambda v: v.timestamp, reverse=True)
    return out


def prune(original: str | os.PathLike, keep: int) -> int:
    removed = 0
    for v in list_versions(original)[max(1, keep):]:
        try:
            v.path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def restore(version: Version, original: str | os.PathLike) -> Path:
    target = Path(original)
    if target.exists():
        record(target, label="Before restore")
    shutil.copy2(version.path, target)
    return target


def set_label(version: Version, label: str) -> None:
    d = version.path.parent
    idx = _index(d)
    idx.setdefault("labels", {})[version.path.name] = label
    (d / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
