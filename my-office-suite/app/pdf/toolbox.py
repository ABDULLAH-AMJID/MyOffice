"""PDF Toolbox tab: Convert, Merge, Split, Compress, OCR, Sign, Protect/Unlock, Watermark, Extract, Edit."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QRadioButton, QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from app.pdf import ops
from app.services.i18n import _t
from app.shell.dialogs import get_directory, get_open_file, get_open_files, get_save_file, show_error
from app.shell.icons import icon

PDF_FILTER = "PDF (*.pdf)"


class FileList(QWidget):
    """Reorderable list of input files with drag & drop."""

    def __init__(self, filters: str, multi: bool = True):
        super().__init__()
        self.filters = filters
        self.multi = multi
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.setMinimumHeight(110)
        self.setAcceptDrops(True)
        v.addWidget(self.list)
        row = QHBoxLayout()
        for ic, tip, fn in (("plus", _t("Add files"), self.add_dialog), ("delete", _t("Remove"), self.remove),
                            ("up", _t("Move up"), lambda: self.move(-1)), ("down", _t("Move down"), lambda: self.move(1)),
                            ("close", _t("Clear"), self.list.clear)):
            b = QToolButton()
            b.setIcon(icon(ic))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.hint = QLabel(_t("Drag files here or click +"))
        self.hint.setObjectName("Muted")
        row.addWidget(self.hint)
        v.addLayout(row)

    def add(self, paths):
        for p in paths:
            if not self.multi:
                self.list.clear()
            it = QListWidgetItem(icon("pdf" if p.lower().endswith(".pdf") else "open"), os.path.basename(p))
            it.setToolTip(p)
            it.setData(Qt.UserRole, p)
            self.list.addItem(it)

    def add_dialog(self):
        paths = get_open_files(self, _t("Add files"), self.filters) if self.multi else [get_open_file(self, _t("Choose file"), self.filters)]
        self.add([p for p in paths if p])

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def move(self, d):
        r = self.list.currentRow()
        if r < 0 or not 0 <= r + d < self.list.count():
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)

    def files(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])


class OutputPicker(QWidget):
    def __init__(self, folder: bool = True):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(_t("Same folder as the input"))
        h.addWidget(self.edit, 1)
        b = QPushButton(_t("Browse\u2026"))
        b.clicked.connect(self._pick)
        h.addWidget(b)

    def _pick(self):
        d = get_directory(self, _t("Output folder"))
        if d:
            self.edit.setText(d)

    def folder_for(self, src: str) -> str:
        d = self.edit.text().strip() or os.path.dirname(os.path.abspath(src))
        os.makedirs(d, exist_ok=True)
        return d


# ============================================================================= dialogs shared with the editor
class WatermarkDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_t("Watermark"))
        f = QFormLayout(self)
        self.text = QLineEdit(_t("CONFIDENTIAL"))
        self.image = QLineEdit()
        b = QPushButton(_t("Image\u2026"))
        b.clicked.connect(lambda: self.image.setText(get_open_file(self, _t("Watermark image"), "Images (*.png *.jpg)") or self.image.text()))
        row = QHBoxLayout()
        row.addWidget(self.image, 1)
        row.addWidget(b)
        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.05, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setValue(0.25)
        self.rotation = QSpinBox()
        self.rotation.setRange(-180, 180)
        self.rotation.setValue(45)
        self.size = QSpinBox()
        self.size.setRange(8, 200)
        self.size.setValue(60)
        self.color = QComboBox()
        for name, c in ((_t("Gray"), "#888888"), (_t("Red"), "#d93025"), (_t("Blue"), "#1a56db"), (_t("Green"), "#188038")):
            self.color.addItem(name, c)
        self.tile = QCheckBox(_t("Repeat across the page"))
        self.behind = QCheckBox(_t("Place behind page content"))
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(_t("All pages (or e.g. 1-3, 5)"))
        for label, w in ((_t("Text:"), self.text), (_t("Image:"), row), (_t("Opacity:"), self.opacity),
                         (_t("Rotation:"), self.rotation), (_t("Font size:"), self.size), (_t("Color:"), self.color),
                         ("", self.tile), ("", self.behind), (_t("Pages:"), self.pages)):
            f.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def options(self) -> dict:
        return dict(text=self.text.text().strip(), image=self.image.text().strip() or None, opacity=self.opacity.value(),
                    rotation=self.rotation.value(), font_size=self.size.value(), color=self.color.currentData(),
                    tile=self.tile.isChecked(), behind=self.behind.isChecked(), pages=self.pages.text().strip())


class SignDialog(QDialog):
    """Digital (PAdES) signature with a PKCS#12 certificate, or a handwritten signature image."""

    def __init__(self, parent, page: int = 0, rect=None, page_height: float = 842.0):
        super().__init__(parent)
        self.setWindowTitle(_t("Sign PDF"))
        self.page_index = page
        self.rect = rect
        self.page_height = page_height
        self.resize(520, 380)
        f = QFormLayout(self)
        self.kind_digital = QRadioButton(_t("Digital signature (certificate, tamper-evident)"))
        self.kind_image = QRadioButton(_t("Signature image (Fill & Sign)"))
        self.kind_digital.setChecked(True)
        f.addRow(self.kind_digital)
        f.addRow(self.kind_image)
        self.cert = QLineEdit()
        b = QPushButton(_t("Browse\u2026"))
        b.clicked.connect(lambda: self.cert.setText(get_open_file(self, _t("Certificate"), "PKCS#12 (*.p12 *.pfx)") or self.cert.text()))
        nb = QPushButton(_t("Create\u2026"))
        nb.clicked.connect(self._create_cert)
        row = QHBoxLayout()
        row.addWidget(self.cert, 1)
        row.addWidget(b)
        row.addWidget(nb)
        f.addRow(_t("Certificate:"), row)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        f.addRow(_t("Certificate password:"), self.password)
        self.reason = QLineEdit(_t("I approve this document"))
        self.location = QLineEdit()
        f.addRow(_t("Reason:"), self.reason)
        f.addRow(_t("Location:"), self.location)
        self.visible = QCheckBox(_t("Visible signature box"))
        self.visible.setChecked(rect is not None)
        f.addRow(self.visible)
        self.image = QLineEdit()
        ib = QPushButton(_t("Browse\u2026"))
        ib.clicked.connect(lambda: self.image.setText(get_open_file(self, _t("Signature image"), "Images (*.png *.jpg)") or self.image.text()))
        irow = QHBoxLayout()
        irow.addWidget(self.image, 1)
        irow.addWidget(ib)
        f.addRow(_t("Signature image:"), irow)
        self.page = QSpinBox()
        self.page.setRange(1, 99999)
        self.page.setValue(page + 1)
        f.addRow(_t("Page:"), self.page)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _create_cert(self):
        from PySide6.QtWidgets import QInputDialog

        from app.services.config import cfg

        name, ok = QInputDialog.getText(self, _t("Create Certificate"), _t("Your name:"), text=cfg().get("author", ""))
        if not ok or not name.strip():
            return
        email, _ok = QInputDialog.getText(self, _t("Create Certificate"), _t("E-mail (optional):"))
        pw, ok = QInputDialog.getText(self, _t("Create Certificate"), _t("Password to protect the certificate:"),
                                      QLineEdit.Password)
        if not ok:
            return
        path = get_save_file(self, _t("Save Certificate"), "PKCS#12 (*.p12)", f"{name.strip().replace(' ', '_')}.p12", "p12")
        if not path:
            return
        try:
            ops.create_self_signed_p12(path, name.strip(), email.strip(), pw)
        except Exception as exc:
            show_error(self, _t("Create Certificate"), str(exc))
            return
        self.cert.setText(path)
        self.password.setText(pw)

    def apply(self, src: str, out: str):
        page = self.page.value() - 1
        r = self.rect
        if self.kind_image.isChecked():
            if not self.image.text():
                raise ops.PdfOpError(_t("Choose a signature image."))
            import pymupdf

            rect = r or pymupdf.Rect(360, self.page_height - 150, 560, self.page_height - 80)
            return ops.stamp_signature_image(src, out, self.image.text(), page, tuple(rect))
        if not self.cert.text():
            raise ops.PdfOpError(_t("Choose or create a certificate."))
        box = None
        if self.visible.isChecked():
            import pymupdf

            rr = r or pymupdf.Rect(360, self.page_height - 150, 560, self.page_height - 80)
            box = (rr.x0, self.page_height - rr.y1, rr.x1, self.page_height - rr.y0)  # PDF user space (bottom-left)
        return ops.sign_pdf(src, out, self.cert.text(), self.password.text(), page, box, self.reason.text(),
                            self.location.text())


