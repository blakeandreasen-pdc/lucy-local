"""Base entity: one device per robot."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LucyCoordinator


class LucyEntity(CoordinatorEntity[LucyCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LucyCoordinator, key: str) -> None:
        super().__init__(coordinator)
        uid = coordinator.config_entry.unique_id
        self._attr_unique_id = f"{uid}_{key}"
        data = coordinator.data or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)}, manufacturer="Trifo", model=coordinator.profile_name,
            name=coordinator.config_entry.title, sw_version=data.get("firmware"),
            configuration_url=coordinator.client.url)

    @property
    def status(self) -> dict:
        return self.coordinator.data or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self.status.get("online"))
