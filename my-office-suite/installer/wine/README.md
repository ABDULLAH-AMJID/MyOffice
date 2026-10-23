# Building the Windows installer on Linux (Wine)

`build_with_wine.sh` produces the same `MyOffice_Setup.exe` as `installer\build_windows.bat` on
Windows. It runs the **genuine Windows tools** under Wine:

| Tool | Version | Source |
|---|---|---|
| Python (Windows, x64) | 3.11.9 | official NuGet package `python` |
| PyInstaller | 6.x (pip) | PyPI, Windows wheels |
| Inno Setup | 6.7.3 | jrsoftware GitHub release (SHA-256 checked) |
| Tesseract OCR runtime | 5.5.3 | official Windows release, unpacked with 7-Zip, 34 files kept (bundled) |
| Ghostscript | 10.08.0 | official Windows release, `gswin64c.exe` + `gsdll64.dll` kept (bundled) |

```bash
sudo dpkg --add-architecture i386 && sudo apt-get update
sudo apt-get install wine wine64 wine32:i386 xvfb cabextract curl p7zip-full binutils-mingw-w64-x86-64 \
                     gcc-mingw-w64-x86-64-win32
pip install pefile
bash installer/wine/build_with_wine.sh 1.2.0             # -> $WORK/out/MyOffice_Setup.exe
bash installer/wine/build_with_wine.sh 1.2.0 --prepare   # only the build environment (prefix, Python, Inno)
```

The helper programs that go into the setup are staged by `tools/fetch_windows_deps.py` on the Linux side (it only
downloads, unpacks and copies files; nothing of it runs under Wine). The Visual C++ runtime DLLs that Ghostscript
needs are taken from the PySide6 wheel installed in the Wine Python.

## Two Wine gaps, and why neither ends up in the installer

1. **`ucrtbase.dll`: `crealf` is missing from Wine 10.** Every NumPy 2.x Windows wheel calls it
   while importing, so Wine aborts. The script installs Microsoft's own `ucrtbase.dll` (Windows SDK
   10.0.22621, from the conda-forge `ucrt` package) into the Wine prefix with a `native,builtin`
   override. This is the same thing `winetricks ucrtbase2019` does.
2. **`icuuc.dll` is missing from Wine.** `Qt6Core.dll` links against the ICU that Windows 10 1703+
   ships in System32. It only uses 20 `ucnv_*` character-set converter functions. `icuuc_stub.c`
   (compiled with MinGW) reports those converters as unavailable, which is what Qt does when ICU
   lacks a codec. Without it, `PySide6.QtCore` cannot load under Wine. Then PyInstaller's Qt hook
   **silently skips every Qt plugin** (`platforms/qwindows.dll`, the SVG icon engine…), and the
   frozen app could not start. `myoffice.spec` now fails the build if those plugins are missing.

Both DLLs live **only in the Wine prefix's `C:\windows\system32`**. PyInstaller never bundles DLLs
from the system directory, and the script checks that neither file is in `dist\MyOffice`. On
Windows, MyOffice uses the system's own UCRT and ICU (Windows 10 1809+ is the installer's minimum).

## Testing the installed app under Wine

- **Clean test prefix:** `mkdir /var/tmp/mo-test && WINEPREFIX=/var/tmp/mo-test wineboot -i`, then copy the same
  `ucrtbase.dll` (with the `native,builtin` override) and `icuuc.dll` into its `system32`.
- **Colour profile (a third Wine gap, test environment only):** Qt 6.12 reads the monitor's colour profile and then
  calls `mscms.WcsCreateIccProfile`, which Wine 10 does not implement. Wine writes
  `C:\windows\system32\spool\drivers\color\srgb color space profile.icm` when a prefix first starts with a
  display, and then every Qt GUI aborts at start-up with *"Cannot create window: no screens available"*.
  Delete that file in test prefixes. Real Windows implements the function (since Vista), and command-line runs
  (`--self-test`, `--convert`) are not affected.
- **Proving "offline":** run the setup and the app inside a network namespace that has no network interface:
  `sudo unshare -n sudo -u $USER env WINEPREFIX=… DISPLAY=:92 wine 'Z:\path\MyOffice_Setup.exe' /VERYSILENT`.
  The X display still works through its socket file in `/tmp/.X11-unix`.

## What was verified under Wine

- The frozen `MyOffice.exe` passes `tools/smoke_test.py`: it opens a docx with Urdu text, an
  xlsx with formulas, a pptx and a pdf, and writes a screenshot.
- The bundled Tesseract and Ghostscript run from the installed program folder: the installed
  `MyOffice.exe --self-test` passes (OCR, compression, built-in Office → PDF).
- The setup wizard runs end-to-end, and the installed app starts.
- Without any network: the setup installs everything it contains, and it also installs the official LibreOffice MSI
  when that file lies next to it; the Legacy Formats Pack installs from an MSI chosen with *Use Installer File…*.
- Uninstall, started from inside MyOffice and from the Start-menu entry, removes the program
  folder, data folders, registry entries and shortcuts (see TESTING.md).

## Not verified here

Wine is not Windows:
- the UAC prompt, SmartScreen and the Windows 11 taskbar-pin prompt (`TaskbarManager`) do not
  exist under Wine;
- Windows Defender is absent.

Test a release build on real Windows 10 and Windows 11 before publishing it.
