"""Render the MyOffice file-type icon family (`resources/icons/app/*.ico` + `*.png`).

Design language (v1.6.3 — "signed document"):
  * every single file carries the **MyOffice brand mark** (blue "M" tile) in the letterhead position,
    so a MyOffice document is recognisable at a glance — exactly like the product logo printed on a page;
  * the **document type is unmistakable**, in three independent channels that all agree with each other:
        colour   - Writer blue, Sheet green, Slide orange, PDF red, Photos violet, Code teal, Vault slate gold
        glyph    - text lines / spreadsheet grid / bar chart / text block + seal / photo montage / angle brackets / padlock
        label    - DOCX, XLSX, PPTX, PDF, IMG, CODE, LOCK printed in a solid colour band at the foot of the page
  * icons are **size aware**: 32 px and above use the full page design, 16–24 px switch to a compact
    colour tile with a thick white glyph, because a 6 px tall text label is unreadable in Explorer's
    small-icon views.  Both variants keep the same hue + glyph, so the family still reads as one set.

Run:  python tools/make_icons.py                 # write the icons into resources/icons/app/
      python tools/make_icons.py --preview x.png # ... and render a preview sheet for review
"""
from __future__ import annotations

import argparse
import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources" / "icons" / "app"

# --- brand -------------------------------------------------------------------------------------
BRAND_TOP = (59, 91, 219)      # MyOffice blue
BRAND_BOT = (30, 58, 175)

# kind, band label, top colour, bottom colour
ICON_SPECS: list[tuple[str, str, tuple[int, int, int], tuple[int, int, int]]] = [
    ("writer", "DOCX", (37, 99, 235), (29, 78, 216)),
    ("sheet", "XLSX", (22, 163, 74), (21, 128, 61)),
    ("slide", "PPTX", (234, 88, 12), (194, 65, 12)),
    ("pdf", "PDF", (220, 38, 38), (185, 28, 28)),
    ("photos", "IMG", (124, 58, 237), (91, 33, 182)),
    ("code", "CODE", (8, 145, 178), (14, 116, 144)),
    ("vault", "LOCK", (71, 85, 105), (40, 51, 68)),
]
ICON_KINDS = ["myoffice", *[s[0] for s in ICON_SPECS]]

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
# Frames below this size use the compact tile variant; the rest use the full page design.
FULL_DESIGN_MIN = 32
BAND_FRAC = 0.225          # share of the page height taken by the colour band
MINI_UNDER = 48            # ICO frames smaller than this are the compact tile variant

FONT_CANDIDATES = [
    ROOT / "resources" / "fonts" / "NotoSans-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
]


def _font(px: float):
    for p in FONT_CANDIDATES:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), max(4, int(round(px))))
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=max(4, int(round(px))))  # Pillow >= 10
    except TypeError:
        return ImageFont.load_default()


def _lerp(c1, c2, t: float):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def _fit_font(draw, text: str, max_w: int, start_px: float, min_px: float = 5):
    px = max(min_px, start_px)
    while px > min_px:
        f = _font(px)
        bb = draw.textbbox((0, 0), text, font=f)
        if bb[2] - bb[0] <= max_w:
            return f
        px -= 1
    return _font(min_px)


def _i(v) -> int:
    return int(round(v))


def _ir(draw, box, radius, **kw):
    """`rounded_rectangle` with integer geometry (Pillow 10+ rejects floats)."""
    x0, y0, x1, y1 = (_i(v) for v in box)
    kw["width"] = _i(kw["width"]) if kw.get("width") else kw.get("width")
    draw.rounded_rectangle([x0, y0, x1, y1], radius=max(0, _i(radius)), **kw)


def _line(draw, pts, **kw):
    if kw.get("width"):
        kw["width"] = max(1, _i(kw["width"]))
    draw.line([(_i(x), _i(y)) for x, y in pts], **kw)


def _ellipse(draw, box, **kw):
    if kw.get("width"):
        kw["width"] = max(1, _i(kw["width"]))
    draw.ellipse([_i(v) for v in box], **kw)


def _polygon(draw, pts, **kw):
    draw.polygon([(_i(x), _i(y)) for x, y in pts], **kw)


