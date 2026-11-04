"""HTTP API and web pages. No authentication: keep it on the LAN or behind a proxy
that restricts access.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import maps
from .drive import Driver
from .homeassistant import HomeAssistantBridge
from .profiles import Command, Profile, ProfileStore
from .robot import Robot
from .scheduler import Entry, Scheduler
from .settings import Settings, SettingsStore
from .talk import Talker

# Endpoints that touch the robot, its event queues or start tasks must be `async def`:
# plain `def` endpoints run in a worker thread with no event loop.
WEB = Path(__file__).parent / "web"
# MediaMTX's WebRTC server as seen from this container (compose service name)
MEDIAMTX_WEBRTC = os.environ.get("MEDIAMTX_WEBRTC", "http://mediamtx:8889").rstrip("/")
WHIP_SESSION = re.compile(r"^[A-Za-z0-9-]{1,64}$")
GOTO_LIMIT = 30.0  # metres from the dock; the Max's maps are a few metres across


class ActionBody(BaseModel):
    value: int | str | None = None


class ActiveBody(BaseModel):
    id: str


class TalkBody(BaseModel):
    on: bool


class GotoBody(BaseModel):
    x: float = Field(ge=-GOTO_LIMIT, le=GOTO_LIMIT)
    y: float = Field(ge=-GOTO_LIMIT, le=GOTO_LIMIT)


def _mediamtx(method: str, path: str, body: bytes | None = None, ctype: str | None = None,
              timeout: float = 10):
    req = urllib.request.Request(MEDIAMTX_WEBRTC + path, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Location"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, None, e.read()


def create_app(robot: Robot, profiles: ProfileStore, driver: Driver, scheduler: Scheduler,
               settings: SettingsStore, ha: HomeAssistantBridge, talker: Talker) -> FastAPI:
    app = FastAPI(title="lucy-local", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

    def not_connected(e: Exception):
        return HTTPException(503, f"robot not connected: {e}")

    # ------------------------------------------------------------ status
    @app.get("/api/status")
    def status():
        return {**robot.status.as_dict(), "device": robot.device.__dict__, "drive": driver.state(),
                "profile": profiles.active_id, "next_schedule": scheduler.next_run(),
                "last_schedule": scheduler.last_run,
                "homeassistant": {"connected": ha.connected, "error": ha.error},
                "talk": talker.state()}

    @app.get("/api/events")
    async def events():
        q = robot.subscribe()

        async def stream():
            try:
                yield f"data: {json.dumps(robot.status.as_dict())}\n\n"
                while True:
                    try:
                        s = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(s)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                robot.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/responses")
    def responses(limit: int = 50):
        return list(robot.responses)[-limit:]

    # ------------------------------------------------------------ actions
    @app.get("/api/actions")
    def actions():
        return {k: v.model_dump() for k, v in profiles.active.actions.items()}

    @app.post("/api/action/{name}")
    async def action(name: str, body: ActionBody | None = None):
        a = profiles.active.actions.get(name)
        if a is None:
            raise HTTPException(404, f"no action {name!r} in profile {profiles.active_id}")
        if a.group == "drive":
            raise HTTPException(400, "drive actions go through /api/drive so the dead-man stop applies")
        value = body.value if body else None
        if a.choices is not None:
            if value is None or str(value) not in a.choices:
                raise HTTPException(422, f"value must be one of {', '.join(a.choices)}")
        elif a.takes_value:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{name} needs a numeric value")
            if (a.min is not None and value < a.min) or (a.max is not None and value > a.max):
                raise HTTPException(422, f"value must be {a.min}-{a.max}")
        try:
            if name in ("dock", "start") and driver.manual:
                await driver.exit_manual()
                await asyncio.sleep(1.5)
            sent = await robot.run_action(name, value)
        except ConnectionError as e:
            raise not_connected(e)
        return {"ok": True, "sent": sent}

    @app.post("/api/send")
    async def send_raw(cmd: Command):
        """Send one raw command, e.g. to try a mapping before saving it to a profile."""
        try:
            await robot.send_command(cmd)
        except ConnectionError as e:
            raise not_connected(e)
        await asyncio.sleep(0.8)
        return {"ok": True, "responses": list(robot.responses)[-5:]}

    @app.post("/api/camera/wake")
    async def camera_wake():
        try:
            await robot.run_action("camera_wake")
        except ConnectionError as e:
            raise not_connected(e)
        return {"ok": True}

    # ------------------------------------------------------------ map
    @app.get("/api/map")
    async def get_map(since: float = 0):
        """Latest map. Calling this keeps the map refreshing for 30 s."""
        supported = "map_refresh" in profiles.active.actions
        if supported:
            robot.want_map()
        m = robot.map
        if m is None or m.received <= since:
            return {"supported": supported, "changed": False, "received": m.received if m else None}
        return {"supported": supported, "changed": True, **m.as_dict()}

    @app.post("/api/goto")
    async def goto(body: GotoBody):
        """Send the robot to a point on its map (metres, map frame). The robot answers ~1 min later."""
        if "goto" not in profiles.active.actions:
            raise HTTPException(400, f"profile {profiles.active_id} has no goto action")
        try:
            if driver.manual:
                await driver.exit_manual()
                await asyncio.sleep(1.5)
            sent = await robot.run_action("goto", f"{body.x:.2f},{body.y:.2f}")
        except ConnectionError as e:
            raise not_connected(e)
        return {"ok": True, "sent": sent}

    @app.get("/api/map.png")
    async def get_map_png(refresh: bool = False, scale: int = 8):
        """The map as a PNG (for Home Assistant). refresh=true also keeps it refreshing for 30 s."""
        if refresh and "map_refresh" in profiles.active.actions:
            robot.want_map()
        m = robot.map
        if m is None:
            raise HTTPException(404, "no map yet")
        png = await asyncio.to_thread(maps.render_png, m, max(1, min(scale, 16)))
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "no-store", "X-Map-Received": f"{m.received:.3f}"})

    # ------------------------------------------------------------ talk
    @app.post("/api/talk")
    async def talk(body: TalkBody):
        """on=true starts (and, repeated every few seconds, keeps) talking; on=false stops."""
        if body.on and "talk_start" not in profiles.active.actions:
            raise HTTPException(400, "this robot profile has no talk command")
        try:
            return await (talker.start() if body.on else talker.stop())
        except ConnectionError as e:
            raise not_connected(e)

    @app.post("/api/talk/whip")
    async def talk_whip(request: Request):
        """WHIP offer for the browser's microphone, passed to MediaMTX path "talk"."""
        sdp = await request.body()
        if len(sdp) > 64_000:
            raise HTTPException(413, "offer too large")
        try:
            status, location, answer = await asyncio.to_thread(
                _mediamtx, "POST", "/talk/whip", sdp, "application/sdp")
        except OSError as e:
            raise HTTPException(502, f"media server unreachable: {e}")
        headers = {}
        if location and location.startswith("/talk/whip/"):
            # relative to this URL, so it also works under a path prefix (HA ingress)
            headers["Location"] = "whip/" + location.rsplit("/", 1)[1]
        return Response(answer, status_code=status, headers=headers,
                        media_type="application/sdp" if status < 300 else "text/plain")

    @app.delete("/api/talk/whip/{session}")
    async def talk_whip_end(session: str):
        if not WHIP_SESSION.match(session):
            raise HTTPException(400, "bad session id")
        try:
            status, _, _ = await asyncio.to_thread(_mediamtx, "DELETE", f"/talk/whip/{session}")
        except OSError:
            status = 502
        return Response(status_code=200 if status < 300 else status)

    # ------------------------------------------------------------ camera signalling
    @app.post("/api/camera/whep")
    async def camera_whep(request: Request):
        """WHEP offer for the camera, passed to MediaMTX path "lucy". Waits while MediaMTX wakes
        the robot's stream (runOnDemand, up to 40 s)."""
        sdp = await request.body()
        if len(sdp) > 64_000:
            raise HTTPException(413, "offer too large")
        try:
            status, location, answer = await asyncio.to_thread(
                _mediamtx, "POST", "/lucy/whep", sdp, "application/sdp", 50)
        except OSError as e:
            raise HTTPException(502, f"media server unreachable: {e}")
        headers = {}
        if location and location.startswith("/lucy/whep/"):
            headers["Location"] = "whep/" + location.rsplit("/", 1)[1]
        return Response(answer, status_code=status, headers=headers,
                        media_type="application/sdp" if status < 300 else "text/plain")

    @app.delete("/api/camera/whep/{session}")
    async def camera_whep_end(session: str):
        if not WHIP_SESSION.match(session):
            raise HTTPException(400, "bad session id")
        try:
            status, _, _ = await asyncio.to_thread(_mediamtx, "DELETE", f"/lucy/whep/{session}")
        except OSError:
            status = 502
        return Response(status_code=200 if status < 300 else status)

    # ------------------------------------------------------------ driving
    @app.websocket("/api/drive/ws")
    async def drive_ws(ws: WebSocket):
        """Send {"move": "<drive action>"|null} on every change and at least every 0.5 s."""
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_json()
                try:
                    state = await driver.move(msg.get("move") or None)
                    await ws.send_json({"ok": True, **state})
                except KeyError:
                    await ws.send_json({"ok": False, "error": "unknown move"})
                except ConnectionError as e:
                    await ws.send_json({"ok": False, "error": f"robot not connected: {e}"})
        except WebSocketDisconnect:
            pass
        finally:
            await driver.move(None)

    @app.post("/api/drive/manual")
    async def manual(body: ActionBody):
        try:
            if body.value in (1, "1", "on", True):
                await driver.enter_manual()
            else:
                await driver.exit_manual()
        except ConnectionError as e:
            raise not_connected(e)
        return driver.state()

    # ------------------------------------------------------------ profiles
    @app.get("/api/profiles")
    def list_profiles():
        return {"active": profiles.active_id,
                "profiles": {k: v.model_dump() for k, v in profiles.all().items()}}

    @app.put("/api/profiles/{pid}")
    async def save_profile(pid: str, p: Profile):
        if p.id != pid:
            raise HTTPException(400, "id mismatch")
        try:
            saved = profiles.put(p)
        except ValueError as e:
            raise HTTPException(400, str(e))
        robot.rename_status()
        return saved

    @app.delete("/api/profiles/{pid}")
    def delete_profile(pid: str):
        try:
            profiles.delete(pid)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.post("/api/profiles/active")
    async def set_active(body: ActiveBody):
        try:
            profiles.set_active(body.id)
        except KeyError:
            raise HTTPException(404, "no such profile")
        robot.rename_status()
        return {"active": profiles.active_id}

    # ------------------------------------------------------------ schedule
    @app.get("/api/schedule")
    def get_schedule():
        return {"entries": [e.model_dump() for e in scheduler.entries], "next": scheduler.next_run(),
                "last": scheduler.last_run}

    @app.post("/api/schedule")
    def put_schedule(e: Entry):
        if e.action not in profiles.active.actions or profiles.active.actions[e.action].group in ("drive", "manual"):
            raise HTTPException(422, "pick a basic or settings action")
        return scheduler.upsert(e)

    @app.delete("/api/schedule/{entry_id}")
    def del_schedule(entry_id: str):
        scheduler.delete(entry_id)
        return {"ok": True}

    # ------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings():
        return settings.public()

    @app.put("/api/settings")
    async def put_settings(s: Settings):
        settings.update(s)
        ha.restart()
        return settings.public()

    # ------------------------------------------------------------ pages
    @app.get("/health")
    def health():
        return {"ok": True, "robot_online": robot.online}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/drive", include_in_schema=False)
    def drive_page():
        return FileResponse(WEB / "drive.html")

    @app.get("/settings", include_in_schema=False)
    def settings_page():
        return FileResponse(WEB / "settings.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
