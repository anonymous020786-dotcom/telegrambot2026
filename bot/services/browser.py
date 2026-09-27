"""Headless-browser fallback for pages that build their player with JavaScript (SPAs).

Static HTML from React/Vue/Angular sites often contains no media URL at all: the player asks an API for the
stream after the page loads. This opens the page in headless Chromium, watches every network response, and
returns the media the page actually requested, best first: HLS/DASH/Smooth Streaming manifests, then whole
video files. Segment requests (.ts/.m4s chunks) are ignored because the manifest describes them all.

DRM-protected streams stay unplayable: their manifests point at encrypted segments that yt-dlp refuses.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .netguard import check_url
from .pagevideo import VideoCandidate

log = logging.getLogger(__name__)
_unavailable = False  # set once Chromium fails to start, so later jobs skip straight past the fallback

MANIFEST_TYPES = {
    "application/vnd.apple.mpegurl": "hls",
    "application/x-mpegurl": "hls",
    "audio/mpegurl": "hls",
    "audio/x-mpegurl": "hls",
    "application/dash+xml": "dash",
    "application/vnd.ms-sstr+xml": "ism",
}
MANIFEST_EXT = re.compile(r"\.(m3u8|mpd)(?:$|[?#])|\.isml?/manifest", re.I)
FILE_EXT = re.compile(r"\.(mp4|m4v|webm|mov|mkv|ogv|flv|3gp)(?:$|[?#])", re.I)
SEGMENT = re.compile(r"\.(ts|m4s|m4f|aac|cmfv|cmfa)(?:$|[?#])|/(seg|segment|chunk|frag)[-_]?\d+|[?&]range=\d+-", re.I)
AD_HOSTS = re.compile(r"doubleclick|googlesyndication|googleads|imasdk|adservice|adsystem|advert|/ads?/", re.I)
# Media must be at least this big to count as a video file (skips tiny previews and hover clips).
MIN_FILE_BYTES = 300_000
VIDEO_SOURCES_JS = """els => els.map(e => {
    const r = e.getBoundingClientRect();
    return [e.currentSrc || e.src || (e.querySelector('source') || {}).src || '', Math.round(r.width * r.height)];
})"""
PLAY_SELECTORS = (
    "video",
    "button[aria-label*='play' i]",
    "[class*='play' i][role='button']",
    ".vjs-big-play-button",
    ".jw-icon-display",
    ".plyr__control--overlaid",
)


@dataclass
class SniffResult:
    candidates: list[VideoCandidate] = field(default_factory=list)
    final_url: str = ""
    title: str = ""
    html: str = ""


def classify(url: str, content_type: str, size: int | None) -> str | None:
    """'hls' | 'dash' | 'ism' | 'file' for media worth downloading, None for everything else."""
    if url.startswith(("blob:", "data:")) or AD_HOSTS.search(url):
        return None
    ctype = content_type.split(";")[0].strip().lower()
    if ctype in MANIFEST_TYPES:
        return MANIFEST_TYPES[ctype]
    if m := MANIFEST_EXT.search(url):
        ext = (m.group(1) or "ism").lower()
        return {"m3u8": "hls", "mpd": "dash"}.get(ext, "ism")
    if SEGMENT.search(urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")):
        return None
    if ctype.startswith("video/") or FILE_EXT.search(url):
        if size is not None and size < MIN_FILE_BYTES:
            return None
        return "file"
    return None


def rank(found: dict[str, str], playing: list[str] | None = None) -> list[VideoCandidate]:
    """Manifests first (they carry every quality), then files: what the page's <video> plays, then request order.

    Request order matters on feed-style pages, which also preload the next videos: the main video loads first.
    """
    playing = playing or []
    kind_rank = {"hls": 0, "dash": 1, "ism": 2, "file": 3}
    position = {url: i for i, url in enumerate(found)}

    def key(url: str) -> tuple[int, int, int]:
        on_page = playing.index(url) if url in playing else len(playing)
        return (kind_rank[found[url]], on_page, position[url])

    kind_map = {"hls": "hls", "dash": "dash", "ism": "dash", "file": "file"}
    return [VideoCandidate(url, "browser", kind_map[found[url]]) for url in sorted(found, key=key)]


async def sniff(
    url: str,
    user_agent: str,
    timeout: float = 45.0,
    allow_private: bool = False,
    proxy: str | None = None,
) -> SniffResult:
    """Open `url` in headless Chromium and return the media it loads. Empty result if Chromium is unavailable."""
    global _unavailable
    if _unavailable:
        return SniffResult()
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.info("browser fallback off: playwright is not installed")
        _unavailable = True
        return SniffResult()

    found: dict[str, str] = {}  # url -> kind, in request order
    playing: list[str] = []
    result = SniffResult(final_url=url)

    async def guard(route) -> None:
        request = route.request
        if request.resource_type in ("image", "font"):
            await route.abort()
            return
        # Keep the browser off the server's own network, like every other fetch (SSRF protection).
        if request.is_navigation_request() and await check_url(request.url, allow_private):
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    def on_response(response) -> None:
        try:
            headers = response.headers
            size = int(headers["content-length"]) if headers.get("content-length", "").isdigit() else None
            kind = classify(response.url, headers.get("content-type", ""), size)
        except Exception:  # noqa: BLE001 - a response we can't read is simply not media
            return
        if kind and response.ok:
            found.setdefault(response.url, kind)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"],
                proxy={"server": proxy} if proxy else None,
            )
        except Exception as exc:  # noqa: BLE001 - Chromium missing or unable to start
            log.warning("browser fallback off: Chromium can't start (%s)", str(exc).splitlines()[0])
            _unavailable = True
            return result
        try:
            context = await browser.new_context(user_agent=user_agent, viewport={"width": 1280, "height": 720})
            page = await context.new_page()
            await page.route("**/*", guard)
            page.on("response", on_response)
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # Give the player time to request its stream. Many players wait for a click, and some aren't ready
            # for one straight away, so press play after 3 s and again after 10 s.
            loop = asyncio.get_running_loop()
            start = loop.time()
            clicks = [3.0, 10.0]
            while loop.time() - start < max(12.0, timeout / 2):
                await asyncio.sleep(1.0)
                if clicks and loop.time() - start >= clicks[0]:
                    clicks.pop(0)
                    for selector in PLAY_SELECTORS:
                        with contextlib.suppress(Exception):
                            await page.click(selector, timeout=1500)
                            break
                if any(kind != "file" for kind in found.values()):
                    await asyncio.sleep(2.0)  # let the player settle on its master playlist
                    break
            with contextlib.suppress(Exception):
                result.title = await page.title()
                result.final_url = page.url
                result.html = await page.content()
                # <video> elements with a plain (non-blob) URL, biggest on screen first: that is the main player,
                # while feed pages also carry small previews of the next videos.
                videos = await page.eval_on_selector_all("video", VIDEO_SOURCES_JS)
                for src, _area in sorted(videos, key=lambda v: -v[1]):
                    if src and (kind := classify(src, "", None)):
                        found.setdefault(src, kind)
                        playing.append(src)
        finally:
            await browser.close()

    result.candidates = rank(found, playing)
    return result
