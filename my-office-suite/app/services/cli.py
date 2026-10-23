"""Command-line interface (ksomisc-compatible switches).

Examples::

    MyOffice.exe report.docx data.xlsx          open files
    MyOffice.exe --new sheet                    start with a new spreadsheet
    MyOffice.exe -setlng ur_PK                  switch UI language (like ``ksomisc -setlng``)
    MyOffice.exe --register / --unregister      file associations
    MyOffice.exe --createlink / --removelink    desktop + start-menu shortcuts
    MyOffice.exe --setservers update=https://example.com/version.json
    MyOffice.exe --repair broken.docx -o fixed.docx
    MyOffice.exe --convert report.docx --to pdf  built-in engine (Word/Excel/PowerPoint); legacy formats
    MyOffice.exe --convert a.doc --to pdf --outdir out/   ... use LibreOffice (Legacy Formats Pack)
    MyOffice.exe --self-test -o report.txt      check the installation (OCR, Ghostscript, PDF export, spelling ...)
    MyOffice.exe --uninstall                    remove MyOffice from this computer (asks first)
"""
from __future__ import annotations

import argparse
import sys

from app.version import APP_NAME, APP_VERSION

KSOMISC_ALIASES = {"-setlng": "--setlng", "-register": "--register", "-unregister": "--unregister",
                   "-createlink": "--createlink", "-removelink": "--removelink", "-setservers": "--setservers",
                   "-repair": "--repair", "-uninstall": "--uninstall"}
SERVER_KEYS = ("sync", "update", "templates", "collab")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="MyOffice", description=f"{APP_NAME} {APP_VERSION} office suite")
    p.add_argument("files", nargs="*", help="documents to open")
    p.add_argument("--new", choices=["writer", "sheet", "slide", "pdf", "photos"], help="create a new document")
    p.add_argument("--setlng", metavar="LANG", help="set UI language: en_US, ur_PK, hi_IN")
    p.add_argument("--register", action="store_true", help="register file associations for this user")
    p.add_argument("--unregister", action="store_true", help="remove file associations")
    p.add_argument("--createlink", action="store_true", help="create desktop and start-menu shortcuts")
    p.add_argument("--removelink", action="store_true", help="remove shortcuts")
    p.add_argument("--setservers", nargs="+", metavar="KEY=URL", help=f"set server URLs ({', '.join(SERVER_KEYS)})")
    p.add_argument("--repair", metavar="FILE", help="repair a damaged docx/xlsx/pptx/pdf")
    p.add_argument("-o", "--output", metavar="FILE", help="output file for --repair, report file for --self-test")
    p.add_argument("--convert", metavar="FILE",
                   help="convert a document (to PDF with MyOffice's own engines; legacy formats with LibreOffice)")
    p.add_argument("--to", metavar="EXT", help="target format for --convert (pdf, docx, xlsx, pptx, ...)")
    p.add_argument("--outdir", metavar="DIR", help="output directory for --convert")
    p.add_argument("--check-update", action="store_true", help="check the update server and exit")
    p.add_argument("--self-test", action="store_true",
                   help="check the installation (bundled OCR and Ghostscript, PDF export, spelling, equations, "
                        "password protection, .xls, Photos, recording) and exit")
    p.add_argument("--reset-config", action="store_true", help="restore default settings")
    p.add_argument("--safe-mode", action="store_true", help="start without add-ins")
    p.add_argument("--no-single-instance", action="store_true", help="always start a new process")
    p.add_argument("--uninstall", action="store_true",
                   help="remove MyOffice from this computer (shows a confirmation first)")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return p


def normalize(argv: list[str]) -> list[str]:
    return [KSOMISC_ALIASES.get(a.lower(), a) if a.startswith("-") and not a.startswith("--") else a for a in argv]


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(normalize(list(sys.argv[1:] if argv is None else argv)))


def run_headless_actions(args: argparse.Namespace) -> int | None:
    """Execute non-GUI switches.  Returns an exit code, or None to continue starting the GUI."""
    from app.services.config import cfg

    did = False
    if args.reset_config:
        import copy

        from app.services.config import DEFAULTS

        c = cfg()
        c.data = copy.deepcopy(DEFAULTS)
        c.save()
        print("Configuration reset to defaults.")
        did = True
    if args.setlng:
        from app.services.i18n import LANGUAGES, set_language

        if args.setlng not in LANGUAGES:
            print(f"Unknown language {args.setlng}. Available: {', '.join(LANGUAGES)}", file=sys.stderr)
            return 2
        set_language(args.setlng)
        print(f"UI language set to {args.setlng}.")
        did = True
    if args.setservers:
        for item in args.setservers:
            key, _, url = item.partition("=")
            if key not in SERVER_KEYS:
                print(f"Unknown server key {key!r}; use {', '.join(SERVER_KEYS)}", file=sys.stderr)
                return 2
            cfg().set(f"servers.{key}", url)
            if key == "update":
                cfg().set("update.url", url)
            if key == "collab":
                cfg().set("collab.server_url", url)
            if key == "templates":
                cfg().set("templates.catalog_url", url)
        cfg().save()
        print("Server settings saved.")
        did = True
    if args.register or args.unregister:
        from app.services import register

        try:
            done = register.register_associations() if args.register else register.unregister_associations()
        except OSError as exc:
            print(f"Registration failed: {exc}", file=sys.stderr)
            return 1
        print(("Registered: " if args.register else "Unregistered: ") + " ".join(done))
        did = True
    if args.createlink or args.removelink:
        from app.services import shortcuts

        res = shortcuts.create_shortcuts() if args.createlink else shortcuts.remove_shortcuts()
        print(("Created:\n  " if args.createlink else "Removed:\n  ") + "\n  ".join(res))
        did = True
    if args.repair:
        from app.services.repair import repair_file

        try:
            rep = repair_file(args.repair, args.output)
        except Exception as exc:
            print(f"Repair failed: {exc}", file=sys.stderr)
            return 1
        print(rep.summary())
        return 0 if rep.ok else 1
    if args.convert:
        if not args.to:
            print("--convert requires --to EXT", file=sys.stderr)
            return 2
        return _convert(args.convert, args.to, args.outdir)
    if args.self_test:
        from app.services import selftest

        return selftest.run(args.output)
    if args.check_update:
        from app.services.updater import check_for_update

        try:
            info = check_for_update(force=True)
        except Exception as exc:
            print(f"Update check failed: {exc}", file=sys.stderr)
            return 1
        print(info.message())
        return 0
    if did and not args.files and not args.new:
        return 0
    return None


def _convert(src: str, target: str, outdir: str | None) -> int:
    """--convert: PDF through MyOffice's own engines where possible, everything else through LibreOffice."""
    import os

    from app.services.office_convert import ConversionError, convert

    try:
        if target.lower().split(":", 1)[0] == "pdf":
            from app.pdf.ops import PdfOpError, to_pdf

            folder = outdir or os.path.dirname(os.path.abspath(src))
            os.makedirs(folder, exist_ok=True)
            try:
                out = to_pdf(src, os.path.join(folder, os.path.splitext(os.path.basename(src))[0] + ".pdf"))
            except PdfOpError as exc:
                raise ConversionError(str(exc)) from exc
        else:
            out = convert(src, target, outdir)
    except ConversionError as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1
    print(out)
    return 0