# ============================================================================= tool forms
class ToolForm(QWidget):
    title = ""
    description = ""
    input_filter = PDF_FILTER
    multi = True

    def __init__(self, toolbox):
        super().__init__()
        self.toolbox = toolbox
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(16, 12, 16, 12)
        h = QLabel(self.title)
        h.setObjectName("H1")
        self.v.addWidget(h)
        d = QLabel(self.description)
        d.setObjectName("Muted")
        d.setWordWrap(True)
        self.v.addWidget(d)
        self.files = FileList(self.input_filter, self.multi)
        self.v.addWidget(QLabel(_t("Input files:")))
        self.v.addWidget(self.files)
        self.form = QFormLayout()
        self.v.addLayout(self.form)
        self.output = OutputPicker()
        self.form.addRow(_t("Output folder:"), self.output)
        self.build()
        row = QHBoxLayout()
        row.addStretch(1)
        self.run_btn = QPushButton(_t("Run"))
        self.run_btn.setObjectName("Primary")
        self.run_btn.setMinimumWidth(140)
        self.run_btn.clicked.connect(self._run)
        row.addWidget(self.run_btn)
        self.v.addLayout(row)
        self.v.addStretch(1)

    def build(self):
        pass

    def out_path(self, src: str, suffix: str, ext: str = ".pdf") -> str:
        return os.path.join(self.output.folder_for(src), f"{Path(src).stem}{suffix}{ext}")

    def work(self, files: list[str], progress) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _run(self):
        from app.shell.tasks import TaskError, run_task

        files = self.files.files()
        if not files:
            show_error(self, self.title, _t("Add at least one input file."))
            return
        try:
            outs = run_task(self, self.title + "\u2026", self.work, files)
        except TaskError as exc:
            self.toolbox.log(f"\u2716 {self.title}: {exc.original}")
            show_error(self, self.title, str(exc.original))
            return
        self.toolbox.finished(self.title, outs or [])


