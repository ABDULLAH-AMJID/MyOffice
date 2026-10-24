"""v1.6.3 - MyOffice-branded, unmistakable file-type icons (Windows Explorer / shortcuts).

Acceptance criteria:
  1. every file type carries the MyOffice brand mark *and* its own colour + glyph + extension label, so a
     PDF, a DOCX, an XLSX and a PPTX can never be mixed up in Explorer;
  2. the artwork is size aware: 16/20/24 px frames use the compact colour tile (a 6 px text label would be
     mud), 32 px and above use the full page design;
  3. the icons are mirrored into the user profile (``%APPDATA%\\MyOffice\\icons``) and ``DefaultIcon`` points
     there, so a read-only Program Files install and a live OTA hot-patch can both deliver new artwork;
  4. a signed OTA hot-patch may ship ``resources/icons/app/*.ico`` and it wins over the copy inside the EXE.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.services import register
from app.services.paths import resource_path

KINDS = ("myoffice", "writer", "sheet", "slide", "pdf", "photos", "code", "vault")
FILE_KINDS = KINDS[1:]
EXPECTED_SIZES = {16, 20, 24, 32, 40, 48, 64, 96, 128, 256}
# kind -> RGB of the bottom colour band: the midpoint of the gradient (top colour -> bottom colour)
BAND_RGB = {
    "writer": (33, 89, 226),
    "sheet": (22, 146, 68),
    "slide": (214, 77, 12),
    "pdf": (203, 33, 33),
    "photos": (108, 46, 210),
    "code": (11, 131, 161),
    "vault": (56, 68, 87),
}


def _band_colour(img: Image.Image) -> tuple[int, int, int]:
    """Average colour of the extension-label strip at the foot of the page (ignores the white letters)."""
    w, h = img.size
    r = g = b = n = 0
    for y in range(int(h * 0.755), int(h * 0.965)):
        for x in range(int(w * 0.14), int(w * 0.86)):
            px = img.getpixel((x, y))
            if px[3] < 200:
                continue
            if min(px[:3]) > 200:      # the white extension label
                continue
            r += px[0]
            g += px[1]
            b += px[2]
            n += 1
    assert n > 200, "no colour band found"
    return (r // n, g // n, b // n)


def _ico_frames(path: Path) -> dict[int, Image.Image]:
    im = Image.open(path)
    sizes = sorted({w for w, _h in im.ico.sizes()})
    out: dict[int, Image.Image] = {}
    for size in sizes:
        im.size = (size, size)
        im.load()
        out[size] = im.convert("RGBA").copy()
    return out


# ------------------------------------------------------------------ 1. artwork exists & is complete
@pytest.mark.parametrize("kind", KINDS)
def test_icon_files_shipped_in_every_explorer_size(kind: str):
    ico = resource_path("icons", "app", f"{kind}.ico")
    png = resource_path("icons", "app", f"{kind}.png")
    assert ico.is_file(), f"missing {ico}"
    assert png.is_file(), f"missing {png}"
    assert ico.stat().st_size > 8_000, "a real multi-resolution .ico is never tiny"
    frames = _ico_frames(ico)
    assert EXPECTED_SIZES <= set(frames), f"{kind}: missing sizes {EXPECTED_SIZES - set(frames)}"
    assert Image.open(png).size == (256, 256)


# ------------------------------------------------- 2. the brand mark + a distinct colour per file type
@pytest.mark.parametrize("kind", FILE_KINDS)
def test_each_file_type_has_its_own_colour_band_and_brand_chip(kind: str):
    frames = _ico_frames(resource_path("icons", "app", f"{kind}.ico"))
    master = frames[256]
    w, h = master.size

    band = master.getpixel((int(w * 0.20), int(h * 0.925)))[:3]
    expected = BAND_RGB[kind]
    assert all(abs(band[i] - expected[i]) <= 26 for i in range(3)), f"{kind}: band {band} != {expected}"

    # the letterhead chip is the MyOffice blue (brand) mark, not the file type colour
    chip = master.getpixel((int(w * 0.175), int(h * 0.135)))[:3]
    assert chip[2] > chip[0] and chip[2] > 120, f"{kind}: brand chip missing, sampled {chip}"

    # paper stays light, so the coloured glyph and the band stand out in both Explorer themes
    paper = master.getpixel((int(w * 0.155), int(h * 0.60)))[:3]
    assert min(paper) > 200, f"{kind}: page is not light ({paper})"


def test_colour_bands_of_all_types_are_mutually_distinct():
    def band(kind: str):
        master = _ico_frames(resource_path("icons", "app", f"{kind}.ico"))[256]
        w, h = master.size
        return master.getpixel((int(w * 0.20), int(h * 0.925)))[:3]

    seen: dict[str, tuple[int, int, int]] = {}
    for kind in FILE_KINDS:
        colour = band(kind)
        for other, other_colour in seen.items():
            distance = sum(abs(a - b) for a, b in zip(colour, other_colour))
            assert distance > 60, f"{kind} and {other} are too similar ({colour} vs {other_colour})"
        seen[kind] = colour


# ----------------------------------------------------------- 3. size-aware frames (tile below 32 px)
@pytest.mark.parametrize("kind", ("writer", "sheet", "slide", "pdf"))
def test_small_frames_use_the_compact_tile_variant(kind: str):
    frames = _ico_frames(resource_path("icons", "app", f"{kind}.ico"))
    tile = frames[16]
    downscaled = frames[256].resize((16, 16), Image.LANCZOS)
    diff = sum(
        1
        for y in range(16)
        for x in range(16)
        if sum(abs(a - b) for a, b in zip(tile.getpixel((x, y))[:3], downscaled.getpixel((x, y))[:3])) > 60
    )
    assert diff > 40, "the 16 px frame must be the purpose-built tile, not a squashed 256 px page"
    # the tile is a solid type colour with white inside - it must stay opaque in the middle
    assert tile.getpixel((8, 8))[3] == 255


# --------------------------------------------- 4. per-user mirror + IconSpec resolution (Explorer)
def test_icon_mirror_is_written_and_used_by_default_icon(tmp_path, monkeypatch):
    monkeypatch.setenv("MYOFFICE_HOME", str(tmp_path))
    written = register.sync_icon_mirror(force=True)
    mirror = register.icons_mirror_dir()
    assert mirror == tmp_path / "icons"
    assert {p.stem for p in written} == set(KINDS)
    for kind in FILE_KINDS:
        copy = mirror / f"{kind}.ico"
        assert copy.is_file() and copy.stat().st_size > 8_000
        assert copy.read_bytes() == resource_path("icons", "app", f"{kind}.ico").read_bytes()
        spec = register.icon_spec_for(kind, r"C:\Program Files\MyOffice\MyOffice.exe")
        assert spec.endswith(f"{kind}.ico")
        assert Path(spec) == copy, "Explorer must read the writable, always-current mirror copy"

    # second run is a no-op (no needless copying on every start)
    assert register.sync_icon_mirror() == []

    # a changed icon on disk is re-mirrored
    stale = mirror / "pdf.ico"
    stale.write_bytes(b"not an icon")
    assert [p.name for p in register.sync_icon_mirror()] == ["pdf.ico"]
    assert (mirror / "icon-pack.txt").is_file()


def test_registration_maps_every_extension_to_the_right_icon_kind():
    by_ext = {ext: data[3] for ext, data in register.ASSOCIATIONS.items()}
    assert by_ext[".pdf"] == "pdf"
    assert {by_ext[e] for e in (".docx", ".docm", ".dotx", ".doc", ".odt", ".rtf", ".wpt")} == {"writer"}
    assert {by_ext[e] for e in (".xlsx", ".xlsm", ".xltx", ".xls", ".csv", ".ods", ".tsv", ".ett")} == {"sheet"}
    assert {by_ext[e] for e in (".pptx", ".ppsx", ".potx", ".ppt", ".odp", ".dpt")} == {"slide"}
    assert {by_ext[e] for e in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".jfif")} == {"photos"}
    assert by_ext[".movault"] == "vault"
    for kind in register.ASSOCIATIONS.values():
        assert kind[3] in KINDS


# ------------------------------------------ 5. a signed OTA hot-patch can deliver new icon artwork
def test_ota_hotpatch_icons_override_the_bundled_ones(tmp_path, monkeypatch):
    ota_root = tmp_path / "ota"
    patch = ota_root / "current"
    (patch / "app").mkdir(parents=True)
    (patch / "app" / "__init__.py").write_text("", encoding="utf-8")
    icons = patch / "resources" / "icons" / "app"
    icons.mkdir(parents=True)
    for kind in FILE_KINDS:
        src = resource_path("icons", "app", f"{kind}.ico")
        shutil.copyfile(src, icons / f"{kind}.ico")

    monkeypatch.setenv("MYOFFICE_OTA_DIR", str(ota_root))
    assert resource_path("icons", "app", "pdf.ico") == icons / "pdf.ico"
    assert resource_path("icons", "app", "does-not-exist.ico") != icons / "does-not-exist.ico"

    # the hot-patched artwork flows into the Explorer mirror
    monkeypatch.setenv("MYOFFICE_HOME", str(tmp_path / "profile"))
    register.sync_icon_mirror(force=True)
    assert (tmp_path / "profile" / "icons" / "pdf.ico").read_bytes() == (icons / "pdf.ico").read_bytes()


def test_windows_hotpatch_bundle_ships_the_icon_family(tmp_path):
    """`publish_release.py --version 1.6.3+` must include the artwork in the ~1 MB hot-patch."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ota-system"))
    try:
        import publish_release
    except Exception as exc:  # cryptography missing in a stripped env
        pytest.skip(f"publish_release not importable: {exc}")

    out = tmp_path / "windows-patch.zip"
    _sha, size, _count = publish_release.package_windows_hotpatch_bundle(out)
    assert size > 0
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    for kind in FILE_KINDS:
        assert f"resources/icons/app/{kind}.ico" in names, "new file icons must reach existing installs"
    assert "app/services/register.py" in names
