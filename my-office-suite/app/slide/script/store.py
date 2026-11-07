"""Sidecar storage for presentation scripts.

A deck keeps its scripts in ``<deck file name>.moscript`` next to the deck — see
``docs/SCRIPTING_DESIGN.md`` §5.  The file is plain UTF-8 JSON so it can be
diffed, backed up, inspected and version-controlled, and so a broken file is a
readable error rather than a corrupt document.  Nothing is ever written into the
``.pptx`` package.

Trust (``docs/SCRIPTING_DESIGN.md`` §6.2) also lives here:

* a sidecar inside a trusted folder (``scripts.trusted_dirs``) or in the user's
  own script library runs without a prompt;
* any other sidecar must be approved once per *file content* — the SHA-256 of
  the file is remembered, so editing it forces a fresh decision.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.config import cfg
from app.services.i18n import _t
from app.services.paths import resource_path, user_subdir

FORMAT = "myoffice-script"
VERSION = 1
SUFFIX = ".moscript"

DEFAULTS = {"enabled": True, "timeout": 30, "trusted_dirs": [], "approved": [], "autosave_with_document": True}


# --------------------------------------------------------------------------- settings helpers
def settings() -> dict:
    out = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        out[key] = cfg().get(f"scripts.{key}", default)
    return out


def enabled() -> bool:
    return bool(settings()["enabled"])


def timeout_seconds() -> float:
    try:
        return max(1.0, float(settings()["timeout"]))
    except (TypeError, ValueError):
        return 30.0


def scripts_dir() -> Path:
    """The user's global script library (always trusted, kept on uninstall)."""
    return user_subdir("slide_scripts")


# --------------------------------------------------------------------------- model
@dataclass
class Script:
    name: str
    code: str = ""
    description: str = ""
    shortcut: str = ""
    created: str = field(default_factory=lambda: _now())
    modified: str = field(default_factory=lambda: _now())

    def touch(self):
        self.modified = _now()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_name(name: str) -> str:
    """Same rule as app.macros.manager.save_macro(), so files look alike everywhere."""
    s = re.sub(r"[^\w\- ]+", "_", str(name)).strip().replace(" ", "_")
    return s or "script"


def sidecar_path(doc_path) -> Path:
    """``/x/Report.pptx`` → ``/x/Report.pptx.moscript`` (suffix appended, never replaced)."""
    return Path(str(doc_path) + SUFFIX)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- store
