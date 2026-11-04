"""Battery and status text."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucyConfigEntry
from .entity import LucyEntity


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    async_add_entities([LucyBattery(c, "battery"), LucyStatus(c, "status")])


class LucyBattery(LucyEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> int | None:
        return self.status.get("battery")


class LucyStatus(LucyEntity, SensorEntity):
    """The robot's own status as lucy-local names it (finer than the vacuum state)."""
    _attr_name = "Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        return self.status.get("name")

    @property
    def extra_state_attributes(self) -> dict:
        return {"code": self.status.get("code"), "firmware": self.status.get("firmware")}
