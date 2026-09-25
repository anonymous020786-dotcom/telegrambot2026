"""Shared services attached to the Telegram Application (application.bot_data['svc'])."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from secrets import token_urlsafe
from typing import Any

from .config import Settings
from .db import Database
from .services.delivery import Delivery
from .services.downloader import Downloader, MediaInfo
from .services.images import ImageService
from .services.jobs import JobManager
from .services.linkserver import LinkServer


class TokenStore:
    """Maps short tokens to URLs (Telegram callback data is limited to 64 bytes)."""

    def __init__(self, capacity: int = 5000):
        self.capacity = capacity
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._by_url: dict[str, str] = {}

    def put(self, url: str, **extra: Any) -> str:
        if url in self._by_url and self._by_url[url] in self._data:
            token = self._by_url[url]
            self._data[token].update(extra)
            self._data.move_to_end(token)
            return token
        token = token_urlsafe(6)
        self._data[token] = {"url": url, "created": time.time(), **extra}
        self._by_url[url] = token
        while len(self._data) > self.capacity:
            _, value = self._data.popitem(last=False)
            self._by_url.pop(value["url"], None)
        return token

    def get(self, token: str) -> dict[str, Any] | None:
        return self._data.get(token)

    def url(self, token: str) -> str | None:
        item = self._data.get(token)
        return item["url"] if item else None


@dataclass
class Services:
    settings: Settings
    db: Database
    downloader: Downloader
    images: ImageService
    delivery: Delivery
    jobs: JobManager
    links: LinkServer | None = None
    tokens: TokenStore = field(default_factory=TokenStore)
    probe_cache: dict[str, tuple[float, MediaInfo]] = field(default_factory=dict)
    last_command: dict[int, float] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    async def probe(self, url: str, playlist: bool = False, ttl: float = 600) -> MediaInfo:
        key = f"{int(playlist)}|{url}"
        hit = self.probe_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        info = await self.downloader.probe(url, playlist=playlist)
        self.probe_cache[key] = (time.time(), info)
        if len(self.probe_cache) > 500:
            for k in sorted(self.probe_cache, key=lambda k: self.probe_cache[k][0])[:100]:
                self.probe_cache.pop(k, None)
        return info
