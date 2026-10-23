"""Recalculation engine: dependency graph + topological (Kahn) recalculation.

* precedents of each formula are indexed for fast dependent lookup: single cells in a dict,
  ranges in a per-column interval index (very wide ranges in a separate list)
* ``recalc(changed)`` marks the transitive dependents dirty and evaluates them in
  topological order; cycles evaluate to ``#CIRC!``
* volatile functions (NOW, RAND, OFFSET, INDIRECT ...) are recalculated every time
* large batches fall back to a full recalculation
"""
from __future__ import annotations

import bisect
import logging
from collections import deque

from app.sheet.formula.compiler import EvalContext, compile_node, finalize
from app.sheet.formula.parser import parse, references
from app.sheet.formula.tokenizer import FormulaSyntaxError
from app.sheet.formula.values import CIRC, NAME, VALUE, XLError

log = logging.getLogger(__name__)

WIDE_COLS = 64          # ranges wider than this go to the linear list
BATCH_FULL = 3000       # changed cells above this trigger a full recalculation


class CompiledFormula:
    __slots__ = ("fn", "volatile", "names", "error")

    def __init__(self, fn, volatile=False, names=(), error=None):
        self.fn, self.volatile, self.names, self.error = fn, volatile, set(names), error


def compile_formula(text: str) -> tuple[CompiledFormula, object]:
    """Compile formula text.  Syntax errors produce a formula evaluating to an error value."""
    try:
        node = parse(text)
        fn, vol, names = compile_node(node)
        return CompiledFormula(fn, vol, names), node
    except FormulaSyntaxError as exc:
        err = NAME if "Unexpected" in str(exc) and "(" not in text else VALUE

        def broken(ctx, _e=err):
            raise _e
        return CompiledFormula(broken, False, (), str(exc)), None


