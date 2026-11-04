"""User settings kept in <data>/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field


class HomeAssistant(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = Field(1883, ge=1, le=65535)
    username: str = ""
    password: str = ""
    discovery_prefix: str = "homeassistant"
    name: str = "Trifo"


class SuctionPresets(BaseModel):
    low: int = Field(40, ge=0, le=100)
    medium: int = Field(63, ge=0, le=100)
    high: int = Field(78, ge=0, le=100)
    max: int = Field(100, ge=0, le=100)


class Settings(BaseModel):
    homeassistant: HomeAssistant = HomeAssistant()
    suction_presets: SuctionPresets = SuctionPresets()


SECRET = "********"


class SettingsStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "settings.json"
        try:
            self.value = Settings.model_validate_json(self.path.read_text())
        except (OSError, ValueError):
            self.value = Settings()
        # an env var wins over the file, so the password can stay out of the data volume
        env_pw = os.environ.get("HA_MQTT_PASSWORD")
        if env_pw:
            self.value.homeassistant.password = env_pw

    def public(self) -> dict:
        d = self.value.model_dump()
        if d["homeassistant"]["password"]:
            d["homeassistant"]["password"] = SECRET
        return d

    def update(self, new: Settings) -> Settings:
        if new.homeassistant.password == SECRET:
            new.homeassistant.password = self.value.homeassistant.password
        self.value = new
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(new.model_dump(), indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        return new
