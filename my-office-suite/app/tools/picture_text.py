"""Picture to Text: recognise the text in photos, screenshots or scans (Urdu, Hindi, English - offline OCR) and
copy, save or translate it."""
from __future__ import annotations

from app.services.i18n import _t


def open_dialog(main) -> None:
    from app.pdf.ops import ocr_image, usable_ocr_choices
    from app.photos import ops as photo_ops
    from app.photos.widget import PhotosDocument, TextResultDialog, _ask_choice
    from app.shell.dialogs import get_open_files, show_error
    from app.shell.tasks import TaskError, run_task

    files = get_open_files(main, _t("Picture to Text"), PhotosDocument.open_filter())
    if not files:
        return
    lang, ok = _ask_choice(main, _t("Picture to Text"), _t("Language of the text:"), usable_ocr_choices(
        [("urd+eng", _t("Urdu + English")), ("eng", _t("English")), ("hin+eng", _t("Hindi + English")),
         ("urd", _t("Urdu")), ("hin", _t("Hindi"))]))
    if not ok:
        return

    def work(progress=None):
        parts = []
        for i, f in enumerate(files):
            text = ocr_image(photo_ops.load(f).convert("RGB"), lang)
            parts.append(text.strip() if len(files) == 1 else f"--- {f} ---\n{text.strip()}")
            if progress:
                progress(int(100 * (i + 1) / len(files)), "")
        return "\n\n".join(parts)
    try:
        text = run_task(main, _t("Reading the text\u2026"), work)
    except TaskError as exc:
        show_error(main, _t("Picture to Text"), str(exc.original), exc.tb)
        return
    TextResultDialog(main, text).exec()