class ConvertForm(ToolForm):
    title = _t("PDF Convert")
    description = _t("Convert Word, Excel, PowerPoint, images and text to PDF with MyOffice's own engines (old .doc, .xls "
                     "and .ppt files with the Legacy Formats Pack) \u2014 or PDF to Word, Excel, PowerPoint, images and text.")
    input_filter = ("Documents (*.pdf *.docx *.doc *.odt *.rtf *.xlsx *.xls *.ods *.csv *.pptx *.ppt *.odp *.png *.jpg "
                    "*.jpeg *.bmp *.tif *.txt *.md *.html);;All files (*)")

    def build(self):
        self.target = QComboBox()
        for label, t in ((_t("PDF (from documents/images)"), "pdf"), (_t("Word (.docx)"), "docx"), (_t("Excel (.xlsx)"), "xlsx"),
                         (_t("PowerPoint (.pptx)"), "pptx"), (_t("Images (.png)"), "png"), (_t("Text (.txt)"), "txt")):
            self.target.addItem(label, t)
        self.combine = QCheckBox(_t("Combine images into one PDF"))
        self.form.addRow(_t("Convert to:"), self.target)
        self.form.addRow(self.combine)

    def work(self, files, progress):
        t = self.target.currentData()
        outs = []
        if t == "pdf" and self.combine.isChecked() and all(f.lower().endswith(ops.IMAGE_EXTS) for f in files):
            return [ops.images_to_pdf(files, self.out_path(files[0], "_images"))]
        for i, f in enumerate(files):
            progress(int(100 * i / len(files)), os.path.basename(f))
            if t == "pdf":
                outs.append(ops.to_pdf(f, self.out_path(f, "")))
            elif not f.lower().endswith(".pdf"):
                raise ops.PdfOpError(_t("{0} is not a PDF").format(os.path.basename(f)))
            elif t == "png":
                outs.extend(ops.pdf_to_images(f, os.path.join(self.output.folder_for(f), Path(f).stem + "_images")))
            else:
                outs.append(ops.from_pdf(f, t, self.out_path(f, "", "." + t)))
        return outs