def _arc(draw, box, **kw):
    if kw.get("width"):
        kw["width"] = max(1, _i(kw["width"]))
    draw.arc([_i(v) for v in box], **kw)


def _vertical_gradient(size: tuple[int, int], c_top, c_bot) -> Image.Image:
    w, h = size
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(*_lerp(c_top, c_bot, y / max(1, h - 1)), 255))
    return im


# ============================================================================================= app icon
def _render_app_master(s: int = 1024) -> Image.Image:
    """Rounded-square MyOffice app icon (blue tile, white M) used for shortcuts and the taskbar."""
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pad = int(s * 0.055)
    rad = int(s * 0.225)

    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad, pad + int(s * 0.012), s - pad, s - pad + int(s * 0.012)], radius=rad, fill=(20, 30, 70, 70))
    im = Image.alpha_composite(im, shadow.filter(ImageFilter.GaussianBlur(s * 0.012)))

    tile = _vertical_gradient((s - 2 * pad, s - 2 * pad), _lerp(BRAND_TOP, (255, 255, 255), 0.10), BRAND_BOT)
    mask = Image.new("L", (s - 2 * pad, s - 2 * pad), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 2 * pad - 1, s - 2 * pad - 1], radius=rad, fill=255)
    im.paste(tile, (pad, pad), mask)

    d = ImageDraw.Draw(im)
    d.rounded_rectangle([pad, pad, s - pad - 1, s - pad - 1], radius=rad,
                        outline=(255, 255, 255, 70), width=max(4, s // 110))
    # crisp white "M" - geometric, optical-centre corrected
    f = _font(s * 0.60)
    bb = d.textbbox((0, 0), "M", font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx, ty = (s - tw) / 2 - bb[0], (s - th) / 2 - bb[1]
    d.text((tx, ty + s * 0.020), "M", font=f, fill=(10, 24, 70, 80))
    d.text((tx, ty), "M", font=f, fill=(255, 255, 255, 255))
    return im


# =========================================================================================== page design
def _draw_glyph(d: ImageDraw.ImageDraw, kind: str, box: tuple[float, float, float, float],
                acc: tuple[int, int, int], s: int, weight: float = 1.0) -> None:
    """Draw the type glyph inside ``box`` (x0, y0, x1, y1) using the type accent colour."""
    gx0, gy0, gx1, gy1 = box
    gw, gh = gx1 - gx0, gy1 - gy0
    cx, cy = gx0 + gw / 2, gy0 + gh / 2
    solid = (*acc, 255)
    soft = (*acc, 38)
    hair = max(3.0, gw * 0.045 * weight)
    stroke = max(4.0, gw * 0.075 * weight)

    if kind == "writer":
        bar_h = gh * 0.135
        gap = gh * 0.245
        top = cy - (gap * 3 + bar_h) / 2
        for i, frac in enumerate((1.0, 0.94, 1.0, 0.62)):
            y = top + i * gap
            _ir(d, [gx0, y, gx0 + gw * frac, y + bar_h], bar_h / 2, fill=solid)

    elif kind == "sheet":
        th = gh * 0.84
        ty = cy - th / 2
        _ir(d, [gx0, ty, gx1, ty + th], gw * 0.06, fill=soft, outline=solid, width=hair)
        hdr = ty + th * 0.26
        d.rectangle([_i(gx0 + hair), _i(ty + hair), _i(gx1 - hair), _i(hdr)], fill=solid)
        for fy in (0.43, 0.72):
            _line(d, [(gx0, ty + th * fy), (gx1, ty + th * fy)], fill=solid, width=hair)
        for fx in (0.38, 0.69):
            _line(d, [(gx0 + gw * fx, hdr), (gx0 + gw * fx, ty + th)], fill=solid, width=hair)

    elif kind == "slide":
        sh = gh * 0.76
        sy = cy - sh / 2
        _ir(d, [gx0, sy, gx1, sy + sh], gw * 0.06, fill=soft, outline=solid, width=hair)
        base = sy + sh - sh * 0.17
        for i, frac in enumerate((0.46, 0.86, 0.60)):
            bx = gx0 + gw * (0.13 + i * 0.26)
            bw = gw * 0.18
            _ir(d, [bx, base - sh * frac, bx + bw, base], gw * 0.02, fill=solid)

    elif kind == "pdf":
        bar_h = gh * 0.135
        gap = gh * 0.235
        top = cy - (gap * 3 + bar_h) / 2
        for i, frac in enumerate((0.92, 1.0, 0.70)):
            y = top + i * gap
            _ir(d, [gx0, y, gx0 + gw * frac, y + bar_h], bar_h / 2, fill=solid)
        r = min(gw, gh) * 0.205
        sx, sy = gx1 - r * 0.82, gy1 - r * 0.82
        _ellipse(d, [sx - r, sy - r, sx + r, sy + r], fill=solid)
        _line(d, [(sx - r * 0.46, sy + r * 0.03), (sx - r * 0.10, sy + r * 0.40), (sx + r * 0.50, sy - r * 0.38)],
              fill=(255, 255, 255, 255), width=max(3.0, r * 0.24), joint="curve")

    elif kind == "photos":
        th = gh * 0.78
        ty = cy - th / 2
        _ir(d, [gx0, ty, gx1, ty + th], gw * 0.07, fill=soft, outline=solid, width=hair)
        sr = min(gw, th) * 0.105
        sx, sy = gx0 + gw * 0.76, ty + th * 0.27
        _ellipse(d, [sx - sr, sy - sr, sx + sr, sy + sr], fill=(245, 158, 11, 255))
        _polygon(d, [(gx0 + gw * 0.05, ty + th - th * 0.08),
                    (gx0 + gw * 0.34, ty + th * 0.42),
                    (gx0 + gw * 0.56, ty + th * 0.70),
                    (gx0 + gw * 0.72, ty + th * 0.50),
                    (gx1 - gw * 0.05, ty + th - th * 0.08)], fill=solid)

    elif kind == "vault":
        gold, gold_dark = (245, 158, 11), (217, 119, 6)
        aw = gw * 0.54
        ax = cx - aw / 2
        _arc(d, [ax, cy - gh * 0.40, ax + aw, cy + gh * 0.08], start=180, end=0,
             fill=(*gold_dark, 255), width=max(6.0, gw * 0.105 * weight))
        _ir(d, [cx - gw * 0.33, cy - gh * 0.02, cx + gw * 0.33, cy + gh * 0.30],
                gw * 0.075, fill=(*gold, 255))
        kh = gh * 0.085
        _ir(d, [cx - kh / 2, cy + gh * 0.07, cx + kh / 2, cy + gh * 0.18], kh / 2, fill=solid)

    else:  # code
        _line(d, [(gx0 + gw * 0.28, gy0 + gh * 0.14), (gx0, cy), (gx0 + gw * 0.28, gy1 - gh * 0.14)],
              fill=solid, width=stroke, joint="curve")
        _line(d, [(gx1 - gw * 0.28, gy0 + gh * 0.14), (gx1, cy), (gx1 - gw * 0.28, gy1 - gh * 0.14)],
              fill=solid, width=stroke, joint="curve")
        _line(d, [(cx + gw * 0.08, gy0 + gh * 0.08), (cx - gw * 0.08, gy1 - gh * 0.08)], fill=solid, width=stroke)


def _render_brand_chip(im: Image.Image, size: int, x: int, y: int, chip: int) -> int:
    """Draw the MyOffice 'M' letterhead tile; returns the y coordinate of its bottom edge."""
    grad = _vertical_gradient((chip, chip), _lerp(BRAND_TOP, (255, 255, 255), 0.08), BRAND_BOT)
    mask = Image.new("L", (chip, chip), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, chip - 1, chip - 1], radius=max(1, int(chip * 0.28)), fill=255)
    im.paste(grad, (x, y), mask)
    d = ImageDraw.Draw(im)
    f = _font(chip * 0.62)
    bb = d.textbbox((0, 0), "M", font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((x + (chip - tw) / 2 - bb[0], y + (chip - th) / 2 - bb[1] + chip * 0.02),
           "M", font=f, fill=(255, 255, 255, 255))
    return y + chip


def render_page(kind: str, label: str, c_top, c_bot, s: int = 1024, band_frac: float = BAND_FRAC) -> Image.Image:
    """Full 'signed document' icon: page + brand chip + type glyph + colour band with the extension."""
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    x0, y0 = int(s * 0.110), int(s * 0.028)
    pw, ph = int(s * 0.780), int(s * 0.944)
    x1, y1 = x0 + pw, y0 + ph
    rad = int(s * 0.064)
    fold = int(s * 0.215)

    page_mask = Image.new("L", (s, s), 0)
    pm = ImageDraw.Draw(page_mask)
    pm.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=255)
    pm.polygon([(x1 - fold, y0 - 4), (x1 + 4, y0 - 4), (x1 + 4, y0 + fold)], fill=0)

    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x0 + int(s * 0.006), y0 + int(s * 0.013), x1 + int(s * 0.006), y1 + int(s * 0.013)],
        radius=rad, fill=(12, 24, 48, 82))
    im = Image.alpha_composite(im, shadow.filter(ImageFilter.GaussianBlur(s * 0.008)))

    paper = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pd = ImageDraw.Draw(paper)
    tint = _lerp((255, 255, 255), c_top, 0.115)
    for y in range(y0, y1 + 1):
        pd.line([(x0, y), (x1, y)], fill=(*_lerp((255, 255, 255), tint, (y - y0) / max(1, ph)), 255))
    band_h = int(ph * band_frac)
    band_top = y1 - band_h
    for y in range(band_top, y1 + 1):
        pd.line([(x0, y), (x1, y)],
                fill=(*_lerp(c_top, c_bot, (y - band_top) / max(1, band_h)), 255))
    im.paste(paper, (0, 0), page_mask)

    edge_col = _lerp(_lerp((255, 255, 255), c_top, 0.42), (0, 0, 0), 0.08)
    # The outline lies exactly on the page silhouette, so draw it straight onto the composite -
    # masking here would replace (not blend) the pixels underneath.
    ImageDraw.Draw(im).rounded_rectangle([x0, y0, x1, y1], radius=rad,
                                        outline=(*edge_col, 240), width=max(2, s // 300))

    d = ImageDraw.Draw(im)
    fold_col = _lerp((255, 255, 255), c_top, 0.18)
    _polygon(d, [(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(*fold_col, 255))
    _line(d, [(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=(*edge_col, 255),
              width=max(2, s // 300), joint="curve")

    chip = int(pw * 0.205)
    chip_bottom = _render_brand_chip(im, s, x0 + int(pw * 0.080), y0 + int(ph * 0.058), chip)

    gx0 = x0 + int(pw * 0.110)
    gx1 = x1 - int(pw * 0.110)
    gy0 = chip_bottom + int(ph * 0.055)
    gy1 = band_top - int(ph * 0.060)
    _draw_glyph(d, kind, (gx0, gy0, gx1, gy1), c_top, s)

    f = _fit_font(d, label, int(pw * 0.80), band_h * 0.60)
    bb = d.textbbox((0, 0), label, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((x0 + (pw - tw) / 2 - bb[0], band_top + (band_h - th) / 2 - bb[1] - band_h * 0.015),
           label, font=f, fill=(255, 255, 255, 255))
    return im


# ======================================================================================= compact tile
def render_tile(kind: str, c_top, c_bot, s: int = 128) -> Image.Image:
    """Compact variant for 16–24 px: solid colour tile + thick white glyph (reads at a glance)."""
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pad = max(1, int(round(s * 0.02)))
    w = s - 2 * pad
    grad = _vertical_gradient((w, w), c_top, c_bot)
    mask = Image.new("L", (w, w), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, w - 1], radius=max(1, int(w * 0.24)), fill=255)
    im.paste(grad, (pad, pad), mask)

    d = ImageDraw.Draw(im)
    white = (255, 255, 255, 255)
    gx0, gy0 = pad + w * 0.19, pad + w * 0.19
    gx1, gy1 = pad + w * 0.81, pad + w * 0.81
    gw = gh = w * 0.62
    cx, cy = gx0 + gw / 2, gy0 + gh / 2
    lw = max(1.6, w * 0.095)

    if kind == "writer":
        bar_h = gh * 0.165
        gap = gh * 0.30
        top = cy - (gap * 2 + bar_h) / 2
        for i, frac in enumerate((1.0, 0.80, 1.0)):
            y = top + i * gap
            _ir(d, [gx0, y, gx0 + gw * frac, y + bar_h], bar_h / 2, fill=white)
    elif kind == "sheet":
        _ir(d, [gx0, gy0, gx1, gy1], w * 0.08, outline=white, width=lw)
        _line(d, [(gx0, cy), (gx1, cy)], fill=white, width=lw)
        _line(d, [(cx, gy0), (cx, gy1)], fill=white, width=lw)
    elif kind == "slide":
        _ir(d, [gx0, gy0, gx1, gy1], w * 0.08, outline=white, width=lw)
        base = gy1 - gh * 0.14
        for i, frac in enumerate((0.46, 0.88, 0.62)):
            bx = gx0 + gw * (0.11 + i * 0.28)
            d.rectangle([_i(bx), _i(base - gh * frac * 0.86), _i(bx + gw * 0.17), _i(base)], fill=white)
    elif kind == "pdf":
        bar_h = gh * 0.165
        gap = gh * 0.30
        top = cy - (gap * 2 + bar_h) / 2
        for i, frac in enumerate((1.0, 0.70, 1.0)):
            y = top + i * gap
            _ir(d, [gx0, y, gx0 + gw * frac, y + bar_h], bar_h / 2, fill=white)
        r = w * 0.155
        sx, sy = gx1 - r * 0.75, gy1 - r * 0.75
        _ellipse(d, [sx - r, sy - r, sx + r, sy + r], fill=white)
        _line(d, [(sx - r * 0.46, sy + r * 0.04), (sx - r * 0.08, sy + r * 0.42), (sx + r * 0.50, sy - r * 0.36)],
              fill=(*c_top, 255), width=max(1.4, w * 0.075), joint="curve")
    elif kind == "photos":
        _ir(d, [gx0, gy0, gx1, gy1], w * 0.08, outline=white, width=lw)
        _polygon(d, [(gx0 + lw, gy1 - lw), (gx0 + gw * 0.36, cy), (gx0 + gw * 0.58, gy1 - gh * 0.30),
                    (gx1 - lw, gy1 - lw)], fill=white)
    elif kind == "vault":
        aw = gw * 0.62
        ax = cx - aw / 2
        _arc(d, [ax, cy - gh * 0.44, ax + aw, cy], start=180, end=0, fill=white, width=max(1.6, w * 0.10))
        _ir(d, [cx - gw * 0.36, cy - gh * 0.02, cx + gw * 0.36, cy + gh * 0.40],
                w * 0.09, fill=(245, 158, 11, 255))
    else:  # code
        _line(d, [(gx0 + gw * 0.26, gy0), (gx0, cy), (gx0 + gw * 0.26, gy1)], fill=white, width=lw, joint="curve")
        _line(d, [(gx1 - gw * 0.26, gy0), (gx1, cy), (gx1 - gw * 0.26, gy1)], fill=white, width=lw, joint="curve")
    return im


# ============================================================================================ ICO writer
def _rgba_to_bmp_entry(img: Image.Image) -> bytes:
    """32-bit BGRA DIB + AND mask, the classic ICO frame format Windows expects below 64 px."""
    w, h = img.size
    raw = img.convert("RGBA").tobytes()
    stride = w * 4
    xor_rows = bytearray()
    for row in range(h - 1, -1, -1):
        line = raw[row * stride:(row + 1) * stride]
        for i in range(0, stride, 4):
            xor_rows.extend((line[i + 2], line[i + 1], line[i], line[i + 3]))
    and_row_bytes = ((w + 31) // 32) * 4
    and_rows = bytearray()
    for row in range(h - 1, -1, -1):
        line = raw[row * stride:(row + 1) * stride]
        bits = bytearray(and_row_bytes)
        for x in range(w):
            if line[x * 4 + 3] < 8:
                bits[x >> 3] |= 0x80 >> (x & 7)
        and_rows.extend(bits)
    bih = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor_rows) + len(and_rows), 0, 0, 0, 0)
    return bih + bytes(xor_rows) + bytes(and_rows)


def _png_frame(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_windows_ico(frames: list[tuple[int, Image.Image]], dest: Path) -> None:
    """Write a multi-resolution `.ico`.  >= 64 px frames are PNG-compressed (Windows Vista+)."""
    entries: list[tuple[int, bytes]] = []
    for size, img in sorted(frames, key=lambda f: f[0]):
        frame = img.convert("RGBA").resize((size, size), Image.LANCZOS)
        entries.append((size, _png_frame(frame) if size >= 64 else _rgba_to_bmp_entry(frame)))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = bytearray()
    payloads = bytearray()
    for size, data in entries:
        dim = 0 if size >= 256 else size
        directory.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        payloads.extend(data)
        offset += len(data)
    dest.write_bytes(header + bytes(directory) + bytes(payloads))


def build_frames(kind: str, label: str, c_top, c_bot, page_master: Image.Image) -> list[tuple[int, Image.Image]]:
    """Per-size frames: full page design above 32 px, compact tile at 16–24 px."""
    frames: list[tuple[int, Image.Image]] = []
    for size in ICO_SIZES:
        if size < MINI_UNDER:
            frames.append((size, render_tile(kind, c_top, c_bot, s=size * 8)))
        else:
            frames.append((size, page_master))
    return frames


def write_icon(kind: str, label: str, c_top, c_bot) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if kind == "myoffice":
        master = _render_app_master()
        frames = [(size, master) for size in ICO_SIZES]
    else:
        master = render_page(kind, label, c_top, c_bot)
        frames = build_frames(kind, label, c_top, c_bot, master)
    write_windows_ico(frames, OUT / f"{kind}.ico")
    master.resize((256, 256), Image.LANCZOS).save(OUT / f"{kind}.png")


# ============================================================================================== preview
def preview_sheet(dest: Path) -> Path:
    """Contact sheet (light + dark) so the family can be reviewed without Windows."""
    kinds = [("myoffice", "", BRAND_TOP, BRAND_BOT), *ICON_SPECS]
    sizes = [256, 128, 48, 32, 24, 16]
    col_w, top_pad = 300, 70
    row_h = [s + 26 for s in sizes[1:]]
    width = col_w * len(kinds) + 40
    height = top_pad + 256 + 40 + sum(row_h) + 70
    for dark in (False, True):
        bg = (30, 32, 36, 255) if dark else (247, 248, 251, 255)
        canvas = Image.new("RGBA", (width, height), bg)
        d = ImageDraw.Draw(canvas)
        head = _font(20)
        d.text((20, 22), "MyOffice 1.6.3 - file type icons" + ("  (dark)" if dark else "  (light)"),
               font=head, fill=(235, 238, 245, 255) if dark else (30, 36, 48, 255))
        y = top_pad
        for row, size in enumerate(sizes):
            for i, (kind, label, c_top, c_bot) in enumerate(kinds):
                if kind == "myoffice":
                    img = _render_app_master()
                    spec = ""
                else:
                    spec = label
                    img = render_page(kind, label, c_top, c_bot) if size >= FULL_DESIGN_MIN else render_tile(kind, c_top, c_bot, s=256)
                cell = img.resize((size, size), Image.LANCZOS)
                x = 20 + i * col_w + (256 - size) // 2
                canvas.alpha_composite(cell, (x, y))
                if row == 0:
                    f = _font(15)
                    tw = d.textlength(kind, font=f)
                    d.text((20 + i * col_w + (256 - tw) / 2, y + size + 8), kind, font=f,
                           fill=(200, 208, 220, 255) if dark else (70, 80, 96, 255))
            y += size + 26
        suffix = "dark" if dark else "light"
        out = dest.with_name(f"{dest.stem}_{suffix}{dest.suffix}")
        canvas.convert("RGB").save(out)
        if not dark:
            final = canvas.convert("RGB")
    final.save(dest) if dest.suffix.lower() in {".png", ".jpg", ".jpeg"} else None
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the MyOffice file-type icon family.")
    ap.add_argument("--preview", type=Path, default=None, help="also write a preview contact sheet PNG here")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    legacy = ROOT / "installer" / "icons"
    if legacy.exists():
        import shutil

        shutil.rmtree(legacy, ignore_errors=True)

    write_icon("myoffice", "", BRAND_TOP, BRAND_BOT)
    print("wrote myoffice")
    for kind, label, c_top, c_bot in ICON_SPECS:
        write_icon(kind, label, c_top, c_bot)
        print("wrote", kind, f"({label})")
    if args.preview:
        print("preview:", preview_sheet(args.preview))


if __name__ == "__main__":
    main()
