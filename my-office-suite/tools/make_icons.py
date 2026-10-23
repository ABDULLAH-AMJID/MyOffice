"""Render crisp Windows 11 multi-resolution `.ico` & `.png` files (`16..256` px) into `resources/icons/app/`.

Generates all 8 distinct brand & folded-sheet document icons:
`myoffice.ico` (index 0), `writer.ico` (index 1), `sheet.ico` (index 2), `slide.ico` (index 3),
`pdf.ico` (index 4), `photos.ico` (index 5), `code.ico` (index 6), `vault.ico` (index 7).

Run:  python tools/make_icons.py
"""
from __future__ import annotations

import io
import math
import shutil
import struct
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources" / "icons" / "app"

ICON_SPECS = [
    ("myoffice", "M", (59, 91, 219), (30, 58, 175)),
    ("writer", "DOCX", (37, 99, 235), (29, 78, 216)),
    ("sheet", "XLSX", (22, 163, 74), (21, 128, 61)),
    ("slide", "PPTX", (234, 88, 12), (194, 65, 12)),
    ("pdf", "PDF", (220, 38, 38), (185, 28, 28)),
    ("photos", "IMG", (124, 58, 237), (91, 33, 182)),
    ("code", "CODE", (8, 145, 178), (14, 116, 144)),
    ("vault", "LOCK", (217, 119, 6), (180, 83, 9)),
]

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

FONT_CANDIDATES = [
    ROOT / "resources" / "fonts" / "NotoSans-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
]


def _font(px: int):
    for p in FONT_CANDIDATES:
        if p.exists():
            return ImageFont.truetype(str(p), max(8, px))
    return ImageFont.load_default()


def _lerp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))  # type: ignore[return-value]


