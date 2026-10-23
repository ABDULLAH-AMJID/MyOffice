#!/usr/bin/env bash
# Build MyOffice_Setup.exe on Linux with Wine (the same Windows tools as build_windows.bat:
# Windows Python 3.11 + PyInstaller + Inno Setup 6), see installer/wine/README.md.
#
#   bash installer/wine/build_with_wine.sh [VERSION]             # everything (first run downloads ~300 MB)
#   bash installer/wine/build_with_wine.sh [VERSION] --prepare   # only the build environment
#   WORK=/var/tmp/mo-wine bash installer/wine/build_with_wine.sh
#
# Needs: wine (64 + 32 bit: wine, wine64, wine32:i386), xvfb (Xvfb), cabextract/curl/tar,
#        x86_64-w64-mingw32-gcc (gcc-mingw-w64-x86-64-win32), 7z (p7zip-full), binutils (strip),
#        python3 with pefile (pip install pefile).  ~6 GB free disk space.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="${WORK:-/var/tmp/mo-wine}"
VERSION="${1:-1.4.0}"
STAGE="${2:-all}"
export WINEPREFIX="$WORK/prefix" WINEARCH=win64 WINEDEBUG=-all LIBGL_ALWAYS_SOFTWARE=1
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$WORK/xdg}"
PY='C:\Python311\python.exe'
ISCC='C:\InnoSetup6\ISCC.exe'
mkdir -p "$WORK/dl" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
winpath() { echo "Z:${1//\//\\}"; }

fetch() {  # url file sha256
  [ -f "$WORK/dl/$2" ] || curl -fsSL --retry 3 -o "$WORK/dl/$2" "$1"
  echo "$3  $WORK/dl/$2" | sha256sum -c --quiet
}

# A plain Xvfb display: the 32-bit Inno Setup wizard fails under xvfb-run's X authentication.
if [ -z "${DISPLAY:-}" ]; then
  Xvfb :91 -screen 0 1280x800x24 >/dev/null 2>&1 &
  XVFB=$!
  trap 'kill $XVFB 2>/dev/null || true' EXIT
  export DISPLAY=:91
  sleep 1
fi

if [ ! -f "$WINEPREFIX/system.reg" ]; then
  echo "== creating the Wine prefix (without the Mono/Gecko download prompts)"
  WINEDLLOVERRIDES="mscoree,mshtml=" wineboot -i
  wineserver -w
fi

SYS32="$WINEPREFIX/drive_c/windows/system32"
echo "== Microsoft UCRT (NumPy 2.x calls crealf(), which Wine's builtin ucrtbase lacks)"
fetch https://conda.anaconda.org/conda-forge/win-64/ucrt-10.0.22621.0-h57928b3_0.tar.bz2 ucrt.tar.bz2 \
  f29cdaf8712008f6b419b8b1a403923b00ab2504bfe0fb2ba8eb60e72d4f14c6
tar xjf "$WORK/dl/ucrt.tar.bz2" -C "$WORK/dl" ucrtbase.dll
cp "$WORK/dl/ucrtbase.dll" "$SYS32/ucrtbase.dll"
wine reg add 'HKCU\Software\Wine\DllOverrides' /v ucrtbase /d native,builtin /f >/dev/null

echo "== icuuc.dll stand-in (Qt6Core imports Windows' system ICU, which Wine lacks)"
x86_64-w64-mingw32-gcc -shared -O2 -s -o "$WORK/icuuc.dll" "$ROOT/installer/wine/icuuc_stub.c"
cp "$WORK/icuuc.dll" "$SYS32/icuuc.dll"

