"""Public object model for presentation scripts ("MyOffice Script").

Scripts are ordinary Python.  Every object below is a thin *live* proxy over the
dataclasses in :mod:`app.slide.model` that a ``SlideDocument`` owns: reading hits
the real model, writing mutates it immediately.  Safety comes from the runner,
which brackets a whole run with a snapshot (rollback on error, one undo step on
success) — see :mod:`app.slide.script.runner`.

Public surface (kept VBA-familiar on purpose):

    app.presentation            PresentationAPI
      .slides                   SlideListAPI   -> SlideAPI
        .shapes                 ShapeListAPI   -> ShapeAPI
          .paragraphs           ParagraphAPI   -> RunAPI
      .theme                    ThemeAPI

Coordinates are EMU integers (``app.emu(pixels)`` converts).  For convenience a
value smaller than 1000 is treated as pixels and converted, with a one-time note
printed to the output pane — nobody types 914400 by hand.

This module must not import anything from ``app.macros`` except ``errors``, and
must not create widgets: it is the model layer of the scripting feature.
"""
from __future__ import annotations

import copy
from pathlib import Path

from app.macros.errors import MacroError
from app.services.i18n import _t
from app.slide.model import (ANIM_EFFECTS, LAYOUTS, SHAPE_KINDS, SIZE_4_3, SIZE_16_9, THEMES, TRANSITIONS, Animation,
                             Paragraph, Run, Shape, Transition, clone_shape, emu, make_slide, new_id, px)

# --------------------------------------------------------------------------- validation helpers

KIND_ALIASES = {
    "box": "rect", "rectangle": "rect", "textbox": "text", "text_box": "text",
    "round_rect": "roundrect", "rounded_rect": "roundrect", "rounded": "roundrect",
    "stadium": "roundrect", "circle": "ellipse", "oval": "ellipse", "tri": "triangle",
    "arrow": "arrow_right", "right_arrow": "arrow_right", "left_arrow": "arrow_left",
    "star": "star5", "picture": "image", "photo": "image",
}
VALID_KINDS = [k for k in SHAPE_KINDS]
CHART_KINDS = ["column", "bar", "line", "pie", "area", "doughnut", "scatter"]
TEXT_ALIGNS = ["left", "center", "right", "justify"]
VERT_ALIGNS = ["top", "middle", "bottom"]
TRIGGERS = ["click", "with", "after"]
SIZE_PRESETS = {"16:9": SIZE_16_9, "4:3": SIZE_4_3}


def norm_kind(kind: str) -> str:
    k = KIND_ALIASES.get(str(kind).strip().lower(), str(kind).strip().lower())
    if k not in VALID_KINDS:
        raise MacroError(_t("Unknown shape kind {0!r}. Use one of: {1}").format(kind, ", ".join(VALID_KINDS)))
    return k


def check_choice(value, allowed, what: str) -> str:
    v = str(value).strip().lower()
    if v not in allowed:
        raise MacroError(_t("Invalid {0} {1!r}. Use one of: {2}").format(what, value, ", ".join(allowed)))
    return v


