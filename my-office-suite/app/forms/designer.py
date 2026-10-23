"""Forms / Survey designer, filler and response collector."""
from __future__ import annotations

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget, QButtonGroup)

from app.forms import schema as S
from app.services.i18n import _t
from app.shell.dialogs import confirm, get_open_file, get_save_file, show_error, show_info


class FormRenderer(QWidget):
    """Builds input widgets for a form and collects/validates the answers."""

    def __init__(self, form: S.Form):
        super().__init__()
        self.form = form
        self.inputs = {}
        f = QFormLayout(self)
        if form.description:
            d = QLabel(form.description)
            d.setWordWrap(True)
            f.addRow(d)
        for fld in form.fields:
            w = self._widget(fld)
            label = QLabel(fld.label + (" *" if fld.required else ""))
            if fld.help and fld.kind != "checkbox":
                label.setToolTip(fld.help)
            f.addRow(label, w)

    def _widget(self, fld: S.FormField):
        k = fld.kind
        if k == "longtext":
            w = QPlainTextEdit()
            w.setMaximumHeight(90)
            get = lambda: w.toPlainText()
        elif k in ("number", "integer"):
            w = QDoubleSpinBox() if k == "number" else QSpinBox()
            lo = fld.minimum if fld.minimum is not None else -1e9
            hi = fld.maximum if fld.maximum is not None else 1e9
            w.setRange(int(lo) if k == "integer" else lo, int(hi) if k == "integer" else hi)
            if k == "number":
                w.setDecimals(3)
            w.setSpecialValueText(" ")
            w.setValue(w.minimum())
            get = lambda: None if w.value() == w.minimum() and w.specialValueText() else (
                float(w.value()) if k == "number" else int(w.value()))
        elif k == "date":
            w = QDateEdit(QDate.currentDate())
            w.setCalendarPopup(True)
            w.setDisplayFormat("yyyy-MM-dd")
            get = lambda: w.date().toString("yyyy-MM-dd")
        elif k == "choice":
            w = QComboBox()
            w.addItem("", "")
            for o in fld.options:
                w.addItem(str(o), str(o))
            get = lambda: w.currentData() or ""
        elif k == "multichoice":
            w = QWidget()
            h = QVBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            boxes = []
            for o in fld.options:
                cb = QCheckBox(str(o))
                h.addWidget(cb)
                boxes.append(cb)
            get = lambda: [b.text() for b in boxes if b.isChecked()]
        elif k == "checkbox":
            w = QCheckBox(fld.help or _t("Yes"))
            get = lambda: w.isChecked()
        elif k == "rating":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            grp = QButtonGroup(w)
            lo, hi = int(fld.minimum or 1), int(fld.maximum or 5)
            for i in range(lo, hi + 1):
                rb = QRadioButton("\u2605" * i if hi <= 5 else str(i))
                grp.addButton(rb, i)
                h.addWidget(rb)
            h.addStretch(1)
            get = lambda: grp.checkedId() if grp.checkedId() >= 0 else None
        else:
            w = QLineEdit()
            w.setPlaceholderText(fld.placeholder)
            get = lambda: w.text().strip()
        self.inputs[fld.key] = get
        return w

    def answers(self) -> dict:
        return {k: get() for k, get in self.inputs.items()}


