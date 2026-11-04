"""Suction and volume, 0-100."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucyConfigEntry
from .api import LucyError
from .coordinator import LucyCoordinator
from .entity import LucyEntity

NUMBERS = (("set_suction", "suction", "Suction", "mdi:fan"),
           ("set_volume", "volume", "Volume", "mdi:volume-high"))


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities(LucyNumber(c, *n) for n in NUMBERS if n[0] in c.actions)


class LucyNumber(LucyEntity, NumberEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LucyCoordinator, action: str, field: str, name: str, icon: str) -> None:
        super().__init__(coordinator, field)
        a = coordinator.actions[action]
        self._action, self._field = action, field
        self._attr_name, self._attr_icon = name, icon
        self._attr_native_min_value = a.get("min") if a.get("min") is not None else 0
        self._attr_native_max_value = a.get("max") if a.get("max") is not None else 100

    @property
    def native_value(self) -> int | None:
        return self.status.get(self._field)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.client.action(self._action, int(value))
        except LucyError as e:
            raise HomeAssistantError(f"{self._attr_name} failed: {e}") from e