if [ ! -f "$WINEPREFIX/drive_c/Python311/python.exe" ]; then
  echo "== Windows Python 3.11.9 (official NuGet package)"
  fetch https://www.nuget.org/api/v2/package/python/3.11.9 python-3.11.9.nupkg \
    9283876d58c017e0e846f95b490da3bca0fc0a6ee1134b2870677cfb7eec3c67
  rm -rf "$WORK/nupkg" && mkdir "$WORK/nupkg"
  (cd "$WORK/nupkg" && python3 -m zipfile -e "$WORK/dl/python-3.11.9.nupkg" .)
  mkdir -p "$WINEPREFIX/drive_c/Python311"
  cp -r "$WORK/nupkg/tools/." "$WINEPREFIX/drive_c/Python311/"
  wine "$PY" -m ensurepip --upgrade
fi
echo "== Python packages"
wine "$PY" -m pip install --no-warn-script-location -q --upgrade pip wheel
wine "$PY" -m pip install --no-warn-script-location -q -r "$(winpath "$ROOT")\\requirements.txt" pyinstaller

if [ ! -f "$WINEPREFIX/drive_c/InnoSetup6/ISCC.exe" ]; then
  echo "== Inno Setup 6.7.3"
  fetch https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe innosetup-6.7.3.exe \
    9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732
  wine "$WORK/dl/innosetup-6.7.3.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- '/DIR=C:\InnoSetup6'
fi
if [ "$STAGE" = "--prepare" ]; then
  echo "== build environment ready: $WORK"
  exit 0
fi

echo "== helper programs bundled into the setup (Tesseract runtime, OCR data, Ghostscript)"
# the Visual C++ runtime DLLs that Ghostscript needs come from the PySide6 wheel (Microsoft redistributables)
python3 "$ROOT/tools/fetch_windows_deps.py" --cache "$WORK/dl" --out "$WORK/deps" \
  --crt-dir "$WINEPREFIX/drive_c/Python311/Lib/site-packages/PySide6"

echo "== PyInstaller (one-folder build)"
rm -rf "$WORK/src" "$WORK/dist" "$WORK/build"
mkdir "$WORK/src"
(cd "$ROOT" && tar cf - --exclude=__pycache__ --exclude=.pytest_cache --exclude=installer/Output \
   --exclude=installer/deps --exclude=installer/.deps-cache .) | (cd "$WORK/src" && tar xf -)
echo "== generating multi-size Windows Explorer icons (16..256 px)"
python3 "$WORK/src/tools/make_icons.py"
echo "== syntax check with the target Python (3.11)"
(cd "$WORK/src" && wine "$PY" -m compileall -q -f app tools > "$WORK/compileall.log" 2>&1) || {
  cat "$WORK/compileall.log"; echo "Python 3.11 cannot compile the sources (see above)"; exit 1; }
(cd "$WORK/src" && wine "$PY" -m PyInstaller installer/myoffice.spec --noconfirm --clean \
   --distpath "$(winpath "$WORK")\\dist" --workpath "$(winpath "$WORK")\\build")
echo "== embedding all 8 PE icon groups (0..7) directly into MyOffice.exe"
python3 "$WORK/src/tools/embed_exe_icons.py" "$WORK/dist/MyOffice/MyOffice.exe" "$WORK/src/resources/icons/app"
if [ -e "$WORK/dist/MyOffice/_internal/icuuc.dll" ] || [ -e "$WORK/dist/MyOffice/_internal/ucrtbase.dll" ]; then
  echo "ERROR: a Wine build-environment DLL ended up in the bundle" >&2
  exit 1
fi

echo "== smoke test of the frozen MyOffice.exe"
(cd "$WORK/src" && wine "$PY" tools/smoke_test.py "$(winpath "$WORK")\\dist\\MyOffice\\MyOffice.exe")

echo "== Inno Setup"
(cd "$WORK/src/installer" && wine "$ISCC" /Q "/DMyAppVersion=$VERSION" /DLzmaThreads=1 \
   "/DBuildDir=$(winpath "$WORK")\\dist\\MyOffice" "/DDepsDir=$(winpath "$WORK")\\deps" \
   "/O$(winpath "$WORK")\\out" setup.iss)
ls -l "$WORK/out/MyOffice_Setup.exe"
sha256sum "$WORK/out/MyOffice_Setup.exe"
