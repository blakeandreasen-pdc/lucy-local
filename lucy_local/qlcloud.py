"""QLCloud wire formats used by Trifo robots (Lucy, Max).

Two framings matter:

* Dispatch packets (TLS on port 7990): the robot sends its MAC and gets back the
  "IP:PORT" of the MQTT broker to use.

      0..3   u32 BE nonce; its low byte is the cipher's starting key
      4..5   u16 BE magic: 0xF1EB = EU, 0xF1EC = US
      6..9   u32 BE payload length N
      10..   payload (ASCII)
      +N     u16 BE CRC-16/MODBUS over the *plaintext* bytes 0..10+N

  Bytes 4..len-4 are obfuscated with a rolling XOR keyed by the nonce and a
  fixed seed (0x7ECF). The last 4 bytes stay in the clear.

* MQTT payloads: a 16-byte header followed by a JSON (or binary) body.

      0..3   u32 BE total length (header + body)
      4..5   protocol, 0x0010
      6..7   command, 0x0201
      8..9   flags
      10..11 opcode, e.g. 0x0504 downdps, 0x0402 rx_data
      12..15 u32 BE sequence number
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass

SEED = 0x7ECF
MAGIC_EU = 0xF1EB
MAGIC_US = 0xF1EC

# Opcodes seen on the wire
OP_REGISTER = 0x0101
OP_AUTH = 0x0201
OP_HEARTBEAT = 0x0301
OP_INFO = 0x0303          # iot_get_info: robot asks for owner (0) / bind status (2)
OP_RX = 0x0402            # app command to the robot (QLRPDataItem)
OP_TX = 0x0403            # robot's reply to an app command
OP_UPDPS = 0x0503         # robot publishes data points (status)
OP_DOWNDPS = 0x0504       # we send data points (smart-speaker commands)
OP_UPDPS_ALT = 0x0B06
# Bind / unbind results the cloud pushes (libtrifo_core_cloud qlcloud_process_bindinfo): a body of
# {"res":{"errcode":0}} sets the robot's bind state to bound (0x0B02, 0x0B07) or unbound (0x0B03).
OP_BIND = 0x0B02
OP_UNBIND = 0x0B03

HEADER_LEN = 16


def _steps(seed: int) -> tuple[int, int]:
    return ((seed >> 10) & 0x3FF) + 0x173, (seed & 0x3FF) + 0x33D


def _next_key(key_input: int, step_hi: int, step_lo: int) -> int:
    return (((key_input * step_hi + step_lo) & 0xFFF) + 0x6F) & 0xFF


def decrypt(buf: bytearray, start: int, end: int, key: int, seed: int = SEED) -> None:
    hi, lo = _steps(seed)
    key &= 0xFF
    for i in range(start, end):
        ct = buf[i]
        buf[i] = ct ^ key
        key = _next_key(ct + key, hi, lo)


def encrypt(buf: bytearray, start: int, end: int, key: int, seed: int = SEED) -> None:
    hi, lo = _steps(seed)
    key &= 0xFF
    for i in range(start, end):
        ct = (buf[i] ^ key) & 0xFF
        buf[i] = ct
        key = _next_key(ct + key, hi, lo)


def crc16_modbus(data: bytes | bytearray) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass
class DispatchPacket:
    nonce: int
    magic: int
    payload: bytes
    crc_ok: bool

    @property
    def region(self) -> str:
        return {MAGIC_EU: "eu", MAGIC_US: "us"}.get(self.magic, "unknown")


def parse_dispatch(raw: bytes) -> DispatchPacket | None:
    if len(raw) < 12:
        return None
    buf = bytearray(raw)
    nonce = struct.unpack_from(">I", buf, 0)[0]
    decrypt(buf, 4, len(buf) - 4, nonce & 0xFF)
    magic = struct.unpack_from(">H", buf, 4)[0]
    n = struct.unpack_from(">I", buf, 6)[0]
    if 10 + n + 2 > len(buf):
        return None
    stored = struct.unpack_from(">H", buf, 10 + n)[0]
    return DispatchPacket(nonce, magic, bytes(buf[10:10 + n]), stored == crc16_modbus(buf[:10 + n]))


def build_dispatch(payload: str, magic: int = MAGIC_EU, nonce: int | None = None) -> bytes:
    data = payload.encode("ascii")
    total = 10 + len(data) + 2
    buf = bytearray(total)
    nonce = int.from_bytes(os.urandom(4), "big") if nonce is None else nonce
    struct.pack_into(">IHI", buf, 0, nonce, magic, len(data))
    buf[10:10 + len(data)] = data
    struct.pack_into(">H", buf, 10 + len(data), crc16_modbus(buf[:10 + len(data)]))
    encrypt(buf, 4, total - 4, nonce & 0xFF)
    return bytes(buf)


def looks_like_dispatch(first: bytes) -> bool:
    return 12 <= len(first) < 64 and first[:2] == b"\x00\x00"


@dataclass
class Frame:
    opcode: int
    seq: int
    flags: int
    body: bytes

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError):
            return None


def parse_frame(payload: bytes) -> Frame | None:
    if len(payload) < HEADER_LEN:
        return None
    flags, opcode, seq = struct.unpack_from(">HHI", payload, 8)
    return Frame(opcode=opcode, seq=seq, flags=flags, body=payload[HEADER_LEN:])


def build_frame(opcode: int, body: dict | bytes, seq: int, flags: int = 0x0100) -> bytes:
    raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("ascii")
    header = struct.pack(">IHHHHI", HEADER_LEN + len(raw), 0x0010, 0x0201, flags, opcode, seq)
    return header + bytes(raw)


def extract_json_object(body: bytes):
    """Replies to rx commands (opcode 0x0403) wrap JSON in a binary prefix."""
    start, end = body.find(b"{"), body.rfind(b"}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(body[start:end + 1])
    except ValueError:
        return None
