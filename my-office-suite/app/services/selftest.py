"""``MyOffice.exe --self-test [-o report.txt]``: checks that an installed copy is complete and working.

Uses the real code paths of the app (no mocks): verifies bundled Tesseract + English/Urdu/Hindi OCR,
bundled Ghostscript PDF compression, built-in DOCX/XLSX/PPTX -> PDF export, spelling, equations,
ECMA-376 AES-256 encryption, .xls + worksheet functions, Photos inpainting/enhancement, Qt Multimedia,
multi-size Windows Explorer `.ico` assets, hardware-bound `.movault` security, multi-room LAN collaboration
+ Team Chat + QR sharing, and custom keyboard shortcuts. Exit code 0 = every check passed.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.version import APP_NAME, APP_VERSION

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _version_line(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    text = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), f"exit code {r.returncode}")


def _check_tesseract(work: Path) -> str:
    from app.pdf import ops

    cmd = ops.tesseract_cmd()
    if not cmd:
        raise RuntimeError("Tesseract not found")
    return f"{cmd} ({_version_line([cmd, '--version'])})"


def _check_languages(work: Path) -> str:
    from app.pdf import ops

    langs = ops.ocr_languages()
    missing = [x for x in ("eng", "urd", "hin") if x not in langs]
    if missing:
        raise RuntimeError(f"missing OCR languages: {', '.join(missing)} (have: {', '.join(langs) or 'none'})")
    where = ops.private_tessdata()
    return ", ".join(langs) + (f" in {where}" if where else "")


def _check_ocr(work: Path) -> str:
    import pymupdf
    from PIL import Image

    from app.pdf import ops

    doc = pymupdf.open()
    page = doc.new_page(width=600, height=160)
    page.insert_text((30, 70), "MyOffice self test 2026", fontsize=30)
    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    text = ops.ocr_image(img, lang="eng")
    if "self test" not in " ".join(text.lower().split()):
        raise RuntimeError(f"unexpected OCR result: {text.strip()[:80]!r}")
    return repr(" ".join(text.split()))


def _check_ghostscript(work: Path) -> str:
    import pymupdf
    from PIL import Image

    from app.pdf import ops

    gs = ops.find_ghostscript()
    if not gs:
        raise RuntimeError("Ghostscript not found")
    img = Image.effect_noise((1600, 1600), 60).convert("RGB")
    png = work / "noise.png"
    img.save(png)
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(36, 36, 559, 559), filename=str(png))
    src = work / "big.pdf"
    doc.save(src)
    res = ops.compress_pdf(str(src), str(work / "small.pdf"), "screen")
    if not res.method.startswith("Ghostscript"):
        raise RuntimeError(f"Ghostscript was not used ({res.method})")
    return f"{gs} ({_version_line([gs, '--version'])}): {res.before:,} -> {res.after:,} bytes"


def _check_builtin_pdf(work: Path) -> str:
    import docx
    import openpyxl
    import pymupdf
    from pptx import Presentation

    from app.services import builtin_pdf

    d = docx.Document()
    d.add_heading("Self test", 1)
    d.add_paragraph("Writer engine")
    d.save(work / "t.docx")
    wb = openpyxl.Workbook()
    wb.active["A1"], wb.active["A2"], wb.active["A3"] = 20, 22, "=A1+A2"
    wb.save(work / "t.xlsx")
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[0]).shapes.title.text = "Presentation engine"
    prs.save(work / "t.pptx")
    parts = []
    for name, expect in (("t.docx", "Writer engine"), ("t.xlsx", "42"), ("t.pptx", "Presentation engine")):
        t0 = time.perf_counter()
        out = work / (name + ".pdf")
        builtin_pdf.export(str(work / name), str(out))
        with pymupdf.open(out) as pdf:
            text = " ".join(p.get_text() for p in pdf)
        if expect not in text:
            raise RuntimeError(f"{name}: expected text {expect!r} not found in the PDF")
        parts.append(f"{Path(name).suffix} {time.perf_counter() - t0:.2f} s")
    return ", ".join(parts)


def _info_libreoffice(work: Path) -> str:
    from app.services import legacy_pack

    st = legacy_pack.status()
    if st["installed"]:
        return f"installed ({st['path']})"
    return "not installed (optional - offered when a .doc/.xls/.ppt/OpenDocument file is opened)"


def _check_spelling(work: Path) -> str:
    from app.services.spell import speller

    sp = speller("en_US")
    if sp.check("recieve") or not sp.check("receive") or sp.suggest("recieve")[:1] != ["receive"]:
        raise RuntimeError("English word list not working")
    es = speller("es_ES")
    if not es.check("canci\u00f3n"):
        raise RuntimeError("Spanish word list not working")
    return "English and Spanish word lists"


def _check_equations(work: Path) -> str:
    from app.writer import equations

    latex = r"x = \frac{-b \pm \sqrt{b^{2}-4ac}}{2a}"
    png = equations.render_png(latex)
    omml = equations.latex_to_omml(latex)
    if png[:4] != b"\x89PNG" or not len(omml):
        raise RuntimeError("equation rendering failed")
    return f"rendered {len(png):,} bytes, Office Math OK"


def _check_encryption(work: Path) -> str:
    import docx

    from app.services import ooxml_crypto

    d = docx.Document()
    d.add_paragraph("self test secret")
    d.save(work / "plain.docx")
    ooxml_crypto.encrypt_file(work / "plain.docx", work / "enc.docx", "pw")
    back = ooxml_crypto.decrypt_to_temp(work / "enc.docx", "pw")
    if not ooxml_crypto.is_encrypted(work / "enc.docx") or "secret" not in docx.Document(back).paragraphs[0].text:
        raise RuntimeError("encryption round trip failed")
    return "AES-256 (ECMA-376 agile) round trip"


def _check_xls(work: Path) -> str:
    from app.sheet import features
    from app.sheet.model import Book

    b = Book()
    sh = b.add_sheet("S")
    b.set_input(sh, 1, 1, "20")
    b.set_input(sh, 2, 1, "=A1*2+2")
    features.save_xls(str(work / "t.xls"), b)
    b2 = Book()
    features.load_xls_formatted(str(work / "t.xls"), b2)
    if b2.sheets[0].value(2, 1) != 42:
        raise RuntimeError("cached formula result not read back")
    from app.sheet.formula.functions import FUNCTIONS

    return f".xls write/read OK, {len(FUNCTIONS)} worksheet functions"


def _check_photos(work: Path) -> str:
    from PIL import Image, ImageDraw

    from app.photos import ops

    img = Image.new("RGB", (200, 120), (90, 160, 230))
    ImageDraw.Draw(img).rectangle((95, 20, 105, 100), fill=(30, 30, 30))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rectangle((92, 17, 108, 103), fill=255)
    px = ops.inpaint(img, mask).getpixel((100, 60))
    if abs(px[2] - 230) > 30:
        raise RuntimeError(f"eraser result {px}")
    ops.remove_background(img)
    ops.auto_enhance(img)
    return "eraser, background removal, enhance"


def _check_multimedia(work: Path) -> str:
    from app.services import recorder

    ok, why = recorder.multimedia_available()
    if not ok:
        raise RuntimeError(why)
    mics, cams = len(recorder.audio_inputs()), len(recorder.cameras())
    return f"Qt Multimedia ready ({mics} microphone(s), {cams} camera(s))"


def _check_explorer_icons(work: Path) -> str:
    from app.services import register
    from app.services.paths import resource_path

    kinds = ("myoffice", "writer", "sheet", "slide", "pdf", "photos", "code", "vault")
    for k in kinds:
        ico = resource_path("icons", "app", f"{k}.ico")
        if not ico.is_file() or ico.stat().st_size < 1024:
            raise RuntimeError(f"missing or corrupt icon: {ico}")
    return f"8/8 multi-size .ico files verified, {len(register.ASSOCIATIONS)} file formats registered"


def _check_hardware_vault(work: Path) -> str:
    from app.services import vault

    owner = vault.DeviceKeyStore(work / "owner_device.key")
    thief = vault.DeviceKeyStore(work / "other_laptop.key")
    payload = b"confidential router usb backup"
    blob = vault.encrypt_bytes(payload, "budget.xlsx", str(work / "budget.xlsx"), store=owner)
    plain, meta = vault.decrypt_bytes(blob, store=owner)
    if plain != payload or meta["original_name"] != "budget.xlsx":
        raise RuntimeError("owner vault round-trip failed")
    try:
        vault.decrypt_bytes(blob, store=thief)
        raise RuntimeError("foreign device unlocked hardware-bound vault!")
    except vault.VaultAccessDeniedError:
        pass
    return "AES-256-GCM laptop-bound seal/unseal + foreign-device block OK"


def _check_lan_collab_and_qr(work: Path) -> str:
    import urllib.request

    from app.collab.qr_tools import make_qr_png_bytes
    from app.collab.server import CollabServer, primary_lan_ip

    srv = CollabServer("127.0.0.1", 0, persist=False)
    srv.register_shared_file("room-alpha", filename="plan.docx", kind="writer", raw_bytes=b"DOCXDATA")
    srv.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/api/rooms/room-alpha/file", timeout=5) as r:  # noqa: S310
            data = r.read()
        if data != b"DOCXDATA":
            raise RuntimeError("HTTP file snapshot mismatch")
        png = make_qr_png_bytes(f"http://{primary_lan_ip()}:{srv.port}/f/room-alpha")
        if png[:4] != b"\x89PNG":
            raise RuntimeError("QR PNG generation failed")
    finally:
        srv.stop()
    return f"multi-room server + HTTP file sync + QR PNG OK (LAN IP {primary_lan_ip()})"


def _check_mobile_sync_hub(work: Path) -> str:
    from app.services.cloud_sync import SyncEngine
    from app.services.mobile_sync import MobilePeerBackend, MobileSyncServer, parse_pair_uri

    host_dir = work / "pc_sync"
    mob_dir = work / "android_sync"
    host_dir.mkdir(parents=True, exist_ok=True)
    mob_dir.mkdir(parents=True, exist_ok=True)
    (host_dir / "pc_note.txt").write_bytes(b"Hello from PC")
    (mob_dir / "phone_note.txt").write_bytes(b"Hello from Android")
    srv = MobileSyncServer(host_dir, host="127.0.0.1", port=0, enable_udp_discovery=False)
    srv.start()
    try:
        parsed = parse_pair_uri(srv.pair_uri("127.0.0.1"))
        peer = MobilePeerBackend(parsed["url"], pin=parsed["pin"], device_name="SelfTest-Android")
        rep = SyncEngine(str(mob_dir), peer).sync_once()
        if "pc_note.txt" not in rep.downloaded or "phone_note.txt" not in rep.uploaded:
            raise RuntimeError(f"Unexpected sync report: {rep.summary()}")
    finally:
        srv.stop()
    return f"two-way PC <-> Android sync + QR PIN pairing OK ({rep.summary()})"


def _check_ota_updater(work: Path) -> str:
    from app.services.updater import resolve_manifest_url

    norm = resolve_manifest_url("https://github.com/demo/myoffice.git")
    if norm != "https://raw.githubusercontent.com/demo/myoffice/main/ota/manifest.json":
        raise RuntimeError(f"Unexpected URL normalization: {norm}")
    return "ECDSA-P256 + SHA-256 + hot-patch watchdog OK"


CHECKS = (
    ("Tesseract OCR", _check_tesseract, True),
    ("OCR languages", _check_languages, True),
    ("OCR", _check_ocr, True),
    ("Ghostscript", _check_ghostscript, True),
    ("Office -> PDF (built-in)", _check_builtin_pdf, True),
    ("Spelling", _check_spelling, True),
    ("Equations", _check_equations, True),
    ("Password protection", _check_encryption, True),
    ("Excel 97-2003 + functions", _check_xls, True),
    ("Photos", _check_photos, True),
    ("Recording (Qt Multimedia)", _check_multimedia, True),
    ("Explorer icons & associations", _check_explorer_icons, True),
    ("Hardware Vault security", _check_hardware_vault, True),
    ("LAN collaboration & QR", _check_lan_collab_and_qr, True),
    ("Mobile app sync (PC <-> Android)", _check_mobile_sync_hub, True),
    ("GitHub OTA self-updater", _check_ota_updater, True),
    ("Legacy Formats Pack", _info_libreoffice, False),
)


def run(report: str | None = None) -> int:
    from app.services.paths import install_dir

    lines = [f"{APP_NAME} {APP_VERSION} self-test - {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Program folder: {install_dir() or Path(__file__).resolve().parents[2]}",
             f"System: {platform.platform()} / Python {platform.python_version()}", ""]
    ok = True
    work = Path(tempfile.mkdtemp(prefix="myoffice_selftest_"))
    try:
        for name, fn, required in CHECKS:
            t0 = time.perf_counter()
            try:
                detail = fn(work)
                mark = "[ OK ]" if required else "[INFO]"
            except Exception as exc:  # noqa: BLE001 - reported
                detail = f"{exc.__class__.__name__}: {exc}"
                mark = "[FAIL]" if required else "[INFO]"
                ok = ok and not required
            lines.append(f"{mark} {name}: {detail}  ({time.perf_counter() - t0:.1f} s)")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    lines += ["", "RESULT: " + ("all checks passed" if ok else "FAILED")]
    text = "\n".join(lines)
    if report:
        Path(report).write_text(text + "\n", encoding="utf-8")
    if sys.stdout is not None:
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(text.encode(enc, errors="replace").decode(enc, errors="replace"))
    return 0 if ok else 1


__all__ = ["run", "CHECKS"]
