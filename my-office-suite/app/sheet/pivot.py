"""Pivot tables (pandas.pivot_table)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from app.services.i18n import _t
from app.sheet.formula.values import XLError

AGGREGATIONS = [("sum", "Sum"), ("count", "Count"), ("mean", "Average"), ("min", "Min"), ("max", "Max"),
                ("median", "Median"), ("std", "Std. deviation"), ("nunique", "Distinct count")]


def range_to_dataframe(sh, rng, header: bool = True):
    import pandas as pd

    r1, c1, r2, c2 = rng
    r2 = min(r2, max(sh.max_row, r1))
    c2 = min(c2, max(sh.max_col, c1))
    rows = []
    for r in range(r1, r2 + 1):
        row = []
        for c in range(c1, c2 + 1):
            v = sh.value(r, c)
            if isinstance(v, XLError):
                v = None
            row.append(v)
        rows.append(row)
    if header and rows:
        cols = []
        seen = {}
        for i, h in enumerate(rows[0]):
            name = str(h) if h not in (None, "") else f"Column{i + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            cols.append(name)
        data = rows[1:]
    else:
        from app.sheet.formula.tokenizer import index_to_col

        cols = [index_to_col(c) for c in range(c1, c2 + 1)]
        data = rows
    df = pd.DataFrame(data, columns=cols)
    df = df.dropna(how="all")
    for col in df.columns:  # numeric columns -> float
        try:
            conv = pd.to_numeric(df[col], errors="raise")
            df[col] = conv
        except (ValueError, TypeError):
            pass
    return df


def compute_pivot(df, rows: list[str], cols: list[str], value: str | None, agg: str, totals: bool = True):
    import pandas as pd

    if not rows and not cols:
        raise ValueError(_t("Choose at least one row or column field."))
    if value is None:
        value = rows[0] if rows else cols[0]
        agg = "count"
    work = df.copy()
    for f in rows + cols:
        work[f] = work[f].astype(object).where(work[f].notna(), "(blank)")
    if agg in ("sum", "mean", "min", "max", "median", "std") and not pd.api.types.is_numeric_dtype(work[value]):
        work[value] = pd.to_numeric(work[value], errors="coerce")
    pv = pd.pivot_table(work, index=rows or None, columns=cols or None, values=value, aggfunc=agg,
                        margins=totals, margins_name=_t("Grand Total"), observed=True, dropna=False)
    if isinstance(pv, pd.Series):
        pv = pv.to_frame(f"{dict(AGGREGATIONS)[agg]} of {value}")
    return pv


def pivot_to_rows(pv, value_label: str) -> list[list]:
    """Flatten a pivot DataFrame into rows of cells (header rows first)."""
    import pandas as pd

    out = []
    idx_names = [n if n is not None else "" for n in (pv.index.names or [""])]
    if isinstance(pv.columns, pd.MultiIndex):
        levels = pv.columns.nlevels
        for lvl in range(levels):
            row = [value_label if lvl == 0 and i == 0 else (idx_names[i] if lvl == levels - 1 else "")
                   for i in range(len(idx_names))]
            row += [str(c[lvl]) for c in pv.columns]
            out.append(row)
    else:
        out.append(list(idx_names) + [str(c) for c in pv.columns])
    for key, vals in pv.iterrows():
        keys = list(key) if isinstance(key, tuple) else [key]
        row = [str(k) for k in keys] + [None if (isinstance(v, float) and v != v) else
                                         (float(v) if isinstance(v, (int, float)) else v) for v in vals]
        out.append(row)
    return out


class PivotDialog(QDialog):
    def __init__(self, parent, sh, rng_text: str, parse_range):
        super().__init__(parent)
        self.setWindowTitle(_t("PivotTable"))
        self.resize(900, 560)
        self.sh = sh
        self.parse_range = parse_range
        self.df = None
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(_t("Source range:")))
        self.src = QLineEdit(rng_text)
        top.addWidget(self.src, 1)
        load = QPushButton(_t("Load Fields"))
        load.clicked.connect(self._load)
        top.addWidget(load)
        v.addLayout(top)
        g = QGridLayout()
        self.fields = QListWidget()
        self.rows = QListWidget()
        self.cols = QListWidget()
        for lw in (self.fields, self.rows, self.cols):
            lw.setDragDropMode(QAbstractItemView.DragDrop)
            lw.setDefaultDropAction(Qt.MoveAction)
            lw.setSelectionMode(QAbstractItemView.SingleSelection)
        g.addWidget(QLabel(_t("Fields (drag into Rows / Columns):")), 0, 0)
        g.addWidget(self.fields, 1, 0, 3, 1)
        g.addWidget(QLabel(_t("Rows")), 0, 1)
        g.addWidget(self.rows, 1, 1)
        g.addWidget(QLabel(_t("Columns")), 2, 1)
        g.addWidget(self.cols, 3, 1)
        btns = QVBoxLayout()
        for text, target in ((_t("Add to Rows \u2192"), self.rows), (_t("Add to Columns \u2192"), self.cols)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, t=target: self._move(self.fields, t))
            btns.addWidget(b)
        b = QPushButton(_t("\u2190 Remove"))
        b.clicked.connect(self._remove)
        btns.addWidget(b)
        btns.addStretch(1)
        g.addLayout(btns, 1, 2, 3, 1)
        vals = QHBoxLayout()
        self.value = QComboBox()
        self.agg = QComboBox()
        for k, label in AGGREGATIONS:
            self.agg.addItem(_t(label), k)
        self.totals = QCheckBox(_t("Grand totals"))
        self.totals.setChecked(True)
        vals.addWidget(QLabel(_t("Values:")))
        vals.addWidget(self.value, 2)
        vals.addWidget(QLabel(_t("Summarize by:")))
        vals.addWidget(self.agg, 1)
        vals.addWidget(self.totals)
        prev = QPushButton(_t("Preview"))
        prev.clicked.connect(self._preview)
        vals.addWidget(prev)
        g.addLayout(vals, 4, 0, 1, 3)
        v.addLayout(g)
        self.table = QTableWidget()
        v.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        v.addWidget(self.status)
        bb = QDialogButtonBox()
        bb.addButton(_t("Insert on New Sheet"), QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._load()

    def _load(self):
        parsed = self.parse_range(self.src.text())
        if parsed is None:
            self.status.setText(_t("Invalid range."))
            return
        sheet_name, rng = parsed
        sh = self.sh.book.sheet_by_name(sheet_name) if sheet_name else self.sh
        if sh is None:
            self.status.setText(_t("Unknown sheet."))
            return
        self.df = range_to_dataframe(sh, rng, True)
        self.fields.clear()
        self.rows.clear()
        self.cols.clear()
        self.value.clear()
        self.value.addItem(_t("(count rows)"), None)
        for c in self.df.columns:
            self.fields.addItem(str(c))
            self.value.addItem(str(c), str(c))
        num_cols = [c for c in self.df.columns if str(self.df[c].dtype).startswith(("float", "int"))]
        if num_cols:
            self.value.setCurrentIndex(self.value.findData(str(num_cols[-1])))
        text_cols = [c for c in self.df.columns if c not in num_cols]
        if text_cols:
            self._take(self.fields, str(text_cols[0]), self.rows)
        self.status.setText(_t("{0} records, {1} fields").format(len(self.df), len(self.df.columns)))
        self._preview()

    def _take(self, src, text, dst):
        for i in range(src.count()):
            if src.item(i).text() == text:
                dst.addItem(src.takeItem(i).text())
                return

    def _move(self, src, dst):
        it = src.currentItem()
        if it:
            dst.addItem(src.takeItem(src.row(it)).text())
            self._preview()

    def _remove(self):
        for lw in (self.rows, self.cols):
            it = lw.currentItem()
            if it:
                self.fields.addItem(lw.takeItem(lw.row(it)).text())
        self._preview()

    def config(self):
        rows = [self.rows.item(i).text() for i in range(self.rows.count())]
        cols = [self.cols.item(i).text() for i in range(self.cols.count())]
        return rows, cols, self.value.currentData(), self.agg.currentData(), self.totals.isChecked()

    def result_rows(self):
        rows, cols, val, agg, tot = self.config()
        pv = compute_pivot(self.df, rows, cols, val, agg, tot)
        label = f"{dict(AGGREGATIONS)[agg if val else 'count']} of {val or _t('records')}"
        return pivot_to_rows(pv, label)

    def _preview(self):
        if self.df is None:
            return
        try:
            data = self.result_rows()
        except Exception as exc:
            self.status.setText(str(exc))
            self.table.clear()
            return
        self.table.clear()
        self.table.setRowCount(min(len(data), 200))
        self.table.setColumnCount(max(len(r) for r in data) if data else 0)
        for i, row in enumerate(data[:200]):
            for j, v in enumerate(row):
                text = "" if v is None else (f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))
                self.table.setItem(i, j, QTableWidgetItem(text))
        self.status.setText(_t("{0} result rows").format(len(data)))

    def _accept(self):
        try:
            self.result = self.result_rows()
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.accept()