def hex_color(value):
    """Accept '#RRGGBB', 'RRGGBB', (r,g,b) or None; return '#rrggbb' or None."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in value))
    s = str(value).strip()
    if not s:
        return None
    if not s.startswith("#"):
        s = "#" + s
    if len(s) not in (4, 7):
        raise MacroError(_t("Invalid color {0!r} — use '#RRGGBB'.").format(value))
    if len(s) == 4:
        s = "#" + "".join(ch * 2 for ch in s[1:])
    try:
        int(s[1:], 16)
    except ValueError as exc:
        raise MacroError(_t("Invalid color {0!r} — use '#RRGGBB'.").format(value)) from exc
    return s.lower()


class RunContext:
    """Shared state for one script run (document handle + capabilities + warnings)."""

    def __init__(self, doc, gate=None):
        self.doc = doc
        self.gate = gate
        self._px_warned = False

    # -- geometry ---------------------------------------------------------
    def u(self, value, axis: str = "x") -> int:
        """EMU integer from an EMU value or (convenience) a pixel value."""
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:
            raise MacroError(_t("{0} must be a number, got {1!r}").format(axis, value)) from exc
        if abs(v) < 1000:
            if not self._px_warned:
                self._px_warned = True
                print(_t("note: values below 1000 are read as pixels (app.emu() converts explicitly)"))
            return emu(v)
        return int(round(v))

    # -- capabilities -----------------------------------------------------
    def read_file(self, path, purpose: str = "") -> bytes:
        if self.gate is None:
            with open(path, "rb") as fh:
                return fh.read()
        return self.gate.read_bytes(path, purpose)


def presentation_for(main, gate=None) -> "PresentationAPI":
    """The deck of the active slide document (what ``app.presentation`` returns)."""
    doc = main.current_of_kind("slide") if main is not None else None
    if doc is None:
        raise MacroError(_t("Open a presentation first (app.new_presentation() creates one)."))
    return PresentationAPI(doc, gate)


# =========================================================================== presentation
class PresentationAPI:
    """The whole deck.  ``app.presentation``."""

    def __init__(self, doc, gate=None):
        self._ctx = RunContext(doc, gate)

    # -- identity ---------------------------------------------------------
    @property
    def _doc(self):
        return self._ctx.doc

    @property
    def _pres(self):
        return self._doc.pres

    @property
    def name(self) -> str:
        return self._doc.display_name()

    @property
    def path(self):
        return str(self._doc.path) if self._doc.path else None

    @property
    def title(self) -> str:
        return self._pres.title

    @title.setter
    def title(self, value):
        self._pres.title = str(value)

    # -- geometry ---------------------------------------------------------
    @property
    def width(self) -> int:
        return self._pres.width

    @property
    def height(self) -> int:
        return self._pres.height

    @property
    def size_px(self):
        return self._pres.size_px

    def set_size(self, preset):
        """``"16:9"``, ``"4:3"`` or ``(width_px, height_px)`` — reuses the editor's own resize."""
        if isinstance(preset, str):
            key = preset.strip()
            if key not in SIZE_PRESETS:
                raise MacroError(_t("Invalid size {0!r}. Use '16:9', '4:3' or (w, h) in pixels.").format(preset))
            size = SIZE_PRESETS[key]
        else:
            try:
                w, h = preset
            except (TypeError, ValueError) as exc:
                raise MacroError(_t("set_size() needs '16:9', '4:3' or (w, h).")) from exc
            w, h = float(w), float(h)
            size = (emu(w), emu(h)) if w < 2000 and h < 2000 else (int(w), int(h))
        self._doc.set_slide_size(size)
        return size

    # -- slides -----------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self._pres.slides)

    def __len__(self) -> int:
        return len(self._pres.slides)

    @property
    def slides(self) -> "SlideListAPI":
        return SlideListAPI(self)

    @property
    def theme(self) -> "ThemeAPI":
        return ThemeAPI(self)

    @property
    def current(self) -> int:
        return self._doc.current

    @current.setter
    def current(self, index: int):
        n = len(self._pres.slides)
        i = int(index)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise MacroError(_t("Slide index {0} is out of range (0..{1}).").format(index, n - 1))
        self._doc.current = i

    def add_slide(self, layout: str = "title_content", title: str = "", body: str = "", index: int | None = None):
        """Append a slide (or insert at ``index``) and return it. Layout keys: LAYOUTS."""
        key = check_choice(layout, list(LAYOUTS), _t("layout"))
        slide = make_slide(self._pres, key, str(title or ""), str(body or ""))
        if index is None:
            self._pres.slides.append(slide)
        else:
            n = len(self._pres.slides)
            i = int(index)
            if i < 0:
                i = max(0, n + i)
            self._pres.slides.insert(max(0, min(i, n)), slide)
        return SlideAPI(self, slide)

    def duplicate(self, index) -> "SlideAPI":
        src = self.slides[index]._slide
        dup = copy.deepcopy(src)
        dup.id = new_id()
        for sh in dup.shapes:
            sh.id = new_id()
        pos = self._pres.slides.index(src) + 1
        self._pres.slides.insert(pos, dup)
        return SlideAPI(self, dup)

    def delete(self, index_or_slide):
        slide = self._resolve_slide(index_or_slide)
        if len(self._pres.slides) <= 1:
            raise MacroError(_t("A presentation must have at least one slide."))
        self._pres.slides.remove(slide)
        self._doc.current = max(0, min(self._doc.current, len(self._pres.slides) - 1))
        return True

    def move(self, from_i, to_i) -> int:
        slides = self._pres.slides
        n = len(slides)
        a, b = int(from_i), int(to_i)
        if a < 0:
            a += n
        if b < 0:
            b += n
        if not (0 <= a < n and 0 <= b < n):
            raise MacroError(_t("move({0}, {1}) is out of range (0..{2}).").format(from_i, to_i, n - 1))
        slides.insert(b, slides.pop(a))
        return b

    def _resolve_slide(self, obj):
        if isinstance(obj, SlideAPI):
            s = obj._slide
        elif isinstance(obj, int):
            s = self.slides[obj]._slide
        else:
            raise MacroError(_t("Expected a slide or a slide index, got {0!r}.").format(obj))
        if s not in self._pres.slides:
            raise MacroError(_t("That slide is no longer part of the presentation."))
        return s

    def apply_theme(self, name: str) -> str:
        """Re-colour the deck with one of THEMES (the editor's own theme switch)."""
        key = name
        if key not in THEMES:
            matches = [k for k in THEMES if k.lower().startswith(str(name).strip().lower())]
            if len(matches) != 1:
                raise MacroError(_t("Unknown theme {0!r}. Use one of: {1}").format(name, ", ".join(THEMES)))
            key = matches[0]
        self._doc.apply_theme(key)
        return key

    def find(self, needle: str):
        """Every shape whose text contains ``needle`` (case-insensitive)."""
        q = str(needle).lower()
        out = []
        for i, sl in enumerate(self._pres.slides):
            api = SlideAPI(self, sl)
            for shape in sl.shapes:
                if q in shape.text.lower():
                    out.append(ShapeAPI(api, shape))
        return out

    # -- file operations --------------------------------------------------
    def save(self) -> bool:
        ok = bool(self._doc.save())
        if ok:
            self._save_sidecar()
        return ok

    def save_as(self) -> bool:
        ok = bool(self._doc.save_as())
        if ok:
            self._save_sidecar()
        return ok

    def _save_sidecar(self):
        store = getattr(self._doc, "script_store", None)
        if store is None or not store.scripts:
            return
        try:
            store.save_for_document(self._doc.path)
        except Exception as exc:  # sidecar trouble must never lose the deck
            print(_t("warning: scripts could not be saved ({0})").format(exc))

    def export_pdf(self, path) -> str:
        self._doc.export_pdf_to(str(path))
        return str(path)

    def refresh(self):
        self._doc.rebuild_all()


