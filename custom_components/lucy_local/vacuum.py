"""The robot as a vacuum entity."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity, VacuumEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucyConfigEntry
from .api import LucyError
from .coordinator import LucyCoordinator
from .entity import LucyEntity

ACTIVITY = {"cleaning": VacuumActivity.CLEANING, "paused": VacuumActivity.PAUSED,
            "returning": VacuumActivity.RETURNING, "docked": VacuumActivity.DOCKED,
            "error": VacuumActivity.ERROR, "idle": VacuumActivity.IDLE}
COORD = vol.All(vol.Coerce(float), vol.Range(min=-30, max=30))


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([LucyVacuum(entry.runtime_data)])
    if "goto" in entry.runtime_data.actions:
        entity_platform.async_get_current_platform().async_register_entity_service(
            "go_to", {vol.Required("x"): COORD, vol.Required("y"): COORD}, "async_go_to")


class LucyVacuum(LucyEntity, StateVacuumEntity):
    _attr_name = None   # the device name

    def __init__(self, coordinator: LucyCoordinator) -> None:
        super().__init__(coordinator, "vacuum")
        a = coordinator.actions
        f = VacuumEntityFeature.STATE | VacuumEntityFeature.SEND_COMMAND
        for name, feat in (("start", VacuumEntityFeature.START), ("pause", VacuumEntityFeature.PAUSE),
                           ("stop", VacuumEntityFeature.STOP), ("dock", VacuumEntityFeature.RETURN_HOME),
                           ("locate", VacuumEntityFeature.LOCATE)):
            if name in a:
                f |= feat
        self._presets: dict[str, int] = dict(coordinator.settings.get("suction_presets") or {})
        if "set_suction" in a and self._presets:
            f |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = list(self._presets)
        self._attr_supported_features = f

    @property
    def available(self) -> bool:
        # stay available while offline so the state reads "error" rather than unavailable,
        # as with lucy-local's MQTT bridge
        return self.coordinator.last_update_success

    @property
    def activity(self) -> VacuumActivity:
        return ACTIVITY[self.coordinator.activity]

    @property
    def fan_speed(self) -> str | None:
        s = self.status.get("suction")
        return next((n for n, v in self._presets.items() if v == s), None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.status
        return {"status": s.get("name"), "status_code": s.get("code"), "suction": s.get("suction"),
                "volume": s.get("volume"), "robot_ip": s.get("ip"), "last_update": s.get("updated")}

    async def _run(self, name: str, value: Any = None) -> None:
        try:
            await self.coordinator.client.action(name, value)
        except LucyError as e:
            raise HomeAssistantError(f"{name} failed: {e}") from e

    async def async_start(self) -> None:
        await self._run("start")

    async def async_pause(self) -> None:
        await self._run("pause")

    async def async_stop(self, **kwargs: Any) -> None:
        await self._run("stop")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self._run("dock")

    async def async_locate(self, **kwargs: Any) -> None:
        await self._run("locate")

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        if fan_speed not in self._presets:
            raise HomeAssistantError(f"unknown fan speed {fan_speed!r}")
        await self._run("set_suction", self._presets[fan_speed])

    async def async_go_to(self, x: float, y: float) -> None:
        try:
            await self.coordinator.client.goto(x, y)
        except LucyError as e:
            raise HomeAssistantError(f"go to failed: {e}") from e

    async def async_send_command(self, command: str, params: dict[str, Any] | list[Any] | None = None,
                                 **kwargs: Any) -> None:
        """Any action of the robot's lucy-local profile by name, e.g. command: set_language,
        params: {value: "1"}; or command: goto, params: {x: 1.0, y: 0.5}."""
        p = params if isinstance(params, dict) else {}
        if command == "goto":
            try:
                await self.async_go_to(float(p["x"]), float(p["y"]))
            except (KeyError, TypeError, ValueError) as e:
                raise HomeAssistantError("goto needs params {x: <metres>, y: <metres>}") from e
            return
        action = self.coordinator.actions.get(command)
        if action is None:
            raise HomeAssistantError(f"no action {command!r} in the robot's profile")
        if action.get("group") in ("drive", "manual"):
            raise HomeAssistantError("driving goes through the lucy-local drive page (dead-man stop)")
        await self._run(command, p.get("value"))
