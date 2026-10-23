"""Template catalogue (WPS-style .wpt / .ett / .dpt templates).

Templates are ordinary OOXML packages with a template extension:
``.wpt`` = Writer (DOCX), ``.ett`` = Spreadsheet (XLSX), ``.dpt`` = Presentation (PPTX).
Each template folder holds an ``index.json`` describing its templates and PNG thumbnails.
Built-in templates ship in ``resources/templates/<kind>``; user templates live in the
user directory.  An optional online catalogue (JSON) can be downloaded from the URL in
``config.json`` (``templates.catalog_url``).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.services.config import cfg
from app.services.paths import resource_path, user_subdir

log = logging.getLogger(__name__)

KIND_EXT = {"writer": ".wpt", "sheet": ".ett", "slide": ".dpt"}
REAL_EXT = {"writer": ".docx", "sheet": ".xlsx", "slide": ".pptx"}
EXT_KIND = {".wpt": "writer", ".dotx": "writer", ".ett": "sheet", ".xltx": "sheet", ".dpt": "slide", ".potx": "slide"}


@dataclass
class Template:
    kind: str
    name: str
    path: str
    thumbnail: str = ""
    category: str = "General"
    description: str = ""
    builtin: bool = True


def builtin_dir(kind: str) -> Path:
    return resource_path("templates", kind)


def user_templates_dir(kind: str) -> Path:
    return user_subdir("templates", kind)


def _read_index(folder: Path) -> list[dict]:
    idx = folder / "index.json"
    if not idx.is_file():
        return []
    try:
        with open(idx, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("templates", []) if isinstance(data, dict) else []
    except (OSError, ValueError) as exc:
        log.warning("Bad template index %s: %s", idx, exc)
        return []


def _write_index(folder: Path, entries: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "index.json.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "templates": entries}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, folder / "index.json")


def list_templates(kind: str | None = None) -> list[Template]:
    kinds = [kind] if kind else list(KIND_EXT)
    out: list[Template] = []
    for k in kinds:
        for folder, builtin in ((builtin_dir(k), True), (user_templates_dir(k), False)):
            listed = set()
            for e in _read_index(folder):
                f = folder / e.get("file", "")
                if not f.is_file():
                    continue
                listed.add(f.name)
                thumb = folder / e.get("thumbnail", "") if e.get("thumbnail") else None
                out.append(Template(k, e.get("name", f.stem), str(f), str(thumb) if thumb and thumb.is_file() else "",
                                    e.get("category", "General"), e.get("description", ""), builtin))
            if folder.is_dir():  # files dropped in without an index entry
                for f in sorted(folder.iterdir()):
                    if f.suffix.lower() in (KIND_EXT[k], REAL_EXT[k]) and f.name not in listed:
                        png = f.with_suffix(".png")
                        out.append(Template(k, f.stem.replace("_", " "), str(f), str(png) if png.is_file() else "",
                                            "My Templates" if not builtin else "General", "", builtin))
    return out


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    return s[:60] or "template"


def save_user_template(kind: str, name: str, src_file: str, thumbnail_png: bytes | None = None,
                       category: str = "My Templates", description: str = "") -> Template:
    folder = user_templates_dir(kind)
    base = _slug(name)
    target = folder / (base + KIND_EXT[kind])
    n = 2
    while target.exists():
        target = folder / f"{base}_{n}{KIND_EXT[kind]}"
        n += 1
    shutil.copyfile(src_file, target)
    thumb = ""
    if thumbnail_png:
        tp = target.with_suffix(".png")
        tp.write_bytes(thumbnail_png)
        thumb = tp.name
    entries = _read_index(folder)
    entries.append({"file": target.name, "name": name, "category": category, "description": description,
                    "thumbnail": thumb})
    _write_index(folder, entries)
    return Template(kind, name, str(target), str(folder / thumb) if thumb else "", category, description, False)


def delete_user_template(t: Template) -> None:
    if t.builtin:
        raise PermissionError("Built-in templates cannot be deleted")
    folder = Path(t.path).parent
    entries = [e for e in _read_index(folder) if e.get("file") != Path(t.path).name]
    _write_index(folder, entries)
    for p in (Path(t.path), Path(t.thumbnail) if t.thumbnail else None):
        if p and p.is_file():
            p.unlink()


def instantiate(t: Template | str, kind: str | None = None) -> str:
    """Copy a template to a temporary file with the real document extension and return its path."""
    path = t.path if isinstance(t, Template) else t
    kind = (t.kind if isinstance(t, Template) else kind) or EXT_KIND.get(Path(path).suffix.lower(), "writer")
    if not zipfile.is_zipfile(path):
        return path  # legacy binary template: caller converts it
    fd, tmp = tempfile.mkstemp(prefix="myoffice_tpl_", suffix=REAL_EXT[kind])
    os.close(fd)
    shutil.copyfile(path, tmp)
    return tmp


def download_catalog(url: str | None = None, progress=None) -> int:
    """Download templates listed in an online JSON catalogue into the user template folders.

    Catalogue format: ``{"templates": [{"kind": "writer", "name": "...", "url": "...",
    "thumbnail_url": "...", "category": "...", "description": "..."}]}``"""
    import requests

    url = url or cfg().get("templates.catalog_url", "") or cfg().get("servers.templates", "")
    if not url:
        raise ValueError("No template catalogue URL configured (Settings \u2192 Servers).")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    items = resp.json().get("templates", [])
    count = 0
    for i, it in enumerate(items):
        kind = it.get("kind", "writer")
        if kind not in KIND_EXT or not it.get("url"):
            continue
        if progress:
            progress(int(100 * i / max(1, len(items))), it.get("name", ""))
        r = requests.get(it["url"], timeout=60)
        r.raise_for_status()
        fd, tmp = tempfile.mkstemp(suffix=KIND_EXT[kind])
        with os.fdopen(fd, "wb") as fh:
            fh.write(r.content)
        thumb = None
        if it.get("thumbnail_url"):
            try:
                tr = requests.get(it["thumbnail_url"], timeout=30)
                if tr.ok:
                    thumb = tr.content
            except requests.RequestException:
                thumb = None
        save_user_template(kind, it.get("name", "Template"), tmp, thumb, it.get("category", "Online"),
                           it.get("description", ""))
        os.unlink(tmp)
        count += 1
    return count
