"""MyOffice Photos - image operations (Pillow + NumPy, all on this PC).

Every function takes and returns a PIL image, so the editor, batch processing and the tests use the same code.
"""
from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif", "GIF": ".gif",
           "ICO": ".ico", "PDF": ".pdf"}
OPEN_EXTS = (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".ico", ".heic", ".heif")


def load(path: str) -> Image.Image:
    img = Image.open(path)
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode in ("P", "LA", "1", "I;16", "I", "F", "CMYK", "YCbCr"):
        img = img.convert("RGBA" if "transparency" in img.info or img.mode == "LA" else "RGB")
    return img


def _rgb_alpha(img: Image.Image):
    if img.mode == "RGBA":
        return img.convert("RGB"), img.getchannel("A")
    return img.convert("RGB"), None


def _with_alpha(rgb: Image.Image, alpha):
    if alpha is None:
        return rgb
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


# ============================================================================ geometry
def rotate(img, degrees: float, expand: bool = True, fill=(255, 255, 255, 0)):
    if degrees % 90 == 0:
        return img.rotate(degrees, expand=True)
    mode_fill = fill if img.mode == "RGBA" else fill[:3]
    return img.rotate(degrees, resample=Image.BICUBIC, expand=expand, fillcolor=mode_fill)


def flip(img, horizontal: bool = True):
    return ImageOps.mirror(img) if horizontal else ImageOps.flip(img)


def crop(img, box: tuple[int, int, int, int]):
    x0, y0, x1, y1 = box
    x0, x1 = sorted((max(0, int(x0)), min(img.width, int(x1))))
    y0, y1 = sorted((max(0, int(y0)), min(img.height, int(y1))))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return img
    return img.crop((x0, y0, x1, y1))


def resize(img, width: int, height: int, quality: str = "high"):
    resample = {"high": Image.LANCZOS, "medium": Image.BICUBIC, "fast": Image.BILINEAR,
                "pixel": Image.NEAREST}.get(quality, Image.LANCZOS)
    return img.resize((max(1, int(width)), max(1, int(height))), resample)


# ============================================================================ adjustments
@dataclass
class Adjust:
    brightness: float = 0     # -100..100
    contrast: float = 0
    saturation: float = 0
    sharpness: float = 0
    exposure: float = 0       # -100..100 (gamma)
    temperature: float = 0    # -100 (cool) .. 100 (warm)
    tint: float = 0           # -100 (green) .. 100 (magenta)
    highlights: float = 0
    shadows: float = 0
    vignette: float = 0       # 0..100

    def is_neutral(self) -> bool:
        return all(v == 0 for v in self.__dict__.values())


def adjust(img, a: Adjust):
    rgb, alpha = _rgb_alpha(img)
    if a.brightness:
        rgb = ImageEnhance.Brightness(rgb).enhance(1 + a.brightness / 100)
    if a.contrast:
        rgb = ImageEnhance.Contrast(rgb).enhance(1 + a.contrast / 100)
    if a.saturation:
        rgb = ImageEnhance.Color(rgb).enhance(1 + a.saturation / 100)
    if a.sharpness:
        rgb = ImageEnhance.Sharpness(rgb).enhance(1 + a.sharpness / 50)
    if a.exposure or a.temperature or a.tint or a.highlights or a.shadows or a.vignette:
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        if a.exposure:
            arr = np.clip(arr, 0, 1) ** (1 / (1 + a.exposure / 100 * 0.9) if a.exposure > 0 else 1 - a.exposure / 100)
        if a.temperature:
            t = a.temperature / 100 * 0.12
            arr[..., 0] += t
            arr[..., 2] -= t
        if a.tint:
            arr[..., 1] -= a.tint / 100 * 0.1
        if a.highlights or a.shadows:
            lum = arr.mean(axis=2, keepdims=True)
            if a.highlights:
                w = np.clip((lum - 0.5) * 2, 0, 1)
                arr += w * (a.highlights / 100) * 0.35
            if a.shadows:
                w = np.clip((0.5 - lum) * 2, 0, 1)
                arr += w * (a.shadows / 100) * 0.35
        if a.vignette:
            h, w = arr.shape[:2]
            yy, xx = np.ogrid[:h, :w]
            d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
            arr *= (1 - (a.vignette / 100) * np.clip(d - 0.25, 0, 1) ** 1.5 * 1.6)[..., None]
        rgb = Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    return _with_alpha(rgb, alpha)


