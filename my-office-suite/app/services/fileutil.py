"""File helpers: atomic writes with sensible permissions."""
from __future__ import annotations

import os
import stat
import tempfile

_UMASK: int | None = None


def default_mode() -> int:
    """Permissions a newly created file would get (0o666 minus the process umask)."""
    global _UMASK
    if _UMASK is None:
        cur = os.umask(0)
        os.umask(cur)
        _UMASK = cur
    return 0o666 & ~_UMASK


def atomic_temp(target: str, suffix: str = ".tmp") -> str:
    """Create a temp file next to ``target`` (same filesystem) and return its path."""
    d = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=suffix)
    os.close(fd)
    return tmp


def finish_atomic(tmp: str, target: str) -> str:
    """Give ``tmp`` the permissions of the file it replaces (or default ones) and move it into place."""
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode) if os.path.exists(target) else default_mode()
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, target)
    return target
