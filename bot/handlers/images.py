"""Image commands: scrape any page, galleries, og:image, favicons."""

from __future__ import annotations

from collections import Counter

from telegram import Update

from ..db import User
from ..registry import command
from ..utils import esc, truncate
from .common import Ctx, enqueue, need_url, non_url_args, reply, svc, url_from


def _min_width(context: Ctx) -> int:
    for arg in non_url_args(context):
        a = arg.lower().rstrip("px").removeprefix("min")
        if a.isdigit() and 0 < int(a) <= 10000:
            return int(a)
    return 0


def _limit(context: Ctx, default: int) -> int:
    for arg in non_url_args(context):
        if arg.lower().startswith("limit="):
            v = arg.split("=", 1)[1]
            if v.isdigit():
                return int(v)
    return default


@command(
    "images",
    "images",
    "Download every image on any web page (album or ZIP)",
    "/images <url> [min-width e.g. 400] [zip]",
)
async def images(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "images")
        return
    mode = "zip" if "zip" in [a.lower() for a in non_url_args(context)] else "auto"
    await enqueue(
        update,
        context,
        user,
        url,
        kind="images",
        options={
            "mode": mode,
            "min_width": _min_width(context),
            "limit": _limit(context, svc(context).settings.max_images_per_request),
        },
    )


@command("img", "images", "The largest image on a page (or a direct image link)", "/img <url>")
async def img(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "img")
        return
    await enqueue(update, context, user, url, kind="images", options={"mode": "album", "pick": "largest"})


@command("gallery", "images", "Full-resolution galleries from 100+ sites (gallery-dl)", "/gallery <url> [limit=50]")
async def gallery(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "gallery")
        return
    await enqueue(update, context, user, url, kind="gallery", options={"mode": "auto", "limit": _limit(context, 50)})


@command("imgzip", "images", "All images on a page as one ZIP file", "/imgzip <url> [min-width]")
async def imgzip(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "imgzip")
        return
    await enqueue(update, context, user, url, kind="images", options={"mode": "zip", "min_width": _min_width(context)})


@command("album", "images", "Images on a page as Telegram albums (10 per album)", "/album <url> [min-width]")
async def album(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "album")
        return
    await enqueue(
        update,
        context,
        user,
        url,
        kind="images",
        options={"mode": "album", "min_width": _min_width(context), "limit": _limit(context, 50)},
    )


async def _pick_sources(update: Update, context: Ctx, user: User, name: str, sources: set[str], label: str) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, name)
        return
    try:
        found, title = await svc(context).images.find(url)
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ Couldn't load the page: {esc(str(exc)[:200])}")
        return
    urls = [f.url for f in found if f.source in sources]
    if not urls:
        await reply(update, f"No {label} found on that page.")
        return
    await enqueue(
        update,
        context,
        user,
        url,
        kind="imagelist",
        title=f"{label} · {truncate(title, 60)}",
        options={"urls": urls[:10], "mode": "album", "as_document": name == "favicon"},
    )


@command("og", "images", "The page's share preview image (Open Graph / Twitter card)", "/og <url>")
async def og(update: Update, context: Ctx, user: User) -> None:
    await _pick_sources(update, context, user, "og", {"meta"}, "preview image")


@command("favicon", "images", "The site's icons and favicons", "/favicon <url>")
async def favicon(update: Update, context: Ctx, user: User) -> None:
    await _pick_sources(update, context, user, "favicon", {"icon"}, "icons")


@command("imgcount", "images", "Count the images on a page without downloading them", "/imgcount <url>")
async def imgcount(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "imgcount")
        return
    try:
        found, title = await svc(context).images.find(url)
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ Couldn't load the page: {esc(str(exc)[:200])}")
        return
    by_source = Counter(f.source for f in found)
    parts = ", ".join(f"{k}: {v}" for k, v in by_source.most_common())
    await reply(
        update,
        f"🖼 <b>{esc(truncate(title, 80))}</b>\n{len(found)} images found"
        f"{' (' + esc(parts) + ')' if parts else ''}.\n"
        f"Download them with <code>/images {esc(url)}</code>",
    )
