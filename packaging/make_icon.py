"""Generate the brand mark — a teal rounded square with three rising bars in
teal tints (mirrors assets/mark.svg from the design system) — as a Windows
``.ico`` (16px + 32px, 32-bit BGRA) OR as a ``.png`` at an arbitrary size for
the macOS ``.icns`` pipeline. Stdlib-only and deterministic, so the build
artifact is reproducible on every platform.

Usage:
    python make_icon.py [out.ico] [--working]         # Windows .ico
    python make_icon.py --png SIZE out.png [--working] # single PNG (macOS)

One generator, one mark: the macOS icon is the same pixels as the Windows one,
so the two native shells never drift visually (design-system rule: extend the
shared primitive, don't fork per surface)."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

TEAL_600 = (0x5F, 0x6C, 0x22, 255)  # BGRA for #226c5f (brand square)
BAR_COLORS = (
    (0xE7, 0xEC, 0xDB, 255),  # #dbece7 (teal-100)
    (0xAE, 0xBC, 0x82, 255),  # #82bcae (teal-300)
    (0xF6, 0xF8, 0xF3, 255),  # #f3f8f6 (text-on-teal)
)
DOT_GREEN = (0x57, 0x7D, 0x2F, 255)  # #2f7d57 (positive-600) presence dot
DOT_RING = (0xF8, 0xFB, 0xFC, 255)  # paper-white ring behind the dot
CLEAR = (0, 0, 0, 0)


def _draw(size: int, working: bool = False) -> list[list[tuple[int, int, int, int]]]:
    pixels = [[CLEAR for _ in range(size)] for _ in range(size)]
    radius = max(2, size // 4)  # mark.svg uses rx=11 on 44px — a soft square
    for y in range(size):
        for x in range(size):
            # Rounded-square hit test.
            dx = max(radius - x, x - (size - 1 - radius), 0)
            dy = max(radius - y, y - (size - 1 - radius), 0)
            if dx * dx + dy * dy <= radius * radius:
                pixels[y][x] = TEAL_600
    # Three rising bars in the mark's tints.
    baseline = size - max(3, size // 5)
    bar_width = max(2, size // 6)
    gap = max(1, size // 10)
    start = max(2, size // 6)
    heights = [size // 4, size // 2 - 1, (size * 2) // 3]
    for index, height in enumerate(heights):
        color = BAR_COLORS[index % len(BAR_COLORS)]
        x0 = start + index * (bar_width + gap)
        for y in range(baseline - height, baseline):
            for x in range(x0, min(x0 + bar_width, size - 2)):
                if 0 <= y < size:
                    pixels[y][x] = color
    if working:
        # Presence dot (bottom-right): a green disc on a paper ring.
        dot_radius = max(3, (size * 9) // 32)
        center = size - dot_radius - 1
        for y in range(size):
            for x in range(size):
                distance_sq = (x - center) ** 2 + (y - center) ** 2
                if distance_sq <= dot_radius * dot_radius:
                    pixels[y][x] = DOT_RING
                if distance_sq <= (dot_radius - max(1, size // 16)) ** 2:
                    pixels[y][x] = DOT_GREEN
    return pixels


def _encode_dib(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    size = len(pixels)
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0
    )
    rows = bytearray()
    for row in reversed(pixels):  # bottom-up
        for b, g, r, a in row:
            rows += struct.pack("<BBBB", b, g, r, a)
    # AND mask: 1bpp rows padded to 32 bits, all zeros (alpha drives shape).
    mask_row_bytes = ((size + 31) // 32) * 4
    mask = bytes(mask_row_bytes * size)
    return header + bytes(rows) + mask


def build_ico(working: bool = False) -> bytes:
    images = [_encode_dib(_draw(size, working)) for size in (16, 32)]
    sizes = (16, 32)
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, image in zip(sizes, images, strict=True):
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset
        )
        offset += len(image)
    return out + entries + b"".join(images)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def build_png(size: int, working: bool = False) -> bytes:
    """Encode the mark as an 8-bit RGBA PNG at ``size``x``size``. Pixels come
    from the same ``_draw`` used for the .ico (BGRA there); we swap B/R here so
    the PNG is true-color RGBA. Deterministic: fixed zlib level, no timestamp
    chunks."""
    pixels = _draw(size, working)
    raw = bytearray()
    for row in pixels:  # PNG is top-down
        raw.append(0)  # filter type 0 (None) per scanline
        for b, g, r, a in row:
            raw += struct.pack(">BBBB", r, g, b, a)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit, RGBA
    idat = zlib.compress(bytes(raw), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    working = "--working" in args
    args = [a for a in args if a != "--working"]

    if args and args[0] == "--png":
        # --png SIZE OUT.png
        size = int(args[1])
        target = Path(args[2]) if len(args) > 2 else Path(f"mark-{size}.png")
        target.write_bytes(build_png(size, working=working))
    else:
        target = Path(args[0]) if args else Path("practicegraph.ico")
        target.write_bytes(build_ico(working=working))
    print(f"wrote {target}")
