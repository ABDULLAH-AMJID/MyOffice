"""Portable ``.mopkg`` package: one file carrying a deck *and* its scripts.

Layout inside the ZIP::

    meta.json            {"format": "myoffice-package", "version": 1, ...}
    document.pptx        the deck itself (unchanged bytes)
    scripts.moscript     the sidecar, if the deck has scripts

Used from the Scripts dialog ("Export package…"), so sending a deck with its
automation never means sending two loose files.  The ``.pptx`` is copied
byte-for-byte — packaging never rewrites the OOXML container.
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from app.services.i18n import _t

FORMAT = "myoffice-package"
VERSION = 1
SUFFIX = ".mopkg"


class BundleError(Exception):
    pass


def export_bundle(doc, target_path) -> Path:
    """Zip the saved deck (and its sidecar, when present) into ``target_path``."""
    if doc.path is None or not Path(doc.path).exists():
        raise BundleError(_t("Save the presentation first, then export a package."))
    deck = Path(doc.path)
    sidecar = Path(str(deck) + ".moscript")
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = {"format": FORMAT, "version": VERSION, "document": deck.name,
            "scripts": sidecar.name if sidecar.exists() else ""}
    tmp = target.with_name(target.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        z.write(deck, "document.pptx")
        if sidecar.exists():
            z.write(sidecar, "scripts.moscript")
    tmp.replace(target)
    return target


def open_bundle(path, into_dir=None) -> dict:
    """Extract a package and return ``{"document": <path>, "scripts": <path|None>, "meta": {...}}``."""
    src = Path(path)
    try:
        z = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleError(_t("This is not a MyOffice package: {0}").format(exc)) from exc
    with z:
        names = set(z.namelist())
        if "meta.json" not in names or "document.pptx" not in names:
            raise BundleError(_t("The package is missing its document or manifest."))
        try:
            meta = json.loads(z.read("meta.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BundleError(_t("The package manifest is damaged: {0}").format(exc)) from exc
        if int(meta.get("version", 0)) > VERSION:
            raise BundleError(_t("This package was made by a newer version of MyOffice."))
        folder = Path(into_dir) if into_dir else Path(tempfile.mkdtemp(prefix="myoffice_pkg_"))
        folder.mkdir(parents=True, exist_ok=True)
        deck = folder / (meta.get("document") or "document.pptx")
        with z.open("document.pptx") as src_fh, open(deck, "wb") as out_fh:
            out_fh.write(src_fh.read())
        scripts = None
        if "scripts.moscript" in names:
            scripts = Path(str(deck) + ".moscript")
            with z.open("scripts.moscript") as src_fh, open(scripts, "wb") as out_fh:
                out_fh.write(src_fh.read())
    return {"document": deck, "scripts": scripts, "meta": meta}
