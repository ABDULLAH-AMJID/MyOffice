# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MyOffice (one-folder build, required for LGPL-compliant Qt deployment).
#   pyinstaller installer/myoffice.spec --noconfirm
import ast
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


def app_modules() -> list:
    """Every module of the ``app`` package, found by walking the source tree.

    The shell imports editors and tools lazily (importlib), so static analysis alone misses them.  We must not use
    collect_submodules("app") here: it imports the package in a subprocess *before* ``pathex`` takes effect and
    silently returns nothing when the project root is not on sys.path - the frozen app then fails on first use.
    """
    mods = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "app")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        package = os.path.relpath(dirpath, ROOT).replace(os.sep, ".")
        for fn in filenames:
            if fn.endswith(".py"):
                mods.append(package if fn == "__init__.py" else f"{package}.{fn[:-3]}")
    mods.remove("app.main")  # the entry script itself
    return sorted(mods)


def macro_imports() -> list:
    """The stdlib modules user macros may import (app/macros/api.py ALLOWED_IMPORTS) - they must be bundled even if
    nothing else in MyOffice imports them."""
    with open(os.path.join(ROOT, "app", "macros", "api.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ALLOWED_IMPORTS" for t in node.targets):
            return sorted(ast.literal_eval(node.value))
    raise SystemExit("ALLOWED_IMPORTS not found in app/macros/api.py")


APP_MODULES = app_modules()
assert len(APP_MODULES) > 80 and "app.sheet.widget" in APP_MODULES, APP_MODULES
print(f"MyOffice spec: {len(APP_MODULES)} app modules, macro stdlib modules: {macro_imports()}")

hidden = (APP_MODULES + macro_imports() + collect_submodules("pyhanko") + collect_submodules("pyhanko_certvalidator")
          + ["websockets.asyncio.client", "websockets.asyncio.server", "pycrdt", "pymupdf", "pytesseract",
             "arabic_reshaper", "jsonschema", "watchdog.observers", "boto3", "matplotlib.backends.backend_agg",
             "openpyxl.cell._writer", "PySide6.QtSvg", "PySide6.QtPrintSupport", "PySide6.QtNetwork",
             "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "spellchecker", "msoffcrypto",
             "msoffcrypto.format.ooxml", "olefile", "latex2mathml.converter", "xlwt", "xlrd",
             "qrcode", "qrcode.image.pil", "importlib.abc", "importlib.machinery", "importlib.util"])
if sys.platform == "win32":  # taskbar pinning (app/services/taskbar.py) - imported lazily
    import importlib.util

    _WINRT = ["winrt.runtime", "winrt.system", "winrt.windows.foundation", "winrt.windows.ui.shell"]
    _missing = [m for m in _WINRT if importlib.util.find_spec(m) is None]
    if _missing:  # PyInstaller would only log an error and ship an app without the Windows 11 pin request
        raise SystemExit(f"MyOffice spec: {_missing} not installed - run: pip install -r requirements.txt")
    hidden += _WINRT + ["comtypes", "comtypes.client", "comtypes.client.dynamic"]

datas = [
    (os.path.join(ROOT, "resources"), "resources"),
    (os.path.join(ROOT, "plugins"), "plugins"),
    (os.path.join(ROOT, "LICENSE"), "."),
    (os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), "."),
]
datas += collect_data_files("docx")          # python-docx default template
datas += collect_data_files("pptx")          # python-pptx default template
datas += collect_data_files("pyhanko")
datas += collect_data_files("jsonschema_specifications")
datas += collect_data_files("arabic_reshaper")
datas += collect_data_files("latex2mathml")     # unimathsymbols.txt
# spelling: only the English and Spanish word lists (the other languages would add ~6 MB)
datas += [d for d in collect_data_files("spellchecker") if os.path.basename(d[0]) in ("en.json.gz", "es.json.gz")]