# =========================================================================== slides
class SlideListAPI:
    """``app.presentation.slides`` — a live, indexable list of slides."""

    def __init__(self, pres: PresentationAPI):
        self._p = pres

    def _items(self):
        return self._p._pres.slides

    def __len__(self) -> int:
        return len(self._items())

    def __iter__(self):
        for s in list(self._items()):
            yield SlideAPI(self._p, s)

    def __getitem__(self, key):
        items = self._items()
        if isinstance(key, slice):
            indices = range(*key.indices(len(items)))
            return [SlideAPI(self._p, items[i]) for i in indices]
        i = int(key)
        if i < 0:
            i += len(items)
        if not 0 <= i < len(items):
            raise MacroError(_t("Slide index {0} is out of range (0..{1}).").format(key, len(items) - 1))
        return SlideAPI(self._p, items[i])

    def add(self, layout: str = "title_content", title: str = "", body: str = "", index: int | None = None):
        return self._p.add_slide(layout, title, body, index)

    def remove(self, index_or_slide):
        return self._p.delete(index_or_slide)

    def index_of(self, slide) -> int:
        return self._items().index(self._p._resolve_slide(slide))

    @property
    def current(self):
        return SlideAPI(self._p, self._items()[self._p._doc.current])


class SlideAPI:
    """One slide."""

    def __init__(self, pres: PresentationAPI, slide):
        self._p = pres
        self._slide = slide

    # -- plumbing ---------------------------------------------------------
    def _check(self):
        if self._slide not in self._p._pres.slides:
            raise MacroError(_t("This slide is no longer part of the presentation."))
        return self._slide

    @property
    def presentation(self):
        return self._p

    def __eq__(self, other):
        return isinstance(other, SlideAPI) and other._slide is self._slide

    def __hash__(self):
        return id(self._slide)

    # -- identity ---------------------------------------------------------
    @property
    def index(self) -> int:
        self._check()
        return self._p._pres.slides.index(self._slide)

    @property
    def id(self) -> str:
        return self._slide.id

    @property
    def is_current(self) -> bool:
        return self._p._pres.slides[self._p._doc.current] is self._slide

    def select(self):
        self._p._doc.current = self.index
        return self

    # -- content ----------------------------------------------------------
    @property
    def layout(self) -> str:
        return self._slide.layout

    @layout.setter
    def layout(self, value: str):
        key = check_choice(value, list(LAYOUTS), _t("layout"))
        # Same rule as SlideDocument.apply_layout(): add only the placeholders
        # the slide does not have yet, then relabel the layout.
        tmpl = make_slide(self._p._pres, key)
        have = {sh.placeholder for sh in self._slide.shapes if sh.placeholder}
        for sh in tmpl.shapes:
            if sh.placeholder not in have:
                self._slide.shapes.append(sh)
        self._slide.layout = key

    @property
    def hidden(self) -> bool:
        return self._slide.hidden

    @hidden.setter
    def hidden(self, value: bool):
        self._slide.hidden = bool(value)

    @property
    def notes(self) -> str:
        return self._slide.notes

    @notes.setter
    def notes(self, value: str):
        self._slide.notes = "" if value is None else str(value)

    @property
    def title(self) -> str:
        for sh in self._slide.shapes:
            if sh.placeholder == "title":
                return sh.text
        return ""

    @title.setter
    def title(self, value: str):
        for sh in self._slide.shapes:
            if sh.placeholder == "title":
                _apply_text(sh, "" if value is None else str(value), keep_format=True)
                return
        raise MacroError(_t("This slide has no title placeholder. Add a text box instead."))

    @property
    def background(self):
        return self._slide.background

    @background.setter
    def background(self, color):
        self._slide.background = hex_color(color)
        if color is None:
            self._slide.background_image = None

    @property
    def transition(self) -> str:
        return self._slide.transition.kind

    @transition.setter
    def transition(self, kind):
        self.set_transition(kind)

    def set_transition(self, kind: str | None = None, duration: float | None = None, direction: str | None = None,
                       all_slides: bool = False):
        targets = self._p._pres.slides if all_slides else [self._slide]
        for s in targets:
            old = s.transition or Transition()
            s.transition = Transition(check_choice(kind, TRANSITIONS, _t("transition")) if kind is not None else old.kind,
                                      float(duration) if duration is not None else old.duration,
                                      check_choice(direction, ["left", "right", "top", "bottom"], _t("direction"))
                                      if direction is not None else old.direction)

    def set_background(self, color=None, image=None, all_slides: bool = False):
        """Solid colour and/or a background picture (``image`` = path or bytes)."""
        targets = self._p._pres.slides if all_slides else [self._slide]
        if image is not None:
            data, _ext = _image_bytes(self._p._ctx, image)
            for s in targets:
                s.background_image = data
        for s in targets:
            s.background = hex_color(color) if color is not None else (None if image is not None else s.background)

    # -- shapes -----------------------------------------------------------
    @property
    def shapes(self) -> "ShapeListAPI":
        return ShapeListAPI(self)

    def texts(self):
        return [sh.text for sh in self._slide.shapes if sh.text.strip()]

    def clear(self):
        self._slide.shapes = []

    def duplicate(self) -> "SlideAPI":
        return self._p.duplicate(self.index)

    def delete(self):
        return self._p.delete(self)

    def move(self, delta: int) -> int:
        return self._p.move(self.index, self.index + int(delta))

    def __repr__(self):
        return "<Slide #{0} {1!r}>".format(self.index + 1, self.title[:24])


