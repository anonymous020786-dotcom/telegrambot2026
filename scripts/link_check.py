"""Check real links the way the bot handles them: yt-dlp (with our plugins), the page-video scan, then a browser.

Run inside the bot container:  python -m scripts.link_check URL [URL ...]
"""

from __future__ import annotations

import asyncio
import sys

import httpx
import yt_dlp

from bot.services.browser import sniff
from bot.services.downloader import load_all_plugins, supported_extractor
from bot.services.pagevideo import find_videos, page_is_adult

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _kind(formats: list[dict]) -> str:
    """Label for formats without a known height: video, audio or unknown."""
    if any(f.get("vcodec") not in (None, "none") for f in formats):
        return "video"
    if any(f.get("acodec") not in (None, "none") for f in formats):
        return "audio"
    return "?"


def probe(url: str, referer: str | None = None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 30, "noplaylist": True}
    if referer:
        opts["http_headers"] = {"Referer": referer, "User-Agent": UA}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    if info.get("entries"):
        info = next((e for e in info["entries"] if e), info)
    fmts = info.get("formats") or [info]
    heights = [f.get("height") for f in fmts if f.get("height")]
    return {
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "title": (info.get("title") or "")[:70],
        "formats": len(fmts),
        "best": max(heights) if heights else _kind(fmts),
    }


def check(url: str) -> str:
    try:
        r = probe(url)
        return f"OK  yt-dlp[{r['extractor']}] {r['best']} · {r['formats']} fmts · {r['title']}"
    except Exception as exc:  # noqa: BLE001 - fall through to the page scan like the bot does
        first = str(exc).replace("ERROR: ", "").split("\n")[0][:120]
    try:
        resp = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
        html, final = resp.text, str(resp.url)
    except Exception as exc:  # noqa: BLE001
        return f"FAIL page unreachable ({exc.__class__.__name__}); yt-dlp: {first}"
    adult = " [adult page]" if page_is_adult(html) else ""
    cands = find_videos(html, final, embed_supported=lambda u: supported_extractor(u) is not None)
    for c in cands[:4]:
        try:
            r = probe(c.url, referer=final)
            return f"OK  page-fallback[{c.source}/{c.kind}] {r['best']} · {r['formats']} fmts{adult} · {c.url[:90]}"
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    tried = {c.url for c in cands[:4]}
    sniffed = asyncio.run(sniff(url, UA, timeout=45))
    for c in [c for c in sniffed.candidates if c.url not in tried][:4]:
        try:
            r = probe(c.url, referer=sniffed.final_url or final)
            return f"OK  browser[{c.kind}] {r['best']} · {r['formats']} fmts{adult} · {c.url[:90]}"
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    return (
        f"FAIL {len(cands)} page + {len(sniffed.candidates)} browser candidates, none playable{adult}; yt-dlp: {first}"
    )


def main() -> None:
    load_all_plugins()
    for url in sys.argv[1:]:
        print(f"{url}\n    {check(url)}", flush=True)


if __name__ == "__main__":
    main()
