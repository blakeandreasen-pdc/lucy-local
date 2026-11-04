"""Manual driving, owned by the server so every client behaves the same.

Without root the robot only takes MANCTRL commands:
  S enters manual mode, K keeps it (she drops out after ~14 s without one), E leaves,
  ANGLE:N moves one of 8 fixed ways, ANGLE:0 stops.

Clients call `move(name)` whenever the wanted direction changes and repeat it at
least every DEADMAN_S while driving. If they go quiet (tab closed, Wi-Fi dropped)
the server stops her itself.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .profiles import DRIVE_ACTIONS
from .robot import Robot

log = logging.getLogger("lucy.drive")

REPEAT_S = 0.2          # resend the held direction this often
DEADMAN_S = 0.7         # stop if the client hasn't confirmed the move for this long
KEEPALIVE_S = 3.0
SETTLE_S = 1.5          # after S, before she takes ANGLE commands
IDLE_EXIT_S = 120.0     # leave manual mode after this long without driving


class Driver:
    def __init__(self, robot: Robot):
        self.robot = robot
        self.manual = False
        self.move_name: str | None = None
        self._last_seen = 0.0
        self._last_drive = 0.0
        self._entering: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def state(self) -> dict:
        return {"manual": self.manual, "entering": bool(self._entering and not self._entering.done()),
                "move": self.move_name}

    async def enter_manual(self) -> None:
        if self.manual:
            if self._entering and not self._entering.done():
                await asyncio.shield(self._entering)
            return
        self.manual = True
        self._entering = asyncio.create_task(self._enter())
        await asyncio.shield(self._entering)

    async def _enter(self) -> None:
        try:
            await self.robot.run_action("manual_enter")
            await asyncio.sleep(SETTLE_S)
        except Exception:
            self.manual = False
            raise

    async def exit_manual(self) -> None:
        await self.halt()
        if self.manual:
            self.manual = False
            try:
                await self.robot.run_action("manual_exit")
            except ConnectionError:
                pass

    async def move(self, name: str | None) -> dict:
        """name: one of DRIVE_ACTIONS, or None/'' to stop."""
        if name and name not in DRIVE_ACTIONS:
            raise KeyError(name)
        now = time.monotonic()
        self._last_seen = now
        if not name:
            if self.move_name:
                await self.halt()
            return self.state()
        self._last_drive = now
        if name != self.move_name:
            self.move_name = name
            await self.enter_manual()
            if self.move_name == name:          # still wanted after the wait
                await self.robot.run_action(name)
        return self.state()

    async def halt(self) -> None:
        was = self.move_name
        self.move_name = None
        if was and self.manual:
            for _ in range(2):                   # twice, in case one arrives late
                try:
                    await self.robot.run_action("halt")
                except ConnectionError:
                    return
                await asyncio.sleep(0.2)

    async def run(self) -> None:
        last_keep = 0.0
        while True:
            await asyncio.sleep(REPEAT_S)
            now = time.monotonic()
            entering = bool(self._entering and not self._entering.done())
            try:
                if entering:
                    # the client is blocked waiting for manual mode; don't count that as silence
                    self._last_seen = now
                elif self.move_name and now - self._last_seen > DEADMAN_S:
                    log.info("dead-man stop (no update for %.1fs)", now - self._last_seen)
                    await self.halt()
                elif self.move_name and self.manual:
                    await self.robot.run_action(self.move_name)
                if self.manual and now - last_keep >= KEEPALIVE_S:
                    last_keep = now
                    if now - max(self._last_drive, self._last_seen) > IDLE_EXIT_S and not self.move_name:
                        log.info("leaving manual mode after %ds idle", IDLE_EXIT_S)
                        await self.exit_manual()
                    else:
                        await self.robot.run_action("manual_keepalive")
            except ConnectionError:
                self.move_name = None
                self.manual = False
            except Exception:
                log.exception("drive loop")