# ============================================================================ filters
def _sepia(rgb):
    g = np.asarray(rgb.convert("L"), dtype=np.float32)
    out = np.stack([g * 1.07 + 20, g * 0.95 + 8, g * 0.75], axis=2)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _pencil(rgb):
    g = rgb.convert("L")
    inv = ImageOps.invert(g).filter(ImageFilter.GaussianBlur(8))
    a, b = np.asarray(g, dtype=np.float32), np.asarray(inv, dtype=np.float32)
    dodge = np.clip(a * 255 / np.maximum(255 - b, 1), 0, 255).astype(np.uint8)
    return Image.fromarray(dodge, "L").convert("RGB")


def _cartoon(rgb):
    smooth = rgb.filter(ImageFilter.ModeFilter(5)).filter(ImageFilter.SMOOTH_MORE)
    poster = ImageOps.posterize(smooth, 3)
    edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: 0 if v > 40 else 255)
    return ImageChops.multiply(poster, edges.convert("RGB"))


FILTERS = {
    "Original": lambda im: im,
    "Black & White": lambda im: im.convert("L").convert("RGB"),
    "Noir": lambda im: ImageEnhance.Contrast(im.convert("L")).enhance(1.6).convert("RGB"),
    "Sepia": _sepia,
    "Vintage": lambda im: ImageEnhance.Color(Image.blend(_sepia(im), im, 0.45)).enhance(0.8),
    "Vivid": lambda im: ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(1.45)).enhance(1.12),
    "Warm": lambda im: adjust(im, Adjust(temperature=55, saturation=8)),
    "Cool": lambda im: adjust(im, Adjust(temperature=-55)),
    "Fade": lambda im: ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(0.7)).enhance(0.75),
    "Dramatic": lambda im: adjust(im, Adjust(contrast=35, saturation=-20, vignette=45)),
    "Pencil Sketch": _pencil,
    "Cartoon": _cartoon,
    "Emboss": lambda im: im.filter(ImageFilter.EMBOSS),
    "Invert": lambda im: ImageOps.invert(im),
}


def apply_filter(img, name: str):
    rgb, alpha = _rgb_alpha(img)
    return _with_alpha(FILTERS[name](rgb).convert("RGB"), alpha)


def blur(img, radius: float = 4):
    return img.filter(ImageFilter.GaussianBlur(radius))


def sharpen(img, amount: float = 120):
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(amount), threshold=2))


# ============================================================================ enhance
def _local_contrast(rgb, strength: float = 0.5):
    """Lift detail: add back the difference between the image and a large blur (like 'clarity')."""
    arr = np.asarray(rgb, dtype=np.float32)
    base = np.asarray(rgb.filter(ImageFilter.GaussianBlur(max(2, min(rgb.size) // 60))), dtype=np.float32)
    out = arr + (arr - base) * strength
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def auto_enhance(img, denoise: bool = True):
    """One-click enhance: levels, gentle colour boost, noise reduction, clarity and sharpening."""
    rgb, alpha = _rgb_alpha(img)
    rgb = ImageOps.autocontrast(rgb, cutoff=0.6)
    if denoise:
        rgb = rgb.filter(ImageFilter.MedianFilter(3))
    rgb = ImageEnhance.Color(rgb).enhance(1.12)
    rgb = _local_contrast(rgb, 0.35)
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.6, percent=90, threshold=2))
    return _with_alpha(rgb, alpha)


def upscale(img, factor: int = 2):
    """HD enlargement: Lanczos up-sampling followed by edge-aware sharpening."""
    w, h = img.size
    big = img.resize((w * factor, h * factor), Image.LANCZOS)
    rgb, alpha = _rgb_alpha(big)
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2 * factor, percent=70, threshold=3))
    return _with_alpha(_local_contrast(rgb, 0.18), alpha)


