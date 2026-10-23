"""Build Qt translation catalogs.

1. extracts every ``_t("...")`` string from the application sources,
2. writes ``resources/langs/<lang>.ts`` (Qt Linguist format) with the translations found in
   ``resources/langs/src/<lang>.json`` (untranslated strings are marked "unfinished" and fall back
   to English at runtime),
3. compiles ``.qm`` files with ``pyside6-lrelease`` (or Qt's ``lrelease``).

Run:  python tools/build_translations.py
Translators can open the .ts files in Qt Linguist and re-run this script.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
LANGS = {"en_US": "en_US", "ur_PK": "ur_PK", "hi_IN": "hi_IN"}
PATTERN = re.compile(r'_t\(\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\s*\)')


def extract() -> dict[str, list[str]]:
    """Every ``_t("...")`` literal in app/ - parsed with ``ast`` so implicitly concatenated literals
    (``_t("first part " "second part")``) are extracted exactly as Python joins them at runtime."""
    found: dict[str, list[str]] = {}
    for p in sorted((ROOT / "app").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:  # fall back to the plain pattern
            for m in PATTERN.finditer(text):
                try:
                    s = ast.literal_eval(m.group(1))
                except Exception:
                    continue
                found.setdefault(s, []).append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}")
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_t"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, []).append(f"{rel}:{node.lineno}")
    for locs in found.values():
        locs.sort(key=lambda loc: (loc.rsplit(":", 1)[0], int(loc.rsplit(":", 1)[1])))
    return found


def write_ts(lang: str, strings: dict[str, list[str]], translations: dict[str, str]) -> Path:
    out = ROOT / "resources" / "langs" / f"{lang}.ts"
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<!DOCTYPE TS>",
             f'<TS version="2.1" language="{lang}" sourcelanguage="en_US">', "<context>", "    <name>MyOffice</name>"]
    done = 0
    for src in sorted(strings):
        tr = translations.get(src) if lang != "en_US" else None
        locs = strings[src][:3]
        lines.append("    <message>")
        for loc in locs:
            fname, ln = loc.rsplit(":", 1)
            lines.append(f'        <location filename="../../{escape(fname)}" line="{ln}"/>')
        lines.append(f"        <source>{escape(src)}</source>")
        if tr:
            lines.append(f"        <translation>{escape(tr)}</translation>")
            done += 1
        else:
            lines.append('        <translation type="unfinished"></translation>')
        lines.append("    </message>")
    lines += ["</context>", "</TS>", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{lang}: {done}/{len(strings)} strings translated -> {out.name}")
    return out


def lrelease() -> str | None:
    for name in ("pyside6-lrelease", "lrelease", "lrelease-qt6"):
        p = shutil.which(name)
        if p:
            return p
    try:
        import PySide6

        cand = Path(PySide6.__file__).parent / ("lrelease.exe" if sys.platform == "win32" else "lrelease")
        if cand.exists():
            return str(cand)
    except ImportError:
        pass
    return None


def main() -> int:
    strings = extract()
    print(f"{len(strings)} translatable strings found")
    # Strings translated through a variable (``_t(tool.title)``, category names ...) cannot be found in
    # the code; every key of the translation sources is therefore compiled into the catalogs as well.
    dynamic = set()
    for lang in LANGS:
        src = ROOT / "resources" / "langs" / "src" / f"{lang}.json"
        if src.exists():
            dynamic |= set(json.loads(src.read_text(encoding="utf-8"))) - set(strings)
    for key in sorted(dynamic):
        strings[key] = ["app/(used through a variable):0"]
    if dynamic:
        print(f"  + {len(dynamic)} strings used through variables (from the translation sources)")
    tool = lrelease()
    if tool is None:
        print("lrelease not found (install PySide6 or Qt Linguist tools)", file=sys.stderr)
    for lang in LANGS:
        src = ROOT / "resources" / "langs" / "src" / f"{lang}.json"
        translations = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
        ts = write_ts(lang, strings, translations)
        if tool:
            qm = ts.with_suffix(".qm")
            r = subprocess.run([tool, str(ts), "-qm", str(qm)], capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout, r.stderr, file=sys.stderr)
                return 1
            print(f"  compiled {qm.name} ({qm.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
