"""Thin async client for the lucy-local web API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp


class LucyError(Exception):
    """lucy-local is unreachable or refused the request."""


class LucyClient:
    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self.url = url.rstrip("/")

    async def _request(self, method: str, path: str, body: Any = None, timeout: float = 15) -> Any:
        try:
            async with self._session.request(method, self.url + path, json=body,
                                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    detail = data.get("detail") if isinstance(data, dict) else None
                    raise LucyError(detail if isinstance(detail, str) else f"HTTP {r.status}")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise LucyError(str(e) or type(e).__name__) from e

    async def status(self) -> dict:
        return await self._request("GET", "/api/status")

    async def actions(self) -> dict:
        return await self._request("GET", "/api/actions")

    async def settings(self) -> dict:
        return await self._request("GET", "/api/settings")

    async def profiles(self) -> dict:
        return await self._request("GET", "/api/profiles")

    async def action(self, name: str, value: Any = None) -> dict:
        return await self._request("POST", f"/api/action/{name}", {} if value is None else {"value": value})

    async def goto(self, x: float, y: float) -> dict:
        return await self._request("POST", "/api/goto", {"x": x, "y": y})

    async def keep_map_fresh(self) -> None:
        """Ask lucy-local to keep requesting the map for 30 s (cheap: returns no map data)."""
        await self._request("GET", "/api/map?since=1e12")

    async def map_png(self) -> bytes | None:
        try:
            async with self._session.get(self.url + "/api/map.png",
                                         timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 404:
                    return None
                r.raise_for_status()
                return await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise LucyError(str(e) or type(e).__name__) from e

    async def events(self) -> AsyncIterator[dict]:
        """Status changes as lucy-local pushes them (server-sent events). Ends when the
        connection drops; the caller reconnects."""
        async with self._session.get(self.url + "/api/events",
                                     timeout=aiohttp.ClientTimeout(total=None, sock_read=45)) as r:
            r.raise_for_status()
            async for raw in r.content:
                line = raw.decode(errors="replace").strip()
                if line.startswith("data:"):
                    try:
                        yield json.loads(line[5:])
                    except ValueError:
                        continue