# =========================================================================== shapes
class ShapeListAPI:
    """``slide.shapes`` — create, find and remove shapes."""

    def __init__(self, slide: SlideAPI):
        self._s = slide

    def _items(self):
        return self._s._check().shapes

    def __len__(self) -> int:
        return len(self._items())

    def __iter__(self):
        for sh in list(self._items()):
            yield ShapeAPI(self._s, sh)

    def __getitem__(self, key):
        items = self._items()
        if isinstance(key, str):
            hit = self.find(key)
            if hit is None:
                raise MacroError(_t("No shape named {0!r} on slide {1}.").format(key, self._s.index + 1))
            return hit
        i = int(key)
        if i < 0:
            i += len(items)
        if not 0 <= i < len(items):
            raise MacroError(_t("Shape index {0} is out of range (0..{1}).").format(key, len(items) - 1))
        return ShapeAPI(self._s, items[i])

    def find(self, name: str):
        for sh in self._items():
            if sh.name == name or sh.id == name:
                return ShapeAPI(self._s, sh)
        return None

    def names(self):
        return [sh.name or sh.id for sh in self._items()]

    # -- creation ---------------------------------------------------------
    def add_textbox(self, x, y, w, h, text: str = "", size: float | None = None, bold: bool | None = None,
                    color=None, align: str = "left", font: str | None = None, fill=None, line=None,
                    name: str = ""):
        ctx = self._s._p._ctx
        th = self._s._p._pres.theme
        sh = Shape("text", ctx.u(x, "x"), ctx.u(y, "y"), ctx.u(w, "w"), ctx.u(h, "h"),
                   name=name or _default_name(self._items(), "TextBox"))
        sh.fill = hex_color(fill)
        sh.line = hex_color(line)
        align = check_choice(align, TEXT_ALIGNS, _t("alignment"))
        sh.set_text("" if text is None else str(text), size=size if size is not None else 18,
                    bold=bold, color=hex_color(color) or th.text_color, align=align)
        for p in sh.paragraphs:
            for r in p.runs:
                r.font = font or th.body_font
        self._items().append(sh)
        return ShapeAPI(self._s, sh)

    def add_shape(self, kind: str, x, y, w, h, text: str = "", fill=None, line=None, size: float | None = None,
                  color=None, align: str = "center", name: str = ""):
        """A drawn shape. Kinds: see model.SHAPE_KINDS (aliases accepted, e.g. 'round_rect')."""
        k = norm_kind(kind)
        ctx = self._s._p._ctx
        th = self._s._p._pres.theme
        sh = Shape(k, ctx.u(x, "x"), ctx.u(y, "y"), ctx.u(w, "w"), ctx.u(h, "h"),
                   name=name or _default_name(self._items(), k.capitalize()))
        if k in ("line", "arrow_line"):
            # Same defaults as SlideDocument.add_shape_at().
            sh.line = hex_color(line) or th.text_color
            sh.line_width = 2.0
        else:
            sh.fill = hex_color(fill) or th.accent
            sh.set_text("" if text is None else str(text), size=size if size is not None else 18,
                        color=hex_color(color) or "#ffffff", align=check_choice(align, TEXT_ALIGNS, _t("alignment")))
            sh.valign = "middle"
            for p in sh.paragraphs:
                for r in p.runs:
                    r.font = th.body_font
        if line is not None and k not in ("line", "arrow_line"):
            sh.line = hex_color(line)
        self._items().append(sh)
        return ShapeAPI(self._s, sh)

    def add_picture(self, source, x=None, y=None, w=None, h=None, name: str = ""):
        """Insert an image from a path or raw bytes.  Reading a path may need permission."""
        data, ext = _image_bytes(self._s._p._ctx, source)
        iw, ih = _image_size(data)
        ctx = self._s._p._ctx
        # Default placement/size mirrors the editor: centred, at most 60% of the slide.
        W, H = self._s._p._pres.size_px
        if w is None and h is None:
            s = min(1.0, W * 0.6 / iw, H * 0.6 / ih)
            w, h = iw * s, ih * s
        elif w is None:
            w = float(h) * iw / ih
        elif h is None:
            h = float(w) * ih / iw
        w_emu = ctx.u(w, "w") if isinstance(w, (int, float)) else ctx.u(w, "w")
        h_emu = ctx.u(h, "h") if isinstance(h, (int, float)) else ctx.u(h, "h")
        x_emu = ctx.u(x, "x") if x is not None else int((self._s._p._pres.width - w_emu) / 2)
        y_emu = ctx.u(y, "y") if y is not None else int((self._s._p._pres.height - h_emu) / 2)
        sh = Shape("image", x_emu, y_emu, w_emu, h_emu, image=data, image_ext=ext,
                   name=name or _default_name(self._items(), _t("Picture")))
        self._items().append(sh)
        return ShapeAPI(self._s, sh)

    def add_table(self, rows, x=0, y=0, w=None, h=None, header: bool = True, color=None, name: str = ""):
        data = _table_rows(rows)
        ctx = self._s._p._ctx
        th = self._s._p._pres.theme
        W, H = self._s._p._pres.size_px
        w = w if w is not None else W * 0.7
        h = h if h is not None else min(H * 0.6, 40 * len(data))
        sh = Shape("table", ctx.u(x, "x"), ctx.u(y, "y"), ctx.u(w, "w"), ctx.u(h, "h"),
                   name=name or _default_name(self._items(), _t("Table")))
        sh.table = data
        sh.table_style = {"header": bool(header), "banded": True, "color": hex_color(color) or th.accent}
        self._items().append(sh)
        return ShapeAPI(self._s, sh)

    def add_chart(self, kind: str, categories, series, title: str = "", x=0, y=0, w=None, h=None,
                  legend: bool = True, name: str = ""):
        """``series`` is ``[[name, [values]], ...]`` — exactly what pptx_io writes."""
        k = check_choice(kind, CHART_KINDS, _t("chart kind"))
        data = _chart_dict(k, categories, series, title, legend)
        ctx = self._s._p._ctx
        W, H = self._s._p._pres.size_px
        w = w if w is not None else W * 0.6
        h = h if h is not None else H * 0.6
        sh = Shape("chart", ctx.u(x, "x"), ctx.u(y, "y"), ctx.u(w, "w"), ctx.u(h, "h"),
                   name=name or _default_name(self._items(), _t("Chart")))
        sh.chart = data
        self._items().append(sh)
        return ShapeAPI(self._s, sh)

    # -- bulk -------------------------------------------------------------
    def remove(self, shape):
        target = shape._shape if isinstance(shape, ShapeAPI) else shape
        if target not in self._items():
            raise MacroError(_t("That shape is not on this slide."))
        self._items().remove(target)
        return True

    def clear(self):
        n = len(self._items())
        self._s._check().shapes = []
        return n

    def top(self):
        items = self._items()
        return ShapeAPI(self._s, items[-1]) if items else None

    def bottom(self):
        items = self._items()
        return ShapeAPI(self._s, items[0]) if items else None


