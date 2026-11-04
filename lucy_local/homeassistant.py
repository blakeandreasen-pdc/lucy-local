"""Home Assistant over MQTT discovery: the robot appears as a vacuum entity plus a
battery sensor. Connects out to your existing broker (e.g. Mosquitto add-on).
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from .robot import Robot
from .settings import SettingsStore

log = logging.getLogger("lucy.ha")

HA_COMMANDS = {"start": "start", "pause": "pause", "stop": "stop",
               "return_to_base": "dock", "locate": "locate"}


def ha_state(name: str, online: bool) -> str:
    n = (name or "").lower()
    if not online:
        return "error"
    if "clean" in n or "scan" in n:
        return "cleaning"
    if "pause" in n:
        return "paused"
    if "return" in n:
        return "returning"
    if "dock" in n or "charg" in n:
        return "docked"
    if "error" in n:
        return "error"
    return "idle"


class HomeAssistantBridge:
    def __init__(self, robot: Robot, settings: SettingsStore):
        self.robot = robot
        self.settings = settings
        self.connected = False
        self.error: str | None = None
        self._task: asyncio.Task | None = None

    def restart(self) -> None:
        if self._task:
            self._task.cancel()
        self.connected, self.error = False, None
        if self.settings.value.homeassistant.enabled and self.settings.value.homeassistant.host:
            self._task = asyncio.create_task(self._run_forever())

    @property
    def node(self) -> str:
        return "lucy_local_" + (self.robot.device.serial or "robot").lower()

    def _fan_speeds(self) -> dict[str, int]:
        return self.settings.value.suction_presets.model_dump()

    def _fan_name(self, suction: int | None) -> str | None:
        if suction is None:
            return None
        speeds = self._fan_speeds()
        return min(speeds, key=lambda k: abs(speeds[k] - suction))

    async def _run_forever(self) -> None:
        cfg = self.settings.value.homeassistant
        while True:
            try:
                await self._session(cfg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected, self.error = False, str(e)
                log.warning("Home Assistant MQTT: %s (retrying in 15 s)", e)
                await asyncio.sleep(15)

    async def _session(self, cfg) -> None:
        base = f"lucy_local/{self.node}"
        avail = f"{base}/availability"
        will = aiomqtt.Will(avail, "offline", retain=True)
        async with aiomqtt.Client(cfg.host, cfg.port, username=cfg.username or None,
                                  password=cfg.password or None, will=will,
                                  identifier=self.node) as client:
            self.connected, self.error = True, None
            log.info("connected to Home Assistant MQTT at %s:%s", cfg.host, cfg.port)
            await self._discovery(client, cfg, base, avail)
            await client.subscribe(f"{base}/command")
            await client.subscribe(f"{base}/fan_speed/set")
            await client.subscribe(f"{base}/send_command")
            await client.publish(avail, "online", retain=True)
            listener = self.robot.subscribe()
            pump = asyncio.create_task(self._publish_states(client, base, listener))
            try:
                await self._publish_state(client, base, self.robot.status.as_dict())
                async for msg in client.messages:
                    await self._on_message(str(msg.topic), msg.payload.decode(errors="replace"), base)
            finally:
                pump.cancel()
                self.robot.unsubscribe(listener)

    async def _discovery(self, client, cfg, base: str, avail: str) -> None:
        d = self.robot.device
        device = {"identifiers": [self.node], "name": cfg.name, "manufacturer": "Trifo",
                  "model": self.robot.profiles.active.name, "sw_version": self.robot.status.firmware or ""}
        vacuum = {
            "name": None, "unique_id": f"{self.node}_vacuum", "object_id": self.node,
            "device": device, "availability_topic": avail,
            "state_topic": f"{base}/state", "command_topic": f"{base}/command",
            "set_fan_speed_topic": f"{base}/fan_speed/set", "send_command_topic": f"{base}/send_command",
            "fan_speed_list": list(self._fan_speeds()),
            "supported_features": ["start", "pause", "stop", "return_home", "locate",
                                   "fan_speed", "send_command", "status"],
        }
        battery = {
            "name": "Battery", "unique_id": f"{self.node}_battery", "device": device,
            "availability_topic": avail, "state_topic": f"{base}/attributes",
            "value_template": "{{ value_json.battery }}", "unit_of_measurement": "%",
            "device_class": "battery", "state_class": "measurement",
        }
        p = cfg.discovery_prefix
        await client.publish(f"{p}/vacuum/{self.node}/config", json.dumps(vacuum), retain=True)
        await client.publish(f"{p}/sensor/{self.node}_battery/config", json.dumps(battery), retain=True)
        log.debug("published discovery for %s (%s)", self.node, d.serial)

    async def _publish_states(self, client, base: str, q: asyncio.Queue) -> None:
        while True:
            await self._publish_state(client, base, await q.get())

    async def _publish_state(self, client, base: str, s: dict) -> None:
        state = {"state": ha_state(s.get("name"), s.get("online")), "fan_speed": self._fan_name(s.get("suction"))}
        await client.publish(f"{base}/state", json.dumps(state), retain=True)
        await client.publish(f"{base}/attributes", json.dumps(s), retain=True)

    async def _on_message(self, topic: str, payload: str, base: str) -> None:
        try:
            if topic == f"{base}/command" and payload in HA_COMMANDS:
                await self.robot.run_action(HA_COMMANDS[payload])
            elif topic == f"{base}/fan_speed/set" and payload in self._fan_speeds():
                await self.robot.run_action("set_suction", self._fan_speeds()[payload])
            elif topic == f"{base}/send_command":
                # {"command": "<profile action>", "params": {"value": N}} or a bare action name
                try:
                    body = json.loads(payload)
                except ValueError:
                    body = {"command": payload}
                name = body.get("command", "")
                value = (body.get("params") or {}).get("value")
                action = self.robot.profiles.active.actions.get(name)
                # driving goes through the drive page's dead-man stop, never a one-shot command
                if action and action.group not in ("drive", "manual"):
                    await self.robot.run_action(name, value)
        except (ConnectionError, KeyError, ValueError) as e:
            log.warning("HA command %s on %s failed: %s", payload, topic, e)