class MergeForm(ToolForm):
    title = _t("Merge PDF")
    description = _t("Combine several PDFs (in the listed order) into one document, with a bookmark per file.")

    def build(self):
        self.name = QLineEdit("merged.pdf")
        self.bookmarks = QCheckBox(_t("Add a bookmark for each file"))
        self.bookmarks.setChecked(True)
        self.form.addRow(_t("Output name:"), self.name)
        self.form.addRow(self.bookmarks)

    def work(self, files, progress):
        name = self.name.text().strip() or "merged.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        pdfs = []
        temps = []
        try:
            for f in files:
                if f.lower().endswith(".pdf"):
                    pdfs.append(f)
                else:
                    fd, tmp = tempfile.mkstemp(suffix=".pdf")
                    os.close(fd)
                    temps.append(tmp)
                    pdfs.append(ops.to_pdf(f, tmp))
            return [ops.merge_pdfs(pdfs, os.path.join(self.output.folder_for(files[0]), name),
                                   self.bookmarks.isChecked(), progress=progress)]
        finally:
            for t in temps:
                if os.path.exists(t):
                    try:
                        os.unlink(t)
                    except OSError:
                        pass


class SplitForm(ToolForm):
    title = _t("Split PDF")
    description = _t("Split a PDF by page ranges, into chunks of N pages, or at its top-level bookmarks.")
    multi = False

    def build(self):
        self.mode = QComboBox()
        self.mode.addItem(_t("By page ranges"), "ranges")
        self.mode.addItem(_t("Every N pages"), "every")
        self.mode.addItem(_t("By bookmarks"), "bookmarks")
        self.ranges = QLineEdit("1-3, 4-")
        self.every = QSpinBox()
        self.every.setRange(1, 10000)
        self.form.addRow(_t("Mode:"), self.mode)
        self.form.addRow(_t("Ranges:"), self.ranges)
        self.form.addRow(_t("N:"), self.every)

    def work(self, files, progress):
        f = files[0]
        outdir = os.path.join(self.output.folder_for(f), Path(f).stem + "_split")
        return ops.split_pdf(f, outdir, self.mode.currentData(), self.ranges.text(), self.every.value(), progress=progress)


class CompressForm(ToolForm):
    title = _t("Compress PDF")
    description = _t("Reduce file size with Ghostscript (or PyMuPDF image recompression when Ghostscript is missing).")

    def build(self):
        self.level = QComboBox()
        for label, lv in ((_t("Recommended (150 dpi)"), "ebook"), (_t("Strong (72 dpi)"), "screen"),
                          (_t("High quality (300 dpi)"), "printer"), (_t("Prepress"), "prepress"), (_t("Lossless"), "lossless")):
            self.level.addItem(label, lv)
        self.form.addRow(_t("Level:"), self.level)

    def work(self, files, progress):
        outs = []
        for i, f in enumerate(files):
            res = ops.compress_pdf(f, self.out_path(f, "_compressed"), self.level.currentData())
            self.toolbox.log(f"{os.path.basename(f)}: {res.before:,} \u2192 {res.after:,} bytes "
                             f"({res.saved_pct:.1f}% smaller, {res.method})", threaded=True)
            outs.append(res.output)
            progress(int(100 * (i + 1) / len(files)), os.path.basename(f))
        return outs