def denoise(img, strength: int = 3):
    rgb, alpha = _rgb_alpha(img)
    return _with_alpha(rgb.filter(ImageFilter.MedianFilter(3 if strength < 4 else 5)), alpha)


# ============================================================================ eraser (smart fill)
def inpaint(img, mask: Image.Image, iterations: int = 400) -> Image.Image:
    """Fill the white area of ``mask`` from its surroundings (objects, blemishes, watermarks, text, scratches).

    Coarse-to-fine harmonic filling on an image pyramid (smooth, seamless colours), then the grain of the
    surroundings is added back so the patch does not look painted."""
    rgb, alpha = _rgb_alpha(img)
    m = np.asarray(mask.convert("L").resize(rgb.size), dtype=np.uint8) > 127
    if not m.any():
        return img
    ys, xs = np.where(m)
    pad = max(12, int(max(ys.max() - ys.min(), xs.max() - xs.min()) * 0.6))
    y0, y1 = max(0, ys.min() - pad), min(rgb.height, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(rgb.width, xs.max() + pad + 1)
    region = np.asarray(rgb, dtype=np.float32)[y0:y1, x0:x1].copy()
    rm = m[y0:y1, x0:x1]
    filled = _span_fill(region, rm)
    for _ in range(max(8, iterations // 25)):  # light smoothing of the interpolation seams
        avg = (np.roll(filled, 1, 0) + np.roll(filled, -1, 0) + np.roll(filled, 1, 1) + np.roll(filled, -1, 1)) / 4
        filled[rm] = 0.5 * filled[rm] + 0.5 * avg[rm]
    # texture: noise with the local grain of the known border
    ring = (_dilate(rm, 6) & ~rm)
    if ring.any():
        hp = region - _box_blur(region, 3)
        std = hp[ring].std(axis=0)
        rng = np.random.default_rng(7)
        noise = rng.normal(0, 1, region.shape).astype(np.float32) * std * 0.8
        filled[rm] += _box_blur(noise, 1)[rm]
    out = np.asarray(rgb, dtype=np.float32).copy()
    out[y0:y1, x0:x1][rm] = np.clip(filled[rm], 0, 255)
    soft = _box_blur(rm.astype(np.float32)[..., None], 1)[..., 0]  # 1-px feather on the seam
    blend = out[y0:y1, x0:x1] * soft[..., None] + np.asarray(rgb, dtype=np.float32)[y0:y1, x0:x1] * (1 - soft[..., None])
    out[y0:y1, x0:x1] = np.where(rm[..., None], out[y0:y1, x0:x1], blend)
    return _with_alpha(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), alpha)


def _nearest_known(known: np.ndarray, axis: int, reverse: bool) -> tuple[np.ndarray, np.ndarray]:
    """Index of the nearest known pixel before (or after) every pixel along ``axis``, and whether one exists."""
    k = known if axis == 1 else known.T
    if reverse:
        k = k[:, ::-1]
    n = k.shape[1]
    idx = np.where(k, np.arange(n)[None, :], -1)
    idx = np.maximum.accumulate(idx, axis=1)
    ok = idx >= 0
    if reverse:
        idx, ok = (n - 1 - idx)[:, ::-1], ok[:, ::-1]
    return (idx, ok) if axis == 1 else (idx.T, ok.T)


def _span_fill(region: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Interpolate every hidden pixel along its row and its column; the shorter span gets more weight, so lines
    that cross the hidden area (a horizon behind a pole, the edge of a table) continue through it."""
    known = ~mask
    h, w = mask.shape
    out = region.copy()
    if not known.any():
        return out
    rows, cols = np.mgrid[0:h, 0:w]
    li, lok = _nearest_known(known, 1, False)
    ri, rok = _nearest_known(known, 1, True)
    ui, uok = _nearest_known(known, 0, False)
    di, dok = _nearest_known(known, 0, True)
    eps = 1e-6
    num = np.zeros_like(region)
    den = np.zeros(mask.shape, np.float32)
    # horizontal
    both = lok & rok
    dl = np.where(lok, cols - li, 0).astype(np.float32)
    dr = np.where(rok, ri - cols, 0).astype(np.float32)
    L = region[rows, np.clip(li, 0, w - 1)]
    R = region[rows, np.clip(ri, 0, w - 1)]
    span = dl + dr + eps
    hval = np.where(both[..., None], (L * dr[..., None] + R * dl[..., None]) / span[..., None],
                    np.where(lok[..., None], L, R))
    hw = np.where(lok | rok, 1.0 / np.where(both, span, 2 * (dl + dr) + 2) ** 2, 0)
    num += hval * hw[..., None]
    den += hw
    # vertical
    both = uok & dok
    du = np.where(uok, rows - ui, 0).astype(np.float32)
    dd = np.where(dok, di - rows, 0).astype(np.float32)
    U = region[np.clip(ui, 0, h - 1), cols]
    D = region[np.clip(di, 0, h - 1), cols]
    span = du + dd + eps
    vval = np.where(both[..., None], (U * dd[..., None] + D * du[..., None]) / span[..., None],
                    np.where(uok[..., None], U, D))
    vw = np.where(uok | dok, 1.0 / np.where(both, span, 2 * (du + dd) + 2) ** 2, 0)
    num += vval * vw[..., None]
    den += vw
    fill = num / np.maximum(den, eps)[..., None]
    out[mask] = fill[mask]
    return out


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return a
    k = 2 * r + 1
    pad = np.pad(a, ((r, r), (r, r)) + ((0, 0),) * (a.ndim - 2), mode="edge")
    c = pad.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)) + ((0, 0),) * (a.ndim - 2))
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    return _box_blur(m.astype(np.float32), r) > 1e-6


def _pyramid_fill(region: np.ndarray, mask: np.ndarray, iterations: int) -> np.ndarray:
    h, w = mask.shape
    if min(h, w) > 48 and mask.mean() > 0:
        small_r = np.asarray(Image.fromarray(np.clip(region, 0, 255).astype(np.uint8)).resize(
            (max(1, w // 2), max(1, h // 2)), Image.BOX), dtype=np.float32)
        small_m = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(
            (max(1, w // 2), max(1, h // 2)), Image.BOX)) > 0
        coarse = _pyramid_fill(small_r, small_m, iterations)
        init = np.asarray(Image.fromarray(np.clip(coarse, 0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR),
                          dtype=np.float32)
        work = region.copy()
        work[mask] = init[mask]
        n = max(20, iterations // 6)
    else:
        work = region.copy()
        known = ~mask
        if known.any():
            work[mask] = region[known].mean(axis=0)
        n = iterations
    for _ in range(n):  # Jacobi iterations of the Laplace equation inside the mask
        avg = (np.roll(work, 1, 0) + np.roll(work, -1, 0) + np.roll(work, 1, 1) + np.roll(work, -1, 1)) / 4
        work[mask] = avg[mask]
    return work


def remove_pen_marks(img, colors=("blue", "red", "green"), strength: int = 60):
    """Remove coloured ink (pen notes, ticks, signatures in blue/red/green) from a scanned page."""
    rgb, alpha = _rgb_alpha(img)
    hsv = np.asarray(rgb.convert("HSV"), dtype=np.int32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sat_min = 255 - int(strength * 2)
    mask = np.zeros(h.shape, bool)
    ranges = {"blue": (135, 190), "red": (230, 256), "green": (55, 120)}
    for c in colors:
        lo, hi = ranges[c]
        mask |= (h >= lo) & (h < hi) & (s > max(40, sat_min // 2)) & (v > 40)
        if c == "red":
            mask |= (h < 12) & (s > max(40, sat_min // 2)) & (v > 40)
    mask = _dilate(mask, 1)
    arr = np.asarray(rgb).copy()
    paper = np.median(arr[~mask].reshape(-1, 3), axis=0) if (~mask).any() else np.array([255, 255, 255])
    arr[mask] = paper.astype(np.uint8)
    return _with_alpha(Image.fromarray(arr, "RGB"), alpha)


# ============================================================================ background removal
def remove_background(img, tolerance: int = 40, feather: float = 1.5, seeds: list[tuple[int, int]] | None = None):
    """Make the background transparent: colours similar to the image border (or to clicked ``seeds``) that are
    connected to the border/seed points.  Works well for plain backgrounds (ID photos, products, logos)."""
    rgb = img.convert("RGB")
    arr = np.asarray(rgb, dtype=np.int32)
    h, w = arr.shape[:2]
    if seeds:
        samples = np.array([arr[min(h - 1, max(0, y)), min(w - 1, max(0, x))] for x, y in seeds])
    else:  # top and side edges: in portraits and product shots the subject usually touches the bottom edge
        border = np.concatenate([arr[0], arr[:, 0], arr[:, -1]])
        samples = border[:: max(1, len(border) // 400)]
    # cluster the background colours (k-means, k <= 4)
    k = min(4, len(samples))
    centers = samples[np.linspace(0, len(samples) - 1, k).astype(int)].astype(np.float64)
    for _ in range(8):
        d = ((samples[:, None, :] - centers[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            if (lab == j).any():
                centers[j] = samples[lab == j].mean(0)
    dist = np.min([np.sqrt(((arr - c) ** 2).sum(-1)) for c in centers], axis=0)
    similar = dist < tolerance * 1.6
    # keep only regions connected to the border (or the seeds): flood fill on the "similar" map
    bg = np.zeros((h, w), bool)
    if seeds:
        stack = [(min(h - 1, max(0, y)), min(w - 1, max(0, x))) for x, y in seeds]
    else:
        stack = [(0, x) for x in range(0, w, 4) if similar[0, x]]
        stack += [(y, x) for y in range(0, h, 4) for x in (0, w - 1) if similar[y, x]]
    small = similar
    scale = 1
    if h * w > 1_200_000:  # flood on a reduced map for speed
        scale = int(math.ceil(math.sqrt(h * w / 1_200_000)))
        small = similar[::scale, ::scale]
        stack = [(y // scale, x // scale) for y, x in stack]
    sh, sw = small.shape
    seen = np.zeros((sh, sw), bool)
    while stack:
        y, x = stack.pop()
        if y < 0 or x < 0 or y >= sh or x >= sw or seen[y, x] or not small[y, x]:
            continue
        x0 = x
        while x0 > 0 and small[y, x0 - 1] and not seen[y, x0 - 1]:
            x0 -= 1
        x1 = x
        while x1 < sw - 1 and small[y, x1 + 1] and not seen[y, x1 + 1]:
            x1 += 1
        seen[y, x0:x1 + 1] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < sh:
                row = small[ny, x0:x1 + 1] & ~seen[ny, x0:x1 + 1]
                for xx in np.where(row)[0]:
                    stack.append((ny, x0 + int(xx)))
    if scale > 1:
        bg = np.repeat(np.repeat(seen, scale, 0), scale, 1)[:h, :w] & similar
    else:
        bg = seen
    alpha = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8), "L")
    alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))   # close small holes
    if feather:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out = rgb.convert("RGBA")
    if img.mode == "RGBA":
        alpha = ImageChops.multiply(alpha, img.getchannel("A"))
    out.putalpha(alpha)
    return out


def replace_background(img, color: str = "#ffffff"):
    if img.mode != "RGBA":
        img = remove_background(img)
    bg = Image.new("RGBA", img.size, color)
    bg.alpha_composite(img)
    return bg.convert("RGB")


# ============================================================================ ID photos
ID_SIZES = {  # name -> (width mm, height mm)
    "Passport / CNIC - Pakistan (35 \u00d7 45 mm)": (35, 45),
    "Passport - India (35 \u00d7 45 mm)": (35, 45),
    "Passport / Visa - USA (2 \u00d7 2 in)": (51, 51),
    "Schengen visa / EU passport (35 \u00d7 45 mm)": (35, 45),
    "Passport - UK (35 \u00d7 45 mm)": (35, 45),
    "Passport - Canada (50 \u00d7 70 mm)": (50, 70),
    "Passport - China (33 \u00d7 48 mm)": (33, 48),
    "Saudi visa (40 \u00d7 60 mm)": (40, 60),
    "Stamp size (20 \u00d7 25 mm)": (20, 25),
}


def id_photo(img, size_mm: tuple[float, float], background: str = "#ffffff", dpi: int = 300,
             remove_bg: bool = True):
    """Crop the centre of ``img`` to the official aspect ratio at ``dpi`` and set the background colour."""
    wpx, hpx = int(size_mm[0] / 25.4 * dpi), int(size_mm[1] / 25.4 * dpi)
    if remove_bg:
        img = replace_background(img, background)
    img = ImageOps.fit(img.convert("RGB"), (wpx, hpx), Image.LANCZOS, centering=(0.5, 0.42))
    return img


def print_sheet(photo, sheet_mm: tuple[float, float] = (152.4, 101.6), dpi: int = 300, gap_mm: float = 2.5):
    """As many copies as fit on a 6 \u00d7 4 in (or A4) sheet, with thin cut lines."""
    sw, sh = int(sheet_mm[0] / 25.4 * dpi), int(sheet_mm[1] / 25.4 * dpi)
    gap = int(gap_mm / 25.4 * dpi)
    sheet = Image.new("RGB", (sw, sh), "white")
    cols = max(1, (sw - gap) // (photo.width + gap))
    rows = max(1, (sh - gap) // (photo.height + gap))
    ox = (sw - cols * photo.width - (cols - 1) * gap) // 2
    oy = (sh - rows * photo.height - (rows - 1) * gap) // 2
    d = ImageDraw.Draw(sheet)
    for r in range(rows):
        for c in range(cols):
            x, y = ox + c * (photo.width + gap), oy + r * (photo.height + gap)
            sheet.paste(photo, (x, y))
            d.rectangle((x - 1, y - 1, x + photo.width, y + photo.height), outline=(200, 200, 200))
    return sheet, rows * cols


# ============================================================================ privacy and annotation
def mosaic(img, box, block: int = 14):
    region = img.crop(box)
    w, h = region.size
    small = region.resize((max(1, w // block), max(1, h // block)), Image.BILINEAR)
    out = img.copy()
    out.paste(small.resize((w, h), Image.NEAREST), box[:2])
    return out


def blur_region(img, box, radius: float = 12):
    out = img.copy()
    out.paste(img.crop(box).filter(ImageFilter.GaussianBlur(radius)), box[:2])
    return out


def find_font(size: int, bold: bool = False):
    candidates = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else
                  ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"), "/usr/share/fonts/truetype/dejavu"]
    for name in candidates:
        for d in dirs:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def add_text(img, text: str, xy, size: int = 48, color: str = "#ffffff", bold: bool = True, stroke: int = 2,
             stroke_color: str = "#000000", font_path: str | None = None):
    out = img.convert("RGBA")
    font = ImageFont.truetype(font_path, size) if font_path else find_font(size, bold)
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).multiline_text(xy, text, font=font, fill=color, stroke_width=stroke,
                                         stroke_fill=stroke_color, spacing=6)
    out.alpha_composite(layer)
    return out if img.mode == "RGBA" else out.convert("RGB")


def text_watermark(img, text: str, opacity: int = 70, size_pct: int = 6, tiled: bool = True, color="#ffffff"):
    out = img.convert("RGBA")
    size = max(12, int(min(out.size) * size_pct / 100))
    font = find_font(size, True)
    layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw, th = d.textbbox((0, 0), text, font=font)[2:]
    rgba = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (int(255 * opacity / 100),)
    if tiled:
        for y in range(-th, out.height + th, th * 4):
            for x in range(-tw, out.width + tw, int(tw * 1.6)):
                d.text((x + (y // (th * 4)) % 2 * tw // 2, y), text, font=font, fill=rgba)
        layer = layer.rotate(25, resample=Image.BICUBIC, center=(out.width / 2, out.height / 2))
    else:
        d.text((out.width - tw - size // 2, out.height - th - size // 2), text, font=font, fill=rgba,
               stroke_width=max(1, size // 25), stroke_fill=(0, 0, 0, rgba[3] // 2))
    out.alpha_composite(layer)
    return out if img.mode == "RGBA" else out.convert("RGB")


# ============================================================================ saving / compression / conversion
def save(img, path: str, quality: int = 92, strip_metadata: bool = False, lossless: bool = False,
         exif: bytes | None = None) -> int:
    """Save in the format given by the extension; returns the file size."""
    ext = os.path.splitext(path)[1].lower()
    params: dict = {}
    out = img
    if ext in (".jpg", ".jpeg", ".jfif"):
        out = img.convert("RGB") if img.mode != "RGB" else img
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.getchannel("A"))
            out = bg
        params = {"quality": quality, "optimize": True, "progressive": True, "subsampling": 0 if quality >= 90 else 2}
        fmt = "JPEG"
    elif ext == ".png":
        params = {"optimize": True, "compress_level": 9}
        fmt = "PNG"
    elif ext == ".webp":
        params = {"lossless": True, "method": 6} if lossless else {"quality": quality, "method": 6}
        fmt = "WEBP"
    elif ext in (".tif", ".tiff"):
        params = {"compression": "tiff_lzw"}
        fmt = "TIFF"
    elif ext == ".bmp":
        out = img.convert("RGB")
        fmt = "BMP"
    elif ext == ".gif":
        out = img.convert("P", palette=Image.ADAPTIVE)
        fmt = "GIF"
    elif ext == ".ico":
        params = {"sizes": [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(img.size)]}
        fmt = "ICO"
    elif ext == ".pdf":
        out = img.convert("RGB")
        params = {"resolution": 150.0}
        fmt = "PDF"
    else:
        raise ValueError(f"Unsupported image format: {ext}")
    if exif and not strip_metadata and fmt in ("JPEG", "WEBP", "PNG", "TIFF"):
        params["exif"] = exif
    tmp = path + ".tmp"
    out.save(tmp, fmt, **params)
    os.replace(tmp, path)
    return os.path.getsize(path)


def compress(img, path: str, mode: str = "balanced", max_side: int | None = None, exif: bytes | None = None) -> int:
    """mode: "lossless" (PNG/WebP lossless, metadata removed), "high" (JPEG 90), "balanced" (JPEG 80),
    "small" (JPEG 65).  ``max_side`` scales large photos down first."""
    if max_side and max(img.size) > max_side:
        s = max_side / max(img.size)
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    ext = os.path.splitext(path)[1].lower()
    if mode == "lossless":
        return save(img, path, strip_metadata=True, lossless=True)
    q = {"high": 90, "balanced": 80, "small": 65}.get(mode, 80)
    if ext == ".png":
        return save(img.quantize(256, method=Image.FASTOCTREE).convert("RGBA" if img.mode == "RGBA" else "RGB")
                    if mode == "small" else img, path)
    return save(img, path, quality=q, strip_metadata=True, exif=exif if mode == "high" else None)


def encoded_size(img, fmt: str = "JPEG", quality: int = 85) -> int:
    buf = io.BytesIO()
    im = img.convert("RGB") if fmt == "JPEG" else img
    im.save(buf, fmt, quality=quality, optimize=True)
    return buf.tell()


def collage(images: list, cols: int, cell: int = 600, gap: int = 12, background: str = "#ffffff"):
    rows = max(1, math.ceil(len(images) / cols))
    W = cols * cell + (cols + 1) * gap
    H = rows * cell + (rows + 1) * gap
    out = Image.new("RGB", (W, H), background)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        tile = ImageOps.fit(im.convert("RGB"), (cell, cell), Image.LANCZOS)
        out.paste(tile, (gap + c * (cell + gap), gap + r * (cell + gap)))
    return out


def histogram(img) -> list[list[int]]:
    rgb = img.convert("RGB")
    h = rgb.histogram()
    return [h[0:256], h[256:512], h[512:768]]


def exif_info(img) -> list[tuple[str, str]]:
    from PIL import ExifTags

    out = [("Size", f"{img.width} \u00d7 {img.height} px"), ("Mode", img.mode)]
    try:
        exif = img.getexif()
        for k, v in exif.items():
            name = ExifTags.TAGS.get(k, str(k))
            if isinstance(v, bytes):
                continue
            out.append((name, str(v)[:120]))
        gps = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else {}
        if gps:
            out.append(("GPS", "present (removed when you save with 'Remove metadata')"))
    except Exception:
        pass
    return out
