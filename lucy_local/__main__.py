"""Entry point: `python -m lucy_local`. Configuration comes from environment variables."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn

from .api import create_app
from .drive import Driver
from .homeassistant import HomeAssistantBridge
from .profiles import ProfileStore
from .robot import Robot, handle_https, tls_context
from .scheduler import Scheduler
from .settings import SettingsStore
from .talk import Talker


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("lucy")

    server_ip = os.environ.get("SERVER_IP", "").strip()
    if not server_ip:
        sys.exit("SERVER_IP is required: the LAN IP the robot should connect to (this host).")
    data = Path(os.environ.get("DATA_DIR", "/data"))
    data.mkdir(parents=True, exist_ok=True)
    cert = Path(os.environ.get("TLS_CERT", data / "cert.pem"))
    key = Path(os.environ.get("TLS_KEY", data / "key.pem"))
    dispatch_port = env_int("DISPATCH_PORT", 7990)
    mqtt_port = env_int("MQTT_PORT", 17766)
    https_port = env_int("HTTPS_PORT", 443)
    web_port = env_int("WEB_PORT", 8080)

    profiles = ProfileStore(data)
    settings = SettingsStore(data)
    robot = Robot(server_ip, mqtt_port, data, profiles)
    driver = Driver(robot)
    scheduler = Scheduler(robot, data)
    ha = HomeAssistantBridge(robot, settings)

    tls = tls_context(cert, key)
    servers = [await asyncio.start_server(robot.handle_connection, "0.0.0.0", p, ssl=tls)
               for p in sorted({dispatch_port, mqtt_port})]
    if https_port:
        servers.append(await asyncio.start_server(handle_https, "0.0.0.0", https_port, ssl=tls))
    log.info("robot ports: dispatch %s, mqtt %s, https %s; telling the robot to use %s",
             dispatch_port, mqtt_port, https_port or "off", server_ip)

    talker = Talker(robot)
    app = create_app(robot, profiles, driver, scheduler, settings, ha, talker)
    web = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=web_port, log_level="warning",
                                        proxy_headers=True, forwarded_allow_ips="*"))
    ha.restart()
    tasks = [asyncio.create_task(t) for t in (robot.keep_status_fresh(), robot.keep_map_fresh(), driver.run(), scheduler.run())]
    log.info("web UI on :%s, profile %s", web_port, profiles.active_id)
    try:
        await web.serve()
    finally:
        for t in tasks:
            t.cancel()
        for s in servers:
            s.close()


if __name__ == "__main__":
    asyncio.run(main())