class ShapeAPI:
    """One shape: geometry, text, style, media, table, chart, animation, z-order."""

    def __init__(self, slide: SlideAPI, shape):
        self._s = slide
        self._shape = shape

    # -- plumbing ---------------------------------------------------------
    def _check(self):
        if self._shape not in self._s._check().shapes:
            raise MacroError(_t("This shape is no longer on the slide."))
        return self._shape

    def __repr__(self):
        label = self._shape.name or self._shape.kind
        txt = self._shape.text.strip().splitlines()[0][:24] if self._shape.text.strip() else ""
        return "<Shape {0!r}{1}>".format(label, (" — " + txt) if txt else "")

    def __eq__(self, other):
        return isinstance(other, ShapeAPI) and other._shape is self._shape

    def __hash__(self):
        return id(self._shape)

    # -- identity ---------------------------------------------------------
    @property
    def id(self) -> str:
        return self._shape.id

    @property
    def name(self) -> str:
        return self._shape.name

    @name.setter
    def name(self, value: str):
        self._shape.name = str(value)

    @property
    def kind(self) -> str:
        return self._shape.kind

    @property
    def placeholder(self):
        """``'title'``, ``'body'``, ``'subtitle'`` or None — same values the editor uses."""
        return self._shape.placeholder

    @placeholder.setter
    def placeholder(self, value):
        self._shape.placeholder = str(value) if value else None

    @property
    def autofit(self) -> bool:
        return self._shape.autofit

    @autofit.setter
    def autofit(self, value: bool):
        self._shape.autofit = bool(value)

    @property
    def has_text(self) -> bool:
        return bool(self._shape.text.strip())

    @property
    def slide(self) -> SlideAPI:
        return self._s

    # -- geometry (EMU) ---------------------------------------------------
    @property
    def x(self) -> int:
        return self._shape.x

    @x.setter
    def x(self, v):
        self._shape.x = self._s._p._ctx.u(v, "x")

    @property
    def y(self) -> int:
        return self._shape.y

    @y.setter
    def y(self, v):
        self._shape.y = self._s._p._ctx.u(v, "y")

    @property
    def w(self) -> int:
        return self._shape.w

    @w.setter
    def w(self, v):
        self._shape.w = max(1, self._s._p._ctx.u(v, "w"))

    @property
    def h(self) -> int:
        return self._shape.h

    @h.setter
    def h(self, v):
        self._shape.h = max(1, self._s._p._ctx.u(v, "h"))

    @property
    def rect_px(self):
        return (px(self._shape.x), px(self._shape.y), px(self._shape.w), px(self._shape.h))

    def move_to(self, x, y):
        self.x, self.y = x, y
        return self

    def resize(self, w, h):
        self.w, self.h = w, h
        return self

    def offset(self, dx, dy):
        ctx = self._s._p._ctx
        self._shape.x += ctx.u(dx, "dx")
        self._shape.y += ctx.u(dy, "dy")
        return self

    @property
    def rotation(self) -> float:
        return self._shape.rotation

    @rotation.setter
    def rotation(self, deg: float):
        self._shape.rotation = float(deg) % 360.0

    # -- text -------------------------------------------------------------
    @property
    def text(self) -> str:
        return self._shape.text

    @text.setter
    def text(self, value: str):
        _apply_text(self._shape, "" if value is None else str(value), keep_format=True)

    def set_text(self, text: str, size: float | None = None, bold: bool | None = None, color=None,
                 align: str = "left", bullet: bool = False, font: str | None = None):
        align = check_choice(align, TEXT_ALIGNS, _t("alignment"))
        self._shape.set_text(str(text), size=size, bold=bold, color=hex_color(color), align=align, bullet=bool(bullet))
        if font:
            for p in self._shape.paragraphs:
                for r in p.runs:
                    r.font = font
        return self

    @property
    def paragraphs(self):
        return [ParagraphAPI(self, p) for p in self._shape.paragraphs]

    def add_paragraph(self, text: str = "", size: float | None = None, color=None, align: str = "left",
                      bullet: bool = False, level: int = 0):
        run = Run(str(text), size=size, color=hex_color(color))
        p = Paragraph([run], check_choice(align, TEXT_ALIGNS, _t("alignment")), int(level), bool(bullet))
        self._shape.paragraphs.append(p)
        return ParagraphAPI(self, p)

    def _each_run(self, **fmt):
        for p in self._shape.paragraphs:
            for r in p.runs:
                for key, val in fmt.items():
                    if val is not None:
                        setattr(r, key, val)
        return self

    @property
    def font(self):
        return self._first_run("font")

    @font.setter
    def font(self, value):
        self._each_run(font=str(value))

    @property
    def size(self):
        return self._first_run("size")

    @size.setter
    def size(self, value):
        self._each_run(size=float(value))

    @property
    def bold(self):
        return bool(self._first_run("bold"))

    @bold.setter
    def bold(self, value):
        self._each_run(bold=bool(value))

    @property
    def italic(self):
        return bool(self._first_run("italic"))

    @italic.setter
    def italic(self, value):
        self._each_run(italic=bool(value))

    @property
    def underline(self):
        return bool(self._first_run("underline"))

    @underline.setter
    def underline(self, value):
        self._each_run(underline=bool(value))

    @property
    def color(self):
        return self._first_run("color")

    @color.setter
    def color(self, value):
        self._each_run(color=hex_color(value))

    def _first_run(self, attr):
        for p in self._shape.paragraphs:
            for r in p.runs:
                return getattr(r, attr)
        return None

    @property
    def align(self) -> str:
        return self._shape.paragraphs[0].align if self._shape.paragraphs else "left"

    @align.setter
    def align(self, value):
        v = check_choice(value, TEXT_ALIGNS, _t("alignment"))
        for p in self._shape.paragraphs:
            p.align = v

    @property
    def valign(self) -> str:
        return self._shape.valign

    @valign.setter
    def valign(self, value):
        self._shape.valign = check_choice(value, VERT_ALIGNS, _t("vertical alignment"))

    @property
    def bullet(self) -> bool:
        return bool(self._shape.paragraphs[0].bullet) if self._shape.paragraphs else False

    @bullet.setter
    def bullet(self, value: bool):
        for p in self._shape.paragraphs:
            p.bullet = bool(value)

    # -- style ------------------------------------------------------------
    @property
    def fill(self):
        return self._shape.fill

    @fill.setter
    def fill(self, value):
        self._shape.fill = hex_color(value)

    @property
    def line(self):
        return self._shape.line

    @line.setter
    def line(self, value):
        self._shape.line = hex_color(value)

    @property
    def line_width(self) -> float:
        return self._shape.line_width

    @line_width.setter
    def line_width(self, pt: float):
        self._shape.line_width = max(0.0, float(pt))

    @property
    def opacity(self) -> float:
        return self._shape.opacity

    @opacity.setter
    def opacity(self, value: float):
        self._shape.opacity = max(0.0, min(1.0, float(value)))

    @property
    def shadow(self) -> bool:
        return self._shape.shadow

    @shadow.setter
    def shadow(self, value: bool):
        self._shape.shadow = bool(value)

    # -- media ------------------------------------------------------------
    @property
    def image(self):
        return self._shape.image

    @property
    def image_ext(self) -> str:
        return self._shape.image_ext

    def set_image(self, source):
        data, ext = _image_bytes(self._s._p._ctx, source)
        self._shape.image = data
        self._shape.image_ext = ext
        return self

    # -- table ------------------------------------------------------------
    @property
    def table(self):
        return self._shape.table

    @table.setter
    def table(self, rows):
        self._shape.table = _table_rows(rows)
        self._shape.kind = "table"

    def set_cell(self, row: int, col: int, text):
        if not self._shape.table:
            raise MacroError(_t("This shape is not a table."))
        rows = self._shape.table
        try:
            rows[int(row)][int(col)] = "" if text is None else str(text)
        except IndexError as exc:
            raise MacroError(_t("Table cell ({0}, {1}) does not exist.").format(row, col)) from exc
        return self

    @property
    def table_style(self):
        return dict(self._shape.table_style)

    # -- chart ------------------------------------------------------------
    @property
    def chart(self):
        return self._shape.chart

    def set_chart(self, kind: str, categories, series, title: str = "", legend: bool = True):
        k = check_choice(kind, CHART_KINDS, _t("chart kind"))
        self._shape.chart = _chart_dict(k, categories, series, title, legend)
        self._shape.kind = "chart"
        return self

    # -- animation --------------------------------------------------------
    @property
    def animation(self) -> dict:
        a = self._shape.animation
        return {"effect": a.effect, "trigger": a.trigger, "duration": a.duration, "delay": a.delay,
                "direction": a.direction, "order": a.order}

    def animate(self, effect: str = "fade", trigger: str = "click", duration: float = 0.5, delay: float = 0.0,
                direction: str = "bottom"):
        eff = check_choice(effect, [e for e in ANIM_EFFECTS if e != "none"], _t("animation effect"))
        trig = check_choice(trigger, TRIGGERS, _t("animation trigger"))
        order = max([s.animation.order for s in self._s._check().shapes if s.animation.effect != "none"] + [-1]) + 1
        self._shape.animation = Animation(eff, trig, float(duration), float(delay),
                                          check_choice(direction, ["left", "right", "top", "bottom"], _t("direction")),
                                          order)
        return self

    def clear_animation(self):
        self._shape.animation = Animation()
        return self

    # -- z-order ----------------------------------------------------------
    def bring_to_front(self):
        items = self._s._check().shapes
        items.remove(self._shape)
        items.append(self._shape)
        return self

    def send_to_back(self):
        items = self._s._check().shapes
        items.remove(self._shape)
        items.insert(0, self._shape)
        return self

    # -- lifecycle --------------------------------------------------------
    def duplicate(self, dx=0, dy=0):
        """Copy of this shape on the same slide, offset by (dx, dy)."""
        ctx = self._s._p._ctx
        clone = clone_shape(self._shape, ctx.u(dx, "dx") if dx else 0, ctx.u(dy, "dy") if dy else 0)
        self._s._check().shapes.append(clone)
        return ShapeAPI(self._s, clone)

    def delete(self):
        self._s._check().shapes.remove(self._shape)
        return True

    def select(self):
        """Show this shape selected in the editor (after the run refreshes)."""
        self._s._p._doc.select_shape_after_run(self._s.index, self._shape.id)
        return self


