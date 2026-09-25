"""Keeps user-supplied links away from the server's own network (SSRF protection).

The bot fetches whatever URL a user sends. Without this, a link like http://127.0.0.1:8081/, http://10.0.0.5/admin
or http://169.254.169.254/ would make the server read its own services, the private network or cloud metadata, and
commands such as /pagedebug and /headers would hand the result back. Every user URL is resolved and refused when
any address it resolves to isn't a public internet address.

Checked: job submission (all downloads, watches, schedules, inline mode), direct yt-dlp probes, and every httpx
request including each redirect hop. Set ALLOW_PRIVATE_URLS=true to allow your own LAN (e.g. a NAS).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

BLOCKED_MESSAGE = "🚫 That link points to a private or local network address, which this bot doesn't fetch."


class BlockedAddress(httpx.RequestError):
    """A request to a non-public address. A RequestError (not a TransportError), so it is never retried."""


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])  # drop an IPv6 zone id
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def check_url(url: str, allow_private: bool = False) -> str | None:
    """None if the URL may be fetched, otherwise a message saying why not."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "That doesn't look like a valid link."
    if parts.scheme not in ("http", "https"):
        return None  # other schemes (ytsearch:, file uploads…) never reach the network through here
    host = parts.hostname
    if not host:
        return "That doesn't look like a valid link."
    if allow_private:
        return None
    try:
        addresses = await asyncio.wait_for(_resolve(host, port or (443 if parts.scheme == "https" else 80)), 10)
    except (OSError, UnicodeError, TimeoutError):
        return None  # unresolvable: the fetch fails on its own with a normal "can't connect" error
    if not addresses or not all(is_public_ip(a) for a in addresses):
        return BLOCKED_MESSAGE
    return None


def request_hook(allow_private: bool):
    """An httpx 'request' event hook: runs before every request, redirects included."""

    async def hook(request: httpx.Request) -> None:
        if message := await check_url(str(request.url), allow_private):
            raise BlockedAddress(message, request=request)

    return hook
