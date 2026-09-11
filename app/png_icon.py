"""Minimal solid-color PNG encoder, stdlib only (no Pillow dependency just
for a flat home-screen icon)."""

from __future__ import annotations

import struct
import zlib


def solid_png(size: int, rgb: tuple[int, int, int]) -> bytes:
    r, g, b = rgb
    row = bytes([0]) + bytes([r, g, b]) * size  # filter-type byte + raw RGB pixels
    raw = row * size
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit truecolor RGB
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
