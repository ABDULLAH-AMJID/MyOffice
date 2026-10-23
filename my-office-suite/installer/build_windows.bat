@echo off
REM ---------------------------------------------------------------------------
REM  Build MyOffice_Setup.exe on Windows 10/11
REM  Requirements: Python 3.11 (x64) on PATH, Inno Setup 6 (iscc.exe), 7-Zip (7z.exe)
REM  Usage: installer\build_windows.bat [version]
REM ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0\.."
set VERSION=%1
if "%VERSION%"=="" set VERSION=1.2.0

echo [1/7] Creating virtual environment
if not exist .venv-build python -m venv .venv-build || goto :error
call .venv-build\Scripts\activate.bat

echo [2/7] Installing dependencies
python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements-dev.txt || goto :error

echo [3/7] Building translations, icons and templates
python tools\build_translations.py || goto :error
set QT_QPA_PLATFORM=offscreen
python tools\make_icons.py || goto :error
python tools\make_templates.py || goto :error
set QT_QPA_PLATFORM=

echo [4/7] Running the test-suite
python -m pytest -q tests || goto :error

echo [5/7] PyInstaller one-folder build
python -m PyInstaller installer\myoffice.spec --noconfirm --clean || goto :error

REM open a docx/xlsx/pptx/pdf with the frozen exe - catches modules missing from the bundle
python tools\smoke_test.py dist\MyOffice\MyOffice.exe || goto :error

echo [6/7] Helper programs carried inside the setup (Tesseract runtime, OCR data, Ghostscript)
REM downloads pinned + SHA-256 checked releases once (cached in installer\.deps-cache), keeps only the files used
python tools\fetch_windows_deps.py --out installer\deps || goto :error

REM installed size check (< 500 MB): the program folder plus the bundled helper programs
for /f "usebackq" %%s in (`powershell -NoProfile -Command "(Get-ChildItem -Recurse dist\MyOffice, installer\deps | Measure-Object -Sum Length).Sum"`) do set DISTBYTES=%%s
echo Installed size: %DISTBYTES% bytes
powershell -NoProfile -Command "if (%DISTBYTES% -gt 500MB) { Write-Error 'Installed size exceeds 500 MB'; exit 1 }" || goto :error

echo [7/7] Inno Setup
set ISCC=iscc
where iscc >nul 2>nul || set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
%ISCC% /DMyAppVersion=%VERSION% installer\setup.iss || goto :error

for %%f in (installer\Output\MyOffice_Setup.exe) do set SETUPBYTES=%%~zf
echo Installer size: %SETUPBYTES% bytes
powershell -NoProfile -Command "if (%SETUPBYTES% -gt 150MB) { Write-Error 'Installer exceeds 150 MB'; exit 1 }" || goto :error

echo.
echo Done: installer\Output\MyOffice_Setup.exe
exit /b 0

:error
echo.
echo BUILD FAILED (error %errorlevel%)
exit /b 1