class ParagraphAPI:
    def __init__(self, shape: ShapeAPI, paragraph):
        self._sh = shape
        self._p = paragraph

    @property
    def text(self) -> str:
        return self._p.text

    @text.setter
    def text(self, value: str):
        keep = self._p.runs[0] if self._p.runs else None
        self._p.runs = [Run(str(value), bold=keep.bold if keep else None, italic=keep.italic if keep else None,
                            underline=keep.underline if keep else None, size=keep.size if keep else None,
                            color=keep.color if keep else None, font=keep.font if keep else None)]

    @property
    def align(self) -> str:
        return self._p.align

    @align.setter
    def align(self, value):
        self._p.align = check_choice(value, TEXT_ALIGNS, _t("alignment"))

    @property
    def level(self) -> int:
        return self._p.level

    @level.setter
    def level(self, value):
        self._p.level = max(0, int(value))

    @property
    def bullet(self) -> bool:
        return self._p.bullet

    @bullet.setter
    def bullet(self, value):
        self._p.bullet = bool(value)

    @property
    def runs(self):
        return [RunAPI(self, r) for r in self._p.runs]

    def add_run(self, text: str, bold=None, italic=None, underline=None, size=None, color=None, font=None):
        run = Run(str(text), bold=bold, italic=italic, underline=underline, size=size, color=hex_color(color), font=font)
        self._p.runs.append(run)
        return RunAPI(self, run)

    def __repr__(self):
        return "<Paragraph {0!r}>".format(self.text[:32])


