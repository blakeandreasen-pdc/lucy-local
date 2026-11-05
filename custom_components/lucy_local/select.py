"""Fixed-choice profile actions (the voice language) as selects.

The robot only answers "OK", never which choice is active, so the select shows the last
choice made from Home Assistant (restored across restarts) rather than the robot's state.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LucyConfigEntry
from .api import LucyError
from .coordinator import LucyCoordinator
from .entity import LucyEntity


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    c = entry.runtime_data
    # manual-mode choices (the driving fan) belong to the drive page, not a standing setting
    async_add_entities(LucySelect(c, name, a) for name, a in c.actions.items()
                       if a.get("choices") and a.get("group") not in ("drive", "manual"))


class LucySelect(LucyEntity, SelectEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:translate"

    def __init__(self, coordinator: LucyCoordinator, action: str, a: dict) -> None:
        super().__init__(coordinator, action)
        self._action = action
        self._attr_name = a.get("label", action)
        self._by_label = {label: value for value, label in a["choices"].items()}
        self._attr_options = list(self._by_label)
        self._attr_current_option = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in self._by_label:
            self._attr_current_option = last.state

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.client.action(self._action, self._by_label[option])
        except LucyError as e:
            raise HomeAssistantError(f"{self._attr_name} failed: {e}") from e
        self._attr_current_option = option
        self.async_write_ha_state()