class ScriptStore:
    """The scripts that belong to one deck (or to the global library)."""

    def __init__(self, path: Path | None = None):
        self.path: Path | None = Path(path) if path else None
        self.scripts: list[Script] = []
        self.doc_hash: str = ""
        self.error: str | None = None      # last load/save problem, shown in the dialog
        self.dirty: bool = False

    # -- list operations --------------------------------------------------
    def __len__(self):
        return len(self.scripts)

    def __iter__(self):
        return iter(self.scripts)

    def names(self) -> list[str]:
        return [s.name for s in self.scripts]

    def get(self, name: str) -> Script | None:
        for s in self.scripts:
            if s.name == name:
                return s
        return None

    def unique_name(self, name: str) -> str:
        base = str(name).strip() or _t("Script")
        if base not in self.names():
            return base
        i = 2
        while f"{base} ({i})" in self.names():
            i += 1
        return f"{base} ({i})"

    def add(self, name: str, code: str = "", description: str = "") -> Script:
        s = Script(self.unique_name(name), code, description)
        self.scripts.append(s)
        self.dirty = True
        return s

    def normalise(self, script: Script) -> Script:
        """Give an imported script a name that does not collide with an existing one."""
        script.name = self.unique_name(script.name)
        return script

    def remove(self, name: str) -> bool:
        s = self.get(name)
        if s is None:
            return False
        self.scripts.remove(s)
        self.dirty = True
        return True

    def rename(self, old: str, new: str) -> bool:
        s = self.get(old)
        if s is None or not str(new).strip():
            return False
        s.name = self.unique_name(str(new).strip()) if str(new).strip() != old else old
        s.touch()
        self.dirty = True
        return True

    # -- persistence ------------------------------------------------------
    def to_json(self, document_name: str = "") -> str:
        data = {
            "format": FORMAT,
            "version": VERSION,
            "engine": "python",
            "document": {"name": document_name, "sha256": self.doc_hash},
            "scripts": [asdict(s) for s in self.scripts],
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def save_to(self, path: Path, document_name: str = "") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(self.to_json(document_name), encoding="utf-8")
        tmp.replace(path)
        self.path = path
        self.dirty = False
        return path

    def save_for_document(self, doc_path) -> Path | None:
        """Write the sidecar next to ``doc_path``; delete it when no scripts are left."""
        if doc_path is None:
            return None
        target = sidecar_path(doc_path)
        if not self.scripts:
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
            self.path = None
            self.dirty = False
            return None
        return self.save_to(target, Path(str(doc_path)).name)

    @classmethod
    def load_file(cls, path) -> "ScriptStore":
        store = cls(path)
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError(_t("Not a MyOffice script file."))
        version = int(data.get("version", 0))
        if version > VERSION:
            raise ValueError(_t("This file was written by a newer version of MyOffice (script format {0}).")
                             .format(version))
        store.doc_hash = str((data.get("document") or {}).get("sha256", ""))
        for item in data.get("scripts", []):
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            store.scripts.append(Script(str(item["name"]), str(item.get("code", "")),
                                        str(item.get("description", "")), str(item.get("shortcut", "")),
                                        str(item.get("created") or _now()), str(item.get("modified") or _now())))
        return store

    @classmethod
    def for_document(cls, doc_path) -> "ScriptStore":
        """Load the sidecar of a deck; never raises — problems end up in ``.error``."""
        store = cls()
        if doc_path is None:
            return store
        target = sidecar_path(doc_path)
        if not target.exists():
            return store
        try:
            return cls.load_file(target)
        except Exception as exc:  # malformed file must never block opening a deck
            store.path = target
            store.error = _t("The script file {0} could not be read: {1}").format(target.name, exc)
            return store

    # -- global library ---------------------------------------------------
    @classmethod
    def library(cls) -> "ScriptStore":
        """My scripts: one file per script, in the user's script folder."""
        store = cls(scripts_dir())
        folder = scripts_dir()
        if not folder.exists():
            return store
        for p in sorted(folder.glob("*.py")):
            try:
                store.scripts.append(Script(p.stem.replace("_", " "), p.read_text(encoding="utf-8")))
            except OSError:
                continue
        for p in sorted(folder.glob(f"*{SUFFIX}")):
            try:
                for s in cls.load_file(p).scripts:
                    store.scripts.append(s)
            except Exception:
                continue
        return store

    def save_library_script(self, script: Script) -> Path:
        folder = scripts_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safe_name(script.name)}.py"
        path.write_text(script.code, encoding="utf-8")
        return path

    def delete_library_script(self, name: str) -> bool:
        folder = scripts_dir()
        s = self.get(name)
        if s is None:
            return False
        for p in (folder / f"{safe_name(name)}.py", folder / f"{safe_name(name)}{SUFFIX}"):
            try:
                p.unlink()
            except OSError:
                pass
        self.scripts.remove(s)
        return True


# --------------------------------------------------------------------------- trust
def trusted_dirs() -> list[Path]:
    dirs = []
    for raw in settings().get("trusted_dirs", []) or []:
        try:
            dirs.append(Path(str(raw)).expanduser().resolve())
        except OSError:
            continue
    dirs.append(scripts_dir().resolve())
    return dirs


def is_trusted(path) -> bool:
    """True when ``path`` sits in a folder the user has declared trusted."""
    if path is None:
        return True
    try:
        rp = Path(str(path)).resolve()
    except OSError:
        return False
    for d in trusted_dirs():
        try:
            rp.relative_to(d)
            return True
        except ValueError:
            continue
    return False


def is_approved(path) -> bool:
    """True when this exact file content was approved before (SHA-256 match)."""
    if path is None:
        return True
    p = Path(str(path))
    if not p.exists():
        return True                     # nothing to approve yet: it is being created here
    try:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest in (settings().get("approved", []) or [])


def approve(path) -> bool:
    """Remember this file content as approved."""
    if path is None:
        return True
    p = Path(str(path))
    if not p.exists():
        return True
    try:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return False
    approved = list(settings().get("approved", []) or [])
    if digest not in approved:
        approved.append(digest)
        cfg().set("scripts.approved", approved[-200:], save=True)   # keep the list bounded
    return True


def needs_consent(path) -> bool:
    """The one gate that matters: does running this file need a fresh decision?"""
    return not (is_trusted(path) or is_approved(path))


# --------------------------------------------------------------------------- examples
def examples_dir() -> Path:
    return resource_path("slide_scripts")


def list_examples() -> list[tuple[str, Path]]:
    folder = examples_dir()
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.py")):
        title = p.stem.replace("_", " ").capitalize()
        out.append((title, p))
    return out