class RunAPI:
    def __init__(self, paragraph: ParagraphAPI, run):
        self._p = paragraph
        self._r = run

    @property
    def text(self) -> str:
        return self._r.text

    @text.setter
    def text(self, value: str):
        self._r.text = str(value)

    @property
    def bold(self):
        return self._r.bold

    @bold.setter
    def bold(self, v):
        self._r.bold = bool(v)

    @property
    def italic(self):
        return self._r.italic

    @italic.setter
    def italic(self, v):
        self._r.italic = bool(v)

    @property
    def underline(self):
        return self._r.underline

    @underline.setter
    def underline(self, v):
        self._r.underline = bool(v)

    @property
    def size(self):
        return self._r.size

    @size.setter
    def size(self, v):
        self._r.size = float(v)

    @property
    def color(self):
        return self._r.color

    @color.setter
    def color(self, v):
        self._r.color = hex_color(v)

    @property
    def font(self):
        return self._r.font

    @font.setter
    def font(self, v):
        self._r.font = str(v)

    def __repr__(self):
        return "<Run {0!r}>".format(self.text[:24])


class ThemeAPI:
    def __init__(self, pres: PresentationAPI):
        self._p = pres

    @property
    def _theme(self):
        return self._p._pres.theme

    @property
    def name(self) -> str:
        return self._theme.name

    @property
    def heading_font(self) -> str:
        return self._theme.heading_font

    @heading_font.setter
    def heading_font(self, value: str):
        self._theme.heading_font = str(value)

    @property
    def body_font(self) -> str:
        return self._theme.body_font

    @body_font.setter
    def body_font(self, value: str):
        self._theme.body_font = str(value)

    @property
    def accent(self):
        return self._theme.accent

    @accent.setter
    def accent(self, value):
        self._theme.accent = hex_color(value)

    @property
    def title_color(self):
        return self._theme.title_color

    @property
    def text_color(self):
        return self._theme.text_color

    def set(self, name: str) -> str:
        return self._p.apply_theme(name)

    def names(self):
        return list(THEMES)

    def __repr__(self):
        return "<Theme {0!r}>".format(self.name)


