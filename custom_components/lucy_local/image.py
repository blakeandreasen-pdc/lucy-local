"""The robot's map as an image: lucy-local renders it (explored area, dock, robot)."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucyConfigEntry
from .api import LucyError
from .coordinator import LucyCoordinator
from .entity import LucyEntity


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    if "map_refresh" in entry.runtime_data.actions:
        async_add_entities([LucyMap(hass, entry.runtime_data)])


class LucyMap(LucyEntity, ImageEntity):
    _attr_name = "Map"
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: LucyCoordinator) -> None:
        LucyEntity.__init__(self, coordinator, "map")
        ImageEntity.__init__(self, hass)
        self._seen: float | None = None
        self._update_stamp()

    @property
    def available(self) -> bool:
        # the last map is still worth showing while the robot is offline
        return self.coordinator.last_update_success and self.status.get("map_updated") is not None

    def _update_stamp(self) -> None:
        t = self.status.get("map_updated")
        if t is not None and t != self._seen:
            self._seen = t
            self._attr_image_last_updated = datetime.fromtimestamp(t, timezone.utc)
            self._cached_image = None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_stamp()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        try:
            return await self.coordinator.client.map_png()
        except LucyError:
            return None
