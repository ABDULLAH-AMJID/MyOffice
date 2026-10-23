"""End-to-end tests for Dual-Tier Signed OTA Self-Updater & Boot Watchdog."""
from __future__ import annotations

import functools
import http.server
import importlib
import json
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from app.services import ota_runtime, updater

# Import publisher helper to sign real test manifests with our ECDSA P-256 release key
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ota-system"))
import publish_release  # noqa: E402


def test_github_repo_url_normalization():
    assert (
        updater.resolve_manifest_url("myuser/myoffice")
        == "https://raw.githubusercontent.com/myuser/myoffice/main/ota/manifest.json"
    )
    assert (
        updater.resolve_manifest_url("https://github.com/myuser/myoffice.git")
        == "https://raw.githubusercontent.com/myuser/myoffice/main/ota/manifest.json"
    )
    assert (
        updater.resolve_manifest_url("https://github.com/myuser/myoffice/tree/release-2")
        == "https://raw.githubusercontent.com/myuser/myoffice/release-2/ota/manifest.json"
    )


def test_live_http_signed_ota_hotpatch_tamper_rejection_and_rollback(tmp_path, monkeypatch):
    serve_dir = tmp_path / "remote_repo" / "ota"
    serve_dir.mkdir(parents=True)
    ota_home = tmp_path / "client_ota"
    monkeypatch.setenv("MYOFFICE_OTA_DIR", str(ota_home))

    priv_key, pub_b64 = publish_release.ensure_signing_keypair(
        priv_path=tmp_path / "test_signing_key.pem",
        pub_b64_path=tmp_path / "test_pub_key.b64",
    )
    monkeypatch.setattr(updater, "RELEASE_PUBLIC_KEY_B64", pub_b64)

    # Build a signed v9.9.1 delta hotpatch adding an upgraded feature constant to `app/ota_probe_mod.py`
    patch_zip = serve_dir / "windows-patch.zip"
    src_mod = tmp_path / "ota_probe_mod.py"
    src_mod.write_text('UPGRADED_BANNER = "LIVE_OTA_9_9_1_ACTIVE"\n', encoding="utf-8")
    init_mod = tmp_path / "__init__.py"
    init_mod.write_text("", encoding="utf-8")
    patch_sha, patch_size = publish_release.build_deterministic_zip(
        [(init_mod, "app/__init__.py"), (src_mod, "app/ota_probe_mod.py")],
        patch_zip,
    )

    manifest = {
        "schema": "myoffice-ota-v2",
        "version": "9.9.1",
        "version_code": 90901,
        "date": "2026-10-23T12:00:00Z",
        "notes": "Instant cloud delta feature upgrade",
        "mandatory": False,
        "min_version": "1.0.0",
        "windows": {
            "version": "9.9.1",
            "min_binary_version": "1.0.0",
            "patch_url": "./windows-patch.zip",
            "patch_sha256": patch_sha,
            "patch_size": patch_size,
        },
    }
    manifest["signature"] = publish_release.sign_manifest(manifest, priv_key)
    (serve_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Serve over real local HTTP server (mimicking raw.githubusercontent.com)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "remote_repo"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        manifest_url = f"http://127.0.0.1:{port}/ota/manifest.json"
        info = updater.check_for_update(manifest_url, force=True)
        assert info.available is True
        assert info.signed is True
        assert info.install_mode == "hotpatch"
        assert info.latest == "9.9.1"

        dl_path = updater.download_update(info, str(tmp_path / "downloads"))
        mode = updater.install_update(dl_path, info=info, restart=False)
        assert mode == "hotpatch"
        assert ota_runtime.active_patch_version(ota_home) == "9.9.1"

        # Activate runtime hot-patch finder and import the newly shipped module dynamically!
        base_app = Path(__file__).resolve().parents[1] / "app"
        active_ver = ota_runtime.bootstrap_ota_runtime(base_app, base=ota_home)
        assert active_ver == "9.9.1"
        sys.modules.pop("app.ota_probe_mod", None)
        probe = importlib.import_module("app.ota_probe_mod")
        assert probe.UPGRADED_BANNER == "LIVE_OTA_9_9_1_ACTIVE"
        ota_runtime.mark_ota_boot_healthy(base=ota_home)
        assert ota_runtime.read_ota_state(ota_home)["healthy"] is True

        # Security check 1: Tamper with manifest notes without signing key -> MUST REJECT!
        tampered = dict(manifest)
        tampered["notes"] = "Injected malicious URL"
        with pytest.raises(updater.UpdateError, match="signature invalid"):
            updater.parse_version_json(tampered, manifest_url=manifest_url)

        # Security check 2: Path traversal inside zip -> MUST REJECT!
        evil_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../../escape.py", "x = 1")
        with pytest.raises(updater.UpdateError, match="Unsafe|Blocked"):
            updater.apply_hotpatch_zip(evil_zip, version="9.9.2", base_dir=ota_home)

        # Self-healing check: Simulate 2 consecutive uncompleted startups -> auto-quarantine!
        ota_runtime.write_ota_state(
            {"active_version": "9.9.9-bad", "boot_in_progress": True, "boot_attempts": 2},
            base=ota_home,
        )
        recovered = ota_runtime.bootstrap_ota_runtime(base_app, base=ota_home)
        assert recovered is None
        assert not (ota_home / "current").exists()
    finally:
        ota_runtime.uninstall_hotpatch_finder()
        httpd.shutdown()
