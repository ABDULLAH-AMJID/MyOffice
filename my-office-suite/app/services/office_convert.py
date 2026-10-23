"""Format conversion through LibreOffice in headless mode.

LibreOffice is an *optional external program* (MPL-2.0) that MyOffice drives through its
command line (``soffice --headless --convert-to``).  It is only needed for the legacy and
OpenDocument formats (.doc/.xls/.ppt/.rtf/.odt/.wps...): Word, Excel and PowerPoint files are
handled by MyOffice's own engines (see builtin_pdf.py).

When LibreOffice is missing, ``convert`` asks the handler registered with
``set_missing_handler`` (the GUI offers to download and install the Legacy Formats Pack, see
legacy_pack.py) and continues if it was installed; otherwise it raises ``LibreOfficeMissing``.
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from app.services.config import cfg
from app.services.paths import user_subdir

log = logging.getLogger(__name__)
_lock = threading.Lock()


class ConversionError(RuntimeError):
    pass


class LibreOfficeMissing(ConversionError):
    """The file format needs LibreOffice (the Legacy Formats Pack), which is not installed."""


_missing_handler = None


def set_missing_handler(handler) -> None:
    """``handler(src_path) -> bool`` is called (from any thread) when a conversion needs LibreOffice
    and it is not installed.  It returns True once LibreOffice is available."""
    global _missing_handler
    _missing_handler = handler


def missing_message() -> str:
    from app.services.i18n import _t

    return _t("This file format needs the Legacy Formats Pack (the free LibreOffice engine), which is not "
              "installed. Install it from MyOffice menu \u2192 Tools \u2192 Legacy Formats Pack, or set the path "
              "of soffice.exe in Settings \u2192 Servers.")


def _candidates() -> list[str]:
    out = []
    env = os.environ.get("MYOFFICE_SOFFICE")
    if env:
        out.append(env)
    conf = cfg().get("servers.soffice", "")
    if conf:
        out.append(conf)
    for name in ("soffice", "libreoffice", "soffice.exe"):
        p = shutil.which(name)
        if p:
            out.append(p)
    if sys.platform.startswith("win"):
        out.extend(_registered_soffice())
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            out.extend(glob.glob(os.path.join(base, "LibreOffice*", "program", "soffice.exe")))
    elif sys.platform == "darwin":
        out.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    else:
        out.extend(["/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice", "/opt/libreoffice/program/soffice"])
    return out


def _registered_soffice() -> list[str]:  # pragma: no cover - Windows only
    """soffice.exe of the LibreOffice installation registered in the 64- and 32-bit registry views."""
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\LibreOffice\UNO\InstallPath", 0,
                                winreg.KEY_READ | view) as key:
                found.append(os.path.join(winreg.QueryValueEx(key, "")[0], "soffice.exe"))
        except OSError:
            pass
    return found


def find_soffice() -> str | None:
    for c in _candidates():
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def available() -> bool:
    return find_soffice() is not None


def convert(src: str, target: str, outdir: str | None = None, timeout: int = 240) -> str:
    """Convert ``src`` to ``target`` (an extension like ``"pdf"`` or ``"docx"``, optionally with a
    LibreOffice filter such as ``"docx:MS Word 2007 XML"``).  Returns the output path."""
    if not os.path.isfile(src):
        raise ConversionError(f"File not found: {src}")
    soffice = find_soffice()
    if not soffice and _missing_handler is not None:
        try:
            if _missing_handler(src):
                soffice = find_soffice()
        except Exception as exc:  # the offer itself must never break the conversion error path
            log.warning("Legacy Formats Pack offer failed: %s", exc)
    if not soffice:
        raise LibreOfficeMissing(missing_message())
    outdir = outdir or tempfile.mkdtemp(prefix="myoffice_conv_")
    os.makedirs(outdir, exist_ok=True)
    profile = Path(user_subdir("lo_profile")).resolve().as_uri()
    cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--nolockcheck",
           "--nodefault", "--nofirststartwizard", "--convert-to", target, "--outdir", outdir, src]
    ext = target.split(":", 1)[0]
    expected = os.path.join(outdir, Path(src).stem + "." + ext)
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    with _lock:
        if os.path.exists(expected):
            os.unlink(expected)
        log.info("LibreOffice convert: %s -> %s", src, target)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"LibreOffice timed out after {timeout}s") from exc
        except OSError as exc:
            raise ConversionError(f"Could not start LibreOffice: {exc}") from exc
    if not os.path.isfile(expected):
        err = (proc.stderr or b"").decode("utf-8", "replace").strip() or (proc.stdout or b"").decode("utf-8", "replace")
        raise ConversionError(f"LibreOffice could not convert the file.\n{err[-800:]}")
    return expected
