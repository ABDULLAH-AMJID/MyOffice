"""Smoke-test a frozen MyOffice build (PyInstaller output) - or the source tree.

    python tools/smoke_test.py dist/MyOffice/MyOffice.exe        # Windows build (CI runs this)
    python tools/smoke_test.py /path/to/MyOffice/MyOffice        # Linux build
    python tools/smoke_test.py                                   # source tree (python app/main.py)

Writes a .docx (with Urdu), .xlsx (with formulas), .pptx and .pdf, starts MyOffice headless with all four and checks:
every document opened (exit code 0, see app/main.py _arm_autoquit), the app quit on its own, and the window
screenshot was written.  This catches packaging bugs that source-tree tests cannot - e.g. a lazily imported module
missing from the frozen build.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXIT_CODES = {0: "ok", 3: "hung (watchdog fired)", 4: "a document failed to open"}


def make_samples(folder: Path) -> list[Path]:
    import docx
    import openpyxl
    import pymupdf
    from pptx import Presentation

    folder.mkdir(parents=True, exist_ok=True)
    d = docx.Document()
    d.add_heading("MyOffice smoke test", 1)
    d.add_paragraph("English paragraph.")
    d.add_paragraph("\u06cc\u06c1 \u0627\u0631\u062f\u0648 \u0645\u062a\u0646 \u06c1\u06d2\u06d4")  # Urdu sentence
    d.save(folder / "smoke.docx")

    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(1, 11):
        ws.cell(i, 1, i)
    ws["B1"] = "=SUM(A1:A10)"
    ws["B2"] = '=IF(B1>50,"big","small")'
    wb.save(folder / "smoke.xlsx")

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Smoke test"
    slide.placeholders[1].text = "Bullet one"
    prs.save(folder / "smoke.pptx")

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "MyOffice smoke test PDF", fontsize=14)
    pdf.save(folder / "smoke.pdf")
    pdf.close()

    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 200), (24, 58, 120))
    ImageDraw.Draw(im).rectangle((20, 20, 300, 180), outline=(255, 255, 255), width=4)
    im.save(folder / "smoke.png")
    return [folder / f"smoke.{ext}" for ext in ("docx", "xlsx", "pptx", "pdf", "png")]


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    work = Path(tempfile.mkdtemp(prefix="myoffice-smoke-"))
    files = make_samples(work / "samples")
    shot = work / "smoke.png"
    home = work / "home"
    autoquit_ms = 8000
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MYOFFICE_HOME=str(home), MYOFFICE_NO_FIRST_RUN="1",
               MYOFFICE_AUTOQUIT_MS=str(autoquit_ms), MYOFFICE_SCREENSHOT=str(shot),
               QT_LOGGING_RULES="qt.text.font.db=false")
    cmd = [exe] if exe else [sys.executable, str(ROOT / "app" / "main.py")]
    cmd += ["--no-single-instance", *map(str, files)]
    print("Running:", " ".join(cmd))
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, errors="replace",
                              timeout=autoquit_ms / 1000 + 60)
        code = proc.returncode
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:  # exc.stdout/stderr are bytes here even with text=True
        code, output = None, "".join(b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")
                                     for b in (exc.stdout, exc.stderr))
    elapsed = time.perf_counter() - t0

    problems = []
    if code != 0:
        problems.append(f"exit code {code}: {EXIT_CODES.get(code, 'timeout' if code is None else 'crash')}")
    if not shot.exists() or shot.stat().st_size < 1000:
        problems.append("no screenshot written")
    logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(home.glob("logs/*.log")))
    for line in logs.splitlines():
        if "Open failed" in line or "Traceback" in line:
            problems.append(line.strip())

    print(f"Finished in {elapsed:.1f} s (includes the {autoquit_ms / 1000:.0f} s autoquit delay)")
    if problems:
        print("SMOKE TEST FAILED:\n  " + "\n  ".join(problems))
        print("--- app output ---\n" + output[-4000:])
        print("--- log tail ---\n" + logs[-4000:])
        return 1
    print(f"SMOKE TEST PASSED: opened {', '.join(f.name for f in files)}; screenshot {shot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