# =========================================================================== internal helpers
def _apply_text(shape: Shape, text: str, keep_format: bool = False):
    """Replace a shape's text, optionally keeping the first run's formatting."""
    if not keep_format:
        shape.set_text(text)
        return
    base = None
    par = shape.paragraphs[0] if shape.paragraphs else None
    for p in shape.paragraphs:
        if p.runs:
            base = p.runs[0]
            par = p
            break
    shape.set_text(text,
                   size=base.size if base else None,
                   bold=base.bold if base else None,
                   color=base.color if base else None,
                   align=par.align if par else "left",
                   bullet=par.bullet if par else False)
    if base is not None:
        for p in shape.paragraphs:
            for r in p.runs:
                r.italic, r.underline, r.font = base.italic, base.underline, base.font


def _default_name(shapes, base: str) -> str:
    used = {s.name for s in shapes}
    if base not in used:
        return base
    i = 2
    while f"{base} {i}" in used:
        i += 1
    return f"{base} {i}"


def _table_rows(rows) -> list:
    if not isinstance(rows, (list, tuple)) or not rows:
        raise MacroError(_t("A table needs a non-empty list of rows, e.g. [['A', 'B'], ['1', '2']]."))
    out = []
    width = 0
    for row in rows:
        if not isinstance(row, (list, tuple)):
            raise MacroError(_t("Each table row must be a list of cell texts."))
        width = max(width, len(row))
        out.append(["" if c is None else str(c) for c in row])
    for row in out:
        while len(row) < width:
            row.append("")
    return out


def _chart_dict(kind, categories, series, title, legend) -> dict:
    cats = [c for c in (categories or [])]
    if kind != "scatter" and not cats:
        raise MacroError(_t("A chart needs categories, e.g. ['Jan', 'Feb', 'Mar']."))
    norm = []
    if not isinstance(series, (list, tuple)) or not series:
        raise MacroError(_t("A chart needs at least one series: [['Sales', [1, 2, 3]]]."))
    for item in series:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise MacroError(_t("Each series must be [name, [values]]."))
        name, values = item
        vals = []
        for v in values:
            if v is None:
                vals.append(None)
            else:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError) as exc:
                    raise MacroError(_t("Chart value {0!r} is not a number.").format(v)) from exc
        if kind != "scatter" and len(vals) != len(cats):
            raise MacroError(_t("Series {0!r} has {1} values but there are {2} categories.")
                             .format(name, len(vals), len(cats)))
        norm.append([str(name), vals])
    return {"kind": kind, "categories": cats, "series": norm, "title": str(title or ""), "legend": bool(legend)}


def _image_bytes(ctx: RunContext, source):
    """``source`` = path (permission-gated) or bytes; returns (data, extension)."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        return data, _guess_ext(data)
    if hasattr(source, "save"):  # QImage/QPixmap passed by the app itself
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        source.save(buf, "PNG")
        return bytes(ba.data()), "png"
    path = Path(str(source))
    if not path.is_file():
        raise MacroError(_t("Image not found: {0}").format(path))
    data = ctx.read_file(path, _t("read an image"))
    if not data:
        raise MacroError(_t("Image file is empty: {0}").format(path))
    return data, (path.suffix.lstrip(".").lower() or _guess_ext(data))


def _guess_ext(data: bytes) -> str:
    if data[:8].startswith(b"\x89PNG"):
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpg"
    if data[:4] == b"GIF8":
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"


def _image_size(data: bytes):
    """(width, height) in pixels; falls back to 4:3 when the image cannot be read."""
    from PySide6.QtGui import QImage

    img = QImage.fromData(data)
    if img.isNull() or img.width() <= 0:
        raise MacroError(_t("The image could not be read (unsupported or corrupt file)."))
    return img.width(), img.height()
