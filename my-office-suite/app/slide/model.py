"""Presentation data model (EMU geometry, rich text, shapes, animations, transitions)."""
from __future__ import annotations

import base64
import copy
import uuid
from dataclasses import asdict, dataclass, field

EMU_PER_INCH = 914400
EMU_PER_PX = 9525            # 96 dpi
EMU_PER_PT = 12700
SIZE_16_9 = (12192000, 6858000)
SIZE_4_3 = (9144000, 6858000)

SHAPE_KINDS = ["text", "rect", "roundrect", "ellipse", "triangle", "diamond", "pentagon", "hexagon", "arrow_right",
               "arrow_left", "star5", "line", "arrow_line", "image", "table", "chart"]
ANIM_EFFECTS = ["none", "appear", "fade", "fly_in", "zoom", "wipe", "float_in"]
TRANSITIONS = ["none", "fade", "push", "wipe", "cover", "zoom", "split"]


def px(emu: float) -> float:
    return emu / EMU_PER_PX


def emu(pixels: float) -> int:
    return int(round(pixels * EMU_PER_PX))


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class Run:
    text: str = ""
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    size: float | None = None        # points
    color: str | None = None
    font: str | None = None
    outline: str | None = None       # text outline colour (WordArt)
    outline_width: float = 0.0       # points
    gradient: str | None = None      # "#rrggbb,#rrggbb" vertical gradient fill (WordArt)


@dataclass
class Paragraph:
    runs: list = field(default_factory=list)
    align: str = "left"              # left, center, right, justify
    level: int = 0
    bullet: bool = False
    rtl: bool = False

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class Animation:
    effect: str = "none"
    trigger: str = "click"           # click, with, after
    duration: float = 0.5
    delay: float = 0.0
    direction: str = "bottom"        # for fly_in / wipe: left, right, top, bottom
    order: int = 0


@dataclass
class Shape:
    kind: str = "text"
    x: int = 0
    y: int = 0
    w: int = 914400
    h: int = 457200
    rotation: float = 0.0
    fill: str | None = None
    line: str | None = None
    line_width: float = 1.0          # points
    paragraphs: list = field(default_factory=list)
    valign: str = "top"              # top, middle, bottom
    autofit: bool = False
    placeholder: str | None = None   # title, body, subtitle
    image: bytes | None = None
    image_ext: str = "png"
    table: list | None = None        # rows of cell strings
    table_style: dict = field(default_factory=lambda: {"header": True, "banded": True, "color": "#4472c4"})
    chart: dict | None = None        # {"kind": ..., "categories": [...], "series": [[name, [values]]], "title": ...}
    animation: Animation = field(default_factory=Animation)
    name: str = ""
    id: str = field(default_factory=new_id)
    shadow: bool = False
    opacity: float = 1.0

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)

    def set_text(self, text: str, size: float | None = None, bold: bool | None = None, color: str | None = None,
                 align: str = "left", bullet: bool = False):
        self.paragraphs = [Paragraph([Run(line, bold=bold, size=size, color=color)], align, 0, bullet)
                           for line in text.split("\n")]


@dataclass
class Transition:
    kind: str = "none"
    duration: float = 0.6
    direction: str = "left"


@dataclass
class Slide:
    shapes: list = field(default_factory=list)
    background: str | None = None
    background_image: bytes | None = None
    layout: str = "title_content"
    notes: str = ""
    transition: Transition = field(default_factory=Transition)
    hidden: bool = False
    id: str = field(default_factory=new_id)

    def animated_shapes(self) -> list:
        items = [s for s in self.shapes if s.animation.effect != "none"]
        return sorted(items, key=lambda s: s.animation.order)


@dataclass
class Theme:
    name: str = "Office"
    background: str = "#ffffff"
    title_color: str = "#1f3864"
    text_color: str = "#262626"
    accent: str = "#4472c4"
    heading_font: str = "Calibri Light"
    body_font: str = "Calibri"


THEMES = {
    "Office": Theme(),
    "Ocean": Theme("Ocean", "#eaf4fb", "#0b4f6c", "#1d3557", "#2a9d8f", "Calibri Light", "Calibri"),
    "Midnight": Theme("Midnight", "#1e2235", "#ffffff", "#e6e8ef", "#f4a261", "Calibri Light", "Calibri"),
    "Sunrise": Theme("Sunrise", "#fff7ed", "#9a3412", "#431407", "#f97316", "Georgia", "Calibri"),
    "Forest": Theme("Forest", "#f0f7f0", "#14532d", "#1f2937", "#16a34a", "Calibri Light", "Calibri"),
    "Royal": Theme("Royal", "#f5f3ff", "#4c1d95", "#1e1b4b", "#7c3aed", "Cambria", "Calibri"),
}


@dataclass
class Presentation:
    slides: list = field(default_factory=list)
    width: int = SIZE_16_9[0]
    height: int = SIZE_16_9[1]
    theme: Theme = field(default_factory=Theme)
    title: str = ""

    @property
    def size_px(self) -> tuple[float, float]:
        return px(self.width), px(self.height)


