"""Urdu Digits add-in: a custom spreadsheet function and a Writer command."""
URDU = str.maketrans("0123456789", "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9")
WESTERN = str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "0123456789")


def urdunum(value, decimals=None):
    from app.sheet.formula.values import MISSING, scalar, to_number, to_text

    v = scalar(value)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        d = None if decimals is MISSING or decimals is None else int(to_number(decimals))
        text = f"{v:,.{d}f}" if d is not None else (str(int(v)) if float(v).is_integer() else f"{v:g}")
    else:
        text = to_text(v)
    return text.translate(URDU).replace(",", "\u066c").replace(".", "\u066b")


def convert_writer(doc):
    total = 0
    for d in "0123456789":
        total += doc.replace_all(d, d.translate(URDU))
    doc.statusChanged.emit(f"Converted {total} digit(s) to Urdu digits")


def register(api):
    api.register_sheet_function("URDUNUM", urdunum, 1, 2, syntax="URDUNUM(value, [decimals])",
                                help="Shows a number with Urdu digits.")
    api.add_ribbon_action("writer", "Urdu Digits", convert_writer, icon="number", tooltip="Convert 0-9 to Urdu digits")


def unregister(api):
    pass