class OcrForm(ToolForm):
    title = _t("OCR")
    description = _t("Make scanned PDFs searchable or extract text from images with Tesseract (Urdu + English by default).")
    input_filter = "Scans (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All files (*)"

    def build(self):
        self.lang = QComboBox()
        self.lang.setEditable(True)
        langs = ops.ocr_languages()
        base_items = [("urd+eng", "Urdu + English"), ("eng", "English"), ("urd", "Urdu"), ("hin+eng", "Hindi + English")]
        preferred = [code for code, _lbl in ops.usable_ocr_choices(base_items)] if hasattr(ops, "usable_ocr_choices") else []
        for opt in preferred + [l for l in langs if l not in ("osd",)] + ["urd+eng", "eng", "urd", "hin+eng"]:
            if opt and self.lang.findText(opt) < 0:
                self.lang.addItem(opt)
        self.output_kind = QComboBox()
        self.output_kind.addItem(_t("Searchable PDF"), "pdf")
        self.output_kind.addItem(_t("Text file (.txt)"), "txt")
        self.dpi = QSpinBox()
        self.dpi.setRange(100, 600)
        self.dpi.setValue(300)
        status = QLabel(_t("Installed languages: {0}").format(", ".join(langs) if langs else _t("Tesseract not found")))
        status.setObjectName("Muted")
        self.form.addRow(_t("Languages:"), self.lang)
        self.form.addRow(_t("Output:"), self.output_kind)
        self.form.addRow(_t("Resolution (dpi):"), self.dpi)
        self.form.addRow(status)

    def work(self, files, progress):
        outs = []
        for f in files:
            if self.output_kind.currentData() == "txt":
                outs.append(ops.ocr_to_text(f, self.out_path(f, "_ocr", ".txt"), self.lang.currentText(), self.dpi.value(), progress))
            else:
                tmp_img_pdf = None
                try:
                    if f.lower().endswith(".pdf"):
                        src = f
                    else:
                        fd, tmp_img_pdf = tempfile.mkstemp(suffix=".pdf")
                        os.close(fd)
                        src = ops.images_to_pdf([f], tmp_img_pdf)
                    res = ops.ocr_pdf(src, self.out_path(f, "_searchable"), self.lang.currentText(), self.dpi.value(), progress=progress)
                    self.toolbox.log(f"{os.path.basename(f)}: {res['ocr_pages']} page(s) recognised, "
                                     f"{res['skipped_pages']} already had text", threaded=True)
                    outs.append(res["output"])
                finally:
                    if tmp_img_pdf and os.path.exists(tmp_img_pdf):
                        try:
                            os.unlink(tmp_img_pdf)
                        except OSError:
                            pass
        return outs


class SignForm(ToolForm):
    title = _t("Sign PDF")
    description = _t("Apply a cryptographic (PAdES) signature with a PKCS#12 certificate \u2014 or create a personal "
                     "certificate \u2014 and verify existing signatures.")
    multi = False

    def build(self):
        self.mode = QComboBox()
        self.mode.addItem(_t("Sign"), "sign")
        self.mode.addItem(_t("Verify signatures"), "verify")
        self.form.addRow(_t("Action:"), self.mode)

    def _run(self):
        files = self.files.files()
        if not files:
            show_error(self, self.title, _t("Add a PDF."))
            return
        f = files[0]
        if self.mode.currentData() == "verify":
            try:
                sigs = ops.verify_signatures(f)
            except Exception as exc:
                show_error(self, self.title, str(exc))
                return
            if not sigs:
                self.toolbox.log(f"{os.path.basename(f)}: " + _t("no signatures"))
            for s in sigs:
                self.toolbox.log(f"{os.path.basename(f)} [{s.field}] {'VALID' if s.intact and s.valid else 'INVALID'} \u2014 "
                                 f"{s.signer} \u2014 coverage {s.coverage}")
            return
        import pymupdf

        with pymupdf.open(f) as d0:
            h = d0[0].rect.height
        d = SignDialog(self, 0, None, h)
        if d.exec():
            out = self.out_path(f, "_signed")
            try:
                d.apply(f, out)
            except Exception as exc:
                show_error(self, self.title, str(exc))
                return
            self.toolbox.finished(self.title, [out])