# ============================================================================= layouts
LAYOUTS = {
    "title": "Title Slide", "title_content": "Title and Content", "section": "Section Header",
    "two_content": "Two Content", "title_only": "Title Only", "blank": "Blank", "comparison": "Comparison",
}


def make_slide(pres: Presentation, layout: str = "title_content", title: str = "", body: str = "") -> Slide:
    W, H = pres.width, pres.height
    th = pres.theme
    s = Slide(layout=layout)
    m = int(W * 0.06)

    def title_shape(y, h, size, text, align="left"):
        sh = Shape("text", m, y, W - 2 * m, h, placeholder="title", valign="middle" if layout != "title_content" else "bottom",
                   name="Title")
        sh.set_text(text or "Click to add title", size=size, color=th.title_color, align=align)
        for p in sh.paragraphs:
            for r in p.runs:
                r.font = th.heading_font
        sh.autofit = True
        return sh

    def body_shape(x, y, w, h, text, placeholder="body"):
        sh = Shape("text", x, y, w, h, placeholder=placeholder, name="Content")
        lines = (text or "Click to add text").split("\n")
        sh.paragraphs = [Paragraph([Run(t, size=24 if placeholder == "body" else 20, color=th.text_color, font=th.body_font)],
                                   "left", 0, placeholder == "body") for t in lines]
        return sh

    if layout == "title":
        s.shapes.append(title_shape(int(H * 0.28), int(H * 0.22), 44, title, "center"))
        sub = Shape("text", m, int(H * 0.53), W - 2 * m, int(H * 0.14), placeholder="subtitle", name="Subtitle")
        sub.set_text(body or "Click to add subtitle", size=24, color=th.text_color, align="center")
        s.shapes.append(sub)
    elif layout == "section":
        s.shapes.append(title_shape(int(H * 0.35), int(H * 0.2), 40, title))
        sub = Shape("text", m, int(H * 0.56), W - 2 * m, int(H * 0.12), placeholder="subtitle", name="Subtitle")
        sub.set_text(body or "Section description", size=20, color=th.accent)
        s.shapes.append(sub)
    elif layout == "title_only":
        s.shapes.append(title_shape(int(H * 0.05), int(H * 0.16), 36, title))
    elif layout in ("two_content", "comparison"):
        s.shapes.append(title_shape(int(H * 0.05), int(H * 0.16), 36, title))
        colw = (W - 3 * m) // 2
        s.shapes.append(body_shape(m, int(H * 0.25), colw, int(H * 0.65), body))
        s.shapes.append(body_shape(2 * m + colw, int(H * 0.25), colw, int(H * 0.65), ""))
    elif layout == "blank":
        pass
    else:
        s.shapes.append(title_shape(int(H * 0.05), int(H * 0.16), 36, title))
        s.shapes.append(body_shape(m, int(H * 0.25), W - 2 * m, int(H * 0.65), body))
    return s


# ============================================================================= serialisation (undo / clipboard)
def _enc(obj):
    if isinstance(obj, bytes):
        return {"__b64__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, dict):
        return {k: _enc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_enc(v) for v in obj]
    return obj


def _dec(obj):
    if isinstance(obj, dict):
        if "__b64__" in obj:
            return base64.b64decode(obj["__b64__"])
        return {k: _dec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec(v) for v in obj]
    return obj


def shape_to_dict(s: Shape) -> dict:
    return _enc(asdict(s))


def shape_from_dict(d: dict) -> Shape:
    d = _dec(copy.deepcopy(d))
    paras = [Paragraph([Run(**r) for r in p.get("runs", [])], p.get("align", "left"), p.get("level", 0),
                       p.get("bullet", False), p.get("rtl", False)) for p in d.pop("paragraphs", [])]
    anim = Animation(**d.pop("animation", {}))
    s = Shape(**{k: v for k, v in d.items() if k in Shape.__dataclass_fields__})
    s.paragraphs = paras
    s.animation = anim
    return s


def slide_to_dict(s: Slide) -> dict:
    d = _enc(asdict(s))
    return d


def slide_from_dict(d: dict) -> Slide:
    d = copy.deepcopy(d)
    shapes = [shape_from_dict(x) for x in d.pop("shapes", [])]
    tr = Transition(**d.pop("transition", {}))
    d = _dec(d)
    s = Slide(**{k: v for k, v in d.items() if k in Slide.__dataclass_fields__})
    s.shapes = shapes
    s.transition = tr
    return s


def pres_to_dict(p: Presentation) -> dict:
    return {"slides": [slide_to_dict(s) for s in p.slides], "width": p.width, "height": p.height,
            "theme": asdict(p.theme), "title": p.title}


def pres_from_dict(d: dict) -> Presentation:
    p = Presentation([slide_from_dict(s) for s in d["slides"]], d["width"], d["height"], Theme(**d["theme"]),
                     d.get("title", ""))
    return p


def clone_shape(s: Shape, dx: int = 0, dy: int = 0) -> Shape:
    n = shape_from_dict(shape_to_dict(s))
    n.id = new_id()
    n.x += dx
    n.y += dy
    return n
