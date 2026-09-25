"""Image scraping from any web page, plus gallery-dl for gallery sites."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from ..config import Settings
from ..utils import IMAGE_EXTS, ext_of, looks_like_image_url, safe_filename

log = logging.getLogger(__name__)

LAZY_ATTRS = (
    "data-src",
    "data-lazy-src",
    "data-original",
    "data-url",
    "data-hi-res",
    "data-full",
    "data-large",
    "data-zoom-image",
    "data-highres",
    "data-image",
    "data-bg",
    "data-background",
)
CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/heic": ".heic",
    "image/jxl": ".jxl",
}


@dataclass
class FoundImage:
    url: str
    source: str
    alt: str = ""
    width: int = 0  # declared width (srcset "w" or attribute), 0 = unknown


@dataclass
class SavedImage:
    path: Path
    url: str
    width: int
    height: int
    size: int


def _best_from_srcset(srcset: str) -> tuple[str, int] | None:
    best: tuple[str, float, int] | None = None
    for part in re.split(r",\s+(?=\S)", srcset.strip()):
        bits = part.strip().split()
        if not bits:
            continue
        url, desc = bits[0], (bits[1] if len(bits) > 1 else "1x")
        m = re.fullmatch(r"([\d.]+)([wx])", desc)
        value = float(m.group(1)) * (10000 if m and m.group(2) == "x" else 1) if m else 1.0
        width = int(float(m.group(1))) if m and m.group(2) == "w" else 0
        if best is None or value > best[1]:
            best = (url, value, width)
    return (best[0], best[2]) if best else None


def extract_images_from_html(html: str, page_url: str) -> list[FoundImage]:
    """Find image URLs in HTML: <img>/srcset/lazy attrs/<picture>/meta/icons/links/CSS/JSON-LD."""
    soup = BeautifulSoup(html, "lxml")
    base = page_url
    if (tag := soup.find("base", href=True)) is not None:
        base = urljoin(page_url, tag["href"])
    found: dict[str, FoundImage] = {}

    def add(raw: str | None, source: str, alt: str = "", width: int = 0) -> None:
        if not raw:
            return
        raw = raw.strip()
        if not raw or raw.startswith("data:"):
            return
        url = urljoin(base, raw)
        if urlparse(url).scheme not in ("http", "https"):
            return
        if url not in found:
            found[url] = FoundImage(url, source, alt, width)
        elif width > found[url].width:
            found[url].width = width

    for img in soup.find_all("img"):
        alt = img.get("alt") or ""
        declared = int(img.get("width")) if str(img.get("width") or "").isdigit() else 0
        best = _best_from_srcset(img.get("srcset") or img.get("data-srcset") or "")
        if best:
            add(best[0], "srcset", alt, best[1] or declared)
        for attr in LAZY_ATTRS:
            add(img.get(attr), "lazy", alt, declared)
        src = img.get("src")
        if src and not (src.startswith("data:") and any(img.get(a) for a in LAZY_ATTRS)):
            add(src, "img", alt, declared)
    for source in soup.select("picture source[srcset]"):
        best = _best_from_srcset(source["srcset"])
        if best:
            add(best[0], "picture", width=best[1])
    for el in soup.select("[data-bg], [data-background]"):
        if el.name != "img":
            for attr in ("data-bg", "data-background"):
                add(el.get(attr), "lazy")
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src", "image"):
            add(meta.get("content"), "meta")
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "icon" in rel or rel == "image_src":
            add(link["href"], "icon" if "icon" in rel else "meta")
    for a in soup.find_all("a", href=True):
        if looks_like_image_url(urljoin(base, a["href"])):
            add(a["href"], "link")
    for el in soup.find_all(style=True):
        for url in CSS_URL_RE.findall(el["style"]):
            add(url, "css")
    for style in soup.find_all("style"):
        for url in CSS_URL_RE.findall(style.get_text() or ""):
            if looks_like_image_url(urljoin(base, url)):
                add(url, "css")
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
                for key, value in node.items():
                    if key in ("image", "logo", "thumbnailUrl", "contentUrl"):
                        for item in value if isinstance(value, list) else [value]:
                            add(item if isinstance(item, str) else (item or {}).get("url"), "ld+json")
                    elif isinstance(value, dict | list):
                        stack.append(value)
    return list(found.values())


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    return (soup.title.get_text().strip() if soup.title else "") or "images"


class ImageService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def client(self, referer: str | None = None) -> httpx.AsyncClient:
        headers = {"User-Agent": self.settings.user_agent, "Accept-Language": "en-US,en;q=0.9"}
        if referer:
            headers["Referer"] = referer
        return httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(30, connect=15),
            proxy=self.settings.proxy,
            limits=httpx.Limits(max_connections=16),
        )

    async def fetch_page(self, url: str) -> tuple[str, str]:
        async with self.client() as client:
            res = await client.get(url)
            res.raise_for_status()
            ctype = res.headers.get("content-type", "")
            if ctype.startswith("image/"):
                return "", str(res.url)
            return res.text, str(res.url)

    async def find(self, url: str) -> tuple[list[FoundImage], str]:
        """Image candidates on a page (or the URL itself if it is an image) and the page title."""
        if looks_like_image_url(url):
            return [FoundImage(url, "direct")], Path(urlparse(url).path).name or "image"
        html, final_url = await self.fetch_page(url)
        if not html:
            return [FoundImage(final_url, "direct")], "image"
        return extract_images_from_html(html, final_url), page_title(html)

    async def download(
        self,
        images: list[FoundImage],
        dest: Path,
        referer: str | None = None,
        min_width: int = 0,
        min_bytes: int = 0,
        limit: int | None = None,
        concurrency: int = 8,
    ) -> list[SavedImage]:
        dest.mkdir(parents=True, exist_ok=True)
        limit = min(limit or self.settings.max_images_per_request, self.settings.max_images_per_request)
        sem = asyncio.Semaphore(concurrency)
        saved: list[SavedImage] = []

        async with self.client(referer) as client:

            async def one(index: int, img: FoundImage) -> None:
                async with sem:
                    if len(saved) >= limit:
                        return
                    try:
                        res = await client.get(img.url)
                        res.raise_for_status()
                    except httpx.HTTPError as exc:
                        log.debug("image failed %s: %s", img.url, exc)
                        return
                    ctype = res.headers.get("content-type", "").split(";")[0].strip()
                    if ctype and not ctype.startswith("image/"):
                        return
                    data = res.content
                    if len(data) < max(min_bytes, 200):
                        return
                    ext = ext_of(img.url) if ext_of(img.url) in IMAGE_EXTS else MIME_EXT.get(ctype, ".jpg")
                    name = safe_filename(Path(urlparse(img.url).path).stem or "image", 60)
                    path = dest / f"{index + 1:03d}_{name}{ext}"
                    path.write_bytes(data)
                    width = height = 0
                    try:
                        with Image.open(path) as im:
                            width, height = im.size
                    except (UnidentifiedImageError, OSError):
                        if ext != ".svg":
                            path.unlink(missing_ok=True)
                            return
                    if min_width and width and width < min_width:
                        path.unlink(missing_ok=True)
                        return
                    if len(saved) < limit:
                        saved.append(SavedImage(path, img.url, width, height, len(data)))
                    else:
                        path.unlink(missing_ok=True)

            await asyncio.gather(*(one(i, img) for i, img in enumerate(images)))
        saved.sort(key=lambda s: s.path.name)
        return saved

    async def gallery_dl(self, url: str, dest: Path, limit: int = 50, timeout: float = 600) -> list[Path]:
        """Run gallery-dl (supports 100+ gallery/social sites) into dest. Returns image/video files."""
        dest.mkdir(parents=True, exist_ok=True)
        exe = shutil.which("gallery-dl")
        cmd = [exe] if exe else [sys.executable, "-m", "gallery_dl"]
        cmd += ["--quiet", "-D", str(dest), "--range", f"1-{limit}", "--no-mtime"]
        if self.settings.proxy:
            cmd += ["--proxy", self.settings.proxy]
        if cookies := self.settings.cookies_path():
            cmd += ["--cookies", str(cookies)]
        cmd.append(url)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            raise
        files = sorted(p for p in dest.rglob("*") if p.is_file() and not p.name.endswith(".part"))
        if not files and proc.returncode:
            lines = (err or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else "gallery-dl failed")
        return files
