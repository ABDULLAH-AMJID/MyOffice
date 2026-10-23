"""Dual-Tier Signed Self-Update Engine (`updater.py`):
  - Accepts GitHub Repo URLs (`owner/repo`, `https://github.com/owner/repo`) OR direct `manifest.json` URLs
  - Verifies `ECDSA-P256-SHA256` manifest signature (`myoffice-ota-v2`) + `SHA-256` file digest
  - Tier 1 (`hotpatch`): Applies `<300 KB` signed `.py` delta bundle in `<1 second` without recompiling/re-downloading `112 MB`
  - Tier 2 (`installer`): Falls back seamlessly to full `MyOffice_Setup.exe` when native binaries change
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.services.config import cfg
from app.services.ota_runtime import (
    RELEASE_PUBLIC_KEY_B64,
    ota_base_dir,
    reset_to_factory,
    write_ota_state,
)
from app.services.paths import user_subdir
from app.version import APP_VERSION, BINARY_VERSION, effective_version, version_tuple

log = logging.getLogger(__name__)


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    current: str
    latest: str = ""
    url: str = ""
    sha256: str = ""
    size: int = 0
    notes: str = ""
    date: str = ""
    mandatory: bool = False
    min_version: str = ""
    install_mode: str = "installer"  # "hotpatch" | "installer"
    signed: bool = False

    @property
    def available(self) -> bool:
        return bool(self.latest) and version_tuple(self.latest) > version_tuple(self.current)

    def message(self) -> str:
        if self.available:
            mode_label = (
                "Instant Delta Patch (<1s, no reinstall)"
                if self.install_mode == "hotpatch"
                else "Full Native Installer Upgrade"
            )
            return f"MyOffice {self.latest} is available (installed: {self.current}) [{mode_label}].\n\n{self.notes}".strip()
        return f"MyOffice {self.current} is up to date."


def update_url() -> str:
    return cfg().get("update.url", "") or cfg().get("servers.update", "")


def resolve_manifest_url(raw: str) -> str:
    """Normalize `owner/repo` or `https://github.com/owner/repo` into raw GitHub manifest URL."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("file://") or os.path.exists(s):
        p = Path(s[7:] if s.startswith("file://") else s)
        if p.is_dir():
            cand = p / "ota" / "manifest.json"
            return str(cand if cand.exists() else (p / "manifest.json"))
        return s
    if s.endswith(".json"):
        return s
    cleaned = s.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if "github.com/" in cleaned:
        after = cleaned.split("github.com/", 1)[1]
        if "/tree/" in after:
            slug, branch = after.split("/tree/", 1)
            return f"https://raw.githubusercontent.com/{slug.strip('/')}/{branch.strip('/')}/ota/manifest.json"
        return f"https://raw.githubusercontent.com/{after.strip('/')}/main/ota/manifest.json"
    if "://" not in cleaned and cleaned.count("/") == 1:
        return f"https://raw.githubusercontent.com/{cleaned}/main/ota/manifest.json"
    return s


def _resolve_asset_url(asset_url: str, base_manifest_url: str) -> str:
    if not asset_url:
        return ""
    if "://" in asset_url or os.path.isabs(asset_url):
        return asset_url
    if base_manifest_url.startswith("file://"):
        base_dir = Path(base_manifest_url[7:]).parent
        return str((base_dir / asset_url).resolve())
    if os.path.exists(base_manifest_url):
        base_dir = Path(base_manifest_url).parent
        return str((base_dir / asset_url).resolve())
    return urllib.parse.urljoin(base_manifest_url, asset_url)


