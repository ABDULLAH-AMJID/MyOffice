"""Spreadsheet dialogs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFontComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget, QWidgetAction, QMenu)

from app.services.i18n import _t
from app.sheet.formula.functions import FUNCTIONS, function_names_by_category
from app.sheet.formula.tokenizer import col_to_index, index_to_col
from app.sheet.model import Border, CondFormat, Style, Validation
from app.sheet.numfmt import BUILTIN_FORMATS, format_value


def _buttons(dlg, layout):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    layout.addWidget(bb) if isinstance(layout, QVBoxLayout) else layout.addRow(bb)
    return bb


class ColorButton(QPushButton):
    def __init__(self, color: str | None = None, allow_none: bool = True):
        super().__init__()
        self.allow_none = allow_none
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color):
        self.color = color
        self.setText(color or _t("None"))
        self.setStyleSheet(f"QPushButton{{background:{color};color:{'#000' if not color or QColor(color).lightness() > 128 else '#fff'};}}"
                           if color else "")

    def _pick(self):
        menu = QMenu(self)
        if self.allow_none:
            menu.addAction(_t("None / Automatic"), lambda: self.set_color(None))
        menu.addAction(_t("Choose\u2026"), self._dialog)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _dialog(self):
        c = QColorDialog.getColor(QColor(self.color or "#ffffff"), self)
        if c.isValid():
            self.set_color(c.name())


# ============================================================================= format cells
class FormatCellsDialog(QDialog):
    def __init__(self, parent, style: Style, sample_value=1234.5678, tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle(_t("Format Cells"))
        self.resize(560, 440)
        self.style0 = style
        self.sample = sample_value
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)
        # number
        w = QWidget()
        g = QGridLayout(w)
        self.cat = QListWidget()
        for name in BUILTIN_FORMATS:
            self.cat.addItem(_t(name))
        self.cat.addItem(_t("Custom"))
        g.addWidget(self.cat, 0, 0, 3, 1)
        self.code = QLineEdit(style.numfmt)
        g.addWidget(QLabel(_t("Format code:")), 0, 1)
        g.addWidget(self.code, 1, 1)
        self.preview = QLabel()
        self.preview.setObjectName("Muted")
        g.addWidget(self.preview, 2, 1, Qt.AlignTop)
        names = list(BUILTIN_FORMATS)
        self.cat.currentRowChanged.connect(
            lambda i: self.code.setText(BUILTIN_FORMATS[names[i]]) if 0 <= i < len(names) else None)
        self.code.textChanged.connect(self._preview)
        for i, n in enumerate(names):
            if BUILTIN_FORMATS[n] == style.numfmt:
                self.cat.setCurrentRow(i)
                break
        else:
            self.cat.setCurrentRow(len(names))
            self.code.setText(style.numfmt)
        self.tabs.addTab(w, _t("Number"))
        # alignment
        w = QWidget()
        f = QFormLayout(w)
        self.halign = QComboBox()
        for label, val in ((_t("General"), None), (_t("Left"), "left"), (_t("Center"), "center"),
                           (_t("Right"), "right"), (_t("Justify"), "justify")):
            self.halign.addItem(label, val)
        self.halign.setCurrentIndex(max(0, self.halign.findData(style.halign)))
        self.valign = QComboBox()
        for label, val in ((_t("Bottom"), None), (_t("Center"), "center"), (_t("Top"), "top")):
            self.valign.addItem(label, val)
        self.valign.setCurrentIndex(max(0, self.valign.findData(style.valign)))
        self.indent = QSpinBox()
        self.indent.setRange(0, 15)
        self.indent.setValue(style.indent)
        self.rotation = QSpinBox()
        self.rotation.setRange(0, 180)
        self.rotation.setSuffix("\u00b0")
        self.rotation.setValue(style.rotation)
        self.wrap = QCheckBox(_t("Wrap text"))
        self.wrap.setChecked(style.wrap)
        f.addRow(_t("Horizontal:"), self.halign)
        f.addRow(_t("Vertical:"), self.valign)
        f.addRow(_t("Indent:"), self.indent)
        f.addRow(_t("Rotation:"), self.rotation)
        f.addRow(self.wrap)
        self.tabs.addTab(w, _t("Alignment"))
        # font
        w = QWidget()
        f = QFormLayout(w)
        self.font = QFontComboBox()
        if style.font:
            self.font.setCurrentFont(QFont(style.font))
        self.font_default = QCheckBox(_t("Use default font"))
        self.font_default.setChecked(style.font is None)
        self.size = QDoubleSpinBox()
        self.size.setRange(1, 409)
        self.size.setValue(style.size or 11)
        self.bold = QCheckBox(_t("Bold"))
        self.bold.setChecked(style.bold)
        self.italic = QCheckBox(_t("Italic"))
        self.italic.setChecked(style.italic)
        self.underline = QCheckBox(_t("Underline"))
        self.underline.setChecked(style.underline)
        self.strike = QCheckBox(_t("Strikethrough"))
        self.strike.setChecked(style.strike)
        self.color = ColorButton(style.color)
        f.addRow(_t("Font:"), self.font)
        f.addRow(self.font_default)
        f.addRow(_t("Size:"), self.size)
        row = QHBoxLayout()
        for cb in (self.bold, self.italic, self.underline, self.strike):
            row.addWidget(cb)
        f.addRow(row)
        f.addRow(_t("Color:"), self.color)
        self.tabs.addTab(w, _t("Font"))
        # border
        w = QWidget()
        f = QFormLayout(w)
        self.b_style = QComboBox()
        for s in ("thin", "medium", "thick", "dashed", "dotted", "double"):
            self.b_style.addItem(_t(s.capitalize()), s)
        self.b_color = ColorButton("#000000", allow_none=False)
        self.b_sides = {}
        row = QHBoxLayout()
        for side, cur in (("top", style.border_top), ("bottom", style.border_bottom), ("left", style.border_left),
                          ("right", style.border_right)):
            cb = QCheckBox(_t(side.capitalize()))
            cb.setChecked(cur is not None)
            self.b_sides[side] = cb
            row.addWidget(cb)
        presets = QHBoxLayout()
        for label, sides in ((_t("None"), ()), (_t("Outline / All"), ("top", "bottom", "left", "right")),
                             (_t("Bottom"), ("bottom",))):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=sides: [cb.setChecked(k in s) for k, cb in self.b_sides.items()])
            presets.addWidget(b)
        f.addRow(_t("Presets:"), presets)
        f.addRow(_t("Sides:"), row)
        f.addRow(_t("Line style:"), self.b_style)
        f.addRow(_t("Line color:"), self.b_color)
        self.tabs.addTab(w, _t("Border"))
        # fill
        w = QWidget()
        f = QFormLayout(w)
        self.fill = ColorButton(style.fill)
        f.addRow(_t("Background color:"), self.fill)
        self.tabs.addTab(w, _t("Fill"))
        # protection
        w = QWidget()
        f = QFormLayout(w)
        self.locked = QCheckBox(_t("Locked (effective when the sheet is protected)"))
        self.locked.setChecked(style.locked)
        f.addRow(self.locked)
        self.tabs.addTab(w, _t("Protection"))
        self.tabs.setCurrentIndex(tab)
        _buttons(self, v)
        self._preview()

    def _preview(self):
        try:
            text, _color = format_value(self.sample, self.code.text() or "General")
        except Exception as exc:
            text = str(exc)
        self.preview.setText(_t("Sample: ") + text)

    def result_style(self) -> Style:
        s = self.style0
        border = Border(self.b_style.currentData(), self.b_color.color or "#000000")
        return s.with_(numfmt=self.code.text().strip() or "General", halign=self.halign.currentData(),
                       valign=self.valign.currentData(), indent=self.indent.value(), rotation=self.rotation.value(),
                       wrap=self.wrap.isChecked(),
                       font=None if self.font_default.isChecked() else self.font.currentFont().family(),
                       size=None if self.size.value() == 11 else self.size.value(), bold=self.bold.isChecked(),
                       italic=self.italic.isChecked(), underline=self.underline.isChecked(),
                       strike=self.strike.isChecked(), color=self.color.color, fill=self.fill.color,
                       border_top=border if self.b_sides["top"].isChecked() else None,
                       border_bottom=border if self.b_sides["bottom"].isChecked() else None,
                       border_left=border if self.b_sides["left"].isChecked() else None,
                       border_right=border if self.b_sides["right"].isChecked() else None,
                       locked=self.locked.isChecked())


# ============================================================================= sort
class SortDialog(QDialog):
    def __init__(self, parent, rng, header_guess: bool, headers: dict):
        super().__init__(parent)
        self.setWindowTitle(_t("Sort"))
        self.rng = rng
        r1, c1, r2, c2 = rng
        f = QFormLayout(self)
        self.header = QCheckBox(_t("My data has headers"))
        self.header.setChecked(header_guess)
        f.addRow(self.header)
        self.levels = []
        for i in range(3):
            row = QHBoxLayout()
            col = QComboBox()
            if i:
                col.addItem(_t("(none)"), None)
            for c in range(c1, c2 + 1):
                col.addItem(f"{_t('Column')} {index_to_col(c)}" + (f" \u2014 {headers.get(c)}" if headers.get(c) else ""), c)
            order = QComboBox()
            order.addItems([_t("A \u2192 Z / smallest first"), _t("Z \u2192 A / largest first")])
            row.addWidget(col, 2)
            row.addWidget(order, 1)
            f.addRow(_t("Sort by") if i == 0 else _t("Then by"), row)
            self.levels.append((col, order))
        _buttons(self, f)

    def keys(self) -> list[tuple[int, bool]]:
        out = []
        for col, order in self.levels:
            c = col.currentData()
            if c is not None:
                out.append((c, order.currentIndex() == 0))
        return out


# ============================================================================= autofilter popup
class FilterPopup(QMenu):
    def __init__(self, parent, values: list[str], selected: set | None, condition: str, on_apply, on_sort):
        super().__init__(parent)
        self.addAction(_t("Sort A \u2192 Z"), lambda: on_sort(True))
        self.addAction(_t("Sort Z \u2192 A"), lambda: on_sort(False))
        self.addSeparator()
        host = QWidget(self)
        v = QVBoxLayout(host)
        v.setContentsMargins(8, 4, 8, 8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search"))
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumSize(220, 200)
        all_item = QListWidgetItem(_t("(Select All)"))
        all_item.setFlags(all_item.flags() | Qt.ItemIsUserCheckable)
        all_item.setCheckState(Qt.Checked if selected is None else Qt.Unchecked)
        self.list.addItem(all_item)
        for val in values:
            it = QListWidgetItem(val if val != "" else _t("(Blanks)"))
            it.setData(Qt.UserRole, val)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if selected is None or val in selected else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(self._changed)
        v.addWidget(self.list)
        self.cond = QLineEdit(condition)
        self.cond.setPlaceholderText(_t("Condition, e.g. >100 or *text*"))
        v.addWidget(self.cond)
        row = QHBoxLayout()
        ok = QPushButton(_t("OK"))
        ok.setObjectName("Primary")
        clear = QPushButton(_t("Clear Filter"))
        row.addWidget(clear)
        row.addWidget(ok)
        v.addLayout(row)
        ok.clicked.connect(lambda: (on_apply(self.selected(), self.cond.text().strip()), self.close()))
        clear.clicked.connect(lambda: (on_apply(None, ""), self.close()))
        self.search.textChanged.connect(self._filter)
        act = QWidgetAction(self)
        act.setDefaultWidget(host)
        self.addAction(act)

    def _changed(self, it):
        if self.list.row(it) == 0:
            self.list.blockSignals(True)
            for i in range(1, self.list.count()):
                if not self.list.item(i).isHidden():
                    self.list.item(i).setCheckState(it.checkState())
            self.list.blockSignals(False)

    def _filter(self, text):
        for i in range(1, self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(text) and text.lower() not in it.text().lower())

    def selected(self) -> set | None:
        vals = set()
        all_on = True
        for i in range(1, self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked and not it.isHidden():
                vals.add(it.data(Qt.UserRole))
            else:
                all_on = False
        return None if all_on else vals


# ============================================================================= conditional formatting
class CondFormatDialog(QDialog):
    KINDS = [("cell", "Cell value is"), ("text", "Text contains"), ("top", "Top N items"), ("bottom", "Bottom N items"),
             ("duplicate", "Duplicate values"), ("unique", "Unique values"), ("scale2", "2-color scale"),
             ("scale3", "3-color scale"), ("databar", "Data bars"), ("formula", "Formula is true")]

    def __init__(self, parent, rng_text: str, existing: list):
        super().__init__(parent)
        self.setWindowTitle(_t("Conditional Formatting"))
        self.resize(520, 420)
        v = QVBoxLayout(self)
        self.rules_list = QListWidget()
        self.existing = list(existing)
        for cf in self.existing:
            self.rules_list.addItem(self._describe(cf))
        v.addWidget(QLabel(_t("Rules on this sheet:")))
        v.addWidget(self.rules_list)
        rm = QPushButton(_t("Delete Selected Rule"))
        rm.clicked.connect(self._remove)
        v.addWidget(rm)
        box = QGroupBox(_t("New rule"))
        f = QFormLayout(box)
        self.range = QLineEdit(rng_text)
        self.kind = QComboBox()
        for k, label in self.KINDS:
            self.kind.addItem(_t(label), k)
        self.op = QComboBox()
        for o in (">", "<", ">=", "<=", "=", "<>", "between"):
            self.op.addItem(o)
        self.v1 = QLineEdit()
        self.v2 = QLineEdit()
        self.fill = ColorButton("#ffc7ce")
        self.color = ColorButton("#9c0006")
        self.bold = QCheckBox(_t("Bold"))
        f.addRow(_t("Apply to:"), self.range)
        f.addRow(_t("Rule:"), self.kind)
        f.addRow(_t("Operator:"), self.op)
        f.addRow(_t("Value:"), self.v1)
        f.addRow(_t("And:"), self.v2)
        f.addRow(_t("Fill:"), self.fill)
        f.addRow(_t("Font color:"), self.color)
        f.addRow(self.bold)
        v.addWidget(box)
        self.add_new = QCheckBox(_t("Add this new rule"))
        self.add_new.setChecked(True)
        v.addWidget(self.add_new)
        _buttons(self, v)

    def _describe(self, cf) -> str:
        from app.sheet.model import range_a1

        return f"{range_a1(*cf.range)}: {dict(self.KINDS).get(cf.kind, cf.kind)} {cf.op if cf.kind == 'cell' else ''} {cf.value1}"

    def _remove(self):
        i = self.rules_list.currentRow()
        if i >= 0:
            self.rules_list.takeItem(i)
            self.existing.pop(i)

    def new_rule(self, parse_range) -> CondFormat | None:
        if not self.add_new.isChecked():
            return None
        rng = parse_range(self.range.text())
        if rng is None:
            return None
        return CondFormat(rng, self.kind.currentData(), self.op.currentText(), self.v1.text().strip(),
                          self.v2.text().strip(), self.fill.color, self.color.color, self.bold.isChecked())


# ============================================================================= validation
class ValidationDialog(QDialog):
    def __init__(self, parent, rng_text: str, current: Validation | None):
        super().__init__(parent)
        self.setWindowTitle(_t("Data Validation"))
        f = QFormLayout(self)
        self.range = QLineEdit(rng_text)
        self.kind = QComboBox()
        for label, k in ((_t("List"), "list"), (_t("Whole number"), "whole"), (_t("Decimal"), "decimal"),
                         (_t("Date"), "date"), (_t("Text length"), "textlength"), (_t("Any value (remove)"), "any")):
            self.kind.addItem(label, k)
        self.op = QComboBox()
        for o in ("between", "notBetween", "equal", "notEqual", "greaterThan", "lessThan", "greaterThanOrEqual",
                  "lessThanOrEqual"):
            self.op.addItem(o)
        self.v1 = QLineEdit()
        self.v1.setPlaceholderText(_t("List items separated by commas, or a minimum"))
        self.v2 = QLineEdit()
        self.blank = QCheckBox(_t("Ignore blank"))
        self.blank.setChecked(True)
        self.msg = QLineEdit()
        self.prompt = QLineEdit()
        if current:
            self.kind.setCurrentIndex(max(0, self.kind.findData(current.kind)))
            self.op.setCurrentText(current.op)
            self.v1.setText(current.value1)
            self.v2.setText(current.value2)
            self.blank.setChecked(current.allow_blank)
            self.msg.setText(current.error_message)
            self.prompt.setText(current.prompt)
        f.addRow(_t("Cells:"), self.range)
        f.addRow(_t("Allow:"), self.kind)
        f.addRow(_t("Data:"), self.op)
        f.addRow(_t("Source / minimum:"), self.v1)
        f.addRow(_t("Maximum:"), self.v2)
        f.addRow(self.blank)
        f.addRow(_t("Input message:"), self.prompt)
        f.addRow(_t("Error message:"), self.msg)
        _buttons(self, f)

    def result(self, rng) -> Validation | None:
        if self.kind.currentData() == "any":
            return None
        return Validation(rng, self.kind.currentData(), self.op.currentText(), self.v1.text().strip(),
                          self.v2.text().strip(), self.blank.isChecked(), self.msg.text().strip(),
                          self.prompt.text().strip())


def validate_value(v: Validation, value) -> bool:
    if value is None or value == "":
        return v.allow_blank
    if v.kind == "list":
        return str(value).lower() in {x.lower() for x in v.list_items()} or (
            isinstance(value, float) and any(_num(x) == value for x in v.list_items()))
    if v.kind == "textlength":
        x = len(str(value))
    elif v.kind in ("whole", "decimal", "date"):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        x = float(value)
        if v.kind == "whole" and x != int(x):
            return False
    else:
        return True
    a, b = _num(v.value1), _num(v.value2)
    op = v.op
    if op == "between":
        return (a is None or x >= a) and (b is None or x <= b)
    if op == "notBetween":
        return not ((a is None or x >= a) and (b is None or x <= b))
    if a is None:
        return True
    return {"equal": x == a, "notEqual": x != a, "greaterThan": x > a, "lessThan": x < a,
            "greaterThanOrEqual": x >= a, "lessThanOrEqual": x <= a}.get(op, True)


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        from app.sheet.formula.values import parse_date_text

        return parse_date_text(s) if isinstance(s, str) and s else None


# ============================================================================= insert function
class InsertFunctionDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Insert Function"))
        self.resize(560, 420)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(_t("Search for a function"))
        self.cat = QComboBox()
        self.cats = function_names_by_category()
        self.cat.addItem(_t("All"), None)
        for c in sorted(self.cats):
            self.cat.addItem(_t(c), c)
        row.addWidget(self.search, 2)
        row.addWidget(self.cat, 1)
        v.addLayout(row)
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(60)
        v.addWidget(self.info)
        self.search.textChanged.connect(self._fill)
        self.cat.currentIndexChanged.connect(self._fill)
        self.list.currentTextChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        _buttons(self, v)
        self._fill()

    def _fill(self, *_):
        q = self.search.text().strip().upper()
        cat = self.cat.currentData()
        names = sorted(FUNCTIONS) if cat is None else self.cats.get(cat, [])
        self.list.clear()
        for n in names:
            if not q or q in n:
                self.list.addItem(n)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _show(self, name):
        spec = FUNCTIONS.get(name)
        if spec:
            self.info.setText(f"<b>{spec.syntax}</b><br>{_t(spec.help) if spec.help else _t(spec.category)}")

    def chosen(self) -> str:
        it = self.list.currentItem()
        return it.text() if it else ""


# ============================================================================= name manager
class NameManagerDialog(QDialog):
    def __init__(self, parent, names: dict, default_ref: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Name Manager"))
        self.resize(520, 360)
        self.names = dict(names)
        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([_t("Name"), _t("Refers to")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.table)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(_t("Name"))
        self.ref = QLineEdit(default_ref)
        add = QPushButton(_t("Add / Update"))
        rm = QPushButton(_t("Delete"))
        row.addWidget(self.name)
        row.addWidget(self.ref, 2)
        row.addWidget(add)
        row.addWidget(rm)
        v.addLayout(row)
        add.clicked.connect(self._add)
        rm.clicked.connect(self._remove)
        self.table.itemSelectionChanged.connect(self._sel)
        _buttons(self, v)
        self._fill()

    def _fill(self):
        self.table.setRowCount(0)
        for n, ref in sorted(self.names.items()):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(n))
            self.table.setItem(r, 1, QTableWidgetItem(ref))

    def _sel(self):
        r = self.table.currentRow()
        if r >= 0:
            self.name.setText(self.table.item(r, 0).text())
            self.ref.setText(self.table.item(r, 1).text())

    def _add(self):
        import re

        n = self.name.text().strip().upper()
        if not re.fullmatch(r"[A-Z_\\][A-Z0-9_.]*", n) or re.fullmatch(r"[A-Z]{1,3}[0-9]+", n):
            QMessageBox.warning(self, _t("Name Manager"), _t("Invalid name. Use letters, digits, '_' and '.', "
                                                            "and do not use a cell address."))
            return
        ref = self.ref.text().strip()
        if not ref:
            return
        self.names[n] = ref if ref.startswith("=") else "=" + ref
        self._fill()

    def _remove(self):
        r = self.table.currentRow()
        if r >= 0:
            self.names.pop(self.table.item(r, 0).text(), None)
            self._fill()


# ============================================================================= simple dialogs
class GoToDialog(QDialog):
    def __init__(self, parent, names: list[str]):
        super().__init__(parent)
        self.setWindowTitle(_t("Go To"))
        f = QFormLayout(self)
        self.ref = QComboBox()
        self.ref.setEditable(True)
        self.ref.addItems(names)
        self.ref.setCurrentText("")
        f.addRow(_t("Reference:"), self.ref)
        _buttons(self, f)


class SeriesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Fill Series"))
        f = QFormLayout(self)
        self.dir = QComboBox()
        self.dir.addItems([_t("Columns (down)"), _t("Rows (right)")])
        self.kind = QComboBox()
        self.kind.addItems([_t("Linear"), _t("Growth"), _t("Date (days)"), _t("Date (months)")])
        self.start = QDoubleSpinBox()
        self.start.setRange(-1e12, 1e12)
        self.start.setDecimals(4)
        self.step = QDoubleSpinBox()
        self.step.setRange(-1e9, 1e9)
        self.step.setDecimals(4)
        self.step.setValue(1)
        self.stop = QDoubleSpinBox()
        self.stop.setRange(-1e12, 1e12)
        self.stop.setDecimals(4)
        self.stop.setValue(10)
        self.count = QSpinBox()
        self.count.setRange(1, 1_000_000)
        self.count.setValue(10)
        self.use_stop = QCheckBox(_t("Stop at value (instead of count)"))
        f.addRow(_t("Series in:"), self.dir)
        f.addRow(_t("Type:"), self.kind)
        f.addRow(_t("Start value:"), self.start)
        f.addRow(_t("Step value:"), self.step)
        f.addRow(_t("Number of cells:"), self.count)
        f.addRow(self.use_stop)
        f.addRow(_t("Stop value:"), self.stop)
        _buttons(self, f)

    def values(self) -> list[float]:
        out = []
        v = self.start.value()
        kind = self.kind.currentIndex()
        n = self.count.value()
        for i in range(1_000_000):
            if self.use_stop.isChecked():
                if (self.step.value() >= 0 and v > self.stop.value()) or (self.step.value() < 0 and v < self.stop.value()):
                    break
            elif i >= n:
                break
            out.append(v)
            if kind == 1:
                v *= self.step.value()
            elif kind == 3:
                from app.sheet.formula.functions import f_edate

                v = f_edate(v, self.step.value())
            else:
                v += self.step.value()
        return out


class FindReplaceSheetDialog(QDialog):
    def __init__(self, doc, replace: bool = False):
        super().__init__(doc)
        self.doc = doc
        self.setWindowTitle(_t("Find and Replace"))
        f = QFormLayout(self)
        self.find = QLineEdit()
        self.repl = QLineEdit()
        self.case = QCheckBox(_t("Match case"))
        self.whole = QCheckBox(_t("Match entire cell contents"))
        self.formulas = QCheckBox(_t("Look in formulas"))
        self.all_sheets = QCheckBox(_t("Search all sheets"))
        f.addRow(_t("Find what:"), self.find)
        f.addRow(_t("Replace with:"), self.repl)
        for cb in (self.case, self.whole, self.formulas, self.all_sheets):
            f.addRow(cb)
        row = QHBoxLayout()
        for text, fn in ((_t("Find Next"), self.find_next), (_t("Replace"), self.replace_one),
                         (_t("Replace All"), self.replace_all), (_t("Close"), self.close)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        f.addRow(row)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        f.addRow(self.status)
        self.set_mode(replace)

    def set_mode(self, replace: bool):
        self.repl.setEnabled(True)
        self.setWindowTitle(_t("Find and Replace") if replace else _t("Find"))

    def _opts(self):
        return dict(text=self.find.text(), case=self.case.isChecked(), whole=self.whole.isChecked(),
                    formulas=self.formulas.isChecked(), all_sheets=self.all_sheets.isChecked())

    def find_next(self):
        if not self.doc.find_next(**self._opts()):
            self.status.setText(_t("No more matches."))
        else:
            self.status.setText("")

    def replace_one(self):
        n = self.doc.replace_matches(self.repl.text(), only_current=True, **self._opts())
        self.status.setText(_t("{0} replaced").format(n))
        self.find_next()

    def replace_all(self):
        n = self.doc.replace_matches(self.repl.text(), only_current=False, **self._opts())
        self.status.setText(_t("{0} replacement(s) made.").format(n))


class PasteSpecialDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_t("Paste Special"))
        v = QVBoxLayout(self)
        self.opts = {}
        for key, label in (("all", _t("All")), ("values", _t("Values")), ("formulas", _t("Formulas")),
                           ("formats", _t("Formats"))):
            rb = QRadioButton(label)
            self.opts[key] = rb
            v.addWidget(rb)
        self.opts["all"].setChecked(True)
        self.transpose = QCheckBox(_t("Transpose"))
        self.skip_blanks = QCheckBox(_t("Skip blanks"))
        v.addWidget(self.transpose)
        v.addWidget(self.skip_blanks)
        _buttons(self, v)

    def mode(self) -> str:
        return next(k for k, rb in self.opts.items() if rb.isChecked())


class TextToColumnsDialog(QDialog):
    def __init__(self, parent, sample: list[str]):
        super().__init__(parent)
        self.setWindowTitle(_t("Text to Columns"))
        f = QFormLayout(self)
        self.delim = QComboBox()
        self.delim.setEditable(True)
        for label, d in ((_t("Comma"), ","), (_t("Tab"), "\t"), (_t("Semicolon"), ";"), (_t("Space"), " "),
                         (_t("Pipe"), "|")):
            self.delim.addItem(label, d)
        self.preview = QPlainTextEdit("\n".join(sample[:8]))
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(120)
        f.addRow(_t("Delimiter:"), self.delim)
        f.addRow(_t("Preview:"), self.preview)
        _buttons(self, f)

    def delimiter(self) -> str:
        d = self.delim.currentData()
        return d if d is not None and self.delim.currentText() == self.delim.itemText(self.delim.currentIndex()) \
            else self.delim.currentText()


class RemoveDuplicatesDialog(QDialog):
    def __init__(self, parent, columns: list[tuple[int, str]], header: bool):
        super().__init__(parent)
        self.setWindowTitle(_t("Remove Duplicates"))
        v = QVBoxLayout(self)
        self.header = QCheckBox(_t("My data has headers"))
        self.header.setChecked(header)
        v.addWidget(self.header)
        v.addWidget(QLabel(_t("Columns to compare:")))
        self.list = QListWidget()
        for c, label in columns:
            it = QListWidgetItem(f"{index_to_col(c)} \u2014 {label}" if label else index_to_col(c))
            it.setData(Qt.UserRole, c)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.list.addItem(it)
        v.addWidget(self.list)
        _buttons(self, v)

    def columns(self) -> list[int]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


def parse_a1_range(text: str):
    """'B2:D10' / 'B2' / 'Sheet!A1:B2' -> (sheet or None, (r1, c1, r2, c2)) or None."""
    from app.sheet.formula.parser import parse_ref_text

    t = text.strip().replace("$", "")
    if not t:
        return None
    try:
        ref = parse_ref_text(t)
    except Exception:
        return None
    return ref.sheet, (ref.r1, ref.c1, ref.r2, ref.c2)


__all__ = ["FormatCellsDialog", "SortDialog", "FilterPopup", "CondFormatDialog", "ValidationDialog",
           "InsertFunctionDialog", "NameManagerDialog", "GoToDialog", "SeriesDialog", "FindReplaceSheetDialog",
           "PasteSpecialDialog", "TextToColumnsDialog", "RemoveDuplicatesDialog", "validate_value", "parse_a1_range",
           "col_to_index"]