class FieldEditor(QWidget):
    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        f = QFormLayout(self)
        self.label = QLineEdit()
        self.kind = QComboBox()
        for k in S.KINDS:
            self.kind.addItem(_t(k.capitalize()), k)
        self.required = QCheckBox(_t("Required"))
        self.options = QPlainTextEdit()
        self.options.setPlaceholderText(_t("One option per line"))
        self.options.setMaximumHeight(90)
        self.minimum = QLineEdit()
        self.maximum = QLineEdit()
        self.pattern = QLineEdit()
        self.help = QLineEdit()
        for label, w in ((_t("Question:"), self.label), (_t("Type:"), self.kind), ("", self.required),
                         (_t("Options:"), self.options), (_t("Minimum:"), self.minimum), (_t("Maximum:"), self.maximum),
                         (_t("Pattern (regex):"), self.pattern), (_t("Help text:"), self.help)):
            f.addRow(label, w)
        for w in (self.label, self.minimum, self.maximum, self.pattern, self.help):
            w.editingFinished.connect(self._changed)
        self.kind.currentIndexChanged.connect(self._changed)
        self.required.toggled.connect(self._changed)
        self.options.textChanged.connect(self._changed)
        self.field: S.FormField | None = None
        self._loading = False

    def load(self, fld: S.FormField | None):
        self._loading = True
        self.field = fld
        self.setEnabled(fld is not None)
        if fld is not None:
            self.label.setText(fld.label)
            self.kind.setCurrentIndex(max(0, self.kind.findData(fld.kind)))
            self.required.setChecked(fld.required)
            self.options.setPlainText("\n".join(map(str, fld.options)))
            self.minimum.setText("" if fld.minimum is None else f"{fld.minimum:g}")
            self.maximum.setText("" if fld.maximum is None else f"{fld.maximum:g}")
            self.pattern.setText(fld.pattern)
            self.help.setText(fld.help)
        self._loading = False

    def _changed(self, *_):
        if self._loading or self.field is None:
            return
        f = self.field
        f.label = self.label.text().strip() or f.label
        f.kind = self.kind.currentData()
        f.required = self.required.isChecked()
        f.options = [o.strip() for o in self.options.toPlainText().splitlines() if o.strip()]

        def num(t):
            try:
                return float(t) if t.strip() else None
            except ValueError:
                return None
        f.minimum, f.maximum = num(self.minimum.text()), num(self.maximum.text())
        f.pattern = self.pattern.text().strip()
        f.help = self.help.text().strip()
        self.on_change()