class ProtectForm(ToolForm):
    title = _t("Protect / Unlock")
    description = _t("Encrypt PDFs with AES-256 and permissions, or remove a known password.")

    def build(self):
        self.mode = QComboBox()
        self.mode.addItem(_t("Protect with password"), "protect")
        self.mode.addItem(_t("Remove password"), "unlock")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.owner = QLineEdit()
        self.owner.setEchoMode(QLineEdit.Password)
        self.owner.setPlaceholderText(_t("Optional: different permissions password"))
        self.print_ = QCheckBox(_t("Allow printing"))
        self.print_.setChecked(True)
        self.copy_ = QCheckBox(_t("Allow copying text"))
        self.modify = QCheckBox(_t("Allow editing"))
        self.annot = QCheckBox(_t("Allow comments and form filling"))
        self.annot.setChecked(True)
        for label, w in ((_t("Action:"), self.mode), (_t("Password:"), self.pw), (_t("Owner password:"), self.owner),
                         ("", self.print_), ("", self.copy_), ("", self.modify), ("", self.annot)):
            self.form.addRow(label, w)

    def work(self, files, progress):
        if not self.pw.text():
            raise ops.PdfOpError(_t("Enter a password."))
        outs = []
        for f in files:
            if self.mode.currentData() == "protect":
                outs.append(ops.protect_pdf(f, self.out_path(f, "_protected"), self.pw.text(), self.owner.text() or None,
                                            self.print_.isChecked(), self.modify.isChecked(), self.copy_.isChecked(),
                                            self.annot.isChecked()))
            else:
                outs.append(ops.unlock_pdf(f, self.out_path(f, "_unlocked"), self.pw.text()))
        return outs


class WatermarkForm(ToolForm):
    title = _t("Watermark")
    description = _t("Stamp a text (Urdu supported) or image watermark on every page.")

    def build(self):
        self.dialog = WatermarkDialog(self)
        b = QPushButton(_t("Watermark options\u2026"))
        b.clicked.connect(self.dialog.exec)
        self.summary = QLabel()
        self.form.addRow(b, self.summary)

    def work(self, files, progress):
        opts = self.dialog.options()
        return [ops.watermark_pdf(f, self.out_path(f, "_watermarked"), progress=progress, **opts) for f in files]


class ExtractForm(ToolForm):
    title = _t("Extract")
    description = _t("Extract text, images, tables (to Excel) or selected pages.")

    def build(self):
        self.what = QComboBox()
        for label, k in ((_t("Text (.txt)"), "text"), (_t("Images"), "images"), (_t("Tables (.xlsx)"), "tables"),
                         (_t("Pages (new PDF)"), "pages")):
            self.what.addItem(label, k)
        self.pages = QLineEdit("1")
        self.form.addRow(_t("Extract:"), self.what)
        self.form.addRow(_t("Pages (for page extraction):"), self.pages)

    def work(self, files, progress):
        outs = []
        k = self.what.currentData()
        for f in files:
            if k == "text":
                outs.append(ops.pdf_to_text(f, self.out_path(f, "", ".txt")))
            elif k == "images":
                outs.extend(ops.extract_images(f, os.path.join(self.output.folder_for(f), Path(f).stem + "_images"), progress=progress))
            elif k == "tables":
                outs.append(ops.extract_tables(f, self.out_path(f, "_tables", ".xlsx")))
            else:
                outs.append(ops.extract_pages(f, self.out_path(f, "_pages"), self.pages.text()))
        return outs


class EditForm(ToolForm):
    title = _t("Edit PDF")
    description = _t("Open a PDF in the editor to change text, add text/images/shapes, annotate, redact and organise pages.")
    multi = False

    def _run(self):
        files = self.files.files()
        if not files:
            files = [get_open_file(self, _t("Open PDF"), PDF_FILTER)]
        if files and files[0]:
            doc = self.toolbox.main.open_path(files[0])
            if doc is not None and hasattr(doc, "enter_edit_mode"):
                doc.enter_edit_mode()


