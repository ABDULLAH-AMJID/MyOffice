"""Application identity and version information."""
from __future__ import annotations

APP_NAME = "MyOffice"
BINARY_VERSION = "1.6.3"
APP_ORG = "MyOffice Project"
APP_DOMAIN = "myoffice.local"
APP_ID = "MyOffice.Suite"  # Windows AppUserModelID; the installer's shortcuts carry the same ID
APP_DESCRIPTION = "Lightweight, open-source office suite (Writer, Spreadsheets, Presentation, PDF)"
# Named mutex held while MyOffice runs, so Setup / Uninstall can wait for the program to close.
APP_MUTEX = "MyOffice.Suite.Running"
# Inno Setup AppId of MyOffice Setup (installer/setup.iss); the uninstaller is registered under
# HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\<INNO_APP_ID>_is1
INNO_APP_ID = "{6F1C2E7A-3B8D-4E59-9A41-2D7C5B9E8F10}"


def version_tuple(v: str):
    parts = []
    for p in str(v or "0.0.0").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def effective_version() -> str:
    try:
        from app.services.ota_runtime import active_patch_version

        patched = active_patch_version()
        if patched and version_tuple(patched) > version_tuple(BINARY_VERSION):
            return patched
    except Exception:
        pass
    return BINARY_VERSION


APP_VERSION = effective_version()
