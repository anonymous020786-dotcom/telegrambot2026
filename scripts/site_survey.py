"""Survey which popular sites this server can read right now, by category.

Uses each yt-dlp extractor's own sample video (like /sitecheck) and reports extractor, formats and the best
quality found. Run inside the bot container:  python -m scripts.site_survey [--json out.json] [category ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import yt_dlp

from bot.services.downloader import load_all_plugins
from bot.services.sitecheck import CATEGORIES, sample_url

SITES = CATEGORIES


def classes_by_key() -> dict[str, type]:
    load_all_plugins()
    return {ie.ie_key(): ie for ie in yt_dlp.extractor.gen_extractor_classes()}


def probe(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 30, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    info = info or {}
    if info.get("entries"):
        info = next((e for e in info["entries"] if e), info)
    fmts = info.get("formats") or []
    heights = [f.get("height") for f in fmts if f.get("height")]
    return {
        "title": (info.get("title") or "")[:60],
        "formats": len(fmts),
        "best_height": max(heights) if heights else None,
        "audio_only": bool(fmts) and not heights,
    }


async def run(categories: list[str], timeout: float, concurrency: int) -> list[dict]:
    classes = classes_by_key()
    sem = asyncio.Semaphore(concurrency)

    async def one(cat: str, label: str, key: str) -> dict:
        row = {"category": cat, "site": label, "extractor": key}
        ie = classes.get(key)
        if ie is None:
            return {**row, "status": "no_extractor"}
        if not ie.working():
            return {**row, "status": "marked_broken"}
        url = sample_url(key)
        if not url:
            return {**row, "status": "no_sample"}
        async with sem:
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(asyncio.to_thread(probe, url), timeout)
                status = "ok" if res["formats"] or res["title"] else "empty"
                return {**row, "status": status, "url": url, **res, "secs": round(time.monotonic() - t0, 1)}
            except TimeoutError:
                return {**row, "status": "timeout", "url": url}
            except Exception as exc:  # noqa: BLE001 - record every failure
                msg = str(exc).replace("ERROR: ", "").split("\n")[0][:160]
                return {**row, "status": "error", "url": url, "error": msg}

    jobs = [one(c, label, key) for c in categories for label, key in SITES[c].items()]
    return list(await asyncio.gather(*jobs))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("categories", nargs="*", default=list(SITES))
    ap.add_argument("--json")
    ap.add_argument("--timeout", type=float, default=75)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    rows = asyncio.run(run(args.categories, args.timeout, args.concurrency))
    for r in rows:
        extra = (
            f"{r.get('formats')} fmts, best {r.get('best_height') or 'audio'}"
            if r["status"] == "ok"
            else r.get("error", "")
        )
        print(f"{r['status']:<13} {r['category']:<11} {r['site']:<20} {extra}")
    total = len(rows)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"\n{ok}/{total} OK")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
