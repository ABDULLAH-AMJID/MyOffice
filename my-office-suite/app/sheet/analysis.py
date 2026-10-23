"""Data Analysis tools (descriptive statistics, correlation, regression, histogram ...) and Goal Seek."""
from __future__ import annotations

import math

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QListWidget, QSpinBox, QVBoxLayout)

from app.services.i18n import _t
from app.sheet.formula.values import XLError

TOOLS = [("descriptive", "Descriptive Statistics"), ("correlation", "Correlation"), ("covariance", "Covariance"),
         ("histogram", "Histogram"), ("regression", "Regression"), ("moving_average", "Moving Average"),
         ("rank", "Rank and Percentile")]


def _columns(sh, rng, labels: bool):
    r1, c1, r2, c2 = rng
    r2 = min(r2, max(sh.max_row, r1))
    cols = []
    for c in range(c1, c2 + 1):
        name = str(sh.value(r1, c)) if labels and sh.value(r1, c) is not None else f"Column {c - c1 + 1}"
        vals = []
        for r in range(r1 + (1 if labels else 0), r2 + 1):
            v = sh.value(r, c)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(float(v))
        cols.append((name, vals))
    return cols


def descriptive(cols) -> list[list]:
    import numpy as np

    out = [[""] + [n for n, _ in cols]]
    stats = []
    for _n, v in cols:
        a = np.array(v, dtype=float)
        n = len(a)
        if n == 0:
            stats.append({})
            continue
        mean = float(a.mean())
        sd = float(a.std(ddof=1)) if n > 1 else float("nan")
        vals, counts = np.unique(a, return_counts=True)
        mode = float(vals[counts.argmax()]) if counts.max() > 1 else None
        skew = float(((a - mean) ** 3).sum() * n / ((n - 1) * (n - 2) * sd ** 3)) if n > 2 and sd else None
        kurt = (float(n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * (((a - mean) / sd) ** 4).sum()
                      - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))) if n > 3 and sd else None)
        stats.append({"Mean": mean, "Standard Error": sd / math.sqrt(n) if n > 1 else None,
                      "Median": float(np.median(a)), "Mode": mode, "Standard Deviation": sd if n > 1 else None,
                      "Sample Variance": sd ** 2 if n > 1 else None, "Kurtosis": kurt, "Skewness": skew,
                      "Range": float(a.max() - a.min()), "Minimum": float(a.min()), "Maximum": float(a.max()),
                      "Sum": float(a.sum()), "Count": float(n),
                      "Q1 (25%)": float(np.percentile(a, 25)), "Q3 (75%)": float(np.percentile(a, 75))})
    for key in ("Mean", "Standard Error", "Median", "Mode", "Standard Deviation", "Sample Variance", "Kurtosis",
                "Skewness", "Range", "Minimum", "Maximum", "Sum", "Count", "Q1 (25%)", "Q3 (75%)"):
        out.append([_t(key)] + [s.get(key) for s in stats])
    return out


def matrix(cols, kind: str) -> list[list]:
    import numpy as np

    n = min(len(v) for _n, v in cols) if cols else 0
    data = np.array([v[:n] for _n, v in cols], dtype=float)
    m = np.corrcoef(data) if kind == "correlation" else np.cov(data, ddof=0)
    m = np.atleast_2d(m)
    out = [[""] + [n for n, _ in cols]]
    for i, (name, _v) in enumerate(cols):
        out.append([name] + [float(m[i, j]) if j <= i else None for j in range(len(cols))])
    return out


def histogram(values: list[float], bins: list[float] | None) -> list[list]:
    import numpy as np

    a = np.array(values, dtype=float)
    if not len(a):
        raise ValueError(_t("No numeric data."))
    edges = sorted(bins) if bins else list(np.histogram_bin_edges(a, bins="auto"))[1:]
    out = [[_t("Bin"), _t("Frequency"), _t("Cumulative %")]]
    prev = -math.inf
    cum = 0
    for e in edges:
        k = int(((a > prev) & (a <= e)).sum())
        cum += k
        out.append([float(e), float(k), cum / len(a)])
        prev = e
    more = int((a > prev).sum())
    cum += more
    out.append([_t("More"), float(more), cum / len(a)])
    return out


def regression(y: list[float], xs: list[tuple[str, list[float]]]) -> list[list]:
    import numpy as np

    n = min([len(y)] + [len(v) for _n, v in xs])
    Y = np.array(y[:n], dtype=float)
    X = np.column_stack([np.ones(n)] + [np.array(v[:n], dtype=float) for _n, v in xs])
    k = X.shape[1] - 1
    if n <= k + 1:
        raise ValueError(_t("Not enough observations for regression."))
    beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ beta
    sse = float(resid @ resid)
    sst = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1 - sse / sst if sst else 1.0
    adj = 1 - (1 - r2) * (n - 1) / (n - k - 1)
    mse = sse / (n - k - 1)
    cov = mse * np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    f = ((sst - sse) / k) / mse if mse else float("inf")
    out = [[_t("Regression Statistics"), ""], [_t("Multiple R"), math.sqrt(max(0.0, r2))], [_t("R Square"), r2],
           [_t("Adjusted R Square"), adj], [_t("Standard Error"), math.sqrt(mse)], [_t("Observations"), float(n)],
           [_t("F statistic"), float(f)], [], ["", _t("Coefficients"), _t("Standard Error"), _t("t Stat")]]
    names = [_t("Intercept")] + [nm for nm, _v in xs]
    for i, nm in enumerate(names):
        out.append([nm, float(beta[i]), float(se[i]), float(beta[i] / se[i]) if se[i] else None])
    return out