class PageNumbersForm(ToolForm):
    title = _t("Page Numbers")
    description = _t("Stamp clean vector page numbers or Bates numbers onto selected pages.")

    def build(self):
        self.fmt = QComboBox()
        self.fmt.setEditable(True)
        for sample in ("Page {n} of {total}", "{n} / {total}", "- {n} -", "BATES-{n:06d}"):
            self.fmt.addItem(sample)
        self.pos = QComboBox()
        for label, val in (
            (_t("Bottom Center"), "bottom-center"),
            (_t("Bottom Right"), "bottom-right"),
            (_t("Bottom Left"), "bottom-left"),
            (_t("Top Right"), "top-right"),
            (_t("Top Center"), "top-center"),
            (_t("Top Left"), "top-left"),
        ):
            self.pos.addItem(label, val)
        self.start = QSpinBox()
        self.start.setRange(1, 999999)
        self.start.setValue(1)
        self.pages = QLineEdit("")
        self.pages.setPlaceholderText(_t("All pages (or e.g. 2-10)"))
        self.form.addRow(_t("Format:"), self.fmt)
        self.form.addRow(_t("Position:"), self.pos)
        self.form.addRow(_t("Start number:"), self.start)
        self.form.addRow(_t("Pages:"), self.pages)

    def work(self, files, progress):
        return [
            ops.add_page_numbers_pdf(
                f,
                self.out_path(f, "_numbered"),
                fmt=self.fmt.currentText() or "Page {n} of {total}",
                position=self.pos.currentData(),
                start_at=self.start.value(),
                pages=self.pages.text().strip(),
                progress=progress,
            )
            for f in files
        ]


class StampForm(ToolForm):
    title = _t("Stamp PDF")
    description = _t("Apply crisp vector commercial stamps (APPROVED, CONFIDENTIAL, PAID, DRAFT, FINAL, or custom).")

    def build(self):
        self.label = QComboBox()
        self.label.setEditable(True)
        for preset in ("APPROVED", "CONFIDENTIAL", "PAID", "DRAFT", "FINAL", "REVIEWED", "VOID"):
            self.label.addItem(preset)
        self.sub = QLineEdit("")
        self.sub.setPlaceholderText(_t("Optional subtitle (e.g. date, department, or signer name)"))
        self.color = QComboBox()
        for cname, hexv in (
            (_t("Crimson Red"), "#DC2626"),
            (_t("Emerald Green"), "#16A34A"),
            (_t("Royal Blue"), "#2563EB"),
            (_t("Amber Gold"), "#D97706"),
            (_t("Slate"), "#334155"),
        ):
            self.color.addItem(cname, hexv)
        self.pos = QComboBox()
        for pname, pval in (
            (_t("Top Right"), "top-right"),
            (_t("Top Left"), "top-left"),
            (_t("Bottom Right"), "bottom-right"),
            (_t("Bottom Left"), "bottom-left"),
            (_t("Center"), "middle-center"),
        ):
            self.pos.addItem(pname, pval)
        self.pages = QLineEdit("1")
        self.form.addRow(_t("Stamp text:"), self.label)
        self.form.addRow(_t("Subtitle:"), self.sub)
        self.form.addRow(_t("Ink color:"), self.color)
        self.form.addRow(_t("Position:"), self.pos)
        self.form.addRow(_t("Pages:"), self.pages)

    def work(self, files, progress):
        return [
            ops.stamp_pdf(
                f,
                self.out_path(f, "_stamped"),
                label=self.label.currentText() or "APPROVED",
                subtitle=self.sub.text().strip(),
                color=self.color.currentData(),
                position=self.pos.currentData(),
                pages=self.pages.text().strip(),
                progress=progress,
            )
            for f in files
        ]


class FlattenForm(ToolForm):
    title = _t("Flatten PDF")
    description = _t("Permanently bake interactive form fields, annotations, highlights, and stamps into static PDF pages.")

    def work(self, files, progress):
        return [ops.flatten_pdf(f, self.out_path(f, "_flattened"), progress=progress) for f in files]


TOOLS = [("convert", "convert", ConvertForm), ("merge", "merge_pdf", MergeForm), ("split", "split_pdf", SplitForm),
         ("compress", "compress", CompressForm), ("ocr", "ocr", OcrForm), ("sign", "sign", SignForm),
         ("protect", "lock", ProtectForm), ("watermark", "watermark", WatermarkForm), ("extract", "extract", ExtractForm),
         ("page_numbers", "list_num", PageNumbersForm), ("stamp", "stamp", StampForm),
         ("flatten", "layers", FlattenForm), ("edit", "edit", EditForm)]


