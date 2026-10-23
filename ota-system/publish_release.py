#!/usr/bin/env python3
"""MyOffice Dual-Tier OTA & GitHub Release Publisher (`publish_release.py`).

Generates cryptographically signed (`ECDSA-P256-SHA256`) OTA delta bundles for:
  1. Android Instant Web/UI Bundle (`ota/android-ui.zip`, ~35 KB — applies live in <1s without APK reinstall)
  2. Windows Instant App/Resource Patch (`ota/windows-patch.zip`, ~180 KB — applies in <1s without 112 MB EXE download)
  3. Native Binary Fallback Metadata (`MyOffice_Android.apk` & `MyOffice_Setup.exe` on GitHub Releases when native code changes)

Usage:
  # Build & sign OTA bundles + manifest locally:
  python3 ota-system/publish_release.py --version 1.6.1 --notes "Added PDF ink highlighter"

  # Build, sign, commit, AND push directly to user's GitHub repo using a Fine-Grained PAT:
  python3 ota-system/publish_release.py \
      --version 1.6.1 \
      --notes "Added PDF ink highlighter" \
      --repo https://github.com/OWNER/REPO \
      --token "$GITHUB_PAT" \
      --push
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import subprocess
import urllib.parse
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SECRETS_DIR = WORKSPACE_ROOT / ".ota-secrets"
PRIVATE_KEY_PATH = SECRETS_DIR / "ota_signing_private.pem"
PUBLIC_KEY_B64_PATH = WORKSPACE_ROOT / "ota-system" / "ota_public_key.b64"
OTA_OUT_DIR = WORKSPACE_ROOT / "ota"

ANDROID_UI_DIR = WORKSPACE_ROOT / "my-office-android" / "app" / "src" / "main" / "assets" / "ui"
WINDOWS_SUITE_DIR = WORKSPACE_ROOT / "my-office-suite"


def ensure_signing_keypair(
    priv_path: Path | None = None,
    pub_b64_path: Path | None = None,
) -> tuple[ec.EllipticCurvePrivateKey, str]:
    """Load existing ECDSA P-256 release signing key or create it once."""
    p_priv = priv_path or PRIVATE_KEY_PATH
    p_pub = pub_b64_path or PUBLIC_KEY_B64_PATH
    p_priv.parent.mkdir(parents=True, exist_ok=True)
    p_pub.parent.mkdir(parents=True, exist_ok=True)
    env_pem = os.environ.get("OTA_SIGNING_PRIVATE_KEY_PEM", "").strip()
    if env_pem and priv_path is None:
        priv = serialization.load_pem_private_key(env_pem.encode("utf-8"), password=None)
    elif p_priv.exists():
        priv = serialization.load_pem_private_key(p_priv.read_bytes(), password=None)
    else:
        priv = ec.generate_private_key(ec.SECP256R1())
        pem_bytes = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        p_priv.write_bytes(pem_bytes)
        try:
            os.chmod(p_priv, 0o600)
        except OSError:
            pass

    spki_der = priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_b64 = base64.b64encode(spki_der).decode("ascii")
    p_pub.write_text(pub_b64 + "\n", encoding="utf-8")
    return priv, pub_b64


def canonical_payload_bytes(manifest_dict: dict) -> bytes:
    """Deterministic UTF-8 JSON bytes over all fields except `signature`."""
    clean = {k: v for k, v in manifest_dict.items() if k != "signature"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_manifest(manifest_dict: dict, priv_key: ec.EllipticCurvePrivateKey) -> str:
    der_sig = priv_key.sign(canonical_payload_bytes(manifest_dict), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(der_sig).decode("ascii")


def verify_manifest_signature(manifest_dict: dict, pub_key_b64: str) -> bool:
    sig_b64 = manifest_dict.get("signature", "")
    if not sig_b64:
        return False
    pub_der = base64.b64decode(pub_key_b64.strip())
    pub_key = serialization.load_der_public_key(pub_der)
    assert isinstance(pub_key, ec.EllipticCurvePublicKey)
    try:
        pub_key.verify(
            base64.b64decode(sig_b64),
            canonical_payload_bytes(manifest_dict),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except Exception:
        return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_deterministic_zip(entries: list[tuple[Path, str]], out_zip: Path) -> tuple[str, int]:
    """Create reproducible level-9 ZIP sorted by archive path."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src_path, arc_name in sorted(entries, key=lambda item: item[1]):
            zi = zipfile.ZipInfo(filename=arc_name, date_time=(2026, 10, 23, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, src_path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return sha256_file(out_zip), out_zip.stat().st_size


def package_android_ui_bundle(out_zip: Path) -> tuple[str, int, int]:
    entries: list[tuple[Path, str]] = []
    for p in ANDROID_UI_DIR.rglob("*"):
        if p.is_file():
            rel = p.relative_to(ANDROID_UI_DIR).as_posix()
            entries.append((p, rel))
    digest, size = build_deterministic_zip(entries, out_zip)
    return digest, size, len(entries)


def package_windows_hotpatch_bundle(out_zip: Path) -> tuple[str, int, int]:
    entries: list[tuple[Path, str]] = []
    app_dir = WINDOWS_SUITE_DIR / "app"
    for p in app_dir.rglob("*.py"):
        if "__pycache__" not in p.parts:
            rel = p.relative_to(WINDOWS_SUITE_DIR).as_posix()
            entries.append((p, rel))
    # Base EXE already ships static icons/fonts/qm; pack dynamic theme QSS + JSON resources
    res_dir = WINDOWS_SUITE_DIR / "resources"
    for p in res_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".qss", ".json"}:
            rel = p.relative_to(WINDOWS_SUITE_DIR).as_posix()
            entries.append((p, rel))
    digest, size = build_deterministic_zip(entries, out_zip)
    return digest, size, len(entries)


def normalize_github_slug(repo_url: str) -> str:
    raw = repo_url.strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith(".git"):
        raw = raw[:-4]
    if "github.com/" in raw:
        return raw.split("github.com/", 1)[1].split("/tree/")[0].strip("/")
    return raw.strip("/")


def build_and_sign_ota(
    version: str,
    version_code: int,
    notes: str,
    repo_url: str = "",
    branch: str = "main",
    min_android_native_code: int = 10600,
    min_windows_binary_version: str = "1.0.0",
    base_override_url: str = "",
) -> dict:
    priv_key, pub_b64 = ensure_signing_keypair()
    OTA_OUT_DIR.mkdir(parents=True, exist_ok=True)

    android_zip = OTA_OUT_DIR / "android-ui.zip"
    windows_zip = OTA_OUT_DIR / "windows-patch.zip"

    and_sha, and_size, and_count = package_android_ui_bundle(android_zip)
    win_sha, win_size, win_count = package_windows_hotpatch_bundle(windows_zip)

    slug = normalize_github_slug(repo_url)
    if base_override_url:
        raw_base = base_override_url.rstrip("/")
        rel_base = base_override_url.rstrip("/")
    elif slug:
        raw_base = f"https://raw.githubusercontent.com/{slug}/{branch}/ota"
        rel_base = f"https://github.com/{slug}/releases/latest/download"
    else:
        # Relative URLs resolve cleanly against whatever repo/server URL the client fetched manifest.json from!
        raw_base = "."
        rel_base = "../releases"

    prev_manifest: dict = {}
    prev_path = OTA_OUT_DIR / "manifest.json"
    if prev_path.exists():
        try:
            prev_manifest = json.loads(prev_path.read_text(encoding="utf-8"))
        except Exception:
            prev_manifest = {}

    apk_local = WORKSPACE_ROOT / "MyOffice_Android.apk"
    exe_local = WORKSPACE_ROOT / "MyOffice_Setup.exe"
    prev_win = prev_manifest.get("windows") if isinstance(prev_manifest.get("windows"), dict) else {}
    prev_and = prev_manifest.get("android") if isinstance(prev_manifest.get("android"), dict) else {}

    exe_sha = sha256_file(exe_local) if exe_local.exists() else str(prev_win.get("installer_sha256", ""))
    exe_size = exe_local.stat().st_size if exe_local.exists() else int(prev_win.get("installer_size", 0))
    apk_sha = sha256_file(apk_local) if apk_local.exists() else str(prev_and.get("apk_sha256", ""))
    apk_size = apk_local.stat().st_size if apk_local.exists() else int(prev_and.get("apk_size", 0))

    manifest = {
        "schema": "myoffice-ota-v2",
        "version": version,
        "version_code": version_code,
        "date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": notes,
        "mandatory": False,
        "min_version": min_windows_binary_version,
        # Legacy top-level compatibility fields allow even pre-1.6 Windows installs to upgrade cleanly to 1.6+!
        "url": f"{rel_base}/MyOffice_Setup.exe",
        "sha256": exe_sha,
        "size": exe_size,
        # Tier-1 + Tier-2 Android descriptor
        "android": {
            "version": version,
            "version_code": version_code,
            "min_native_code": min_android_native_code,
            "bundle_url": f"{raw_base}/android-ui.zip",
            "bundle_sha256": and_sha,
            "bundle_size": and_size,
            "bundle_files": and_count,
            "apk_url": f"{rel_base}/MyOffice_Android.apk",
            "apk_sha256": apk_sha,
            "apk_size": apk_size,
        },
        # Tier-1 + Tier-2 Windows descriptor
        "windows": {
            "version": version,
            "min_binary_version": min_windows_binary_version,
            "patch_url": f"{raw_base}/windows-patch.zip",
            "patch_sha256": win_sha,
            "patch_size": win_size,
            "patch_files": win_count,
            "installer_url": f"{rel_base}/MyOffice_Setup.exe",
            "installer_sha256": exe_sha,
            "installer_size": exe_size,
        },
    }
    manifest["signature"] = sign_manifest(manifest, priv_key)
    assert verify_manifest_signature(manifest, pub_b64), "Self-verification of ECDSA signature failed!"

    manifest_path = OTA_OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


DEFAULT_REPO_URL = "https://github.com/ABDULLAH-AMJID/MyOffice"
PAT_SECRET_PATH = SECRETS_DIR / "github_pat.txt"


def push_to_github(
    repo_url: str,
    token: str,
    version: str,
    notes: str,
    branch: str = "main",
    tag: bool = False,
) -> None:
    effective_repo = (repo_url or "").strip() or DEFAULT_REPO_URL
    slug = normalize_github_slug(effective_repo)
    if not slug:
        raise SystemExit("--repo must be provided (e.g. 'owner/repo' or 'https://github.com/owner/repo')")
    effective_token = (token or "").strip()
    if not effective_token and PAT_SECRET_PATH.exists():
        effective_token = PAT_SECRET_PATH.read_text(encoding="utf-8").strip()
    if not effective_token:
        raise SystemExit("--push requires GITHUB_TOKEN, --token, or /home/user/.ota-secrets/github_pat.txt")

    clean_remote = f"https://github.com/{slug}.git"
    basic = base64.b64encode(f"x-access-token:{effective_token}".encode("utf-8")).decode("ascii")
    git_dir = Path("/var/tmp/myoffice-git")

    def run_git(*args: str, use_auth: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", f"--git-dir={git_dir}", f"--work-tree={WORKSPACE_ROOT}"]
        if use_auth:
            cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
        cmd += list(args)
        return subprocess.run(cmd, cwd=WORKSPACE_ROOT, check=check, capture_output=True, text=True)

    first_init = not git_dir.exists()
    if first_init:
        git_dir.mkdir(parents=True, exist_ok=True)
        run_git("init", "-b", branch)
    run_git("config", "user.name", "MyOffice Release Bot")
    run_git("config", "user.email", "release@myoffice.local")

    existing = run_git("remote").stdout.split()
    if "origin" in existing:
        run_git("remote", "set-url", "origin", clean_remote)
    else:
        run_git("remote", "add", "origin", clean_remote)

    if first_init:
        fetched = run_git("fetch", "--depth=1", "origin", branch, use_auth=True, check=False)
        if fetched.returncode == 0:
            run_git("reset", "--mixed", "FETCH_HEAD")

    run_git("add", "-A")
    status = run_git("status", "--porcelain").stdout
    if status.strip():
        run_git("commit", "-m", f"release(ota): v{version} — {notes}")
    run_git("push", "-u", "origin", f"HEAD:{branch}", use_auth=True)
    if tag:
        tag_name = f"v{version}"
        run_git("tag", "-f", tag_name)
        run_git("push", "-f", "origin", tag_name, use_auth=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build, sign, and optionally publish MyOffice Dual-Tier OTA updates.")
    ap.add_argument("--version", default="1.6.0")
    ap.add_argument("--version-code", type=int, default=10600)
    ap.add_argument("--notes", default="Dual-tier signed OTA self-update architecture")
    ap.add_argument("--repo", default=DEFAULT_REPO_URL)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--min-android-code", type=int, default=10600)
    ap.add_argument("--min-windows-binary", default="1.0.0")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--tag-binary-release", action="store_true")
    args = ap.parse_args()

    manifest = build_and_sign_ota(
        version=args.version,
        version_code=args.version_code,
        notes=args.notes,
        repo_url=args.repo,
        branch=args.branch,
        min_android_native_code=args.min_android_code,
        min_windows_binary_version=args.min_windows_binary,
    )
    print(f"[OK] Signed OTA v{manifest['version']} ready:")
    print(f"     Android bundle : {manifest['android']['bundle_size']:,} bytes ({manifest['android']['bundle_sha256'][:16]}...)")
    print(f"     Windows patch  : {manifest['windows']['patch_size']:,} bytes ({manifest['windows']['patch_sha256'][:16]}...)")
    print(f"     Public Key B64 : {PUBLIC_KEY_B64_PATH.read_text().strip()}")

    if args.push:
        push_to_github(args.repo, args.token, args.version, args.notes, branch=args.branch, tag=args.tag_binary_release)
        print(f"[OK] Pushed cleanly to https://github.com/{normalize_github_slug(args.repo)} (zero secrets stored in git config).")


if __name__ == "__main__":
    main()
