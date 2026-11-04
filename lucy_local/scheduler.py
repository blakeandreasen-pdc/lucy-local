"""Timed actions (e.g. clean at 09:00 on weekdays), kept in <data>/schedule.json."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from .robot import Robot

log = logging.getLogger("lucy.schedule")


class Entry(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] = Field(default=[0, 1, 2, 3, 4, 5, 6], description="0 = Monday")
    action: str
    value: int | None = None
    suction: int | None = Field(None, ge=0, le=100, description="set before a clean starts")
    enabled: bool = True


class Scheduler:
    def __init__(self, robot: Robot, data_dir: Path):
        self.robot = robot
        self.path = data_dir / "schedule.json"
        self.entries: list[Entry] = []
        self.last_run: dict | None = None
        try:
            self.entries = [Entry.model_validate(e) for e in json.loads(self.path.read_text())]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.model_dump() for e in self.entries], indent=2))
        tmp.replace(self.path)

    def upsert(self, entry: Entry) -> Entry:
        self.entries = [e for e in self.entries if e.id != entry.id] + [entry]
        self.entries.sort(key=lambda e: e.time)
        self.save()
        return entry

    def delete(self, entry_id: str) -> None:
        self.entries = [e for e in self.entries if e.id != entry_id]
        self.save()

    def next_run(self, now: datetime | None = None) -> dict | None:
        now = now or datetime.now()
        best = None
        for e in self.entries:
            if not e.enabled or not e.days:
                continue
            h, m = map(int, e.time.split(":"))
            for add in range(8):
                day = now + timedelta(days=add)
                at = day.replace(hour=h, minute=m, second=0, microsecond=0)
                if at > now and at.weekday() in e.days:
                    if best is None or at < best[0]:
                        best = (at, e)
                    break
        return {"at": best[0].isoformat(timespec="minutes"), "entry": best[1].model_dump()} if best else None

    async def run(self) -> None:
        fired: set[str] = set()
        while True:
            now = datetime.now()
            stamp = now.strftime("%Y-%m-%d %H:%M")
            for e in self.entries:
                key = f"{e.id}@{stamp}"
                if (e.enabled and e.time == now.strftime("%H:%M") and now.weekday() in e.days
                        and key not in fired):
                    fired.add(key)
                    asyncio.create_task(self._fire(e))
            fired = {k for k in fired if k.endswith(stamp)}
            await asyncio.sleep(20)

    async def _fire(self, e: Entry) -> None:
        try:
            if e.suction is not None and e.action == "start":
                await self.robot.run_action("set_suction", e.suction)
                await asyncio.sleep(2)
            await self.robot.run_action(e.action, e.value)
            ok = True
        except Exception as ex:
            log.warning("scheduled %s failed: %s", e.action, ex)
            ok = False
        self.last_run = {"at": datetime.now().isoformat(timespec="seconds"), "action": e.action, "ok": ok}
        log.info("scheduled %s at %s: %s", e.action, e.time, "ok" if ok else "failed")