class PdfToolbox(QWidget):
    def __init__(self, main):
        super().__init__()
        from PySide6.QtWidgets import QScrollArea

        self.main = main
        self.setObjectName("HomePage")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QWidget()
        side.setObjectName("SidePanel")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 12, 8, 8)
        t = QLabel(_t("PDF Toolbox"))
        t.setObjectName("H2")
        sv.addWidget(t)
        self.nav = QListWidget()
        self.nav.setIconSize(QSize(22, 22))
        self.nav.setFrameShape(QListWidget.NoFrame)
        sv.addWidget(self.nav, 1)
        h.addWidget(side)
        right = QVBoxLayout()
        self.stack = QStackedWidget()
        self.forms = {}
        for key, ic, cls in TOOLS:
            form = cls(self)
            self.forms[key] = form
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.NoFrame)
            sc.setWidget(form)
            self.stack.addWidget(sc)
            it = QListWidgetItem(icon(ic, "#d92d20"), form.title)
            it.setData(Qt.UserRole, key)
            self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        right.addWidget(self.stack, 1)
        logbox = QGroupBox(_t("Results"))
        lv = QVBoxLayout(logbox)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumHeight(130)
        lv.addWidget(self.logview)
        row = QHBoxLayout()
        self.open_btn = QPushButton(_t("Open Result"))
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_result)
        self.folder_btn = QPushButton(_t("Open Folder"))
        self.folder_btn.setEnabled(False)
        self.folder_btn.clicked.connect(self._open_folder)
        row.addStretch(1)
        row.addWidget(self.folder_btn)
        row.addWidget(self.open_btn)
        lv.addLayout(row)
        right.addWidget(logbox)
        rw = QWidget()
        rw.setLayout(right)
        h.addWidget(rw, 1)
        self.last_outputs: list[str] = []
        self.nav.setCurrentRow(0)

    def select(self, key: str):
        keys = [k for k, _i, _c in TOOLS]
        if key in keys:
            self.nav.setCurrentRow(keys.index(key))

    def log(self, text: str, threaded: bool = False):
        if threaded:
            from PySide6.QtCore import QMetaObject, Q_ARG

            QMetaObject.invokeMethod(self.logview, "appendPlainText", Qt.QueuedConnection, Q_ARG(str, text))
        else:
            self.logview.appendPlainText(text)

    def finished(self, title: str, outputs: list[str]):
        self.last_outputs = [o for o in outputs if o]
        self.log(f"\u2714 {title}: {len(self.last_outputs)} file(s)")
        for o in self.last_outputs[:20]:
            self.log("   " + o)
        self.open_btn.setEnabled(bool(self.last_outputs))
        self.folder_btn.setEnabled(bool(self.last_outputs))

    def _open_result(self):
        if not self.last_outputs:
            return
        first = self.last_outputs[0]
        if self.main.kind_for(first):
            self.main.open_path(first)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(first))

    def _open_folder(self):
        if self.last_outputs:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.last_outputs[0])))

    def close_request(self) -> bool:
        return True


def _open(main, key):
    tb = main.open_toolbox()
    tb.select(key)
    return tb


def open_convert(main):
    return _open(main, "convert")


def open_merge(main):
    return _open(main, "merge")


def open_split(main):
    return _open(main, "split")


def open_compress(main):
    return _open(main, "compress")


def open_ocr(main):
    return _open(main, "ocr")


def open_sign(main):
    return _open(main, "sign")


def open_protect(main):
    return _open(main, "protect")


def open_watermark(main):
    return _open(main, "watermark")


def open_extract(main):
    return _open(main, "extract")


def open_page_numbers(main):
    return _open(main, "page_numbers")


def open_stamp(main):
    return _open(main, "stamp")


def open_flatten(main):
    return _open(main, "flatten")


__all__ = ["PdfToolbox", "SignDialog", "WatermarkDialog", "QRadioButton"]
