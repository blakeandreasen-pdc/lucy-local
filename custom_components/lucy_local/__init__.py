"""Lucy Local: Home Assistant integration for a Trifo robot run by a lucy-local server.

lucy-local stays the robot's "cloud" (the robot only talks Trifo's protocol); this integration
talks to lucy-local's web API, so no MQTT broker is needed. lucy-local's own MQTT bridge still
works; use one or the other, or you get two vacuum devices.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LucyClient, LucyError
from .const import CONF_URL
from .coordinator import LucyCoordinator

PLATFORMS = [Platform.VACUUM, Platform.CAMERA, Platform.IMAGE, Platform.SENSOR,
             Platform.NUMBER, Platform.SELECT, Platform.SWITCH]

type LucyConfigEntry = ConfigEntry[LucyCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry) -> bool:
    client = LucyClient(async_get_clientsession(hass), entry.data[CONF_URL])
    coordinator = LucyCoordinator(hass, entry, client)
    try:
        await coordinator.async_load_static()
    except LucyError as e:
        raise ConfigEntryNotReady(f"lucy-local unreachable: {e}") from e
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_create_background_task(hass, coordinator.listen(), "lucy_local events")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: LucyConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LucyConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
