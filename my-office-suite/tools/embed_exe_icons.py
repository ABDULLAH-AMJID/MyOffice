"""Embed all 8 MyOffice multi-resolution icon groups directly into `MyOffice.exe` PE resources (indices 0..7).

Why we ship BOTH `{app}\\icons\\*.ico` files AND embed all 8 `RT_GROUP_ICON` resources into `MyOffice.exe`:
- PyInstaller on Windows embeds only 1 icon group (index `0`) by default.
- Embedding all 8 groups (`1..8` resource IDs -> explorer icon indices `0..7`) ensures that BOTH `"MyOffice.exe",0..7`
  AND `"{app}\\icons\\<kind>.ico",0` resolve to razor-sharp multi-size icons on every version of Windows 10 & 11!
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import struct
import sys
from pathlib import Path

RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL_SUBLANG_NEUTRAL = 0x0409  # English (US) standard PE resource language ID

ICON_ORDER = [
    "myoffice",  # index 0 (resource group ID 1)
    "writer",    # index 1 (resource group ID 2)
    "sheet",     # index 2 (resource group ID 3)
    "slide",     # index 3 (resource group ID 4)
    "pdf",       # index 4 (resource group ID 5)
    "photos",    # index 5 (resource group ID 6)
    "code",      # index 6 (resource group ID 7)
    "vault",     # index 7 (resource group ID 8)
]


def parse_ico(path: Path) -> list[tuple[bytes, bytes]]:
    """Return list of `(12_byte_dir_prefix, raw_frame_bytes)` from a `.ico` file."""
    data = path.read_bytes()
    reserved, itype, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or itype != 1 or count < 1:
        raise ValueError(f"Invalid .ico header in {path}")
    out: list[tuple[bytes, bytes]] = []
    for i in range(count):
        base = 6 + i * 16
        dir_prefix = data[base:base + 12]  # width, height, colorCount, reserved, planes, bitCount, bytesInRes
        size, offset = struct.unpack_from("<II", data, base + 8)
        frame = data[offset:offset + size]
        if len(frame) != size:
            raise ValueError(f"Truncated frame {i} in {path}")
        out.append((dir_prefix, frame))
    return out


def embed_icons_win32(exe_path: Path, icons_dir: Path) -> int:
    """Embed all 8 `.ico` files into `exe_path` using Win32 `BeginUpdateResourceW` / `UpdateResourceW`."""
    if sys.platform != "win32":
        import os
        import subprocess

        work = os.environ.get("WORK", "/var/tmp/mo-wine")
        prefix = os.path.join(work, "prefix")
        wine_py = os.path.join(prefix, "drive_c", "Python311", "python.exe")
        if os.path.isfile(wine_py):
            env = dict(os.environ, WINEPREFIX=prefix, WINEARCH="win64", WINEDEBUG="-all")
            z_self = "Z:" + str(Path(__file__).resolve()).replace("/", "\\")
            z_exe = "Z:" + str(exe_path.resolve()).replace("/", "\\")
            z_icons = "Z:" + str(icons_dir.resolve()).replace("/", "\\")
            subprocess.run(["wine", r"C:\Python311\python.exe", z_self, z_exe, z_icons], check=True, env=env)
            return len(ICON_ORDER)
        raise RuntimeError("embed_exe_icons.py must be invoked under Windows / Wine Python (`wine python ...`).")

    kernel32 = ctypes.windll.kernel32
    kernel32.BeginUpdateResourceW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.BOOL]
    kernel32.BeginUpdateResourceW.restype = ctypes.wintypes.HANDLE
    kernel32.UpdateResourceW.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.WORD, ctypes.c_void_p, ctypes.wintypes.DWORD,
    ]
    kernel32.UpdateResourceW.restype = ctypes.wintypes.BOOL
    kernel32.EndUpdateResourceW.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.BOOL]
    kernel32.EndUpdateResourceW.restype = ctypes.wintypes.BOOL

    def makeintresource(i: int):
        return ctypes.cast(ctypes.c_void_p(i), ctypes.wintypes.LPCWSTR)

    h = kernel32.BeginUpdateResourceW(str(exe_path), False)
    if not h:
        raise OSError(f"BeginUpdateResourceW failed on {exe_path} (err={kernel32.GetLastError()})")

    next_icon_id = 100
    groups_written = 0
    try:
        for group_idx, name in enumerate(ICON_ORDER):
            ico_file = icons_dir / f"{name}.ico"
            frames = parse_ico(ico_file)
            grp_header = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
            for dir_prefix, frame in frames:
                icon_id = next_icon_id
                next_icon_id += 1
                buf = ctypes.create_string_buffer(frame, len(frame))
                if not kernel32.UpdateResourceW(
                    h, makeintresource(RT_ICON), makeintresource(icon_id),
                    LANG_NEUTRAL_SUBLANG_NEUTRAL, buf, len(frame),
                ):
                    raise OSError(f"UpdateResourceW(RT_ICON, {icon_id}) failed (err={kernel32.GetLastError()})")
                # GRPICONDIRENTRY = 12-byte ICO prefix + 2-byte WORD nID
                grp_header.extend(dir_prefix)
                grp_header.extend(struct.pack("<H", icon_id))

            group_res_id = group_idx + 1  # IDs 1..8 -> Windows Explorer icon indices 0..7
            gbuf = ctypes.create_string_buffer(bytes(grp_header), len(grp_header))
            for lang in (LANG_NEUTRAL_SUBLANG_NEUTRAL, 0):
                kernel32.UpdateResourceW(
                    h, makeintresource(RT_GROUP_ICON), makeintresource(group_res_id),
                    lang, gbuf, len(grp_header),
                )
            groups_written += 1
    except Exception:
        kernel32.EndUpdateResourceW(h, True)
        raise

    if not kernel32.EndUpdateResourceW(h, False):
        raise OSError(f"EndUpdateResourceW failed on {exe_path} (err={kernel32.GetLastError()})")
    return groups_written


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/embed_exe_icons.py <path/to/MyOffice.exe> [icons_dir]")
        return 2
    exe = Path(sys.argv[1]).resolve()
    icons_dir = Path(sys.argv[2]).resolve() if len(sys.argv) >= 3 else (
        Path(__file__).resolve().parent.parent / "resources" / "icons" / "app"
    )
    n = embed_icons_win32(exe, icons_dir)
    print(f"Embedded {n} multi-resolution icon groups (indices 0..{n - 1}) into {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
