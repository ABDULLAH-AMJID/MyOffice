"""Stage the helper programs that MyOffice Setup carries inside MyOffice_Setup.exe.

    python tools/fetch_windows_deps.py --out installer/deps [--cache DIR] [--crt-dir DIR]

Nothing is downloaded while the user installs MyOffice: this script runs at *build* time and
produces the folder that installer/setup.iss packs (LZMA2, solid) into the setup:

    deps/tesseract/   tesseract.exe and exactly the DLLs it imports (read from the PE import
                      tables), with their debug symbols stripped - 34 files instead of the
                      ~100 MB the Tesseract installer unpacks (training tools, ICU, Cairo ...)
    deps/tessdata/    eng, urd, hin (tessdata_fast 4.1.0) + Tesseract's configs/ and pdf.ttf
    deps/gs/          gswin64c.exe + gsdll64.dll - Ghostscript's PostScript resources are compiled
                      into the DLL (%rom% file system), so these two files are a complete Ghostscript.
                      Plus vcruntime140.dll, vcruntime140_1.dll and msvcp140.dll (Microsoft
                      redistributables, "local deployment"), so no Visual C++ setup is needed.
    deps/licenses/    licence texts and where to get the source code of every bundled file
    deps/manifest.json  versions and SHA-256 of every download and every staged file

Every download is pinned and verified against its SHA-256.  A DLL that is not in the licence table
below stops the build, so a new upstream dependency is always reviewed before it is shipped.

Needs 7-Zip (``7z``) to unpack the NSIS installers and ``pefile`` (``pip install pefile``).
Debug symbols are stripped with ``strip`` / ``x86_64-w64-mingw32-strip`` / ``llvm-strip`` when
one of them is available (otherwise the files are staged unstripped: ~5 MB larger setup).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = "MyOffice-build/1.1 (+https://github.com/myoffice/myoffice)"

TESSERACT = {
    "name": "Tesseract OCR",
    "version": "5.5.3",
    "urls": ["https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/"
             "tesseract-ocr-w64-setup-5.5.3.20260724.exe"],
    "sha256": "bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4",
    "file": "tesseract-ocr-w64-setup-5.5.3.20260724.exe",
    "source": "https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3",
}
GHOSTSCRIPT = {
    "name": "Ghostscript",
    "version": "10.08.0",
    "urls": ["https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs10080/gs10080w64.exe"],
    "sha256": "52a91b8bf09298788d7a57b9206127026c23eacd75405f0a131e26dc381dce50",
    "file": "gs10080w64.exe",
    "source": "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/tag/gs10080 "
              "(ghostscript-10.08.0.tar.gz)",
}
TESSDATA_BASES = ["https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/",
                  "https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@4.1.0/"]
TESSDATA = {  # tessdata_fast 4.1.0 (Apache-2.0)
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "urd": "62e8250ce2a994106e313a82e26a516a39e2cf159d0ce3c5b5008387fd0d555f",
    "hin": "4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825",
}
TESS_CONFIGS = ("txt", "pdf", "tsv", "hocr", "alto")
CRT_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
MIN_CRT = (14, 40)  # MSVC 17.10+ binaries crash with older msvcp140.dll (std::mutex change)

MSYS2 = "https://github.com/msys2/MINGW-packages (package mingw-w64-x86_64-{0})"
# DLL -> (component, licence, licence file in deps/licenses, where the source code is)
LICENSES = {
    "tesseract.exe": ("Tesseract OCR 5.5.3", "Apache-2.0", "Apache-2.0.txt", TESSERACT["source"]),
    "libtesseract-5.dll": ("Tesseract OCR 5.5.3", "Apache-2.0", "Apache-2.0.txt", TESSERACT["source"]),
    "libleptonica-6.dll": ("Leptonica", "BSD-2-Clause", None, MSYS2.format("leptonica")),
    "libarchive-13.dll": ("libarchive", "BSD-2-Clause", None, MSYS2.format("libarchive")),
    "libb2-1.dll": ("BLAKE2 (libb2)", "CC0-1.0", None, MSYS2.format("libb2")),
    "libbrotlicommon.dll": ("Brotli", "MIT", None, MSYS2.format("brotli")),
    "libbrotlidec.dll": ("Brotli", "MIT", None, MSYS2.format("brotli")),
    "libbz2-1.dll": ("bzip2", "bzip2-1.0.6 (BSD-style)", None, MSYS2.format("bzip2")),
    "libcurl-4.dll": ("curl", "curl (MIT-style)", None, MSYS2.format("curl")),
    "libdeflate.dll": ("libdeflate", "MIT", None, MSYS2.format("libdeflate")),
    "libexpat-1.dll": ("Expat", "MIT", None, MSYS2.format("expat")),
    "libgcc_s_seh-1.dll": ("GCC runtime", "GPL-3.0 with GCC Runtime Library Exception 3.1", "GPL-3.0.txt",
                           MSYS2.format("gcc")),
    "libstdc++-6.dll": ("GCC C++ runtime", "GPL-3.0 with GCC Runtime Library Exception 3.1", "GPL-3.0.txt",
                        MSYS2.format("gcc")),
    "libgif-7.dll": ("giflib", "MIT", None, MSYS2.format("giflib")),
    "libiconv-2.dll": ("GNU libiconv", "LGPL-2.1-or-later", "LGPL-2.1.txt", MSYS2.format("libiconv")),
    "libidn2-0.dll": ("GNU libidn2", "LGPL-3.0-or-later OR GPL-2.0-or-later", "LGPL-3.0.txt",
                      MSYS2.format("libidn2")),
    "libintl-8.dll": ("GNU gettext runtime", "LGPL-2.1-or-later", "LGPL-2.1.txt", MSYS2.format("gettext-runtime")),
    "libjbig-0.dll": ("JBIG-KIT", "GPL-2.0-or-later", "GPL-2.0.txt", MSYS2.format("jbigkit")),
    "libjpeg-8.dll": ("libjpeg-turbo", "IJG / BSD-3-Clause", None, MSYS2.format("libjpeg-turbo")),
    "libLerc.dll": ("LERC", "Apache-2.0", "Apache-2.0.txt", MSYS2.format("lerc")),
    "liblz4.dll": ("LZ4", "BSD-2-Clause", None, MSYS2.format("lz4")),
    "liblzma-5.dll": ("XZ Utils (liblzma)", "0BSD", None, MSYS2.format("xz")),
    "libopenjp2-7.dll": ("OpenJPEG", "BSD-2-Clause", None, MSYS2.format("openjpeg2")),
    "libpng16-16.dll": ("libpng", "libpng-2.0", None, MSYS2.format("libpng")),
    "libpsl-5.dll": ("libpsl", "MIT", None, MSYS2.format("libpsl")),
    "libsharpyuv-0.dll": ("libwebp", "BSD-3-Clause", None, MSYS2.format("libwebp")),
    "libwebp-7.dll": ("libwebp", "BSD-3-Clause", None, MSYS2.format("libwebp")),
    "libwebpmux-3.dll": ("libwebp", "BSD-3-Clause", None, MSYS2.format("libwebp")),
    "libssh2-1.dll": ("libssh2", "BSD-3-Clause", None, MSYS2.format("libssh2")),
    "libtiff-6.dll": ("LibTIFF", "libtiff (BSD-style)", None, MSYS2.format("libtiff")),
    "libunistring-5.dll": ("GNU libunistring", "LGPL-3.0-or-later OR GPL-2.0-or-later", "LGPL-3.0.txt",
                           MSYS2.format("libunistring")),
    "libwinpthread-1.dll": ("mingw-w64 winpthreads", "MIT AND BSD-3-Clause", None, MSYS2.format("winpthreads")),
    "libzstd.dll": ("Zstandard", "BSD-3-Clause OR GPL-2.0", None, MSYS2.format("zstd")),
    "zlib1.dll": ("zlib", "Zlib", None, MSYS2.format("zlib")),
    "gswin64c.exe": ("Ghostscript 10.08.0", "AGPL-3.0", "AGPL-3.0.txt", GHOSTSCRIPT["source"]),
    "gsdll64.dll": ("Ghostscript 10.08.0", "AGPL-3.0", "AGPL-3.0.txt", GHOSTSCRIPT["source"]),
    "vcruntime140.dll": ("Microsoft Visual C++ runtime", "Microsoft Visual C++ Redistributable licence", None,
                         "redistributable binary (no source)"),
    "vcruntime140_1.dll": ("Microsoft Visual C++ runtime", "Microsoft Visual C++ Redistributable licence", None,
                           "redistributable binary (no source)"),
    "msvcp140.dll": ("Microsoft Visual C++ runtime", "Microsoft Visual C++ Redistributable licence", None,
                     "redistributable binary (no source)"),
}


class BuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(urls: list[str], dest: Path, digest: str) -> Path:
    if dest.is_file() and sha256(dest) == digest:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in urls:
        for attempt in range(3):
            tmp = dest.with_name(dest.name + ".part")
            try:
                print(f"  downloading {url}")
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
                    shutil.copyfileobj(resp, out, 1 << 20)
                got = sha256(tmp)
                if got != digest:
                    raise BuildError(f"SHA-256 mismatch for {url}: {got}")
                os.replace(tmp, dest)
                return dest
            except (OSError, BuildError) as exc:
                errors.append(f"{url}: {exc}")
                tmp.unlink(missing_ok=True)
                time.sleep(2 * (attempt + 1))
    raise BuildError("download failed:\n  " + "\n  ".join(errors))


def find_tool(names: list[str], extra: list[str] = ()) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in extra:
        if p and os.path.isfile(p):
            return p
    return None


def find_7z() -> str:
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
    tool = find_tool(["7z", "7zz", "7za"], [os.path.join(b, "7-Zip", "7z.exe") for b in pf if b])
    if not tool:
        raise BuildError("7-Zip is needed to unpack the NSIS installers (apt install p7zip-full / 7-zip.org)")
    return tool


def find_strip() -> str | None:
    """A strip that understands 64-bit PE files (MinGW binutils, LLVM, or a multi-target binutils)."""
    for name in ("x86_64-w64-mingw32-strip", "llvm-strip"):
        p = shutil.which(name)
        if p:
            return p
    p = shutil.which("strip")
    if p:
        try:
            out = subprocess.run([p, "--help"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if "pe-x86-64" in out or "pei-x86-64" in out:
            return p
    return None


def unpack(installer: Path, dest: Path, seven: str) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    r = subprocess.run([seven, "x", "-y", f"-o{dest}", str(installer)], capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError(f"7-Zip could not unpack {installer.name}:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
    return dest


def _pefile():
    try:
        import pefile
    except ImportError as exc:
        raise BuildError("the 'pefile' package is needed: pip install pefile") from exc
    return pefile


def pe_imports(path: Path) -> set[str]:
    pefile = _pefile()
    pe = pefile.PE(str(path), fast_load=True)
    if pe.FILE_HEADER.Machine != 0x8664:
        raise BuildError(f"{path.name} is not a 64-bit Windows binary")
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                           pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
    names = set()
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for entry in getattr(pe, attr, []):
            names.add(entry.dll.decode("ascii", "replace").lower())
    pe.close()
    return names


def dll_closure(folder: Path, exe: str) -> tuple[list[Path], set[str]]:
    """``exe`` plus every DLL of ``folder`` it needs, directly or indirectly.  Returns the files and the
    names of the imports that are not in the folder (Windows system DLLs)."""
    local = {p.name.lower(): p for p in folder.iterdir() if p.suffix.lower() in (".dll", ".exe")}
    need, todo, system = {}, [exe.lower()], set()
    while todo:
        name = todo.pop()
        if name in need:
            continue
        if name not in local:
            raise BuildError(f"{name} not found in {folder}")
        need[name] = local[name]
        for dep in pe_imports(local[name]):
            (todo.append(dep) if dep in local else system.add(dep))
    return sorted(need.values(), key=lambda p: p.name.lower()), system


def file_version(path: Path) -> tuple[int, ...]:
    pefile = _pefile()
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    try:
        ms, ls = pe.VS_FIXEDFILEINFO[0].FileVersionMS, pe.VS_FIXEDFILEINFO[0].FileVersionLS
        return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF
    except (AttributeError, IndexError):
        return (0,)
    finally:
        pe.close()


def strip_debug(path: Path, tool: str | None) -> bool:
    if not tool:
        return False
    backup = path.with_name(path.name + ".orig")
    shutil.copy2(path, backup)
    try:
        r = subprocess.run([tool, "--strip-debug", str(path)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise BuildError(r.stderr.strip())
        pe_imports(path)  # still a valid PE with the same imports
        backup.unlink()
        return True
    except Exception as exc:  # keep the original file
        print(f"  warning: could not strip {path.name}: {exc}")
        os.replace(backup, path)
        return False


def crt_source(crt_dir: str | None) -> Path:
    candidates = [Path(crt_dir)] if crt_dir else []
    if not crt_dir:
        try:  # building on Windows: the PySide6 wheel carries the Visual C++ runtime
            import PySide6

            candidates.append(Path(PySide6.__file__).parent)
        except ImportError:
            pass
    for c in candidates:
        if all((c / n).is_file() for n in CRT_DLLS):
            return c
    raise BuildError("vcruntime140.dll / msvcp140.dll not found - pass --crt-dir (e.g. the PySide6 package folder "
                     "of a Windows Python)")


def stage(out: Path, cache: Path, crt_dir: str | None) -> dict:
    seven = find_7z()
    strip = find_strip()
    print(f"7-Zip: {seven}\nstrip: {strip or 'not found - files are staged unstripped'}")
    if out.exists():
        shutil.rmtree(out)
    (out / "licenses").mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix="myoffice_deps_"))
    manifest: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "downloads": {}, "files": {}}
    try:
        # ---- Tesseract: the program and the DLLs it really imports
        print(f"== Tesseract {TESSERACT['version']}")
        inst = download(TESSERACT["urls"], cache / TESSERACT["file"], TESSERACT["sha256"])
        manifest["downloads"][TESSERACT["file"]] = TESSERACT["sha256"]
        src = unpack(inst, work / "tesseract", seven)
        files, system = dll_closure(src, "tesseract.exe")
        (out / "tesseract").mkdir()
        for f in files:
            shutil.copy2(f, out / "tesseract" / f.name)
            strip_debug(out / "tesseract" / f.name, strip)
        print(f"  {len(files)} files; Windows DLLs used: {', '.join(sorted(system))}")
        tdata = out / "tessdata"
        (tdata / "configs").mkdir(parents=True)
        for c in TESS_CONFIGS:
            shutil.copy2(src / "tessdata" / "configs" / c, tdata / "configs" / c)
        shutil.copy2(src / "tessdata" / "pdf.ttf", tdata / "pdf.ttf")
        shutil.copy2(src / "doc" / "LICENSE", out / "licenses" / "Tesseract-LICENSE.txt")

        # ---- OCR language data
        print("== OCR language data (tessdata_fast 4.1.0)")
        for lang, digest in TESSDATA.items():
            name = f"{lang}.traineddata"
            f = download([b + name for b in TESSDATA_BASES], cache / "tessdata_fast-4.1.0" / name, digest)
            shutil.copy2(f, tdata / name)
            manifest["downloads"][name] = digest

        # ---- Ghostscript: two files + the Visual C++ runtime next to them
        print(f"== Ghostscript {GHOSTSCRIPT['version']}")
        inst = download(GHOSTSCRIPT["urls"], cache / GHOSTSCRIPT["file"], GHOSTSCRIPT["sha256"])
        manifest["downloads"][GHOSTSCRIPT["file"]] = GHOSTSCRIPT["sha256"]
        src = unpack(inst, work / "gs", seven)
        (out / "gs").mkdir()
        for name in ("gswin64c.exe", "gsdll64.dll"):
            shutil.copy2(src / "bin" / name, out / "gs" / name)
        if b"%rom%Resource/Init/" not in (out / "gs" / "gsdll64.dll").read_bytes():
            raise BuildError("gsdll64.dll has no compiled-in resources - the two-file Ghostscript would not work")
        crt = crt_source(crt_dir)
        for name in CRT_DLLS:
            shutil.copy2(crt / name, out / "gs" / name)
        ver = file_version(out / "gs" / "msvcp140.dll")
        if ver[:2] < MIN_CRT:
            raise BuildError(f"msvcp140.dll {'.'.join(map(str, ver))} is older than 14.40")
        manifest["msvcp140_version"] = ".".join(map(str, ver))
        crt_names = {n.lower() for n in CRT_DLLS}
        needed = set()
        for name in ("gswin64c.exe", "gsdll64.dll"):
            needed |= pe_imports(out / "gs" / name) & crt_names
        missing = needed - {n.lower() for n in os.listdir(out / "gs")}
        if missing:
            raise BuildError(f"Ghostscript needs {sorted(missing)}")
        shutil.copy2(src / "doc" / "COPYING", out / "licenses" / "Ghostscript-COPYING.txt")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    write_licenses(out)
    for p in sorted(out.rglob("*")):
        if p.is_file():
            manifest["files"][p.relative_to(out).as_posix()] = {"size": p.stat().st_size, "sha256": sha256(p)}
    manifest["versions"] = {"tesseract": TESSERACT["version"], "ghostscript": GHOSTSCRIPT["version"],
                            "tessdata_fast": "4.1.0"}
    manifest["stripped"] = bool(strip)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def write_licenses(out: Path) -> None:
    binaries = sorted(p.name for d in ("tesseract", "gs") for p in (out / d).iterdir())
    unknown = [b for b in binaries if b.lower() not in {k.lower() for k in LICENSES}]
    if unknown:
        raise BuildError("no licence information for: " + ", ".join(unknown) +
                         " - review the new dependency and add it to LICENSES in tools/fetch_windows_deps.py")
    lower = {k.lower(): v for k, v in LICENSES.items()}
    texts = set()
    lines = [
        "Third-party programs bundled with MyOffice",
        "==========================================",
        "",
        "These programs are separate executables that MyOffice starts when needed. They are copied",
        "unchanged from the official Windows releases (only debug symbols were removed) and keep their own",
        "licences. Licence texts are in this folder; the source code is available at the addresses below.",
        "",
        "tesseract\\  - Tesseract OCR " + TESSERACT["version"] + " and the libraries it uses",
        "tessdata\\   - OCR language data, tessdata_fast 4.1.0 (Apache-2.0): https://github.com/tesseract-ocr/tessdata_fast",
        "gs\\         - Ghostscript " + GHOSTSCRIPT["version"] + " (AGPL-3.0) and the Microsoft Visual C++ runtime",
        "",
        f"{'File':<22} {'Component':<32} {'Licence':<48} Source code",
        "-" * 150,
    ]
    for b in binaries:
        comp, lic, text, src = lower[b.lower()]
        lines.append(f"{b:<22} {comp:<32} {lic:<48} {src}")
        if text:
            texts.add(text)
    lines += ["", "Ghostscript is used through its command line only. If you received MyOffice from someone, you may",
              "ask them for the corresponding source code of the GPL/LGPL/AGPL components listed above, or",
              "download it from the addresses given. The Microsoft Visual C++ runtime files are distributed under",
              "the Microsoft Visual C++ Redistributable licence (local deployment).", ""]
    (out / "licenses" / "THIRD_PARTY_PROGRAMS.txt").write_text("\r\n".join(lines), encoding="utf-8")
    for t in sorted(texts):
        shutil.copy2(ROOT / "installer" / "licenses" / t, out / "licenses" / t)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "installer" / "deps"), help="output folder (replaced)")
    ap.add_argument("--cache", default=str(ROOT / "installer" / ".deps-cache"), help="download cache")
    ap.add_argument("--crt-dir", help="folder with vcruntime140.dll, vcruntime140_1.dll and msvcp140.dll")
    args = ap.parse_args(argv)
    try:
        m = stage(Path(args.out), Path(args.cache), args.crt_dir)
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    total = sum(f["size"] for f in m["files"].values())
    by_dir: dict = {}
    for name, f in m["files"].items():
        by_dir[name.split("/")[0]] = by_dir.get(name.split("/")[0], 0) + f["size"]
    print("staged:", ", ".join(f"{d} {s / 1e6:.1f} MB" for d, s in sorted(by_dir.items())), f"- total {total / 1e6:.1f} MB")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
