// Standalone Offline Excel Formula Engine + SVG Chart Renderer for MyOffice Android
(function () {
  function colToIdx(col) {
    let n = 0;
    for (let i = 0; i < col.length; i++) n = n * 26 + (col.charCodeAt(i) - 64);
    return n - 1;
  }

  function idxToCol(idx) {
    let n = idx + 1, s = "";
    while (n > 0) {
      const r = (n - 1) % 26;
      s = String.fromCharCode(65 + r) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }

  function parseRef(ref) {
    const m = /^(\$?)([A-Z]+)(\$?)(\d+)$/i.exec(ref.trim());
    if (!m) return null;
    return { col: colToIdx(m[2].toUpperCase()), row: parseInt(m[4], 10) - 1 };
  }

  function expandRange(a, b, getVal) {
    const p1 = parseRef(a), p2 = parseRef(b);
    if (!p1 || !p2) return [];
    const out = [];
    for (let r = Math.min(p1.row, p2.row); r <= Math.max(p1.row, p2.row); r++) {
      for (let c = Math.min(p1.col, p2.col); c <= Math.max(p1.col, p2.col); c++) {
        out.push(getVal(idxToCol(c) + (r + 1)));
      }
    }
    return out;
  }

  function toNum(v) {
    if (typeof v === "number") return v;
    if (typeof v === "boolean") return v ? 1 : 0;
    const n = parseFloat(String(v ?? "").replace(/,/g, "").trim());
    return Number.isFinite(n) ? n : NaN;
  }

  function numsOf(args) {
    const res = [];
    for (const a of args) {
      if (Array.isArray(a)) res.push(...numsOf(a));
      else {
        const n = toNum(a);
        if (!Number.isNaN(n)) res.push(n);
      }
    }
    return res;
  }

  function flatArgs(args) {
    const res = [];
    for (const a of args) {
      if (Array.isArray(a)) res.push(...flatArgs(a));
      else res.push(a);
    }
    return res;
  }

  function matchCriteria(val, crit) {
    const s = String(crit ?? "").trim();
    const m = /^(<=|>=|<>|<|>|=)(.*)$/.exec(s);
    if (m) {
      const op = m[1], rhs = m[2].trim();
      const nv = toNum(val), nr = toNum(rhs);
      const bothNum = !Number.isNaN(nv) && !Number.isNaN(nr);
      const cmp = bothNum ? nv - nr : String(val).localeCompare(rhs);
      if (op === "=") return cmp === 0;
      if (op === "<>") return cmp !== 0;
      if (op === "<") return cmp < 0;
      if (op === "<=") return cmp <= 0;
      if (op === ">") return cmp > 0;
      if (op === ">=") return cmp >= 0;
    }
    return String(val).toLowerCase() === s.toLowerCase();
  }

  const FUNCS = {
    SUM: (a) => numsOf(a).reduce((x, y) => x + y, 0),
    AVERAGE: (a) => { const n = numsOf(a); return n.length ? n.reduce((x, y) => x + y, 0) / n.length : "#DIV/0!"; },
    MIN: (a) => { const n = numsOf(a); return n.length ? Math.min(...n) : 0; },
    MAX: (a) => { const n = numsOf(a); return n.length ? Math.max(...n) : 0; },
    COUNT: (a) => numsOf(a).length,
    COUNTA: (a) => flatArgs(a).filter((x) => String(x ?? "").trim() !== "").length,
    PRODUCT: (a) => numsOf(a).reduce((x, y) => x * y, 1),
    MEDIAN: (a) => {
      const n = numsOf(a).sort((x, y) => x - y);
      if (!n.length) return 0;
      const mid = Math.floor(n.length / 2);
      return n.length % 2 ? n[mid] : (n[mid - 1] + n[mid]) / 2;
    },
    ABS: (a) => Math.abs(toNum(a[0]) || 0),
    SQRT: (a) => Math.sqrt(toNum(a[0]) || 0),
    POWER: (a) => Math.pow(toNum(a[0]) || 0, toNum(a[1]) || 0),
    ROUND: (a) => { const f = Math.pow(10, toNum(a[1]) || 0); return Math.round((toNum(a[0]) || 0) * f) / f; },
    INT: (a) => Math.floor(toNum(a[0]) || 0),
    MOD: (a) => (toNum(a[0]) || 0) % (toNum(a[1]) || 1),
    IF: (a) => (Boolean(a[0]) && a[0] !== 0 && a[0] !== "FALSE" ? a[1] ?? true : a[2] ?? false),
    IFERROR: (a) => (String(a[0] ?? "").startsWith("#") ? a[1] ?? "" : a[0]),
    AND: (a) => flatArgs(a).every((x) => Boolean(x) && x !== 0),
    OR: (a) => flatArgs(a).some((x) => Boolean(x) && x !== 0),
    NOT: (a) => !(Boolean(a[0]) && a[0] !== 0),
    LEN: (a) => String(a[0] ?? "").length,
    UPPER: (a) => String(a[0] ?? "").toUpperCase(),
    LOWER: (a) => String(a[0] ?? "").toLowerCase(),
    TRIM: (a) => String(a[0] ?? "").trim(),
    CONCAT: (a) => flatArgs(a).join(""),
    CONCATENATE: (a) => flatArgs(a).join(""),
    LEFT: (a) => String(a[0] ?? "").slice(0, toNum(a[1]) || 1),
    RIGHT: (a) => { const s = String(a[0] ?? ""), n = toNum(a[1]) || 1; return s.slice(Math.max(0, s.length - n)); },
    MID: (a) => String(a[0] ?? "").substr(Math.max(0, (toNum(a[1]) || 1) - 1), toNum(a[2]) || 1),
    TODAY: () => new Date().toISOString().slice(0, 10),
    NOW: () => new Date().toISOString().replace("T", " ").slice(0, 19),
    PMT: (a) => {
      const r = toNum(a[0]) || 0, nper = toNum(a[1]) || 1, pv = toNum(a[2]) || 0;
      if (r === 0) return -(pv / nper);
      return -((pv * r) / (1 - Math.pow(1 + r, -nper))).toFixed(2);
    },
    COUNTIF: (a) => flatArgs([a[0]]).filter((v) => matchCriteria(v, a[1])).length,
    SUMIF: (a) => {
      const range = flatArgs([a[0]]), crit = a[1], sumRange = a[2] ? flatArgs([a[2]]) : range;
      let total = 0;
      for (let i = 0; i < range.length; i++) {
        if (matchCriteria(range[i], crit)) total += toNum(sumRange[i]) || 0;
      }
      return total;
    }
  };

  function evaluateSheet(cellsObj) {
    const memo = {};
    const visiting = new Set();

    function getCellVal(ref) {
      const key = ref.toUpperCase().replace(/\$/g, "");
      if (key in memo) return memo[key];
      if (visiting.has(key)) return "#REF!";
      const entry = cellsObj[key];
      if (!entry) return "";
      const raw = String(entry.raw ?? entry.value ?? "");
      if (!raw.startsWith("=")) {
        const n = toNum(raw);
        const val = raw.trim() !== "" && !Number.isNaN(n) && /^-?\d+(\.\d+)?$/.test(raw.trim()) ? n : raw;
        memo[key] = val;
        entry.value = String(val);
        return val;
      }
      visiting.add(key);
      let out;
      try {
        out = evalFormulaBody(raw.slice(1).trim(), getCellVal);
      } catch (e) {
        out = "#VALUE!";
      }
      visiting.delete(key);
      if (typeof out === "number" && !Number.isFinite(out)) out = "#DIV/0!";
      if (typeof out === "number") out = Math.round(out * 1e8) / 1e8;
      memo[key] = out;
      entry.value = String(out);
      return out;
    }

    for (const k of Object.keys(cellsObj)) getCellVal(k);
    return cellsObj;
  }

  function evalFormulaBody(expr, getVal) {
    // Handle function calls iteratively from innermost parentheses outward
    const rangeReplaced = expr;
    let guard = 0;
    let work = rangeReplaced;

    // Evaluate innermost FUNC(args) calls
    const fnRegex = /\b([A-Z0-9_]+)\(([^()]*)\)/i;
    const stash = [];
    while (fnRegex.test(work) && guard++ < 80) {
      work = work.replace(fnRegex, (_, fnName, rawInside) => {
        const parts = rawInside.trim() === "" ? [] : rawInside.split(",").map((p) => evalSimpleToken(p.trim(), getVal, stash));
        const fn = FUNCS[fnName.toUpperCase()];
        const res = fn ? fn(parts) : "#NAME?";
        const idx = stash.length;
        stash.push(res);
        return `__STASH_${idx}__`;
      });
    }
    return evalSimpleExpr(work, getVal, stash);
  }

  function evalSimpleToken(tok, getVal, stash) {
    const sm = /^__STASH_(\d+)__$/.exec(tok);
    if (sm) return stash[parseInt(sm[1], 10)];
    if (/^".*"$/.test(tok)) return tok.slice(1, -1);
    const rm = /^(\$?[A-Z]+\$?\d+):(\$?[A-Z]+\$?\d+)$/i.exec(tok);
    if (rm) return expandRange(rm[1], rm[2], getVal);
    return evalSimpleExpr(tok, getVal, stash);
  }

  function evalSimpleExpr(expr, getVal, stash) {
    const trimmed = expr.trim();
    const sm = /^__STASH_(\d+)__$/.exec(trimmed);
    if (sm) return stash[parseInt(sm[1], 10)];
    if (/^".*"$/.test(trimmed)) return trimmed.slice(1, -1);

    // Replace stash placeholders and A1 references into safe arithmetic/comparison JS expression
    let js = trimmed.replace(/__STASH_(\d+)__/g, (_, idx) => JSON.stringify(stash[parseInt(idx, 10)]));
    js = js.replace(/\$?[A-Z]+\$?\d+/gi, (ref) => {
      const v = getVal(ref);
      const n = toNum(v);
      return !Number.isNaN(n) && String(v).trim() !== "" ? String(n) : JSON.stringify(String(v));
    });
    js = js.replace(/<>/g, "!==").replace(/(?<![<>!])=(?!=)/g, "===").replace(/\^/g, "**");
    // eslint-disable-next-line no-new-func
    return Function(`"use strict"; return (${js});`)();
  }

  function buildChartSvg(labels, values, type) {
    const w = 360, h = 210, pad = 32;
    const nums = values.map((v) => Math.max(0, toNum(v) || 0));
    const maxV = Math.max(1, ...nums);
    const colors = ["#2563EB", "#16A34A", "#EA580C", "#7C3AED", "#0D9488", "#DC2626"];
    if (type === "pie") {
      const total = nums.reduce((a, b) => a + b, 0) || 1;
      let acc = 0;
      const cx = 115, cy = 105, r = 74;
      let paths = "";
      nums.forEach((v, i) => {
        const a0 = (acc / total) * Math.PI * 2 - Math.PI / 2;
        acc += v;
        const a1 = (acc / total) * Math.PI * 2 - Math.PI / 2;
        const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
        const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
        const large = v / total > 0.5 ? 1 : 0;
        paths += `<path d="M ${cx} ${cy} L ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)} Z" fill="${colors[i % colors.length]}"/>`;
      });
      const legend = labels.slice(0, 6).map((lb, i) =>
        `<rect x="210" y="${28 + i * 24}" width="11" height="11" rx="3" fill="${colors[i % colors.length]}"/><text x="227" y="${38 + i * 24}" font-size="11" fill="#334155">${String(lb).slice(0, 14)} (${nums[i]})</text>`
      ).join("");
      return `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg"><rect width="${w}" height="${h}" rx="12" fill="#FFFFFF"/>${paths}${legend}</svg>`;
    }
    const plotW = w - pad * 2, plotH = h - pad * 2;
    const step = plotW / Math.max(1, nums.length);
    if (type === "line") {
      const pts = nums.map((v, i) => `${(pad + i * step + step / 2).toFixed(1)},${(h - pad - (v / maxV) * plotH).toFixed(1)}`);
      const circles = nums.map((v, i) => `<circle cx="${(pad + i * step + step / 2).toFixed(1)}" cy="${(h - pad - (v / maxV) * plotH).toFixed(1)}" r="4" fill="#2563EB"/>`).join("");
      return `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg"><rect width="${w}" height="${h}" rx="12" fill="#FFFFFF"/><polyline fill="none" stroke="#2563EB" stroke-width="3" points="${pts.join(" ")}"/>${circles}</svg>`;
    }
    const bars = nums.map((v, i) => {
      const bh = (v / maxV) * plotH;
      const x = pad + i * step + 6;
      const bw = Math.max(10, step - 12);
      const y = h - pad - bh;
      const lbl = String(labels[i] ?? "").slice(0, 7);
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="5" fill="${colors[i % colors.length]}"/><text x="${(x + bw / 2).toFixed(1)}" y="${h - 10}" font-size="10" text-anchor="middle" fill="#475569">${lbl}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg"><rect width="${w}" height="${h}" rx="12" fill="#FFFFFF"/>${bars}</svg>`;
  }

  window.MyOfficeFormula = { colToIdx, idxToCol, evaluateSheet, buildChartSvg };
})();
