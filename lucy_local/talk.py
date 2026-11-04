"""Talk through the robot's speaker.

The browser publishes its microphone to MediaMTX path "talk" over WebRTC (WHIP, proxied by
lucy-local so it works through the same origin). MediaMTX's runOnReady re-encodes it to AAC and
publishes RTMP at app-name/<TALK_ID> (docker/mediamtx/talk-relay.sh). START_TALK <TALK_ID> on rx
key 14 then makes the robot pull that stream from its stream host (which DNS points at us) and
play it. The robot only plays while its own live stream runs, so the camera is kept awake.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .robot import Robot

log = logging.getLogger("lucy.talk")

TALK_ID = "lltalk"     # must match talk-relay.sh
WAKE_EVERY = 2.5       # camera/live-stream keepalive
DEADMAN = 8.0          # stop if the browser hasn't said "still talking" for this long
RELAY_DELAY = 2.0      # give MediaMTX time to start publishing app-name/TALK_ID


class Talker:
    def __init__(self, robot: Robot):
        self.robot = robot
        self.active = False
        self.started: float | None = None
        self._last_ping = 0.0
        self._task: asyncio.Task | None = None

    def state(self) -> dict:
        return {"talking": self.active, "since": self.started}

    async def start(self) -> dict:
        self._last_ping = time.time()
        if self.active:
            return self.state()
        if not self.robot.online:
            raise ConnectionError("robot is not connected")
        self.active, self.started = True, time.time()
        self._task = asyncio.create_task(self._run())
        return self.state()

    async def stop(self) -> dict:
        if not self.active:
            return self.state()
        self.active = False
        if self._task:
            self._task.cancel()
            self._task = None
        try:
            await self.robot.run_action("talk_stop")
        except (ConnectionError, KeyError):
            pass
        log.info("talk stopped after %.0fs", time.time() - (self.started or time.time()))
        self.started = None
        return self.state()

    async def _run(self) -> None:
        try:
            await self.robot.run_action("camera_wake")
            await asyncio.sleep(RELAY_DELAY)
            await self.robot.run_action("talk_start")
            log.info("talk started")
            while self.active:
                await asyncio.sleep(WAKE_EVERY)
                if time.time() - self._last_ping > DEADMAN:
                    log.info("talk: browser went quiet, stopping")
                    asyncio.create_task(self.stop())
                    return
                await self.robot.run_action("camera_wake")
        except asyncio.CancelledError:
            pass
        except (ConnectionError, KeyError) as e:
            log.warning("talk failed: %s", e)
            self.active = False
