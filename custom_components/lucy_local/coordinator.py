"""Keeps the robot's status: a slow poll plus lucy-local's pushed status events."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LucyClient, LucyError
from .const import DOMAIN, MAP_NUDGE_SECONDS, POLL_SECONDS

log = logging.getLogger(__name__)


def activity_name(status: dict) -> str:
    """Same mapping as lucy-local's MQTT bridge (homeassistant.ha_state)."""
    n = (status.get("name") or "").lower()
    if not status.get("online"):
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


class LucyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: LucyClient) -> None:
        super().__init__(hass, log, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=POLL_SECONDS))
        self.client = client
        self.actions: dict[str, dict] = {}
        self.settings: dict[str, Any] = {}
        self.profile_name = "Trifo robot"
        self._last_nudge = 0.0

    async def async_load_static(self) -> None:
        """Things that only change when the active profile or settings change (then reload)."""
        self.actions = await self.client.actions()
        self.settings = await self.client.settings()
        profiles = await self.client.profiles()
        self.profile_name = profiles["profiles"].get(profiles["active"], {}).get("name", self.profile_name)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.status()
        except LucyError as e:
            raise UpdateFailed(f"lucy-local unreachable: {e}") from e

    @property
    def activity(self) -> str:
        return activity_name(self.data or {})

    async def listen(self) -> None:
        """Merge pushed status into the data; reconnect forever."""
        backoff = 2
        while True:
            try:
                async for s in self.client.events():
                    backoff = 2
                    self.async_set_updated_data({**(self.data or {}), **s})
                    await self._nudge_map()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any drop: wait and reconnect
                log.debug("lucy-local event stream dropped: %s", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _nudge_map(self) -> None:
        # the map only changes while the robot moves; keep lucy-local asking for it then
        if self.activity not in ("cleaning", "returning") or "map_refresh" not in self.actions:
            return
        if time.monotonic() - self._last_nudge < MAP_NUDGE_SECONDS:
            return
        self._last_nudge = time.monotonic()
        try:
            await self.client.keep_map_fresh()
        except LucyError as e:
            log.debug("map refresh request failed: %s", e)
