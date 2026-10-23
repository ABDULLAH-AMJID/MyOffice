"""Generate the MyOffice Setup wizard artwork (installer/art/*.png).

* ``wizard-<dpi>.png`` / ``wizard-dark-<dpi>.png`` - the tall image on the Welcome and Finished pages,
  one file per Windows DPI setting (Inno Setup picks the best match), light and dark appearance.
* ``small-<dpi>.png`` - the square logo in the top-right corner of the other wizard pages.

Sizes follow the Inno Setup 6.6+ documentation (WizardImageFile / WizardSmallImageFile).  Shapes are
drawn at 3x and downsampled for clean anti-aliased edges.  Fonts: Noto Sans (pass its path with
``--font``) or DejaVu Sans as a fallback - the font is only used to render the images, it is not shipped.

Run:  python tools/make_installer_art.py [--font NotoSans.ttf]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "installer" / "art"
sys.path.insert(0, str(ROOT))
from app.version import APP_NAME, APP_VERSION  # noqa: E402

WIZARD_SIZES = {100: (202, 386), 125: (269, 515), 150: (336, 643), 175: (403, 772), 200: (430, 824),
                225: (498, 953), 250: (534, 1022)}
SMALL_SIZES = {100: 58, 125: 77, 150: 97, 175: 116, 200: 124, 225: 143, 250: 159}
BRAND = "#3b5bdb"
APPS = (("W", "Writer", "#2b6be6"), ("S", "Spreadsheets", "#159a4b"), ("P", "Presentation", "#e8590c"),
        ("PDF", "PDF tools", "#d92d20"))
THEMES = {
    "light": {"top": (59, 91, 219), "bottom": (24, 39, 120), "glow": (255, 255, 255, 46)},
    "dark": {"top": (30, 41, 82), "bottom": (10, 14, 32), "glow": (91, 124, 250, 56)},
}
SS = 3  # supersampling factor


class Fonts:
    def __init__(self, path: str | None):
        self.path = path
        self.dejavu = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        self.dejavu_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    def get(self, size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
        size = max(6, int(size))
        if self.path:
            f = ImageFont.truetype(self.path, size)
            try:
                f.set_variation_by_name(weight)
            except (OSError, ValueError):
                pass
            return f
        return ImageFont.truetype(self.dejavu_bold if weight in ("Bold", "SemiBold", "ExtraBold") else self.dejavu, size)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def gradient(w: int, h: int, top, bottom) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = row
    return img


def centered(draw: ImageDraw.ImageDraw, cx: float, y: float, text: str, font, fill) -> None:
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def badge(size: int, color, glyph: str, fonts: Fonts, glyph_color=(255, 255, 255), radius=0.22,
          weight: str = "Bold") -> Image.Image:
    """Rounded-square badge with a centred glyph (drawn at SS x, returned at *size*)."""
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * radius), fill=color)
    fsize = S * (0.62 if len(glyph) == 1 else 0.36)
    font = fonts.get(fsize, weight)
    bbox = d.textbbox((0, 0), glyph, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((S - tw) / 2 - bbox[0], (S - th) / 2 - bbox[1]), glyph, font=font, fill=glyph_color)
    return img.resize((size, size), Image.LANCZOS)


def wizard_image(w: int, h: int, theme: str, fonts: Fonts) -> Image.Image:
    th = THEMES[theme]
    s = w / 202.0
    W, H = w * SS, h * SS
    base = gradient(W, H, th["top"], th["bottom"]).convert("RGBA")
    # soft radial highlights (smooth falloff, no visible edges)
    for (fx, fy, fr, strength) in ((0.15, 0.10, 1.25, 1.0), (0.95, 0.92, 1.05, 0.7)):
        size = int(fr * 2 * W)
        mask = ImageOps.invert(Image.radial_gradient("L")).resize((size, size), Image.BICUBIC)
        mask = mask.point(lambda v, a=th["glow"][3] * strength: int(a * (v / 255.0) ** 2.2))
        light = Image.new("RGBA", (size, size), th["glow"][:3] + (0,))
        light.putalpha(mask)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.alpha_composite(light, (int(fx * W - size / 2), int(fy * H - size / 2))) if (
            0 <= int(fx * W - size / 2) and 0 <= int(fy * H - size / 2)) else layer.paste(
            light, (int(fx * W - size / 2), int(fy * H - size / 2)), light)
        base = Image.alpha_composite(base, layer)
    d = ImageDraw.Draw(base)
    cx = W / 2

    # app badge with a soft shadow
    bsize = int(66 * s)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    bx, by = cx - bsize * SS / 2, 40 * s * SS
    sd.rounded_rectangle((bx, by + 6 * s * SS, bx + bsize * SS, by + bsize * SS + 6 * s * SS),
                         radius=int(bsize * SS * 0.24), fill=(0, 0, 0, 70))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7 * s * SS))
    base = Image.alpha_composite(base, shadow)
    big = badge(bsize * SS // SS, (255, 255, 255), "M", fonts, glyph_color=hex_rgb(BRAND), radius=0.24)
    big = big.resize((bsize * SS, bsize * SS), Image.LANCZOS)
    base.alpha_composite(big, (int(bx), int(by)))
    d = ImageDraw.Draw(base)

    centered(d, cx, (40 + 66 + 16) * s * SS, APP_NAME, fonts.get(27 * s * SS, "Bold"), (255, 255, 255))
    centered(d, cx, (40 + 66 + 16 + 36) * s * SS, f"Version {APP_VERSION}", fonts.get(11 * s * SS),
             (255, 255, 255, 190))
    ly = 196 * s * SS
    d.line((cx - 52 * s * SS, ly, cx + 52 * s * SS, ly), fill=(255, 255, 255, 70), width=max(1, int(1 * s * SS)))

    y = 214 * s
    for glyph, label, color in APPS:
        b = badge(int(24 * s), hex_rgb(color), glyph, fonts)
        b = b.resize((int(24 * s) * SS, int(24 * s) * SS), Image.LANCZOS)
        base.alpha_composite(b, (int(44 * s * SS), int(y * SS)))
        d = ImageDraw.Draw(base)
        font = fonts.get(13 * s * SS, "Medium")
        bbox = d.textbbox((0, 0), label, font=font)
        d.text((78 * s * SS, y * SS + (24 * s * SS - (bbox[3] - bbox[1])) / 2 - bbox[1]), label, font=font,
               fill=(255, 255, 255))
        y += 33 * s
    centered(d, cx, H - 30 * s * SS, "Free \u00b7 Open source", fonts.get(10 * s * SS), (255, 255, 255, 160))
    return base.convert("RGB").resize((w, h), Image.LANCZOS)


def small_image(size: int, fonts: Fonts) -> Image.Image:
    pad = max(2, int(size * 0.06))
    inner = size - 2 * pad
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.alpha_composite(badge(inner, hex_rgb(BRAND), "M", fonts, radius=0.24), (pad, pad))
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--font", help="Noto Sans (variable) .ttf used to render the text")
    args = ap.parse_args()
    fonts = Fonts(args.font)
    OUT.mkdir(parents=True, exist_ok=True)
    for dpi, (w, h) in WIZARD_SIZES.items():
        wizard_image(w, h, "light", fonts).save(OUT / f"wizard-{dpi}.png", optimize=True)
        wizard_image(w, h, "dark", fonts).save(OUT / f"wizard-dark-{dpi}.png", optimize=True)
    for dpi, size in SMALL_SIZES.items():
        small_image(size, fonts).save(OUT / f"small-{dpi}.png", optimize=True)
    total = sum(p.stat().st_size for p in OUT.glob("*.png"))
    print(f"{len(list(OUT.glob('*.png')))} images written to {OUT.relative_to(ROOT)} ({total / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
