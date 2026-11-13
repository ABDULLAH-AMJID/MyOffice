"""Headless smoke test for the Android Home screen, with a stubbed native bridge.

Verifies the v1.7.0 on-device library: the permission card, the scope switch, the
sort sheet, type filters and the phone-file rows — without a phone attached.
Run: python3 tools/ui_smoke_device_library.py
"""
from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

UI = pathlib.Path(__file__).resolve().parent.parent / "my-office-android/app/src/main/assets/ui"
OUT = pathlib.Path("/home/user/docs/android-library-smoke")

NOW = 1_760_000_000_000

DEVICE_FILES = [
    {"path": "/storage/emulated/0/Download/Salary Slip Aug.pdf", "name": "Salary Slip Aug.pdf", "ext": "pdf",
     "kind": "pdf", "size": 184_320, "modified": NOW - 3600_000, "folder": "Download", "source": "device"},
    {"path": "/storage/emulated/0/Download/Expense Sheet.xlsx", "name": "Expense Sheet.xlsx", "ext": "xlsx",
     "kind": "sheet", "size": 22_400, "modified": NOW - 9000_000, "folder": "Download", "source": "device"},
    {"path": "/storage/emulated/0/WhatsApp/Media/Pitch Deck.pptx", "name": "Pitch Deck.pptx", "ext": "pptx",
     "kind": "slide", "size": 3_120_000, "modified": NOW - 86_400_000, "folder": "WhatsApp/Media", "source": "device"},
    {"path": "/storage/emulated/0/Documents/Letter to Bank.docx", "name": "Letter to Bank.docx", "ext": "docx",
     "kind": "writer", "size": 44_100, "modified": NOW - 172_800_000, "folder": "Documents", "source": "device"},
    {"path": "/storage/emulated/0/DCIM/Camera/IMG_2026.jpg", "name": "IMG_2026.jpg", "ext": "jpg",
     "kind": "photos", "size": 2_400_000, "modified": NOW - 400_000_000, "folder": "DCIM/Camera", "source": "device"},
]

WORKSPACE_FILES = [
    {"name": "Weekly Report.docx", "rel": "Weekly Report.docx", "ext": "docx", "type": "writer",
     "size": 12_800, "mtime": NOW - 7200_000, "starred": True},
]

BOOTSTRAP = {
    "ok": True,
    "files": WORKSPACE_FILES,
    "config": {"theme": "light", "lang": "en"},
    "version": "1.7.0",
    "nativeVersion": "1.7.0",
    "deviceName": "Infinix X6885",
    "primaryIp": "192.168.1.14",
    "vaultKeyId": "9F2A-11C0",
}


def run(granted: bool) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 412, "height": 892}, device_scale_factor=2)
        page.add_init_script(
            """
            window.AndroidNative = {
              callAsync(reqId, action, args) {
                const payload = {
                  getBootstrap: %s,
                  listFiles: { ok: true, files: %s },
                  deviceAccess: { ok: true, granted: %s, apiLevel: 35, canRequest: true },
                  listDeviceFiles: { ok: true, granted: %s, scannedAt: %d, fromCache: false, total: %d,
                                     truncated: false, files: %s },
                  requestDeviceAccess: { ok: true, launched: true, granted: false },
                  saveSettings: { ok: true }
                }[action] || { ok: true };
                setTimeout(() => window.__resolveNativeCall && window.__resolveNativeCall(reqId, JSON.stringify(payload)), 0);
              }
            };
            """ % (json.dumps(BOOTSTRAP), json.dumps(WORKSPACE_FILES), str(granted).lower(),
                   str(granted).lower(), NOW, len(DEVICE_FILES), json.dumps(DEVICE_FILES))
        )
        page.goto((UI / "index.html").as_uri())
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / f"home-{'granted' if granted else 'denied'}.png"))
        rows = page.locator("#fileList .file-row").count()
        titles = page.locator("#fileList .file-title span").all_inner_texts()[:6]
        print(f"granted={granted} rows={rows} titles={titles}")

        page.click("#libSortBtn")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / f"sort-sheet-{'granted' if granted else 'denied'}.png"))
        page.click("text=Last used")
        page.wait_for_timeout(300)
        print("  sort label ->", page.inner_text("#libSortLabel"))

        page.click(".filter-tab[data-filter='pdf']")
        page.wait_for_timeout(300)
        print("  pdf filter rows ->", page.locator("#fileList .file-row").count())
        page.click(".filter-tab[data-filter='all']")
        page.click(".lib-scope-btn[data-scope='myoffice']")
        page.wait_for_timeout(300)
        print("  myoffice scope rows ->", page.locator("#fileList .file-row").count())
        page.screenshot(path=str(OUT / f"myoffice-scope-{'granted' if granted else 'denied'}.png"))
        browser.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    run(granted=True)
    run(granted=False)
    print("screenshots in", OUT)