class Engine:
    def __init__(self, book):
        self.book = book
        self.ctx = EvalContext(book)
        self.reset_index()
        self.last_recalc_count = 0

    # ------------------------------------------------------------------ index
    def reset_index(self):
        self.prec: dict[tuple, tuple[list, list]] = {}
        self.cell_deps: dict[tuple, set] = {}
        self.col_ranges: dict[int, dict[int, list]] = {}
        self.wide: dict[int, list] = {}
        self.volatile: set[tuple] = set()
        self.name_users: dict[str, set] = {}

    def _register(self, key: tuple, node, compiled: CompiledFormula):
        sid0 = key[0]
        cells, ranges = [], []
        if node is not None:
            for ref in references(node):
                if ref.sheet is None:
                    sid = sid0
                else:
                    sh = self.book.sheet_by_name(ref.sheet)
                    if sh is None:
                        continue
                    sid = sh.id
                if ref.is_cell:
                    k = (sid, ref.r1, ref.c1)
                    cells.append(k)
                    self.cell_deps.setdefault(k, set()).add(key)
                else:
                    rng = (sid, ref.r1, ref.c1, ref.r2, ref.c2)
                    ranges.append(rng)
                    if ref.c2 - ref.c1 + 1 > WIDE_COLS:
                        self.wide.setdefault(sid, []).append((ref.r1, ref.c1, ref.r2, ref.c2, key))
                    else:
                        cols = self.col_ranges.setdefault(sid, {})
                        for c in range(ref.c1, ref.c2 + 1):
                            cols.setdefault(c, []).append((ref.r1, ref.r2, key))
        for nm in compiled.names:
            self.name_users.setdefault(nm, set()).add(key)
            target = self.book.names.get(nm)
            if target:
                try:
                    for ref in references(parse(target if target.startswith("=") else "=" + target)):
                        sh = self.book.sheet_by_name(ref.sheet) if ref.sheet else self.book.sheet_by_id(sid0)
                        if sh is None:
                            continue
                        rng = (sh.id, ref.r1, ref.c1, ref.r2, ref.c2)
                        ranges.append(rng)
                        if ref.c2 - ref.c1 + 1 > WIDE_COLS:
                            self.wide.setdefault(sh.id, []).append((ref.r1, ref.c1, ref.r2, ref.c2, key))
                        else:
                            cols = self.col_ranges.setdefault(sh.id, {})
                            for c in range(ref.c1, ref.c2 + 1):
                                cols.setdefault(c, []).append((ref.r1, ref.r2, key))
                except FormulaSyntaxError:
                    pass
        if compiled.volatile:
            self.volatile.add(key)
        self.prec[key] = (cells, ranges)

    def _unregister(self, key: tuple):
        entry = self.prec.pop(key, None)
        self.volatile.discard(key)
        for users in self.name_users.values():
            users.discard(key)
        if not entry:
            return
        cells, ranges = entry
        for k in cells:
            s = self.cell_deps.get(k)
            if s is not None:
                s.discard(key)
                if not s:
                    del self.cell_deps[k]
        for sid, r1, c1, r2, c2 in ranges:
            if c2 - c1 + 1 > WIDE_COLS:
                lst = self.wide.get(sid, [])
                self.wide[sid] = [e for e in lst if e[4] != key]
            else:
                cols = self.col_ranges.get(sid, {})
                for c in range(c1, c2 + 1):
                    lst = cols.get(c)
                    if lst:
                        cols[c] = [e for e in lst if e[2] != key]

    # ------------------------------------------------------------------ formulas
    def set_formula(self, sid: int, row: int, col: int, text: str, cell) -> None:
        key = (sid, row, col)
        self._unregister(key)
        compiled, node = compile_formula(text)
        cell.formula = text
        cell.fn = compiled
        self._register(key, node, compiled)

    def clear_formula(self, sid: int, row: int, col: int) -> None:
        self._unregister((sid, row, col))

    def rebuild(self) -> None:
        """Recompile and re-index every formula (after load / structural edits / renames)."""
        self.reset_index()
        self.book.name_cache.clear()
        for sh in self.book.sheets:
            for (r, c), cell in sh.cells.items():
                if cell.formula:
                    compiled, node = compile_formula(cell.formula)
                    cell.fn = compiled
                    self._register((sh.id, r, c), node, compiled)

    # ------------------------------------------------------------------ dependents
    def dependents(self, key: tuple):
        sid, r, c = key
        out = set(self.cell_deps.get(key, ()))
        lst = self.col_ranges.get(sid, {}).get(c)
        if lst:
            for r1, r2, fk in lst:
                if r1 <= r <= r2:
                    out.add(fk)
        for r1, c1, r2, c2, fk in self.wide.get(sid, ()):
            if r1 <= r <= r2 and c1 <= c <= c2:
                out.add(fk)
        return out

    def formula_keys(self) -> list[tuple]:
        return list(self.prec.keys())

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, key: tuple):
        sid, r, c = key
        sh = self.book.sheet_by_id(sid)
        if sh is None:
            return
        cell = sh.cells.get((r, c))
        if cell is None or not cell.formula or cell.fn is None:
            return
        ctx = self.ctx
        ctx.sid, ctx.row, ctx.col, ctx.cells, ctx.depth = sid, r, c, sh.cells, 0
        try:
            v = finalize(cell.fn.fn(ctx))
        except XLError as e:
            v = e
        except RecursionError:
            v = XLError("#NUM!")
        except (ZeroDivisionError, OverflowError):
            v = XLError("#NUM!")
        except Exception as exc:  # defensive: never let a function bug break recalculation
            log.debug("Formula error at %s: %s", key, exc)
            v = VALUE
        cell.value = v
        sh._colcache.pop(c, None)

    def _toposort(self, nodes: set) -> tuple[list, set]:
        indeg = dict.fromkeys(nodes, 0)
        adj: dict[tuple, list] = {}
        for n in nodes:
            for d in self.dependents(n):
                if d in indeg and d != n:
                    adj.setdefault(n, []).append(d)
                    indeg[d] += 1
                elif d == n:
                    indeg[n] += 1  # self reference: cycle
        q = deque(k for k, v in indeg.items() if v == 0)
        order = []
        while q:
            n = q.popleft()
            order.append(n)
            for d in adj.get(n, ()):
                indeg[d] -= 1
                if indeg[d] == 0:
                    q.append(d)
        cyclic = {k for k, v in indeg.items() if v > 0}
        return order, cyclic

    def _apply_order(self, order, cyclic):
        for sh in self.book.sheets:
            sh._colmiss.clear()
        for k in order:
            self.evaluate(k)
        for k in cyclic:
            sid, r, c = k
            sh = self.book.sheet_by_id(sid)
            cell = sh.cells.get((r, c)) if sh else None
            if cell is not None:
                cell.value = CIRC
                sh._colcache.pop(c, None)

    def recalc_all(self) -> set:
        nodes = set(self.prec.keys())
        order, cyclic = self._toposort(nodes)
        self._apply_order(order, cyclic)
        self.last_recalc_count = len(nodes)
        return nodes

    def recalc(self, changed) -> set:
        """Recalculate everything depending on ``changed`` cell keys; returns recalculated keys."""
        changed = list(changed)
        if len(changed) > BATCH_FULL:
            return self.recalc_all()
        dirty: set = set()
        queue = deque()
        for k in changed:
            if k in self.prec:
                dirty.add(k)
            queue.append(k)
        for k in self.volatile:
            if k not in dirty:
                dirty.add(k)
                queue.append(k)
        while queue:
            k = queue.popleft()
            for d in self.dependents(k):
                if d not in dirty:
                    dirty.add(d)
                    queue.append(d)
        if not dirty:
            self.last_recalc_count = 0
            return dirty
        order, cyclic = self._toposort(dirty)
        self._apply_order(order, cyclic)
        self.last_recalc_count = len(dirty)
        return dirty

    def names_changed(self, names) -> set:
        """Re-index formulas using changed defined names and recalc them."""
        keys = set()
        for nm in names:
            keys |= self.name_users.get(nm.upper(), set())
        self.book.name_cache.clear()
        for key in keys:
            sid, r, c = key
            cell = self.book.sheet_by_id(sid).cells.get((r, c))
            if cell is not None and cell.formula:
                self.set_formula(sid, r, c, cell.formula, cell)
        return self.recalc(keys)

    # ------------------------------------------------------------------ helpers
    def precedent_cells(self, key: tuple) -> list[tuple]:
        cells, ranges = self.prec.get(key, ([], []))
        return list(cells) + [(sid, r1, c1, r2, c2) for sid, r1, c1, r2, c2 in ranges]


def insort_unique(lst: list, v) -> None:
    i = bisect.bisect_left(lst, v)
    if i == len(lst) or lst[i] != v:
        lst.insert(i, v)
