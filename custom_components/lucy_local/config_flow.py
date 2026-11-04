"""Set up from the UI: the lucy-local URL, and optionally a camera stream URL."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LucyClient, LucyError
from .const import CONF_STREAM_URL, CONF_URL, DOMAIN


def _clean_url(url: str) -> str | None:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "http://" + url
    u = urlparse(url)
    return url if u.scheme in ("http", "https") and u.hostname else None


class LucyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _clean_url(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                client = LucyClient(async_get_clientsession(self.hass), url)
                try:
                    status = await client.status()
                    profiles = await client.profiles()
                except LucyError:
                    errors["base"] = "cannot_connect"
                else:
                    serial = (status.get("device") or {}).get("serial") or urlparse(url).hostname
                    await self.async_set_unique_id(serial.lower())
                    self._abort_if_unique_id_configured(updates={CONF_URL: url})
                    title = profiles["profiles"].get(profiles["active"], {}).get("name", "Trifo robot")
                    return self.async_create_entry(title=title, data={CONF_URL: url})
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema(
            {vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str}))

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return LucyOptionsFlow()


class LucyOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            stream = user_input.get(CONF_STREAM_URL, "").strip()
            if stream and urlparse(stream).scheme not in ("rtsp", "rtsps", "http", "https"):
                errors[CONF_STREAM_URL] = "invalid_url"
            else:
                return self.async_create_entry(data={CONF_STREAM_URL: stream})
        current = self.config_entry.options.get(CONF_STREAM_URL, "")
        return self.async_show_form(step_id="init", errors=errors, data_schema=vol.Schema(
            {vol.Optional(CONF_STREAM_URL, description={"suggested_value": current}): str}))
