"""File-type registration (ksomisc ``-register`` / ``-unregister`` equivalent) for all 32 supported formats.

Windows: per-user registration under ``HKCU\\Software\\Classes`` (no admin rights needed):
ProgIDs, ``DefaultIcon`` pointing directly to crisp multi-resolution ``.ico`` files (plus multi-icon PE indices `0..7`
embedded inside ``MyOffice.exe``), ``OpenWithProgids``, ``myoffice://`` protocol, application capabilities, and
``RegisteredApplications`` so the suite shows up cleanly in *Settings -> Default apps* and Windows Explorer.

Every file type carries the MyOffice brand mark **and** its own colour, glyph and extension label, so a PDF, a
DOCX, an XLSX and a PPTX can never be confused in Explorer.  The artwork lives in ``resources/icons/app`` and is
mirrored into ``%APPDATA%\\MyOffice\\icons`` (see :func:`sync_icon_mirror`) before registration, so:
  * Explorer always reads the newest icons, even when the program folder is read-only (Program Files);
  * a live OTA hot-patch can ship new artwork without a 116 MB installer download.

Linux: freedesktop ``.desktop`` entries plus ``xdg-mime default``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.services.paths import executable_command, resource_path, user_subdir
from app.version import APP_NAME, BINARY_VERSION

log = logging.getLogger(__name__)
_NO_WIN = 0x08000000 if os.name == "nt" else 0

# Exact 1:1 match with `#define AExt[i]` / `#define AProg[i]` / `#define AIcon[i]` in `installer/setup.iss`:
# ext -> (progid, friendly name, mime type, app kind)
ASSOCIATIONS: dict[str, tuple[str, str, str, str]] = {
    # --- Primary Office, PDF & Hardware Vault formats ---
    ".docx": ("MyOffice.Writer.docx", "MyOffice Writer Document",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "writer"),
    ".docm": ("MyOffice.Writer.docm", "MyOffice Macro-Enabled Document",
              "application/vnd.ms-word.document.macroEnabled.12", "writer"),
    ".dotx": ("MyOffice.Writer.dotx", "MyOffice Writer Template",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.template", "writer"),
    ".xlsx": ("MyOffice.Sheet.xlsx", "MyOffice Spreadsheet",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "sheet"),
    ".xlsm": ("MyOffice.Sheet.xlsm", "MyOffice Macro-Enabled Spreadsheet",
              "application/vnd.ms-excel.sheet.macroEnabled.12", "sheet"),
    ".xltx": ("MyOffice.Sheet.xltx", "MyOffice Spreadsheet Template",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.template", "sheet"),
    ".pptx": ("MyOffice.Slide.pptx", "MyOffice Presentation",
              "application/vnd.openxmlformats-officedocument.presentationml.presentation", "slide"),
    ".ppsx": ("MyOffice.Slide.ppsx", "MyOffice Slide Show",
              "application/vnd.openxmlformats-officedocument.presentationml.slideshow", "slide"),
    ".potx": ("MyOffice.Slide.potx", "MyOffice Presentation Template",
              "application/vnd.openxmlformats-officedocument.presentationml.template", "slide"),
    ".pdf": ("MyOffice.PDF", "PDF Document", "application/pdf", "pdf"),
    ".movault": ("MyOffice.Vault", "MyOffice Hardware-Encrypted Vault", "application/x-myoffice-vault", "vault"),
    # --- MyOffice Photos image formats ---
    ".png": ("MyOffice.Photos.png", "MyOffice PNG Image", "image/png", "photos"),
    ".jpg": ("MyOffice.Photos.jpg", "MyOffice JPEG Image", "image/jpeg", "photos"),
    ".jpeg": ("MyOffice.Photos.jpeg", "MyOffice JPEG Image", "image/jpeg", "photos"),
    ".webp": ("MyOffice.Photos.webp", "MyOffice WebP Image", "image/webp", "photos"),
    ".bmp": ("MyOffice.Photos.bmp", "MyOffice Bitmap Image", "image/bmp", "photos"),
    ".gif": ("MyOffice.Photos.gif", "MyOffice GIF Image", "image/gif", "photos"),
    ".tif": ("MyOffice.Photos.tif", "MyOffice TIFF Image", "image/tiff", "photos"),
    ".tiff": ("MyOffice.Photos.tiff", "MyOffice TIFF Image", "image/tiff", "photos"),
    ".jfif": ("MyOffice.Photos.jfif", "MyOffice JPEG Image", "image/jpeg", "photos"),
    # --- Legacy, OpenDocument & Tabular formats ---
    ".doc": ("MyOffice.Writer.doc", "MyOffice Writer 97-2003 Document", "application/msword", "writer"),
    ".rtf": ("MyOffice.Writer.rtf", "Rich Text Document", "application/rtf", "writer"),
    ".wpt": ("MyOffice.Writer.wpt", "MyOffice Writer Template", "application/x-myoffice-wpt", "writer"),
    ".odt": ("MyOffice.Writer.odt", "OpenDocument Text Document", "application/vnd.oasis.opendocument.text", "writer"),
    ".xls": ("MyOffice.Sheet.xls", "MyOffice 97-2003 Spreadsheet", "application/vnd.ms-excel", "sheet"),
    ".csv": ("MyOffice.Sheet.csv", "CSV File", "text/csv", "sheet"),
    ".tsv": ("MyOffice.Sheet.tsv", "TSV Spreadsheet", "text/tab-separated-values", "sheet"),
    ".ett": ("MyOffice.Sheet.ett", "MyOffice Spreadsheet Template", "application/x-myoffice-ett", "sheet"),
    ".ods": ("MyOffice.Sheet.ods", "OpenDocument Spreadsheet", "application/vnd.oasis.opendocument.spreadsheet", "sheet"),
    ".ppt": ("MyOffice.Slide.ppt", "MyOffice 97-2003 Presentation", "application/vnd.ms-powerpoint", "slide"),
    ".dpt": ("MyOffice.Slide.dpt", "MyOffice Presentation Template", "application/x-myoffice-dpt", "slide"),
    ".odp": ("MyOffice.Slide.odp", "OpenDocument Presentation", "application/vnd.oasis.opendocument.presentation", "slide"),
}

DEFAULT_EXTS = (".docx", ".xlsx", ".pptx", ".pdf", ".movault")
ICON_INDEX = {"myoffice": 0, "writer": 1, "sheet": 2, "slide": 3, "pdf": 4, "photos": 5, "code": 6, "vault": 7}


def _command_line(extra: str = '"%1"') -> str:
    parts = executable_command()
    return " ".join(f'"{p}"' for p in parts) + (" " + extra if extra else "")


def icons_mirror_dir() -> Path:
    """Per-user icon folder (``%APPDATA%\\MyOffice\\icons``) that Explorer's ``DefaultIcon`` points at.

    Program Files is read-only for a normal user and PyInstaller's ``_internal`` folder belongs to the
    installation, so the newest artwork is copied here first.  This also means a signed OTA hot-patch can
    refresh the file-type icons without reinstalling the 116 MB setup.
    """
    return user_subdir("icons")


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def _stamp_slug() -> str:
    """Version string safe for a file name (``1.6.3`` stays ``1.6.3``)."""
    raw = "".join(ch for ch in icon_pack_stamp() if ch.isalnum() or ch in ".-_")
    return raw or BINARY_VERSION


def icon_mirror_name(kind: str) -> str:
    """File name of the mirrored icon.

    The name carries the icon-pack version on purpose: Explorer keys its icon cache on the file path, so
    keeping the name stable across updates would leave users staring at the *previous* artwork until the
    cache expired.  A new name per pack guarantees the new picture is loaded immediately.
    """
    return f"{kind}-{_stamp_slug()}.ico"


def sync_icon_mirror(force: bool = False) -> list[Path]:
    """Copy the bundled ``.ico`` files into the per-user mirror; returns the written files."""
    src_dir = resource_path("icons", "app")
    if not src_dir.is_dir():
        return []
    try:
        dst_dir = icons_mirror_dir()
    except OSError as exc:  # read-only profile - fall back to the bundled files
        log.debug("Icon mirror unavailable: %s", exc)
        return []
    written: list[Path] = []
    current = {icon_mirror_name(kind) for kind in ICON_INDEX}
    for kind in ICON_INDEX:
        src = src_dir / f"{kind}.ico"
        if not src.is_file():
            continue
        dst = dst_dir / icon_mirror_name(kind)
        try:
            if not force and dst.is_file() and dst.stat().st_size == src.stat().st_size \
                    and _file_digest(dst) == _file_digest(src):
                continue
            shutil.copyfile(src, dst)
            written.append(dst)
        except OSError as exc:
            log.debug("Could not mirror icon %s: %s", kind, exc)
    # drop the artwork of earlier versions so the profile folder never grows with every update
    try:
        for stale in dst_dir.glob("*.ico"):
            if stale.name not in current:
                stale.unlink(missing_ok=True)
    except OSError:
        pass
    stamp = dst_dir / "icon-pack.txt"
    if written or force or not stamp.is_file():
        try:
            stamp.write_text(f"{icon_pack_stamp()}\n", encoding="utf-8")
        except OSError:
            pass
    return written


def icon_spec_for(kind: str, exe: str | None = None) -> str:
    """Path Explorer should use: the per-user mirror, else the bundled `.ico`, else the PE icon index."""
    try:
        mirrored = icons_mirror_dir() / icon_mirror_name(kind)
        if mirrored.is_file():
            return str(mirrored)
        legacy = icons_mirror_dir() / f"{kind}.ico"
        if legacy.is_file():
            return str(legacy)
    except OSError:
        pass
    ico = resource_path("icons", "app", f"{kind}.ico")
    if ico.is_file():
        return str(ico.resolve())
    exe_path = exe or executable_command()[0]
    return f'"{exe_path}",{ICON_INDEX.get(kind, 0)}'


# ------------------------------------------------------------------------- Windows
def _win_register(exts) -> list[str]:  # pragma: no cover - Windows only
    import winreg

    done = []
    sync_icon_mirror()
    exe = executable_command()[0]
    classes = r"Software\Classes"
    cap = r"Software\MyOffice\Capabilities"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cap) as k:
        winreg.SetValueEx(k, "ApplicationName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "ApplicationDescription", 0, winreg.REG_SZ,
                          "MyOffice - Writer, Spreadsheets, Presentation, PDF, Photos, Cloud Sync & Collaboration")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications") as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cap)
    for ext in exts:
        progid, friendly, mime, kind = ASSOCIATIONS[ext]
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{progid}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, friendly)
            winreg.SetValueEx(k, "FriendlyTypeName", 0, winreg.REG_SZ, friendly)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{progid}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, icon_spec_for(kind, exe))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{progid}\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _command_line())
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\{ext}\OpenWithProgids") as k:
            winreg.SetValueEx(k, progid, 0, winreg.REG_NONE, b"")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{cap}\FileAssociations") as k:
            winreg.SetValueEx(k, ext, 0, winreg.REG_SZ, progid)
        done.append(ext)

    # Register `myoffice://` protocol handler for 1-click Live Collaboration room links
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\myoffice") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:MyOffice Live Collaboration Protocol")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\myoffice\DefaultIcon") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, icon_spec_for("myoffice", exe))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{classes}\myoffice\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _command_line())

    _win_notify()
    return done


def notify_shell_icons() -> None:
    """Ask Explorer to drop its icon cache (used after new artwork landed under the same file names)."""
    if sys.platform == "win32":
        _win_notify()


def icon_pack_stamp() -> str:
    """Identity of the icon pack currently in effect: binary version plus any active OTA hot-patch."""
    try:
        from app.version import effective_version

        return effective_version()
    except Exception:
        return BINARY_VERSION


def _progid_in_hklm(progid: str) -> bool:  # pragma: no cover - Windows only
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"Software\Classes\{progid}") as k:
            winreg.QueryValueEx(k, "")
        return True
    except OSError:
        return False


def repair_stale_icon_registrations(force: bool = False) -> int:
    """Re-point every ``MyOffice.*`` ProgID at the current artwork and refresh the Explorer cache.

    Runs at startup (cheap: it only writes when a value really differs) and again after an update, so a
    user upgrading from any earlier version immediately sees the new, clearly distinguishable file icons.
    """
    if sys.platform != "win32":
        return 0
    repaired = 0
    try:  # pragma: no cover - Windows only
        import winreg

        sync_icon_mirror(force=force)
        exe = executable_command()[0]
        seen: set[str] = set()
        for progid, _friendly, _mime, kind in ASSOCIATIONS.values():
            if progid in seen:
                continue
            seen.add(progid)
            subkey = rf"Software\Classes\{progid}\DefaultIcon"
            desired = icon_spec_for(kind, exe)
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as k:
                    cur, _ = winreg.QueryValueEx(k, "")
                exists = True
            except FileNotFoundError:
                cur, exists = "", False
            except OSError:
                continue
            if not exists:
                # A per-machine install (MyOffice Setup runs elevated) registers the ProgIDs under
                # HKLM, which a normal user cannot rewrite.  A per-user DefaultIcon override with the
                # same ProgID wins over HKLM and needs no admin rights - exactly what we do here.
                if not _progid_in_hklm(progid):
                    continue  # not registered at all (user unticked file associations in Setup)
            elif not force and cur == desired:
                continue
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, desired)
                repaired += 1
            except OSError:
                continue
        # The shell keeps its own icon cache: rebuild it + broadcast the association change so the new
        # artwork appears in open Explorer windows without a log-off, reboot or manual cache purge.
        if repaired:
            _win_notify()
    except Exception as exc:
        log.debug("Stale icon check skipped: %s", exc)
    return repaired


def refresh_file_type_icons() -> tuple[int, bool]:
    """Rewrite every ``DefaultIcon`` with the newest artwork and flush the Explorer icon cache.

    Returns ``(progids_updated, registered)``.  Used by *Tools -> Refresh File Type Icons* and right after
    an update has been applied, so a user never has to reinstall just to get new file icons.
    """
    if sys.platform != "win32":
        return 0, False
    registered = any(_win_is_registered(ext) for ext in DEFAULT_EXTS)
    updated = repair_stale_icon_registrations(force=True) if registered else 0
    return updated, registered


def _win_unregister(exts) -> list[str]:  # pragma: no cover - Windows only
    import winreg

    def delete_tree(root, path):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        sub = winreg.EnumKey(k, 0)
                    except OSError:
                        break
                    delete_tree(root, rf"{path}\{sub}")
            winreg.DeleteKey(root, path)
        except FileNotFoundError:
            pass

    done = []
    for ext in exts:
        progid = ASSOCIATIONS[ext][0]
        delete_tree(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{progid}")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids", 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, progid)
        except FileNotFoundError:
            pass
        done.append(ext)
    if set(exts) >= set(ASSOCIATIONS):
        delete_tree(winreg.HKEY_CURRENT_USER, r"Software\Classes\myoffice")
        delete_tree(winreg.HKEY_CURRENT_USER, r"Software\MyOffice\Capabilities")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_NAME)
        except FileNotFoundError:
            pass
    _win_notify()
    return done


def _win_notify():  # pragma: no cover - Windows only
    import ctypes

    SHCNE_ASSOCCHANGED = 0x08000000
    SHCNF_IDLIST, SHCNF_FLUSH, SHCNF_FLUSHNOWAIT = 0x0000, 0x1000, 0x2000
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSHNOWAIT,
                                            None, None)
    except Exception:
        pass
    for exe, args in (
        ("ie4uinit.exe", ["-show"]),      # rebuild the shell icon cache
        ("ie4uinit.exe", ["-ClearIconCache"]),
    ):
        path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", exe)
        if not os.path.isfile(path):
            continue
        try:
            subprocess.Popen([path, *args], creationflags=_NO_WIN, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass


def _win_is_registered(ext: str) -> bool:  # pragma: no cover - Windows only
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"Software\Classes\{ext}\OpenWithProgids") as k:
                winreg.QueryValueEx(k, ASSOCIATIONS[ext][0])
                return True
        except OSError:
            continue
    return False


# ------------------------------------------------------------------------- Linux / freedesktop
def _xdg_apps_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    d = base / "applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


def desktop_entry(kind: str, mimes: list[str]) -> str:
    names = {
        "writer": "MyOffice Writer",
        "sheet": "MyOffice Spreadsheets",
        "slide": "MyOffice Presentation",
        "pdf": "MyOffice PDF",
        "photos": "MyOffice Photos",
        "code": "MyOffice Code & Text",
        "vault": "MyOffice Vault",
        "main": APP_NAME,
    }
    icon = resource_path("icons", "app", f"{kind if kind != 'main' else 'myoffice'}.png")
    if not icon.exists():
        icon = resource_path("icons", "app", "myoffice.png")
    exec_line = " ".join(f'"{p}"' for p in executable_command())
    if kind != "main":
        exec_line += f" --new {kind}" if not mimes else ""
    return "\n".join([
        "[Desktop Entry]", "Type=Application", f"Name={names.get(kind, APP_NAME)}", f"Exec={exec_line} %F",
        f"Icon={icon}", "Terminal=false", "Categories=Office;", f"MimeType={';'.join(mimes)};" if mimes else "",
        "StartupWMClass=MyOffice", ""])


def _linux_register(exts) -> list[str]:
    by_kind: dict[str, list[str]] = {}
    for ext in exts:
        _progid, _f, mime, kind = ASSOCIATIONS[ext]
        if mime not in by_kind.setdefault(kind, []):
            by_kind[kind].append(mime)
    apps = _xdg_apps_dir()
    for kind, mimes in by_kind.items():
        (apps / f"myoffice-{kind}.desktop").write_text(desktop_entry(kind, mimes), encoding="utf-8")
    if shutil.which("xdg-mime"):
        for kind, mimes in by_kind.items():
            for m in mimes:
                subprocess.run(["xdg-mime", "default", f"myoffice-{kind}.desktop", m], check=False,
                               capture_output=True, timeout=20)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps)], check=False, capture_output=True, timeout=30)
    return list(exts)


def _linux_unregister(exts) -> list[str]:
    kinds = {ASSOCIATIONS[e][3] for e in exts}
    for kind in kinds:
        (_xdg_apps_dir() / f"myoffice-{kind}.desktop").unlink(missing_ok=True)
    return list(exts)


def _linux_is_registered(ext: str) -> bool:
    return (_xdg_apps_dir() / f"myoffice-{ASSOCIATIONS[ext][3]}.desktop").exists()


# ------------------------------------------------------------------------- public API
def register_associations(exts=None) -> list[str]:
    exts = [e for e in (exts or ASSOCIATIONS) if e in ASSOCIATIONS]
    if sys.platform == "win32":
        return _win_register(exts)
    if sys.platform.startswith("linux"):
        return _linux_register(exts)
    raise OSError("File association registration is supported on Windows and Linux.")


def unregister_associations(exts=None) -> list[str]:
    exts = [e for e in (exts or ASSOCIATIONS) if e in ASSOCIATIONS]
    if sys.platform == "win32":
        return _win_unregister(exts)
    if sys.platform.startswith("linux"):
        return _linux_unregister(exts)
    raise OSError("File association registration is supported on Windows and Linux.")


def is_registered(ext: str) -> bool:
    if ext not in ASSOCIATIONS:
        return False
    if sys.platform == "win32":
        return _win_is_registered(ext)
    if sys.platform.startswith("linux"):
        return _linux_is_registered(ext)
    return False


def open_default_apps_settings() -> None:
    if sys.platform == "win32":  # pragma: no cover
        os.startfile("ms-settings:defaultapps")  # noqa: S606
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", str(_xdg_apps_dir())])