def verify_manifest_ecdsa(data: dict, pub_key_b64: str | None = None) -> bool:
    sig_b64 = data.get("signature", "")
    if not sig_b64:
        return False
    effective_pub = pub_key_b64 or RELEASE_PUBLIC_KEY_B64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    clean = {k: v for k, v in data.items() if k != "signature"}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    pub_key = serialization.load_der_public_key(base64.b64decode(effective_pub.strip()))
    assert isinstance(pub_key, ec.EllipticCurvePublicKey)
    try:
        pub_key.verify(base64.b64decode(sig_b64), canonical, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def parse_version_json(
    data: dict,
    current: str | None = None,
    manifest_url: str = "",
    require_signature_if_v2: bool = True,
) -> UpdateInfo:
    if not isinstance(data, dict) or not data.get("version"):
        raise UpdateError("Invalid manifest: missing 'version'")

    cur_ver = current or effective_version()
    is_v2 = data.get("schema") == "myoffice-ota-v2" or "signature" in data
    signed_ok = False
    if is_v2:
        signed_ok = verify_manifest_ecdsa(data)
        if require_signature_if_v2 and not signed_ok:
            raise UpdateError("Rejected tampered update manifest: ECDSA cryptographic signature invalid.")

    win = data.get("windows") if isinstance(data.get("windows"), dict) else None
    if win:
        min_bin = str(win.get("min_binary_version") or data.get("min_version") or "1.0.0")
        can_hotpatch = bool(win.get("patch_url")) and version_tuple(BINARY_VERSION) >= version_tuple(min_bin)
        if can_hotpatch:
            chosen_url = _resolve_asset_url(str(win.get("patch_url", "")), manifest_url)
            chosen_sha = str(win.get("patch_sha256") or "").lower()
            chosen_size = int(win.get("patch_size") or 0)
            mode = "hotpatch"
        else:
            chosen_url = _resolve_asset_url(str(win.get("installer_url") or data.get("url") or ""), manifest_url)
            chosen_sha = str(win.get("installer_sha256") or data.get("sha256") or "").lower()
            chosen_size = int(win.get("installer_size") or data.get("size") or 0)
            mode = "installer"
    else:
        chosen_url = _resolve_asset_url(str(data.get("url") or ""), manifest_url)
        chosen_sha = str(data.get("sha256") or "").lower()
        chosen_size = int(data.get("size") or 0)
        mode = "hotpatch" if chosen_url.lower().endswith(".zip") else "installer"

    return UpdateInfo(
        current=cur_ver,
        latest=str(data["version"]),
        url=chosen_url,
        sha256=chosen_sha,
        size=chosen_size,
        notes=str(data.get("notes", "")),
        date=str(data.get("date", "")),
        mandatory=bool(data.get("mandatory", False)),
        min_version=str(data.get("min_version", "")),
        install_mode=mode,
        signed=signed_ok,
    )


def _auth_headers() -> dict[str, str]:
    headers = {
        "User-Agent": f"MyOffice/{APP_VERSION}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    tok = (cfg().get("update.github_token", "") or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def check_for_update(url: str | None = None, force: bool = False, timeout: int = 15) -> UpdateInfo:
    import requests

    raw_input = url or update_url()
    if not raw_input:
        raise UpdateError("No GitHub repository or update URL configured yet.")
    resolved = resolve_manifest_url(raw_input)

    last = float(cfg().get("update.last_check", 0) or 0)
    if not force and time.time() - last < 1800 and cfg().get("update.cached"):
        return parse_version_json(cfg().get("update.cached"), manifest_url=resolved)

    if resolved.startswith("file://") or os.path.exists(resolved):
        path = resolved[7:] if resolved.startswith("file://") else resolved
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        sep = "&" if "?" in resolved else "?"
        fetch_url = f"{resolved}{sep}ts={int(time.time())}"
        try:
            r = requests.get(fetch_url, timeout=timeout, headers=_auth_headers())
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as exc:
            raise UpdateError(f"Could not reach update source: {exc}") from exc
        except ValueError as exc:
            raise UpdateError("Update source returned invalid JSON manifest") from exc

    info = parse_version_json(data, manifest_url=resolved)
    cfg().set("update.last_check", time.time())
    cfg().set("update.cached", data, save=True)
    return info


def download_update(info: UpdateInfo, dest_dir: str | None = None, progress=None, is_cancelled=None) -> str:
    import requests

    if not info.url:
        raise UpdateError("The update manifest has no download URL")
    dest_dir = dest_dir or str(user_subdir("updates"))
    os.makedirs(dest_dir, exist_ok=True)
    default_ext = ".zip" if info.install_mode == "hotpatch" else ".exe"
    name = os.path.basename(info.url.split("?")[0]) or f"MyOffice_Update_{info.latest}{default_ext}"
    path = os.path.join(dest_dir, name)
    tmp = path + ".partial"
    h = hashlib.sha256()

    if info.url.startswith("file://") or os.path.exists(info.url):
        src = info.url[7:] if info.url.startswith("file://") else info.url
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while True:
                b = fin.read(1 << 20)
                if not b:
                    break
                fout.write(b)
                h.update(b)
    else:
        with requests.get(info.url, stream=True, timeout=30, headers=_auth_headers()) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or info.size or 0)
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    if is_cancelled and is_cancelled():
                        raise UpdateError("Download cancelled")
                    fh.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(int(100 * done / total), f"{done // 1024:,} / {total // 1024:,} KB")

    if info.sha256 and h.hexdigest().lower() != info.sha256.lower():
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise UpdateError("The downloaded file failed SHA-256 integrity verification and was deleted.")
    os.replace(tmp, path)
    return path


def apply_hotpatch_zip(zip_path: str | Path, version: str, base_dir: Path | None = None) -> Path:
    """Safely extract verified delta `.zip` into `ota/current` with atomic rollback backup."""
    target_base = base_dir or ota_base_dir()
    staging = target_base / "staging"
    current = target_base / "current"
    previous = target_base / "previous"

    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    staging_resolved = staging.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            rel = member.filename.replace("\\", "/")
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                shutil.rmtree(staging, ignore_errors=True)
                raise UpdateError(f"Blocked unsafe archive path in OTA bundle: {member.filename!r}")
            dest = (staging / rel).resolve()
            if dest != staging_resolved and not dest.is_relative_to(staging_resolved):
                shutil.rmtree(staging, ignore_errors=True)
                raise UpdateError(f"Archive path escapes staging root: {member.filename!r}")
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))

    meta = {"version": version, "applied_at": int(time.time())}
    (staging / "ota_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)
    if current.exists():
        shutil.move(str(current), str(previous))
    shutil.move(str(staging), str(current))

    write_ota_state(
        {
            "active_version": version,
            "boot_in_progress": False,
            "boot_attempts": 0,
            "healthy": True,
            "applied_at": int(time.time()),
        },
        base=target_base,
    )
    return current


def install_update(path: str, info: UpdateInfo | None = None, restart: bool = True) -> str:
    """Apply live OTA hotpatch (`<1s`) OR launch native installer seamlessly."""
    is_zip = path.lower().endswith(".zip") or (info is not None and info.install_mode == "hotpatch")
    if is_zip:
        ver = info.latest if info and info.latest else effective_version()
        apply_hotpatch_zip(path, version=ver)
        if restart:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable], close_fds=True)
            else:
                subprocess.Popen([sys.executable, "-m", "app.main"], close_fds=True)
        return "hotpatch"

    if sys.platform == "win32":  # pragma: no cover - Windows only
        subprocess.Popen(
            [path, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
            close_fds=True,
            creationflags=0x00000008,
        )
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, os.path.dirname(path)])
    return "installer"


def rollback_updates(base_dir: Path | None = None) -> None:
    reset_to_factory(base_dir)
