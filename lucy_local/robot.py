"""The fake Trifo cloud: dispatch + MQTT broker the robot talks to, and the
link we send commands over.

Flow when the robot boots (DNS sends *.trifo.com here):
  1. TLS :7990, binary dispatch packet carrying its MAC -> we answer "SERVER_IP:17766".
  2. TLS :17766, MQTT CONNECT / SUBSCRIBE d/s/<id>.
  3. Registration publish (has product_sign)  -> reply opcode 0x0101.
  4. Auth publish (has dev_sign)              -> reply opcode 0x0201. Now online.
  5. Heartbeats (0x0301) and info requests (0x0303) need answers or she keeps asking.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import ssl
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import maps, mqtt, qlcloud as ql
from .profiles import Command, ProfileStore

log = logging.getLogger("lucy.robot")

# The robot asks for its bound owner (type 0) and bind status (type 2). Answering type 0
# with the wrong user id makes her unbind herself, so only type 2 is ever answered, and
# streaming / control work without the owner reply.
INFO_REPLIES = {2: {"t": 2, "v": {"b": 1}}}
MAP_WAIT = 150          # seconds to wait for a GET_MAP reply before asking again


@dataclass
class Status:
    online: bool = False
    code: str | None = None
    name: str = "unknown"
    battery: int | None = None
    suction: int | None = None
    volume: int | None = None
    muted: bool | None = None
    position: str | None = None
    firmware: str | None = None
    ip: str | None = None
    updated: str | None = None
    map_updated: float | None = None   # unix time of the newest map, so clients know to refetch

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Device:
    serial: str = ""
    subid: str = ""
    mac: str = ""
    topic: str = ""
    region: str = ""
    product_id: int | None = None
    last_seen: str | None = None


def _int(v) -> int | None:
    try:
        n = int(float(v))
        return None if n < 0 else n
    except (TypeError, ValueError):
        return None


class Robot:
    def __init__(self, server_ip: str, mqtt_port: int, data_dir: Path, profiles: ProfileStore):
        self.server_ip = server_ip
        self.mqtt_port = mqtt_port
        self.data_dir = data_dir
        self.profiles = profiles
        self.status = Status()
        self.device = self._load_device()
        self.responses: deque[dict] = deque(maxlen=200)
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._seq = 1000
        self._listeners: set[asyncio.Queue] = set()
        # CAPTURE=1: append every frame (both directions, except heartbeats) to capture.jsonl
        self.capture = os.environ.get("CAPTURE", "") == "1"
        self.map: maps.GridMap | None = self._load_map()
        self.status.map_updated = self.map.received if self.map else None
        self._map_asked = 0.0          # when GET_MAP was last sent
        self._map_wanted_until = 0.0   # someone is looking at the map until then
        self._parts: dict[tuple, dict[int, str]] = {}   # split replies being reassembled

    def record(self, direction: str, frame: ql.Frame | None = None, raw: bytes = b"", **extra) -> None:
        if not self.capture or (frame and frame.opcode == ql.OP_HEARTBEAT):
            return
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "dir": direction, **extra}
        body = frame.body if frame else raw
        if frame:
            rec.update(op=f"0x{frame.opcode:04X}", seq=frame.seq, flags=f"0x{frame.flags:04X}")
        rec["len"] = len(body)
        try:
            rec["text"] = body.decode("utf-8")
        except UnicodeDecodeError:
            rec["b64"] = base64.b64encode(body).decode()
        try:
            with open(self.data_dir / "capture.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError as e:
            log.warning("capture write failed: %s", e)

    # ---------------------------------------------------------------- persistence
    def _load_map(self) -> maps.GridMap | None:
        try:
            m = maps.parse_ogm((self.data_dir / "map.ogm").read_bytes())
        except OSError:
            return None
        if m:
            m.received = (self.data_dir / "map.ogm").stat().st_mtime
        return m

    @property
    def _device_path(self) -> Path:
        return self.data_dir / "device.json"

    def _load_device(self) -> Device:
        try:
            return Device(**json.loads(self._device_path.read_text()))
        except (OSError, ValueError, TypeError):
            return Device()

    def _save_device(self) -> None:
        tmp = self._device_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.device.__dict__, indent=2))
        tmp.replace(self._device_path)

    # ---------------------------------------------------------------- events
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _changed(self) -> None:
        self.status.updated = datetime.now().isoformat(timespec="seconds")
        snapshot = self.status.as_dict()
        for q in list(self._listeners):
            if q.full():
                q.get_nowait()
            q.put_nowait(snapshot)

    def _set_code(self, code) -> None:
        code = None if code in (None, "", "-1") else str(code)
        if code is None:
            return
        if code != self.status.code:
            log.info("state %s -> %s (%s)", self.status.code, code,
                     self.profiles.active.status_names.get(code, "unnamed"))
        self.status.code = code
        self.status.name = self.profiles.active.status_names.get(code, f"state {code}")

    def rename_status(self) -> None:
        """Re-apply status names after the active profile changes."""
        if self.status.code:
            self._set_code(self.status.code)
            self._changed()

    # ---------------------------------------------------------------- sending
    @property
    def online(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _send(self, opcode: int, body: dict | bytes, seq: int | None = None) -> None:
        if not self.online or not self.device.topic:
            raise ConnectionError("robot is not connected")
        if seq is None:
            self._seq += 1
            seq = self._seq
        frame = ql.build_frame(opcode, body, seq)
        self.record("out", ql.parse_frame(frame))
        pkt = mqtt.publish(self.device.topic, frame)
        async with self._write_lock:
            self._writer.write(pkt)
            await self._writer.drain()

    async def send_dp(self, dp_id: int, value: str) -> None:
        await self._send(ql.OP_DOWNDPS, {"data": [{"i": dp_id, "v": value}],
                                         "subid": self.device.subid,
                                         "expire": int(time.time()) + 300})
        log.info("-> dp %s %r", dp_id, value)

    async def send_rx(self, cmd: str, data: str = "", key: str = "2") -> None:
        item = {"head": "rq", "key": str(key), "cmd": cmd, "sum": 0, "index": 0,
                "requestid": f"ll{int(time.time() * 1000)}", "data": data}
        await self._send(ql.OP_RX, {"data": json.dumps(item), "ts": int(time.time()),
                                    "subid": self.device.subid})
        log.debug("-> rx %s %r key=%s", cmd, data, key)

    async def send_command(self, c: Command) -> None:
        if c.type == "dp":
            await self.send_dp(c.dp, c.value)
        else:
            await self.send_rx(c.cmd, c.data, c.key)

    async def run_action(self, name: str, value=None) -> list[dict]:
        action = self.profiles.active.actions.get(name)
        if action is None:
            raise KeyError(name)
        if action.choices is not None:
            if value is None or str(value) not in action.choices:
                raise ValueError(f"{name} takes one of {', '.join(action.choices)}")
            value = str(value)
        sent = []
        for i, cmd in enumerate(action.commands):
            rendered = cmd.render(value)
            await self.send_command(rendered)
            sent.append(rendered.model_dump(exclude_defaults=True))
            if i + 1 < len(action.commands):
                await asyncio.sleep(0.1)
        if name in ("mute", "unmute") and "unmute" in self.profiles.active.actions:
            # explicit voice off/on: show it now; the robot's VOICE reply confirms it later
            self.status.muted = name == "mute"
            self._changed()
        return sent

    # ---------------------------------------------------------------- receiving
    def _on_status_point(self, raw) -> None:
        try:
            s = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return
        if not isinstance(s, dict):
            return
        if "status" in s:
            self._set_code(s.get("status"))
        if "battery" in s:
            self.status.battery = _int(s["battery"])
        if "blower" in s:
            self.status.suction = _int(s["blower"])
        if "volume" in s:
            self.status.volume = _int(s["volume"])
        if "mute" in s:
            self.status.muted = str(s["mute"]).lower() == "true"
        if "position" in s:
            self.status.position = s["position"]
        if "firmware" in s:
            self.status.firmware = s["firmware"]
        self._changed()

    def _reassemble(self, item: dict) -> dict | None:
        """Long replies may come split: same requestid, "sum" parts numbered by "index"."""
        try:
            total, index = int(item.get("sum") or 0), int(item.get("index") or 0)
        except (TypeError, ValueError):
            return item
        if "lz4_source_size" in item:
            log.warning("%s reply is LZ4-compressed; not supported yet", item.get("cmd"))
        if total <= 1:
            return item
        key = (item.get("cmd"), item.get("requestid"))
        parts = self._parts.setdefault(key, {})
        parts[index] = str(item.get("data") or "")
        if len(parts) < total:
            return None
        del self._parts[key]
        return {**item, "data": "".join(parts[i] for i in sorted(parts))}

    def _on_reply(self, item: dict) -> None:
        cmd, data = item.get("cmd", ""), item.get("data", "")
        shown = data if len(str(data)) < 500 else str(data)[:500] + "…"
        if cmd == "GET_MAP":
            shown = f"<map, {len(str(data))} chars>"
        self.responses.append({"ts": datetime.now().isoformat(timespec="seconds"),
                               "cmd": cmd, "data": shown, "key": item.get("key", "")})
        if cmd == "GET_MAP":
            self._on_map(str(data))
            return
        if cmd == "ALL_INFO" and isinstance(data, str) and ";" in data:
            # Max: "12;5.313000;4;100;<ssid>;<ip>;<mac>;63;0;-1;1;-1;-1;0;0"
            f = data.split(";")
            self._set_code(f[0])
            if len(f) > 3:
                self.status.battery = _int(f[3])
            if len(f) > 5:
                self.status.ip = f[5] or self.status.ip
            if len(f) > 7:
                self.status.suction = _int(f[7])
            self._changed()
        elif cmd == "ALL_INFO_JSON" and data:
            try:
                info = json.loads(data)
            except ValueError:
                return
            self._set_code(info.get("clean_state"))
            self.status.battery = _int(info.get("battery")) or self.status.battery
            self.status.suction = _int(info.get("blower")) or self.status.suction
            self._changed()
        elif cmd == "VOICE" and str(data) in ("0", "1"):
            self.status.muted = str(data) == "0"     # the app's voice switch, read with key 3
            self._changed()
        elif cmd == "VOICE_VOLUME":
            self.status.volume = _int(data)
            self._changed()

    async def _on_frame(self, frame: ql.Frame, session: dict) -> None:
        body = frame.json()
        op = frame.opcode
        if op in (ql.OP_UPDPS, ql.OP_UPDPS_ALT) and isinstance(body, dict):
            for point in body.get("data") or []:
                if point.get("i") in (2, 24):
                    self._on_status_point(point.get("v"))
        elif op == ql.OP_TX:
            item = ql.extract_json_object(frame.body)
            if isinstance(item, dict):
                item = self._reassemble(item)
                if item:
                    self._on_reply(item)
        elif op == ql.OP_HEARTBEAT:
            await self._reply(session, ql.OP_HEARTBEAT, {"res": {"errcode": 0}}, frame.seq)
        elif op == ql.OP_INFO and isinstance(body, dict):
            try:
                kind = int((body.get("data") or {}).get("type"))
            except (TypeError, ValueError):
                kind = None
            if kind in INFO_REPLIES:
                await self._reply(session, ql.OP_INFO, INFO_REPLIES[kind], frame.seq)
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            return
        data = body["data"]
        if "dev_sign" in data:
            await self._on_auth(session, data, frame.seq)
        elif "product_sign" in data:
            await self._on_register(session, body, frame.seq)

    async def _reply(self, session: dict, opcode: int, body: dict, seq: int) -> None:
        topic = session.get("topic")
        if not topic:
            return
        frame = ql.build_frame(opcode, body, seq)
        self.record("out", ql.parse_frame(frame))
        session["writer"].write(mqtt.publish(topic, frame))
        await session["writer"].drain()

    async def _on_register(self, session: dict, body: dict, seq: int) -> None:
        data = body["data"]
        sn = data.get("sn", "")
        self.device.serial = sn
        self.device.subid = sn[:-1] if len(sn) > 1 else sn
        self.device.mac = data.get("mac", "")
        self.device.topic = session.get("topic", "")
        self.device.product_id = (body.get("req") or {}).get("product_id") or self.device.product_id
        self.device.region = session.get("region") or getattr(self, "_last_region", "") or self.device.region
        self.device.last_seen = datetime.now().isoformat(timespec="seconds")
        self._save_device()
        dev_key = base64.b64encode(bytes(range(1, 17))).decode()
        await self._reply(session, ql.OP_REGISTER, {"res": {"errcode": 0},
                                                    "data": {"dev_id": sn, "dev_key": dev_key}}, seq)
        log.info("registered %s (%s) on %s", sn, self.device.mac, self.device.topic)

    async def _on_auth(self, session: dict, data: dict, seq: int) -> None:
        sn = data.get("dev_id") or self.device.subid
        token = hashlib.sha256(os.urandom(16)).hexdigest()[:44]
        await self._reply(session, ql.OP_AUTH, {"res": {"errcode": 0},
                                                "data": {"iotid": sn, "token": token, "ts": int(time.time())}}, seq)
        self.device.topic = session.get("topic") or self.device.topic
        self.device.last_seen = datetime.now().isoformat(timespec="seconds")
        self._save_device()
        old, self._writer = self._writer, session["writer"]
        if old is not None and old is not self._writer:
            old.close()
        self.status.online = True
        self._changed()
        log.info("robot online: %s", self.device.serial)
        asyncio.create_task(self._ask_status_soon())

    async def _ask_status_soon(self) -> None:
        # Asked straight after auth she ignores it, so wait a little and retry until answered.
        for delay in (8, 6, 10):
            await asyncio.sleep(delay)
            if self.status.code is not None or not self.online:
                return
            try:
                await self.run_action("status_refresh")
            except (ConnectionError, KeyError):
                return

    # ---------------------------------------------------------------- connections
    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        session = {"writer": writer, "topic": None, "region": None}
        buf = bytearray()
        try:
            first = await asyncio.wait_for(reader.read(8192), timeout=30)
            if not first:
                return
            if ql.looks_like_dispatch(first):
                await self._dispatch(first, writer, session)
            elif first[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD"):
                await self._http(first, reader, writer)
                return
            else:
                buf += first
            while True:
                for pkt in mqtt.split_packets(buf):
                    await self._on_mqtt(pkt, session)
                chunk = await asyncio.wait_for(reader.read(65536), timeout=300)
                if not chunk:
                    break
                if not buf and ql.looks_like_dispatch(chunk):
                    await self._dispatch(chunk, writer, session)
                    continue
                buf += chunk
        except (asyncio.TimeoutError, ConnectionError, ssl.SSLError, OSError) as e:
            log.debug("connection %s ended: %s", peer, e)
        except Exception:
            log.exception("connection %s failed", peer)
        finally:
            if writer is self._writer:
                self._writer = None
                self.status.online = False
                self._changed()
                log.info("robot offline")
            writer.close()

    async def _dispatch(self, raw: bytes, writer: asyncio.StreamWriter, session: dict) -> None:
        pkt = ql.parse_dispatch(raw)
        if pkt is None:
            log.warning("unparseable dispatch packet: %s", raw.hex())
            return
        session["region"] = pkt.region
        self._last_region = pkt.region      # dispatch and MQTT arrive on separate connections
        answer = f"{self.server_ip}:{self.mqtt_port}"
        writer.write(ql.build_dispatch(answer, magic=pkt.magic))
        await writer.drain()
        log.info("dispatch from %s (%s, crc %s) -> %s", pkt.payload.decode("ascii", "replace"),
                 pkt.region, "ok" if pkt.crc_ok else "BAD", answer)

    async def _http(self, first: bytes, reader, writer) -> None:
        """Plain HTTP on the TLS ports: JSON dispatch (older firmware) or anything else."""
        head, _, body = first.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n")[1:]:
            k, _, v = line.partition(b":")
            if k.strip().lower() == b"content-length":
                length = int(v.strip() or 0)
        while len(body) < min(length, 65536):
            more = await reader.read(65536)
            if not more:
                break
            body += more
        try:
            req = json.loads(body) if body else {}
        except ValueError:
            req = {}
        if isinstance(req, dict) and isinstance(req.get("data"), dict) and "product_sign" in req["data"]:
            d = req["data"]
            token = hashlib.sha256(os.urandom(16)).hexdigest()[:32]
            resp = {"res": {"code": 0, "msg": "ok"},
                    "data": {"ip": self.server_ip, "port": self.mqtt_port, "token": token,
                             "dev_id": d.get("sn") or d.get("mac", "").replace(":", ""),
                             "dev_key": "local_key_" + token[:8]}}
        else:
            resp = {"code": 0, "msg": "ok"}
        await _write_http_json(writer, resp)

    async def _on_mqtt(self, pkt: bytes, session: dict) -> None:
        kind = pkt[0] >> 4
        w = session["writer"]
        if kind == mqtt.CONNECT:
            session["client_id"] = mqtt.parse_connect_client_id(pkt)
            w.write(mqtt.connack())
        elif kind == mqtt.SUBSCRIBE:
            pid, topics = mqtt.parse_subscribe(pkt)
            if topics and not session["topic"]:
                session["topic"] = topics[0][0]
            w.write(mqtt.suback(pid, [q for _, q in topics]))
        elif kind == mqtt.PUBLISH:
            pub = mqtt.parse_publish(pkt)
            if pub.qos and pub.packet_id is not None:
                w.write(mqtt.puback(pub.packet_id))
            frame = ql.parse_frame(pub.payload)
            if frame:
                self.record("in", frame, topic=pub.topic)
                await self._on_frame(frame, session)
            else:
                self.record("in", raw=pub.payload, topic=pub.topic, note="not a QLCloud frame")
        elif kind == mqtt.PINGREQ:
            w.write(mqtt.pingresp())
        await w.drain()

    def _on_map(self, data: str) -> None:
        m = maps.parse_reply(data)
        if m is None:
            log.warning("could not parse GET_MAP reply (%d chars)", len(data))
            return
        self.map = m
        self._map_asked = 0.0
        self.status.map_updated = m.received
        self._changed()
        try:
            (self.data_dir / "map.ogm").write_bytes(base64.b64decode(data))
        except (OSError, ValueError) as e:
            log.warning("could not save map: %s", e)
        log.debug("map %dx%d @ %.2fm, extra %s", m.width, m.height, m.resolution, m.extra)

    def want_map(self, seconds: float = 30) -> None:
        """Keep the map fresh while someone is looking at it."""
        self._map_wanted_until = max(self._map_wanted_until, time.time() + seconds)

    async def keep_map_fresh(self, every: float = 5) -> None:
        """Ask for the map while it's on screen: one request at a time, at most every `every` s.

        The Max is slow to answer GET_MAP (seconds when docked, 1-2 min while cleaning, when it
        only builds a new map that often), so wait up to MAP_WAIT before asking again: re-asking
        sooner only queues requests on the robot and brings back stale maps."""
        while True:
            await asyncio.sleep(1)
            now = time.time()
            if not self.online or now > self._map_wanted_until:
                continue
            if "map_refresh" not in self.profiles.active.actions:
                continue
            got = self.map.received if self.map else 0
            waiting = self._map_asked and self._map_asked > got and now - self._map_asked < MAP_WAIT
            if waiting or now - max(got, self._map_asked) < every:
                continue
            self._map_asked = now
            try:
                await self.run_action("map_refresh")
            except (ConnectionError, KeyError):
                pass

    async def keep_status_fresh(self) -> None:
        """Status reports only arrive on a change; ask when ours is over a minute old."""
        while True:
            await asyncio.sleep(20)
            if not self.online:
                continue
            last = self.status.updated
            stale = not last or (datetime.now() - datetime.fromisoformat(last)).total_seconds() > 60
            if stale:
                try:
                    await self.run_action("status_refresh")
                except (ConnectionError, KeyError):
                    pass


async def _write_http_json(writer: asyncio.StreamWriter, obj: dict) -> None:
    body = json.dumps(obj).encode()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                 + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    await writer.drain()


async def handle_https(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Port 443: the robot asks for voice packages / OTA; say there's nothing new."""
    try:
        first = await asyncio.wait_for(reader.read(8192), timeout=15)
        path = first.split(b" ", 2)[1].decode("ascii", "replace") if first.count(b" ") >= 2 else ""
        log.log(logging.INFO if os.environ.get("CAPTURE") == "1" else logging.DEBUG,
                "https %s", first.split(b"\r\n\r\n", 1)[0].decode("ascii", "replace").replace("\r\n", " | "))
        resp = {"code": 0, "data": {"version": "1.0.0", "packages": []}} if "language_pack" in path \
            else {"code": 0, "msg": "ok"}
        await _write_http_json(writer, resp)
    except (asyncio.TimeoutError, ConnectionError, ssl.SSLError, OSError):
        pass
    finally:
        writer.close()


def tls_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    # The robot's TLS stack is old; this is the setting proven to work with it.
    ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    return ctx
