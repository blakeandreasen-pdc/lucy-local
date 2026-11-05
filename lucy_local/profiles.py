"""Control profiles: which wire command each action uses on a given robot model.

Two kinds of command reach the robot:

* "dp": a smart-speaker data point (opcode 0x0504), e.g. {"dp": 2, "value": "start"}
* "rx": an app command (opcode 0x0402), e.g. {"cmd": "MANCTRL", "data": "ANGLE:4", "key": "2"}

"{value}" in a value/data string is replaced with the caller's value, e.g. the
suction level. Built-in profiles ship read-only; edits are saved as a custom
profile in the data directory, so updates never overwrite them.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Command(BaseModel):
    type: Literal["dp", "rx"]
    dp: int = 2
    value: str = ""
    cmd: str = ""
    data: str = ""
    key: str = "2"

    def render(self, value: str | int | None) -> "Command":
        v = "" if value is None else str(value)
        return self.model_copy(update={"value": self.value.replace("{value}", v),
                                       "data": self.data.replace("{value}", v)})


class Action(BaseModel):
    label: str
    # "extra": actions added in the profile editor; the dashboard shows them as extra controls
    group: Literal["basic", "settings", "drive", "manual", "camera", "status", "extra"]
    commands: list[Command]
    takes_value: bool = False
    min: int | None = None
    max: int | None = None
    # a fixed list instead of a number: value sent -> label shown (rendered as a dropdown)
    choices: dict[str, str] | None = None


class Profile(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    name: str
    builtin: bool = False
    status_parser: Literal["all_info_csv", "all_info_json"] = "all_info_csv"
    status_names: dict[str, str] = {}
    actions: dict[str, Action]


def dp(value: str) -> dict:
    return {"type": "dp", "dp": 2, "value": value}


def rx(cmd: str, data: str = "", key: str = "2") -> dict:
    return {"type": "rx", "cmd": cmd, "data": data, "key": key}


def _drive(angle: int, label: str) -> dict:
    return {"label": label, "group": "drive", "commands": [rx("MANCTRL", f"ANGLE:{angle}")]}


_COMMON_ACTIONS = {
    "start": {"label": "Clean", "group": "basic", "commands": [dp("start")]},
    "pause": {"label": "Pause", "group": "basic", "commands": [dp("stop")]},
    "stop": {"label": "Stop", "group": "basic", "commands": [dp("stop")]},
    "dock": {"label": "Dock", "group": "basic", "commands": [dp("dock")]},
    "locate": {"label": "Find", "group": "basic", "commands": [dp("locate")]},
    "mute": {"label": "Mute / unmute", "group": "settings", "commands": [dp("mute")]},
    "set_volume": {"label": "Volume", "group": "settings", "commands": [dp("setVolume:{value}")],
                   "takes_value": True, "min": 0, "max": 100},
    "set_suction": {"label": "Suction", "group": "settings", "commands": [dp("blowerMode:{value}")],
                    "takes_value": True, "min": 0, "max": 100},
    "manual_enter": {"label": "Enter manual mode", "group": "manual", "commands": [rx("MANCTRL", "S")]},
    "manual_keepalive": {"label": "Keep manual mode", "group": "manual", "commands": [rx("MANCTRL", "K")]},
    "manual_exit": {"label": "Leave manual mode", "group": "manual", "commands": [rx("MANCTRL", "E")]},
    # The app's manual screen (ManCtrlActivity) sends MANBLOWER on key 2 as a fan percentage,
    # 0 (off) to 100 in steps of 5, from a slider. CommandKey's -1/0/1/2 constants are unused
    # leftovers: tested on the Max 2026-11, "-1" ran the fan at full and "1"/"2" barely at all.
    "manual_fan": {"label": "Vacuum while driving", "group": "manual",
                   "commands": [rx("MANBLOWER", "{value}")],
                   "choices": {"0": "Off", "30": "Low", "60": "Medium", "100": "High"}},
    "forward": _drive(4, "Forward"),
    "gentle_left": _drive(5, "Gentle left"),
    "gentle_right": _drive(3, "Gentle right"),
    "turn_left": _drive(6, "Turn left"),
    "turn_right": _drive(2, "Turn right"),
    "spin_left": _drive(7, "Spin left"),
    "spin_right": _drive(1, "Spin right"),
    "reverse": _drive(8, "Reverse"),
    "halt": _drive(0, "Stop moving"),
    "camera_wake": {"label": "Wake camera stream", "group": "camera",
                    "commands": [rx("RTMP_THROB", "", "10"), rx("STREAM_CONTROL", "1", "10")]},
    "status_refresh": {"label": "Ask for status", "group": "status", "commands": [rx("ALL_INFO", "", "11")]},
}

# Talk: the robot pulls app-name/lltalk from its stream host and plays it (see talk.py).
# Works on the Lucy (upstream). The Max pulls the stream and answers "0,lltalk" but plays
# nothing (tested 2026-11: AAC 8/16/44.1 kHz, MP3, manual mode, both mute states).
_TALK_ACTIONS = {
    "talk_start": {"label": "Start talking", "group": "camera", "commands": [rx("START_TALK", "lltalk", "14")]},
    "talk_stop": {"label": "Stop talking", "group": "camera", "commands": [rx("STOP_TALK", "lltalk", "14")]},
}

# Voice language: LANGUAGE <n> on rx key 2 (key 3 reads it back). n is the index into the
# Trifo app's per-model list (res/values/arrays.xml): voice_language_lists_max for the Max,
# voice_language_lists for the others. The Max ships only English; any n replies OK.
def _language(choices: dict[str, str]) -> dict:
    return {"label": "Voice language", "group": "settings", "commands": [rx("LANGUAGE", "{value}", "2")],
            "choices": choices}


_COMMON_STATUS = {"0": "idle", "1": "cleaning", "2": "cleaning", "3": "paused", "4": "error",
                  "5": "returning", "6": "charging", "12": "docked", "20": "scanning"}


def _profile(pid: str, name: str, overrides: dict, status_extra: dict, parser: str) -> Profile:
    actions = copy.deepcopy(_COMMON_ACTIONS)
    actions.update(overrides)
    return Profile(id=pid, name=name, builtin=True, status_parser=parser,
                   status_names={**_COMMON_STATUS, **status_extra}, actions=actions)


BUILTINS = {
    # Trifo Max as tested 2026-11: reverse is ANGLE:10. Asked for ALL_INFO it answers twice,
    # with the semicolon ALL_INFO and a full ALL_INFO_JSON; asked for ALL_INFO_JSON directly
    # it answers with empty data. State codes differ from the Lucy's; see the names below.
    # Suction: the smart-speaker blowerMode:N is ignored by the Max; the app's BLOWER (rx key 2)
    # works but the robot applies (or at least reports) it 1-3 min later.
    "max": _profile("max", "Trifo Max", {
        "reverse": _drive(10, "Reverse"),
        "set_language": _language({"0": "Chinese (Simplified)", "1": "English (US)"}),
        "map_refresh": {"label": "Ask for map", "group": "status", "commands": [rx("GET_MAP", "", "8")]},
        # Drive to a point in the map frame, "x,y" metres (dock at 0,0, x out of the dock).
        # Tested 2026-11: "0.5,0.0" drove 0.5 m straight off the dock; "OK" came back ~70 s later.
        "goto": {"label": "Go to point", "group": "manual", "commands": [rx("A2B_NO_CLEAN", "{value}", "12")]},
        # The app's voice switch (rx VOICE 0/1 on key 2; key 3 reads it back). The smart-speaker
        # "mute" toggle changes nothing visible on the Max, and it reports no state change.
        "mute": {"label": "Voice off", "group": "settings", "commands": [rx("VOICE", "0", "2")]},
        "unmute": {"label": "Voice on", "group": "settings", "commands": [rx("VOICE", "1", "2")]},
        "status_refresh": {"label": "Ask for status", "group": "status",
                           "commands": [rx("ALL_INFO", "", "11"), rx("VOICE", "", "3")]},
        "set_suction": {"label": "Suction", "group": "settings", "commands": [rx("BLOWER", "{value}")],
                        "takes_value": True, "min": 0, "max": 100}},
                    # seen on the Max: 1 cleaning and 5 returning (as on the Lucy), 12/14 on the dock
                    # at any charge level, 10 docked after a run, 20 docked with only the camera on
                    {"10": "docked (10)", "14": "docked (14)", "20": "camera on (20)"},
                    "all_info_csv"),
    # Trifo Lucy per the upstream research notes (ANGLE:8 back, ALL_INFO_JSON status).
    "lucy": _profile("lucy", "Trifo Lucy", {"status_refresh": {
        "label": "Ask for status", "group": "status", "commands": [rx("ALL_INFO_JSON", "", "11")]},
        # untested on a real Lucy: the list is what the app offers it
        "set_language": _language({"0": "Chinese (Simplified)", "1": "English (US)", "2": "English (UK)",
                                   "3": "Spanish", "4": "German", "5": "French"}),
        **_TALK_ACTIONS},
        {}, "all_info_json"),
}

DRIVE_ACTIONS = ("forward", "gentle_left", "gentle_right", "turn_left", "turn_right",
                 "spin_left", "spin_right", "reverse")


class ProfileStore:
    """Built-ins plus user profiles in <data>/profiles.json, and the active choice."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "profiles.json"
        self.custom: dict[str, Profile] = {}
        self.active_id = "max"
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self.active_id = raw.get("active", "max")
            for p in raw.get("custom", []):
                prof = Profile.model_validate({**p, "builtin": False})
                self.custom[prof.id] = prof
        if self.active_id not in self.all():
            self.active_id = "max"

    def all(self) -> dict[str, Profile]:
        return {**BUILTINS, **self.custom}

    @property
    def active(self) -> Profile:
        return self.all()[self.active_id]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"active": self.active_id,
                                   "custom": [p.model_dump() for p in self.custom.values()]}, indent=2))
        tmp.replace(self.path)

    def set_active(self, pid: str) -> None:
        if pid not in self.all():
            raise KeyError(pid)
        self.active_id = pid
        self.save()

    def put(self, profile: Profile) -> Profile:
        if profile.id in BUILTINS:
            raise ValueError("built-in profiles can't be changed; save a copy under a new id")
        profile = profile.model_copy(update={"builtin": False})
        self.custom[profile.id] = profile
        self.save()
        return profile

    def delete(self, pid: str) -> None:
        if pid in BUILTINS:
            raise ValueError("built-in profiles can't be deleted")
        self.custom.pop(pid)
        if self.active_id == pid:
            self.active_id = "max"
        self.save()


def safe_id(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", text.lower()).strip("-")[:32] or "custom"
