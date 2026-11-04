"""Voice prompts on/off, for profiles with separate mute and unmute (the Max's VOICE 0/1)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucyConfigEntry
from .api import LucyError
from .entity import LucyEntity


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    if "mute" in c.actions and "unmute" in c.actions:
        async_add_entities([LucyVoice(c, "voice")])


class LucyVoice(LucyEntity, SwitchEntity):
    _attr_name = "Voice prompts"
    _attr_icon = "mdi:account-voice"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        m = self.status.get("muted")
        return None if m is None else not m

    async def _run(self, name: str) -> None:
        try:
            await self.coordinator.client.action(name)
        except LucyError as e:
            raise HomeAssistantError(f"{name} failed: {e}") from e

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._run("unmute")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._run("mute")
