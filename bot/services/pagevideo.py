"""Universal fallback: find downloadable video streams in any web page's HTML.

Used when yt-dlp has no extractor for a site. Finds plain (DRM-free) streams that the page itself exposes:
<video>/<source>, og:video / twitter player streams, JSON-LD VideoObject, links to media files, stream URLs
inside inline scripts, and embedded players that yt-dlp does support.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

VIDEO_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.(?:mp4|m4v|webm|mov|m3u8|mpd)(?:\?[^\s\"'<>\\]*)?", re.I)
MEDIA_EXT_RE = re.compile(r"\.(mp4|m4v|webm|mov|m3u8|mpd)(?:$|\?)", re.I)
QUALITY_RE = re.compile(r"(?<!\d)(2160|1440|1080|720|480|360|240)p?(?!\d)")
# Standard self-labels adult sites put in <meta name="rating"> (RTA = "Restricted To Adults").
ADULT_RATINGS = ("rta-5042-1996-1400-1577-rta", "adult", "mature", "restricted", "18+", "xxx")


@dataclass
class VideoCandidate:
    url: str
    source: str
    kind: str  # file | hls | dash | embed
    height: int = 0

    @property
    def score(self) -> tuple[int, int, int]:
        kind_rank = {"embed": 4, "hls": 3, "file": 2, "dash": 1}[self.kind]
        source_rank = {"embed": 3, "video": 3, "ld+json": 2, "meta": 2, "link": 1, "script": 0}.get(self.source, 0)
        return (kind_rank, self.height, source_rank)


def _kind(url: str) -> str:
    m = MEDIA_EXT_RE.search(urlparse(url).path + ("?" if "?" in url else ""))
    ext = m.group(1).lower() if m else "mp4"
    return {"m3u8": "hls", "mpd": "dash"}.get(ext, "file")


def _height(url: str, hint: str = "") -> int:
    m = QUALITY_RE.search(f"{hint} {url}")
    return int(m.group(1)) if m else 0


def page_is_adult(html: str) -> bool:
    """True if the page labels itself adult (RTA label or rating/content-rating meta)."""
    soup = BeautifulSoup(html[:200_000], "lxml")
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or meta.get("http-equiv") or "").lower()
        if name in ("rating", "content-rating", "pics-label", "og:restrictions:age"):
            value = (meta.get("content") or "").lower()
            if any(tag in value for tag in ADULT_RATINGS) or value.strip() in ("18", "18+"):
                return True
    return False


def find_videos(html: str, page_url: str, embed_supported=None) -> list[VideoCandidate]:
    """All video candidates on a page, best first. `embed_supported(url) -> bool` accepts iframe players."""
    soup = BeautifulSoup(html, "lxml")
    base = page_url
    if (tag := soup.find("base", href=True)) is not None:
        base = urljoin(page_url, tag["href"])
    found: dict[str, VideoCandidate] = {}

    def add(raw: str | None, source: str, hint: str = "", kind: str | None = None) -> None:
        if not raw:
            return
        url = urljoin(base, raw.strip().replace("\\/", "/").replace("&amp;", "&"))
        if urlparse(url).scheme not in ("http", "https") or url in found:
            return
        found[url] = VideoCandidate(url, source, kind or _kind(url), _height(url, hint))

    for video in soup.find_all("video"):
        add(video.get("src"), "video", video.get("data-quality") or "")
        for s in video.find_all("source"):
            add(s.get("src"), "video", " ".join(str(s.get(a) or "") for a in ("label", "size", "res", "title")))
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
        if key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream", "contenturl"):
            content = meta.get("content") or ""
            if MEDIA_EXT_RE.search(content):
                add(content, "meta")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text() or "null")
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if str(node.get("@type", "")).lower() == "videoobject":
                    url = node.get("contentUrl")
                    if isinstance(url, str) and MEDIA_EXT_RE.search(url):
                        add(url, "ld+json", str(node.get("height") or ""))
                    embed = node.get("embedUrl")
                    if isinstance(embed, str) and embed_supported and embed_supported(urljoin(base, embed)):
                        add(embed, "embed", kind="embed")
                stack.extend(v for v in node.values() if isinstance(v, dict | list))
    for a in soup.find_all("a", href=True):
        if MEDIA_EXT_RE.search(a["href"]):
            add(a["href"], "link", a.get_text(" ", strip=True))
    for script in soup.find_all("script"):
        text = (script.get_text() or "").replace("\\/", "/")
        for m in VIDEO_URL_RE.finditer(text):
            window = text[max(0, m.start() - 60) : m.start()]
            add(m.group(0), "script", window)
    if embed_supported:
        for frame in soup.find_all(["iframe", "embed"]):
            src = frame.get("src") or frame.get("data-src")
            if src and embed_supported(urljoin(base, src)):
                add(src, "embed", kind="embed")
    # Skip obvious ads/trailers/previews when real streams exist.
    candidates = sorted(found.values(), key=lambda c: c.score, reverse=True)
    main = [c for c in candidates if not re.search(r"(preview|trailer|thumb|/ads?/|advert|teaser|promo)", c.url, re.I)]
    return main or candidates
