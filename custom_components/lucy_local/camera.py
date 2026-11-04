"""The robot's camera, served by MediaMTX next to lucy-local.

Opening the stream wakes the robot's camera (it takes 10-40 s to start; MediaMTX's
runOnDemand keeps it awake while anyone watches). Stills come only from a running stream:
fetching one would otherwise wake the robot every time a dashboard refreshes a thumbnail.
"""

from __future__ import annotations

from urllib.parse import urlparse

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LucyConfigEntry
from .const import CONF_STREAM_URL, RTSP_PORT, STREAM_PATH
from .coordinator import LucyCoordinator
from .entity import LucyEntity


async def async_setup_entry(hass: HomeAssistant, entry: LucyConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    if "camera_wake" in entry.runtime_data.actions:
        async_add_entities([LucyCamera(entry.runtime_data, entry.options.get(CONF_STREAM_URL) or "")])


class LucyCamera(LucyEntity, Camera):
    _attr_name = "Camera"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: LucyCoordinator, stream_url: str) -> None:
        LucyEntity.__init__(self, coordinator, "camera")
        Camera.__init__(self)
        host = urlparse(coordinator.client.url).hostname
        self._source = stream_url or f"rtsp://{host}:{RTSP_PORT}/{STREAM_PATH}"

    async def stream_source(self) -> str:
        return self._source

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        if self.stream is not None and self.stream.available:
            return await self.stream.async_get_image(width, height)
        return None
