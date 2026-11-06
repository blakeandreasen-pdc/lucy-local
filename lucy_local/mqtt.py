"""The small slice of MQTT 3.1.1 the robot uses, as a server.

The robot connects, subscribes to one topic (d/s/<id>), publishes on another
(d/p/<id>) and pings. We only need to frame packets and answer.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK = 1, 2, 3, 4, 8, 9
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14


def encode_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def decode_length(buf: bytes, offset: int = 1) -> tuple[int, int] | None:
    """Returns (remaining_length, header_end) or None if more bytes are needed."""
    value, mult = 0, 1
    for i in range(offset, min(len(buf), offset + 4)):
        value += (buf[i] & 0x7F) * mult
        mult *= 128
        if not buf[i] & 0x80:
            return value, i + 1
    return None


def split_packets(buf: bytearray) -> list[bytes]:
    """Pop every complete packet off the front of buf."""
    packets = []
    while len(buf) >= 2:
        decoded = decode_length(buf)
        if decoded is None:
            break
        length, header_end = decoded
        total = header_end + length
        if len(buf) < total:
            break
        packets.append(bytes(buf[:total]))
        del buf[:total]
    return packets


def _string(buf: bytes, offset: int) -> tuple[str, int]:
    n = struct.unpack_from("!H", buf, offset)[0]
    return buf[offset + 2:offset + 2 + n].decode("utf-8", "replace"), offset + 2 + n


@dataclass
class Publish:
    topic: str
    payload: bytes
    qos: int
    packet_id: int | None


def parse_publish(pkt: bytes) -> Publish:
    qos = (pkt[0] >> 1) & 3
    _, offset = decode_length(pkt)
    topic, offset = _string(pkt, offset)
    packet_id = None
    if qos:
        packet_id = struct.unpack_from("!H", pkt, offset)[0]
        offset += 2
    return Publish(topic, pkt[offset:], qos, packet_id)


def parse_subscribe(pkt: bytes) -> tuple[int, list[tuple[str, int]]]:
    length, offset = decode_length(pkt)
    end = offset + length
    packet_id = struct.unpack_from("!H", pkt, offset)[0]
    offset += 2
    topics = []
    while offset < end:
        topic, offset = _string(pkt, offset)
        topics.append((topic, pkt[offset]))
        offset += 1
    return packet_id, topics


def parse_connect_client_id(pkt: bytes) -> str:
    _, offset = decode_length(pkt)
    _, offset = _string(pkt, offset)     # protocol name
    offset += 4                          # level, flags, keepalive
    client_id, _ = _string(pkt, offset)
    return client_id


def parse_connect_keepalive(pkt: bytes) -> int:
    """Keep-alive seconds from a CONNECT (the client must send something at least this often)."""
    _, offset = decode_length(pkt)
    _, offset = _string(pkt, offset)     # protocol name
    return struct.unpack_from("!H", pkt, offset + 2)[0]


def connack() -> bytes:
    return bytes([0x20, 0x02, 0x00, 0x00])


def suback(packet_id: int, qos: list[int]) -> bytes:
    body = struct.pack("!H", packet_id) + bytes(qos)
    return bytes([0x90]) + encode_length(len(body)) + body


def puback(packet_id: int) -> bytes:
    return bytes([0x40, 0x02]) + struct.pack("!H", packet_id)


def pingresp() -> bytes:
    return bytes([0xD0, 0x00])


def publish(topic: str, payload: bytes) -> bytes:
    t = topic.encode()
    body = struct.pack("!H", len(t)) + t + payload
    return bytes([0x30]) + encode_length(len(body)) + body
