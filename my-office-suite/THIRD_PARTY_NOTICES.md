# Third-party components

MyOffice's own source code is MIT-licensed (see `LICENSE`). It uses the following libraries,
fonts, icons and external programs. Licences were checked against the project rule
"open-source libraries under permissive licences"; the two exceptions are marked **⚠** — both were
explicitly mandated by the product specification (PySide6 as GUI stack, PyMuPDF for PDF rendering).

| Component | Purpose | Licence |
|---|---|---|
| PySide6 / Qt 6 **⚠** | GUI toolkit | LGPL-3.0 (dynamic linking; the PyInstaller *one-folder* build keeps the Qt libraries as separate, replaceable DLLs) |
| PyMuPDF / MuPDF **⚠** | PDF rendering, editing, OCR layer, conversion | AGPL-3.0 (or commercial). Distributing binaries requires offering the complete corresponding source — satisfied by publishing this MIT source tree, or replace with a commercial MuPDF licence |
| python-docx, openpyxl, python-pptx | DOCX / XLSX / PPTX I/O | MIT |
| pypdf | PDF merge/split/encrypt | BSD-3-Clause |
| reportlab | Watermark rendering | BSD-3-Clause |
| pyHanko, pyhanko-certvalidator | PAdES digital signatures | MIT |
| cryptography | Certificates / PKCS#12 | Apache-2.0 / BSD |
| pandas, numpy | Pivot tables, data analysis | BSD-3-Clause |
| matplotlib | Charts | PSF-based (BSD-compatible) |
| fontTools | Font Manager | MIT |
| Pillow | Images | MIT-CMU (HPND) |
| lxml | XML | BSD-3-Clause |
| pytesseract | OCR wrapper | Apache-2.0 |
| arabic-reshaper | Urdu chart labels | MIT |
| jsonschema | Forms validation | MIT |
| watchdog | Cloud Sync file watching | Apache-2.0 |
| boto3 / botocore | S3 / Supabase Storage | Apache-2.0 |
| websockets | Collaboration transport | BSD-3-Clause |
| pycrdt (y-crdt) | CRDT (Yjs-compatible) | MIT |
| requests | Updates, template catalogue | Apache-2.0 |
| pywinrt (winrt-runtime, winrt-Windows.Foundation, winrt-Windows.UI.Shell) | Windows 11 *Pin to taskbar* request (Windows only) | MIT |
| xlrd, xlwt | Excel 97-2003 (.xls) reading and writing | BSD |
| olefile | VBA project and encrypted-file containers (OLE) | BSD-2-Clause |
| msoffcrypto-tool | Password protection of DOCX/XLSX/PPTX (ECMA-376 agile encryption) | MIT |
| pyspellchecker (+ its English and Spanish word frequency lists) | Spelling | MIT |
| latex2mathml | Equations: LaTeX → MathML (then MathML → Office Math in MyOffice) | MIT |
| comtypes | Windows scanner dialog (WIA) for Scan to PDF (Windows only) | MIT |
| Qt Multimedia with FFmpeg (in PySide6-Addons) **⚠** | Screen and slide-show recording (MP4) | LGPL-3.0 (Qt) / LGPL-2.1+ (FFmpeg, dynamically linked DLLs, no GPL parts) |
| Noto Nastaliq Urdu, Noto Sans Devanagari | Bundled fonts | SIL Open Font License 1.1 (`resources/fonts/OFL.txt`) |
| Lucide icons | UI icons and the 1,891-icon library of Presentation ▸ Icons (`resources/icon_library.zip`) | ISC (`resources/icons/LICENSE-lucide.txt`) |

External programs. They run as separate processes; MyOffice never links to them.

**Bundled in the Windows installer** (staged by `tools/fetch_windows_deps.py` from the official Windows releases,
unchanged apart from stripped debug symbols; licence texts and source addresses are installed in `{app}\licenses`):

| Program | Used for | Licence |
|---|---|---|
| Tesseract OCR 5.5.3 (`tesseract\`) | OCR | Apache-2.0. The libraries it loads are permissive (Leptonica, libarchive, curl, zlib, libpng, libjpeg-turbo, libtiff, libwebp, OpenJPEG ...), except: GCC runtime (GPL-3.0 with the GCC Runtime Library Exception), GNU libiconv / gettext runtime (LGPL-2.1+), libidn2 and libunistring (LGPL-3.0+ / GPL-2.0+), JBIG-KIT (GPL-2.0+). Full list: `licenses/THIRD_PARTY_PROGRAMS.txt` |
| tessdata_fast 4.1.0 `eng`, `urd`, `hin` (`tessdata\`) | OCR language data | Apache-2.0 |
| Ghostscript 10.08.0 (`gs\`) | Strong PDF compression | AGPL-3.0 - used through its command line; source: ghostpdl-downloads release gs10080 |
| Microsoft Visual C++ runtime 14.44 (`gs\`, and inside the PySide6 folder) | Runtime of Qt and Ghostscript | Microsoft Visual C++ Redistributable licence (local deployment) |

**Optional, downloaded only on request** (setup task *Download LibreOffice*, or *Tools ▸ Legacy Formats Pack*):

| Program | Used for | Licence |
|---|---|---|
| LibreOffice 26.8.1 (headless) | Legacy `.doc/.xls/.ppt/.odt/.ods/.odp/.rtf` import and conversion | MPL-2.0 |

## Test data

`tests/data/SimpleMacro.xlsm` comes from the Apache POI test files (Apache-2.0); it is only used by the test suite and is not part of the installer.

## AI providers

MyOffice AI talks to the provider the user chooses (OpenAI, Google Gemini, Anthropic, Azure OpenAI, OpenRouter, Groq, or a local Ollama / LM Studio server). No model and no provider SDK is shipped; the provider's own terms apply to the text that is sent to it.
