"""Tiny HTTP server that serves finished downloads through signed, expiring links.

Used for files larger than Telegram's upload limit when the link delivery mode is on.
Links look like  {LINK_BASE_URL}/f/<token>/<filename>  and stop working after LINK_TTL_HOURS.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

log = logging.getLogger(__name__)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(rel_path: str, secret: str, ttl_seconds: float, now: float | None = None) -> str:
    payload = _b64(json.dumps({"p": rel_path, "e": int((now or time.time()) + ttl_seconds)}).encode())
    mac = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:18])
    return f"{payload}.{mac}"


def verify(token: str, secret: str, now: float | None = None) -> str | None:
    """Relative path if the token is authentic and unexpired, else None."""
    try:
        payload, mac = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:18])
        if not hmac.compare_digest(mac, expected):
            return None
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("e", 0) < (now or time.time()):
        return None
    return data.get("p")


class LinkServer:
    def __init__(self, root: Path, secret: str, base_url: str, host: str, port: int, ttl_hours: float):
        self.root = root.resolve()
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.host = host
        self.port = port
        self.ttl = ttl_hours * 3600
        self.runner: web.AppRunner | None = None

    def link_for(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root).as_posix()
        token = sign(rel, self.secret, self.ttl)
        return f"{self.base_url}/f/{token}/{quote(path.name)}"

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/f/{token}/{name}", self.handle_file)
        app.router.add_get("/healthz", self.handle_health)
        return app

    async def handle_health(self, _request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def handle_file(self, request: web.Request) -> web.StreamResponse:
        rel = verify(request.match_info["token"], self.secret)
        if not rel:
            raise web.HTTPForbidden(text="This link is invalid or has expired.")
        path = (self.root / rel).resolve()
        if self.root not in path.parents or not path.is_file():
            raise web.HTTPNotFound(text="File no longer available.")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}",
            "Cache-Control": "private, max-age=0",
        }
        return web.FileResponse(path, headers=headers)

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()
        log.info("Link server listening on %s:%s (%s)", self.host, self.port, self.base_url)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