def _render_app_master(c_top: tuple[int, int, int], c_bot: tuple[int, int, int], s: int = 1024) -> Image.Image:
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        gd.line([(0, y), (s, y)], fill=(*_lerp(c_top, c_bot, y / max(1, s - 1)), 255))

    mask = Image.new("L", (s, s), 0)
    pad = int(s * 0.05)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, s - pad - 1, s - pad - 1], radius=int(s * 0.22), fill=255)
    im.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(im)
    d.rounded_rectangle([pad, pad, s - pad - 1, s - pad - 1], radius=int(s * 0.22),
                        outline=(255, 255, 255, 92), width=max(4, s // 72))
    f = _font(int(s * 0.56))
    bb = d.textbbox((0, 0), "M", font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    tx, ty = (s - tw) / 2 - bb[0], (s - th) / 2 - bb[1]
    d.text((tx, ty + s * 0.015), "M", font=f, fill=(0, 0, 0, 58))
    d.text((tx, ty), "M", font=f, fill=(255, 255, 255, 255))
    return im


def _render_doc_master(kind: str, label: str, c_top: tuple[int, int, int],
                       c_bot: tuple[int, int, int], s: int = 1024) -> Image.Image:
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    x0, y0 = int(s * 0.14), int(s * 0.04)
    pw, ph = int(s * 0.78), int(s * 0.91)
    x1, y1 = x0 + pw, y0 + ph
    fold, rad = int(s * 0.22), int(s * 0.075)

    page_mask = Image.new("L", (s, s), 0)
    pm = ImageDraw.Draw(page_mask)
    pm.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=255)
    pm.polygon([(x1 - fold, y0 - 2), (x1 + 2, y0 - 2), (x1 + 2, y0 + fold)], fill=0)

    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x0 + int(s * 0.012), y0 + int(s * 0.022), x1 + int(s * 0.012), y1 + int(s * 0.022)],
        radius=rad, fill=(0, 0, 0, 72),
    )
    im = Image.alpha_composite(im, shadow)

    sheet = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    shd = ImageDraw.Draw(sheet)
    for y in range(y0, y1 + 1):
        c = _lerp((255, 255, 255), (236, 242, 249), (y - y0) / max(1, ph))
        shd.line([(x0, y), (x1, y)], fill=(*c, 255))
    im.paste(sheet, (0, 0), page_mask)

    d = ImageDraw.Draw(im)
    border_col = (190, 202, 218, 255)
    lw = max(6, s // 90)
    d.line([(x0 + rad, y0), (x1 - fold, y0)], fill=border_col, width=lw)
    d.line([(x1 - fold, y0), (x1, y0 + fold)], fill=border_col, width=lw)
    d.line([(x1, y0 + fold), (x1, y1 - rad)], fill=border_col, width=lw)
    d.line([(x0 + rad, y1), (x1 - rad, y1)], fill=border_col, width=lw)
    d.line([(x0, y0 + rad), (x0, y1 - rad)], fill=border_col, width=lw)
    d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)],
              fill=(*_lerp(c_top, (255, 255, 255), 0.62), 255))

    ix0, iy0 = x0 + int(pw * 0.15), y0 + fold + int(ph * 0.05)
    iw, ih = int(pw * 0.70), int(ph * 0.36)
    acc = (*c_top, 255)
    acc_soft = (*c_top, 48)
    stroke = max(10, s // 42)

    if kind == "writer":
        for idx, frac in enumerate((0.96, 1.0, 0.88, 0.64)):
            yy = iy0 + int(ih * idx / 3.1)
            d.rounded_rectangle([ix0, yy - stroke // 2, ix0 + int(iw * frac), yy + stroke // 2],
                                radius=stroke // 2, fill=acc)
    elif kind == "sheet":
        d.rounded_rectangle([ix0, iy0, ix0 + iw, iy0 + ih], radius=int(s * 0.025), fill=acc_soft,
                            outline=acc, width=max(8, s // 60))
        d.line([(ix0 + int(iw * 0.36), iy0), (ix0 + int(iw * 0.36), iy0 + ih)], fill=acc, width=max(8, s // 65))
        d.line([(ix0, iy0 + int(ih * 0.35)), (ix0 + iw, iy0 + int(ih * 0.35))], fill=acc, width=max(8, s // 65))
        d.line([(ix0, iy0 + int(ih * 0.70)), (ix0 + iw, iy0 + int(ih * 0.70))], fill=acc, width=max(8, s // 65))
    elif kind == "slide":
        d.rounded_rectangle([ix0, iy0, ix0 + iw, iy0 + int(ih * 0.94)], radius=int(s * 0.03), fill=acc_soft,
                            outline=acc, width=max(8, s // 60))
        for bi, bh in enumerate((0.42, 0.72, 0.56)):
            bx = ix0 + int(iw * (0.16 + bi * 0.25))
            bw2 = int(iw * 0.16)
            by2 = iy0 + int(ih * (0.82 - bh * 0.72))
            d.rounded_rectangle([bx, by2, bx + bw2, iy0 + int(ih * 0.82)], radius=int(s * 0.012), fill=acc)
    elif kind == "pdf":
        cx, cy = ix0 + iw // 2, iy0 + ih // 2
        pts = [(cx + min(iw, ih) * 0.44 * math.sin(3 * math.radians(deg) + 0.5) * math.cos(math.radians(deg)),
                cy + min(iw, ih) * 0.44 * math.sin(3 * math.radians(deg) + 0.5) * math.sin(math.radians(deg)))
               for deg in range(0, 361, 4)]
        d.line(pts, fill=acc, width=max(14, s // 34), joint="curve")
    elif kind == "photos":
        d.rounded_rectangle([ix0, iy0, ix0 + iw, iy0 + ih], radius=int(s * 0.03),
                            fill=(237, 233, 254, 255), outline=acc, width=max(8, s // 60))
        sr = int(min(iw, ih) * 0.14)
        sx, sy = ix0 + int(iw * 0.75), iy0 + int(ih * 0.28)
        d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(245, 158, 11, 255))
        d.polygon([
            (ix0 + int(iw * 0.06), iy0 + int(ih * 0.93)),
            (ix0 + int(iw * 0.38), iy0 + int(ih * 0.40)),
            (ix0 + int(iw * 0.60), iy0 + int(ih * 0.68)),
            (ix0 + int(iw * 0.78), iy0 + int(ih * 0.46)),
            (ix0 + int(iw * 0.95), iy0 + int(ih * 0.93)),
        ], fill=acc)
    elif kind == "vault":
        aw = int(iw * 0.42)
        ax0 = ix0 + (iw - aw) // 2
        d.arc([ax0, iy0, ax0 + aw, iy0 + int(ih * 0.64)], start=180, end=0, fill=(*c_bot, 255),
              width=max(12, s // 36))
        d.rounded_rectangle([ix0 + int(iw * 0.20), iy0 + int(ih * 0.38), ix0 + int(iw * 0.80), iy0 + ih],
                            radius=int(s * 0.03), fill=acc)
    else:
        mid = iy0 + ih // 2
        d.line([(ix0 + int(iw * 0.28), iy0 + int(ih * 0.14)), (ix0 + int(iw * 0.05), mid),
                (ix0 + int(iw * 0.28), iy0 + int(ih * 0.86))], fill=acc, width=stroke, joint="curve")
        d.line([(ix0 + int(iw * 0.72), iy0 + int(ih * 0.14)), (ix0 + int(iw * 0.95), mid),
                (ix0 + int(iw * 0.72), iy0 + int(ih * 0.86))], fill=acc, width=stroke, joint="curve")

    bx0, bh, bw = int(s * 0.04), int(s * 0.285), int(s * 0.68)
    by0 = y1 - bh - int(s * 0.055)
    d.rounded_rectangle([bx0, by0 + int(s * 0.015), bx0 + bw, by0 + bh + int(s * 0.015)],
                        radius=int(s * 0.06), fill=(0, 0, 0, 64))
    badge_grad = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    bgd = ImageDraw.Draw(badge_grad)
    for y in range(bh):
        bgd.line([(0, y), (bw, y)], fill=(*_lerp(c_top, c_bot, y / max(1, bh - 1)), 255))
    bmask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(bmask).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=int(s * 0.06), fill=255)
    im.paste(badge_grad, (bx0, by0), bmask)

    f = _font(int(bh * 0.56))
    bb = d.textbbox((0, 0), label, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text((bx0 + (bw - tw) / 2 - bb[0], by0 + (bh - th) / 2 - bb[1]), label, font=f, fill=(255, 255, 255, 255))
    return im


def _rgba_to_bmp_entry(img: Image.Image) -> bytes:
    w, h = img.size
    rgba = img.convert("RGBA")
    raw = rgba.tobytes()
    xor_rows = bytearray()
    stride = w * 4
    for row in range(h - 1, -1, -1):
        line = raw[row * stride:(row + 1) * stride]
        for i in range(0, stride, 4):
            r, g, b, a = line[i], line[i + 1], line[i + 2], line[i + 3]
            xor_rows.extend((b, g, r, a))
    and_row_bytes = ((w + 31) // 32) * 4
    and_rows = bytearray()
    for row in range(h - 1, -1, -1):
        line = raw[row * stride:(row + 1) * stride]
        bits = bytearray(and_row_bytes)
        for x in range(w):
            if line[x * 4 + 3] < 8:
                bits[x >> 3] |= (0x80 >> (x & 7))
        and_rows.extend(bits)
    bih = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor_rows) + len(and_rows), 0, 0, 0, 0)
    return bih + bytes(xor_rows) + bytes(and_rows)


def write_windows_ico(master: Image.Image, dest: Path, sizes: list[int] = ICO_SIZES) -> None:
    frames: list[tuple[int, bytes]] = []
    for sz in sizes:
        scaled = master.resize((sz, sz), Image.LANCZOS)
        if sz >= 256:
            buf = io.BytesIO()
            scaled.save(buf, format="PNG", optimize=True)
            frames.append((sz, buf.getvalue()))
        else:
            frames.append((sz, _rgba_to_bmp_entry(scaled)))

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    directory = bytearray()
    payloads = bytearray()
    for sz, data in frames:
        b_dim = 0 if sz >= 256 else sz
        directory.extend(struct.pack("<BBBBHHII", b_dim, b_dim, 0, 0, 1, 32, len(data), offset))
        payloads.extend(data)
        offset += len(data)
    dest.write_bytes(header + bytes(directory) + bytes(payloads))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Remove any old temporary folder if created earlier
    old_alt = ROOT / "installer" / "icons"
    if old_alt.exists():
        shutil.rmtree(old_alt, ignore_errors=True)
    for name, label, c_top, c_bot in ICON_SPECS:
        master = _render_app_master(c_top, c_bot) if name == "myoffice" else _render_doc_master(name, label, c_top, c_bot)
        write_windows_ico(master, OUT / f"{name}.ico")
        master.resize((256, 256), Image.LANCZOS).save(OUT / f"{name}.png")
        print("wrote", name)


if __name__ == "__main__":
    main()