# Qt modules / big packages MyOffice does not use (keeps the installer < 150 MB)
excludes = [
    "tkinter", "IPython", "jupyter", "notebook", "pytest", "sphinx", "PyQt5", "PyQt6",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DExtras", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_tkagg", "matplotlib.tests", "numpy.tests",
    "pandas.tests", "PIL.ImageQt",
    # optional accelerators pandas/matplotlib/pymupdf4llm 2.x would import if present - not used by MyOffice
    # (pymupdf 2.x / pymupdf4llm 2.x catch ImportError on pymupdf.layout and use pure-Python pymupdf_rag;
    #  partially bundling pymupdf.layout without its 49 MB ONNX weights raises FileNotFoundError on import!)
    "pymupdf.layout", "onnxruntime",
    "numba", "llvmlite", "scipy", "sklearn", "torch", "tensorflow", "pyarrow", "tables", "sqlalchemy", "pydantic",
    "pydantic_core", "nltk", "spacy", "bottleneck", "numexpr", "xarray", "dask", "jinja2.ext",
]

# Qt libraries pulled in by plugins but never used by a QtWidgets application
QT_DROP = ("Qt6Quick", "Qt6Qml", "Qt6VirtualKeyboard", "qtvirtualkeyboard", "Qt6Pdf", "qpdf", "Qt6Wayland",
           "Qt6WlShell", "Qt6EglFS", "Qt6EglFs", "Qt6Designer", "Qt6MultimediaQuick", "Qt6WebEngine",
           "Qt6ShaderTools", "Qt6SpatialAudio", "Qt6Quick3D",
           "Qt63D", "Qt6Charts", "Qt6DataVisualization", "Qt6Positioning", "Qt6Location", "Qt6Sensors",
           "Qt6SerialPort", "Qt6Sql", "Qt6Test", "Qt6Bluetooth", "Qt6Nfc", "Qt6RemoteObjects", "Qt6Scxml",
           "Qt6TextToSpeech", "Qt6SpatialAudio", "Qt6HttpServer", "Qt6Graphs", "opengl32sw", "Qt6Lottie")


def _keep(entry) -> bool:
    name = os.path.basename(entry[0]).lower()
    return not any(tag.lower() in name for tag in QT_DROP)

a = Analysis(
    [os.path.join(ROOT, "app", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
BOTO_KEEP = {"s3", "sts"}   # Cloud Sync only talks to S3-compatible storage


def _keep_data(entry) -> bool:
    dest = entry[0].replace("\\", "/")
    if dest.startswith("botocore/data/"):
        rest = dest[len("botocore/data/"):]
        return "/" not in rest or rest.split("/", 1)[0] in BOTO_KEEP
    return not dest.startswith("matplotlib/mpl-data/sample_data/")


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep_data(d)]
a.datas = [d for d in a.datas
           if not d[0].replace("\\", "/").startswith(("PySide6/Qt/qml", "PySide6/qml"))
           and not ("/translations/" in d[0].replace("\\", "/")
                    and not os.path.basename(d[0]).startswith(("qt_en", "qtbase_en", "qt_ar", "qtbase_ar")))]
# Fail loudly if PyInstaller's Qt hook could not inspect PySide6 (it then silently skips every Qt plugin
# and the frozen app cannot start: "no Qt platform plugin could be initialized").
_REQUIRED_QT_PLUGINS = {"win32": ("qwindows.dll", "qsvgicon.dll", "qsvg.dll", "ffmpegmediaplugin.dll"),
                        "linux": ("libqxcb.so", "libqsvgicon.so", "libqsvg.so")}.get(sys.platform, ())
_bundled = {os.path.basename(b[0]).lower() for b in a.binaries}
_missing = [p for p in _REQUIRED_QT_PLUGINS if p not in _bundled]
if _missing:
    raise SystemExit(f"Qt plugins missing from the bundle: {_missing} - the PySide6 hook could not load QtCore")
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MyOffice",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "resources", "icons", "app", "myoffice.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MyOffice")
