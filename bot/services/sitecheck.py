"""Live health check: can this server read media info from the major platforms right now?

Default targets are the sample URLs yt-dlp's own extractors ship with, so they track upstream changes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import yt_dlp

from .downloader import Downloader, friendly_error

# Platform label → yt-dlp extractor key whose first real test URL is used.
PLATFORMS: dict[str, str] = {
    "YouTube": "Youtube",
    "Instagram": "Instagram",
    "TikTok": "TikTok",
    "X / Twitter": "Twitter",
    "Facebook": "Facebook",
    "Reddit": "Reddit",
    "Vimeo": "Vimeo",
    "Dailymotion": "Dailymotion",
    "SoundCloud": "Soundcloud",
    "Twitch": "TwitchVod",
    "Pinterest": "Pinterest",
    "Bilibili": "BiliBili",
    "Tumblr": "Tumblr",
    "Bluesky": "Bluesky",
}


@dataclass
class CheckResult:
    name: str
    url: str
    ok: bool
    detail: str
    seconds: float


def sample_url(extractor_key: str) -> str | None:
    """First non-'only_matching' test URL of a yt-dlp extractor."""
    for ie in yt_dlp.extractor.gen_extractor_classes():
        if ie.ie_key() != extractor_key:
            continue
        for test in ie.get_testcases(include_onlymatching=False):
            if test.get("url", "").startswith("http"):
                return test["url"]
    return None


def default_targets() -> list[tuple[str, str]]:
    return [(name, url) for name, key in PLATFORMS.items() if (url := sample_url(key))]


async def check(
    downloader: Downloader, targets: list[tuple[str, str]], timeout: float = 60, concurrency: int = 4
) -> list[CheckResult]:
    sem = asyncio.Semaphore(concurrency)

    async def one(name: str, url: str) -> CheckResult:
        async with sem:
            t0 = time.monotonic()
            try:
                info = await asyncio.wait_for(downloader.probe(url), timeout)
                detail = info.title if not info.is_playlist else f"{info.title} ({len(info.entries)} items)"
                return CheckResult(name, url, True, detail, time.monotonic() - t0)
            except TimeoutError:
                return CheckResult(name, url, False, "timed out", time.monotonic() - t0)
            except Exception as exc:  # noqa: BLE001 - report every failure kind
                return CheckResult(name, url, False, friendly_error(exc), time.monotonic() - t0)

    return list(await asyncio.gather(*(one(n, u) for n, u in targets)))
