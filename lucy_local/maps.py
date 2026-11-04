"""The Trifo Max's map.

`GET_MAP` on rx key 8 is answered (after ~10 s) with base64 of an uncompressed grid:

    0   "ogm\\0"
    4   u32  0 (unknown)
    8   u32  width
    12  u32  height
    16  f64  cell size in metres (0.1)
    24  width*height bytes, row by row
    ..  trailer, little-endian f64s: 3 = robot x, y, theta; 6 = dock x, y, theta, then robot

Cells are occupancy 0-10: 5 unknown, below 5 free, above 5 likely obstacle. Poses are metres
and radians in a frame centred on the grid, x pointing up the rows and y left along the
columns (the Trifo app's h3.z decoder, Max branch):

    row = round((height * cell / 2 - x) / cell - 0.5)
    col = round((width * cell / 2 - y) / cell - 0.5)
"""

from __future__ import annotations

import base64
import math
import struct
import time
import zlib
from dataclasses import dataclass, field

MAGIC = b"ogm\x00"
HEADER = 24


@dataclass
class GridMap:
    width: int
    height: int
    resolution: float
    cells: bytes
    extra: list[float] = field(default_factory=list)
    received: float = field(default_factory=time.time)

    def to_cell(self, x: float, y: float) -> tuple[int, int]:
        r = self.resolution
        return (round((self.height * r / 2 - x) / r - 0.5), round((self.width * r / 2 - y) / r - 0.5))

    def _pose(self, x: float, y: float, theta: float) -> dict:
        row, col = self.to_cell(x, y)
        return {"x": x, "y": y, "theta": theta, "row": row, "col": col}

    @property
    def dock(self) -> dict | None:
        return self._pose(*self.extra[0:3]) if len(self.extra) >= 6 else None

    @property
    def robot(self) -> dict | None:
        if len(self.extra) >= 6:
            return self._pose(*self.extra[3:6])
        return self._pose(*self.extra[0:3]) if len(self.extra) >= 3 else None

    def as_dict(self) -> dict:
        return {"width": self.width, "height": self.height, "resolution": self.resolution,
                "cells": base64.b64encode(self.cells).decode(), "robot": self.robot,
                "dock": self.dock, "received": self.received}


def parse_ogm(raw: bytes) -> GridMap | None:
    if len(raw) < HEADER or raw[:4] != MAGIC:
        return None
    width, height = struct.unpack_from("<II", raw, 8)
    (resolution,) = struct.unpack_from("<d", raw, 16)
    end = HEADER + width * height
    if width == 0 or height == 0 or width > 4000 or height > 4000 or len(raw) < end:
        return None
    tail = raw[end:]
    extra = list(struct.unpack_from(f"<{len(tail) // 8}d", tail)) if len(tail) >= 8 else []
    return GridMap(width, height, resolution, raw[HEADER:end], extra)


def parse_reply(data: str) -> GridMap | None:
    try:
        return parse_ogm(base64.b64decode(data, validate=False))
    except (ValueError, struct.error):
        return None


# ---------------------------------------------------------------- PNG (for Home Assistant)
FREE = (79, 140, 255)
WALL = (74, 80, 104, 255)
DOCK = (62, 207, 142, 255)
ROBOT = (242, 168, 59, 255)
OUTLINE = (15, 17, 23, 255)
MAX_SIDE = 1024


def _png(width: int, height: int, rgba: bytearray) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    stride = width * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def render_png(m: GridMap, scale: int = 8, pad: int = 4) -> bytes:
    """The explored part of the map, cropped, `scale` px per cell (shrunk to fit MAX_SIDE),
    unknown cells transparent, with the dock (green square) and robot (orange dot + heading)."""
    W, H, cells = m.width, m.height, m.cells
    known = [(i % W, i // W) for i, v in enumerate(cells) if v != 5]
    poses = [p for p in (m.robot, m.dock) if p and 0 <= p["col"] < W and 0 <= p["row"] < H]
    pts = known + [(p["col"], p["row"]) for p in poses]
    if not pts:
        return _png(1, 1, bytearray(4))
    x0 = max(0, min(x for x, _ in pts) - pad); x1 = min(W - 1, max(x for x, _ in pts) + pad)
    y0 = max(0, min(y for _, y in pts) - pad); y1 = min(H - 1, max(y for _, y in pts) + pad)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    k = max(1, min(scale, MAX_SIDE // max(w, h)))
    PW, PH = w * k, h * k
    img = bytearray(PW * PH * 4)
    for cy in range(h):
        row = cells[(cy + y0) * W + x0:(cy + y0) * W + x0 + w]
        line = bytearray()
        for v in row:
            if v == 5:
                px = b"\x00\x00\x00\x00"
            elif v < 5:
                px = bytes((*FREE, 90 + v * 10))
            else:
                px = bytes(WALL)
            line += px * k
        for dy in range(k):
            start = ((cy * k + dy) * PW) * 4
            img[start:start + PW * 4] = line

    def put(x: int, y: int, c) -> None:
        if 0 <= x < PW and 0 <= y < PH:
            img[(y * PW + x) * 4:(y * PW + x) * 4 + 4] = bytes(c)

    def centre(p) -> tuple[float, float]:
        return (p["col"] - x0 + 0.5) * k, (p["row"] - y0 + 0.5) * k

    r = max(5.0, 1.6 * k)
    if m.dock:
        cx, cy = centre(m.dock)
        s = r * 0.7
        for y in range(int(cy - s - 1), int(cy + s + 2)):
            for x in range(int(cx - s - 1), int(cx + s + 2)):
                dx, dy = abs(x + 0.5 - cx), abs(y + 0.5 - cy)
                if dx <= s + 1 and dy <= s + 1:
                    put(x, y, DOCK if dx <= s and dy <= s else OUTLINE)
    if m.robot:
        cx, cy = centre(m.robot)
        for y in range(int(cy - r - 2), int(cy + r + 3)):
            for x in range(int(cx - r - 2), int(cx + r + 3)):
                d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                if d <= r + 1.5:
                    put(x, y, ROBOT if d <= r else OUTLINE)
        # x is up (-row), y is left (-col): heading (cos t, sin t) -> image (-sin t, -cos t)
        t = m.robot["theta"]
        hx, hy = -math.sin(t), -math.cos(t)
        for i in range(int(r * 1.9 * 2) + 1):
            d = i / 2
            for o in (-1, 0, 1):
                put(int(cx + hx * d - hy * o * 0.6), int(cy + hy * d + hx * o * 0.6), ROBOT)
    return _png(PW, PH, img)