def moving_average(values: list[float], interval: int) -> list[list]:
    out = [[_t("Value"), _t("Moving Average ({0})").format(interval)]]
    for i, v in enumerate(values):
        ma = sum(values[i - interval + 1:i + 1]) / interval if i >= interval - 1 else None
        out.append([v, ma])
    return out


def rank_percentile(name: str, values: list[float]) -> list[list]:
    order = sorted(range(len(values)), key=lambda i: -values[i])
    out = [[_t("Point"), name, _t("Rank"), _t("Percent")]]
    n = len(values)
    for pos, i in enumerate(order):
        v = values[i]
        rank = 1 + sum(1 for x in values if x > v)
        below = sum(1 for x in values if x < v)
        out.append([float(i + 1), v, float(rank), below / (n - 1) if n > 1 else 1.0])
    return out


class AnalysisDialog(QDialog):
    def __init__(self, parent, rng_text: str, tool: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(_t("Data Analysis"))
        self.resize(520, 380)
        v = QVBoxLayout(self)
        self.tools = QListWidget()
        for k, label in TOOLS:
            self.tools.addItem(_t(label))
        self.tools.setCurrentRow([k for k, _ in TOOLS].index(tool) if tool in dict(TOOLS) else 0)
        v.addWidget(QLabel(_t("Analysis tool:")))
        v.addWidget(self.tools)
        f = QFormLayout()
        self.input = QLineEdit(rng_text)
        self.labels = QCheckBox(_t("Labels in first row"))
        self.labels.setChecked(True)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText(_t("Histogram: bin range (optional) \u2022 Regression: X range"))
        self.interval = QSpinBox()
        self.interval.setRange(2, 1000)
        self.interval.setValue(3)
        self.chart = QCheckBox(_t("Chart output (histogram)"))
        self.chart.setChecked(True)
        f.addRow(_t("Input range (Y for regression):"), self.input)
        f.addRow(self.labels)
        f.addRow(_t("Second range:"), self.extra)
        f.addRow(_t("Interval (moving average):"), self.interval)
        f.addRow(self.chart)
        v.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def tool(self) -> str:
        return TOOLS[self.tools.currentRow()][0]


def run_tool(tool: str, sh, rng, labels: bool, extra_rng=None, interval: int = 3) -> tuple[str, list[list]]:
    cols = _columns(sh, rng, labels)
    if not cols or not any(v for _n, v in cols):
        raise ValueError(_t("The input range contains no numbers."))
    if tool == "descriptive":
        return _t("Descriptive Statistics"), descriptive(cols)
    if tool in ("correlation", "covariance"):
        if len(cols) < 2:
            raise ValueError(_t("Select at least two columns."))
        return _t(dict(TOOLS)[tool]), matrix(cols, tool)
    if tool == "histogram":
        bins = None
        if extra_rng is not None:
            bins = [v for _n, vals in _columns(sh, extra_rng, False) for v in vals]
        return _t("Histogram"), histogram(cols[0][1], bins)
    if tool == "regression":
        if extra_rng is None:
            if len(cols) < 2:
                raise ValueError(_t("Give an X range, or select Y followed by X columns."))
            return _t("Regression"), regression(cols[0][1], cols[1:])
        xs = _columns(sh, extra_rng, labels)
        return _t("Regression"), regression(cols[0][1], xs)
    if tool == "moving_average":
        return _t("Moving Average"), moving_average(cols[0][1], interval)
    if tool == "rank":
        return _t("Rank and Percentile"), rank_percentile(cols[0][0], cols[0][1])
    raise ValueError(tool)


# ============================================================================= goal seek
class GoalSeekDialog(QDialog):
    def __init__(self, parent, current: str):
        super().__init__(parent)
        self.setWindowTitle(_t("Goal Seek"))
        f = QFormLayout(self)
        self.set_cell = QLineEdit(current)
        self.target = QLineEdit()
        self.change = QLineEdit()
        f.addRow(_t("Set cell (formula):"), self.set_cell)
        f.addRow(_t("To value:"), self.target)
        f.addRow(_t("By changing cell:"), self.change)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


def goal_seek(book, sh, set_rc, target: float, change_rc, max_iter: int = 200, tol: float = 1e-9):
    """Secant/bisection search changing ``change_rc`` until ``set_rc`` equals ``target``.

    Returns (found, x, achieved_value)."""
    def f(x):
        book.set_value(sh, change_rc[0], change_rc[1], float(x))
        v = sh.value(*set_rc)
        if isinstance(v, XLError) or not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError("non-numeric")
        return float(v) - target

    x0 = sh.value(*change_rc)
    x0 = float(x0) if isinstance(x0, (int, float)) and not isinstance(x0, bool) else 0.0
    x1 = x0 * 1.01 + 0.01
    try:
        f0, f1 = f(x0), f(x1)
    except ValueError:
        return False, x0, None
    for _ in range(max_iter):
        if abs(f1) < tol:
            return True, x1, f1 + target
        if f1 == f0:
            x1 += 1e-3 * (abs(x1) + 1)
            f1 = f(x1)
            continue
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        if not math.isfinite(x2):
            break
        x0, f0 = x1, f1
        x1 = x2
        try:
            f1 = f(x1)
        except ValueError:
            break
    return abs(f1) < 1e-6, x1, f1 + target


__all__ = ["AnalysisDialog", "GoalSeekDialog", "run_tool", "goal_seek", "QComboBox"]