class FormsDialog(QDialog):
    def __init__(self, main, sheet_doc=None):
        super().__init__(main)
        self.main = main
        self.sheet_doc = sheet_doc
        self.setWindowTitle(_t("Forms / Survey"))
        self.resize(1080, 640)
        self.form: S.Form | None = None
        v = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel(_t("Forms")))
        self.forms = QListWidget()
        self.forms.currentItemChanged.connect(self._open_form)
        lv.addWidget(self.forms, 1)
        for text, fn in ((_t("New Form"), self.new_form), (_t("Sample Form"), self.sample), (_t("Delete Form"), self.delete_form)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            lv.addWidget(b)
        split.addWidget(left)
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit()
        self.title.setPlaceholderText(_t("Form title"))
        self.title.editingFinished.connect(self._meta_changed)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText(_t("Description"))
        self.desc.editingFinished.connect(self._meta_changed)
        mv.addWidget(self.title)
        mv.addWidget(self.desc)
        self.fields = QListWidget()
        self.fields.setDragDropMode(QAbstractItemView.InternalMove)
        self.fields.currentRowChanged.connect(self._select_field)
        self.fields.model().rowsMoved.connect(self._reordered)
        mv.addWidget(self.fields, 1)
        row = QHBoxLayout()
        for text, fn in ((_t("Add Question"), self.add_field), (_t("Remove"), self.remove_field)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        mv.addLayout(row)
        self.editor = FieldEditor(self._field_changed)
        mv.addWidget(self.editor)
        split.addWidget(mid)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel(_t("Preview / fill in")))
        self.preview_area = QScrollArea()
        self.preview_area.setWidgetResizable(True)
        rv.addWidget(self.preview_area, 1)
        self.submit = QPushButton(_t("Submit Response"))
        self.submit.setObjectName("Primary")
        self.submit.clicked.connect(self.submit_response)
        rv.addWidget(self.submit)
        self.count = QLabel()
        self.count.setObjectName("Muted")
        rv.addWidget(self.count)
        r2 = QHBoxLayout()
        for text, fn in ((_t("Save"), self.save), (_t("Export HTML\u2026"), self.export_html),
                         (_t("Import Responses\u2026"), self.import_responses),
                         (_t("Responses to Spreadsheet"), self.to_sheet), (_t("Export Schema\u2026"), self.export_schema)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r2.addWidget(b)
        rv.addLayout(r2)
        split.addWidget(right)
        split.setSizes([200, 420, 460])
        v.addWidget(split, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self._close)
        v.addWidget(bb)
        self.refresh_forms()

    # -- forms list
    def refresh_forms(self, select: str | None = None):
        self.forms.blockSignals(True)
        self.forms.clear()
        for p in S.list_forms():
            try:
                f = S.load_form(p)
            except Exception:
                continue
            it = QListWidgetItem(f.title)
            it.setData(Qt.UserRole, str(p))
            self.forms.addItem(it)
            if select and f.id == select:
                self.forms.setCurrentItem(it)
        self.forms.blockSignals(False)
        if self.forms.currentItem() is None and self.forms.count():
            self.forms.setCurrentRow(0)
        if self.forms.currentItem() is not None:
            self._open_form(self.forms.currentItem())
        elif self.form is None:
            self.sample()

    def _open_form(self, it, _prev=None):
        if it is None:
            return
        self.form = S.load_form(it.data(Qt.UserRole))
        self._show_form()

    def _show_form(self):
        f = self.form
        self.title.setText(f.title)
        self.desc.setText(f.description)
        self.fields.blockSignals(True)
        self.fields.clear()
        for fld in f.fields:
            self.fields.addItem(f"{fld.label}  [{fld.kind}{', required' if fld.required else ''}]")
        self.fields.blockSignals(False)
        self.editor.load(None)
        if f.fields:
            self.fields.setCurrentRow(0)
        self._preview()

    def _preview(self):
        if self.form is None:
            return
        self.renderer = FormRenderer(self.form)
        self.preview_area.setWidget(self.renderer)
        self.count.setText(_t("{0} response(s) collected").format(len(S.load_responses(self.form))))

    def new_form(self):
        self.form = S.Form(_t("Untitled form"), "", [S.FormField("question_1", _t("Question 1"))])
        self._show_form()

    def sample(self):
        self.form = S.sample_form()
        self._show_form()

    def delete_form(self):
        it = self.forms.currentItem()
        if it and confirm(self, _t("Delete Form"), _t("Delete \u201c{0}\u201d (responses are kept)?").format(it.text())):
            from pathlib import Path

            Path(it.data(Qt.UserRole)).unlink(missing_ok=True)
            self.form = None
            self.refresh_forms()

    # -- editing
    def _meta_changed(self):
        if self.form is not None:
            self.form.title = self.title.text().strip() or self.form.title
            self.form.description = self.desc.text().strip()
            self._preview()

    def _select_field(self, row):
        self.editor.load(self.form.fields[row] if self.form and 0 <= row < len(self.form.fields) else None)

    def _field_changed(self):
        row = self.fields.currentRow()
        if 0 <= row < len(self.form.fields):
            fld = self.form.fields[row]
            self.fields.item(row).setText(f"{fld.label}  [{fld.kind}{', required' if fld.required else ''}]")
        self._preview()

    def _reordered(self, *_):
        labels = [self.fields.item(i).text() for i in range(self.fields.count())]
        by_label = {f"{f.label}  [{f.kind}{', required' if f.required else ''}]": f for f in self.form.fields}
        self.form.fields = [by_label[t] for t in labels if t in by_label]
        self._preview()

    def add_field(self):
        if self.form is None:
            self.new_form()
            return
        existing = {f.key for f in self.form.fields}
        n = len(self.form.fields) + 1
        fld = S.FormField(S.slug_key(f"question {n}", existing), _t("Question {0}").format(n))
        self.form.fields.append(fld)
        self.fields.addItem(f"{fld.label}  [{fld.kind}]")
        self.fields.setCurrentRow(self.fields.count() - 1)
        self._preview()

    def remove_field(self):
        row = self.fields.currentRow()
        if self.form and 0 <= row < len(self.form.fields):
            self.form.fields.pop(row)
            self._show_form()

    def save(self):
        if self.form is None:
            return
        for fld in self.form.fields:  # derive stable keys from labels for new questions
            if fld.key.startswith("question_") and fld.label and not fld.label.lower().startswith("question"):
                fld.key = S.slug_key(fld.label, {f.key for f in self.form.fields if f is not fld})
        path = S.save_form(self.form)
        self.refresh_forms(self.form.id)
        self.main.statusBar().showMessage(_t("Form saved: {0}").format(path), 5000) if hasattr(self.main, "statusBar") else None

    def _close(self):
        if self.form is not None:
            S.save_form(self.form)
        self.reject()

    # -- responses
    def submit_response(self):
        if self.form is None:
            return
        data = self.renderer.answers()
        errors = S.validate(self.form, data)
        if errors:
            show_error(self, _t("Please correct the form"), "\n".join(errors))
            return
        S.save_form(self.form)
        rec = S.store_response(self.form, data)
        if self.sheet_doc is not None:
            self._append_to_sheet(rec)
        show_info(self, self.form.title, _t("Thank you! The response was recorded."))
        self._preview()

    def _responses_sheet(self, doc):
        name = f"{self.form.title[:24]} Responses"[:31]
        sh = doc.book.sheet_by_name(name)
        if sh is None:
            sh = doc.add_sheet(name)
            header = S.response_rows(self.form, [])[0]
            from app.macros.api import SheetAPI
            from app.sheet.model import a1

            api = SheetAPI(doc, sh)
            for c, text in enumerate(header, start=1):
                api[a1(1, c)] = text
            from app.sheet.formula.tokenizer import index_to_col

            api.format(f"A1:{index_to_col(len(header))}1", bold=True, fill="#d9e2f3")
        return sh

    def _append_to_sheet(self, rec: dict):
        from app.macros.api import SheetAPI
        from app.sheet.model import a1

        doc = self.sheet_doc
        sh = self._responses_sheet(doc)
        api = SheetAPI(doc, sh)
        row = S.response_rows(self.form, [rec])[1]
        r = sh.max_row + 1
        for c, v in enumerate(row, start=1):
            api[a1(r, c)] = v if v is not None else ""

    def to_sheet(self):
        if self.form is None:
            return
        responses = S.load_responses(self.form)
        doc = self.sheet_doc or (self.main.current_of_kind("sheet") if hasattr(self.main, "current_of_kind") else None)
        if doc is None:
            doc = self.main.new_sheet()
        self.sheet_doc = doc
        doc.write_table(S.response_rows(self.form, responses), f"{self.form.title[:20]} Responses")
        show_info(self, _t("Responses"), _t("{0} response(s) written to the spreadsheet.").format(len(responses)))

    def export_html(self):
        if self.form is None:
            return
        path = get_save_file(self, _t("Export Form as HTML"), "HTML (*.html)", f"{self.form.id}.html", "html")
        if path:
            S.export_html(self.form, path)
            show_info(self, _t("Export HTML"), _t("Share this page; respondents' answers download as JSON files that you "
                                                 "can import here.\n\n{0}").format(path))

    def export_schema(self):
        if self.form is None:
            return
        import json

        path = get_save_file(self, _t("Export JSON Schema"), "JSON (*.json)", f"{self.form.id}.schema.json", "json")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(S.to_json_schema(self.form), fh, indent=2, ensure_ascii=False)

    def import_responses(self):
        if self.form is None:
            return
        path = get_open_file(self, _t("Import Responses"), "JSON (*.json *.jsonl);;All files (*)")
        if not path:
            return
        try:
            ok, problems = S.import_responses(self.form, path)
        except Exception as exc:
            show_error(self, _t("Import Responses"), str(exc))
            return
        msg = _t("{0} response(s) imported.").format(ok)
        if problems:
            msg += "\n\n" + _t("Rejected:") + "\n" + "\n".join(problems[:20])
        show_info(self, _t("Import Responses"), msg)
        self._preview()


def open_forms(main):
    doc = main.current_of_kind("sheet") if hasattr(main, "current_of_kind") else None
    FormsDialog(main, doc).exec()
