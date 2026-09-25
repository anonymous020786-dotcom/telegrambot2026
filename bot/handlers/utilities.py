"""Small utilities: QR codes, link tools, file hashes."""

from __future__ import annotations

import asyncio
import io
import shutil

import httpx
import qrcode
from telegram import InputFile, Update

from ..db import User
from ..registry import command
from ..services.netguard import request_hook
from ..utils import esc, extract_urls, file_digest, human_size, truncate
from .common import Ctx, arg_text, fetch_replied_file, need_media, need_url, reply, svc, url_from, usage


def _client(context: Ctx, follow: bool = True) -> httpx.AsyncClient:
    st = svc(context).settings
    return httpx.AsyncClient(
        headers={"User-Agent": st.user_agent},
        follow_redirects=follow,
        timeout=20,
        proxy=st.proxy,
        event_hooks={"request": [request_hook(st.allow_private_urls)]},
    )


async def _head(client: httpx.AsyncClient, url: str) -> httpx.Response:
    res = await client.head(url)
    if res.status_code in (403, 405, 501):  # some servers refuse HEAD
        async with client.stream("GET", url) as streamed:
            return streamed
    return res


@command("qr", "utils", "Make a QR code from text or a link", "/qr <text>")
async def qr(update: Update, context: Ctx, user: User) -> None:
    text = arg_text(context) or (
        update.effective_message.reply_to_message.text if update.effective_message.reply_to_message else ""
    )
    if not text:
        await reply(update, usage("qr"))
        return
    if len(text) > 1500:
        await reply(update, "That's too long for a QR code (max 1500 characters).")
        return
    img = qrcode.make(text, box_size=10, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    await update.effective_message.reply_photo(InputFile(buf, filename="qr.png"), caption=esc(truncate(text, 200)))


@command("unshorten", "utils", "Reveal where a short link really goes", "/unshorten <url>")
async def unshorten(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "unshorten")
        return
    chain = [url]
    async with _client(context, follow=False) as client:
        current = url
        for _ in range(10):
            try:
                res = await client.head(current)
            except httpx.HTTPError as exc:
                await reply(update, f"⚠️ {esc(exc)}")
                return
            location = res.headers.get("location")
            if res.status_code not in (301, 302, 303, 307, 308) or not location:
                break
            current = str(httpx.URL(current).join(location))
            chain.append(current)
    lines = ["🔗 <b>Redirect chain</b>"] + [f"{i}. {esc(u)}" for i, u in enumerate(chain)]
    await reply(update, "\n".join(lines))


@command("headers", "utils", "HTTP status and response headers of a link", "/headers <url>")
async def headers(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "headers")
        return
    try:
        async with _client(context) as client:
            res = await _head(client, url)
    except httpx.HTTPError as exc:
        await reply(update, f"⚠️ {esc(exc)}")
        return
    lines = [f"🌐 <b>{res.status_code} {esc(res.reason_phrase)}</b> · {esc(truncate(str(res.url), 100))}", "<pre>"]
    for k, v in list(res.headers.items())[:40]:
        lines.append(f"{esc(k)}: {esc(truncate(v, 150))}")
    lines.append("</pre>")
    await reply(update, "\n".join(lines))


@command("mime", "utils", "File type and size of a link (without downloading it)", "/mime <url>")
async def mime(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "mime")
        return
    try:
        async with _client(context) as client:
            res = await _head(client, url)
    except httpx.HTTPError as exc:
        await reply(update, f"⚠️ {esc(exc)}")
        return
    size = res.headers.get("content-length")
    await reply(
        update,
        f"📄 Type: <code>{esc(res.headers.get('content-type', 'unknown'))}</code>\n"
        f"📦 Size: {human_size(int(size)) if size and size.isdigit() else 'unknown'}\n"
        f"↪️ Final URL: {esc(truncate(str(res.url), 200))}",
    )


@command("hash", "utils", "MD5 / SHA-1 / SHA-256 of a file", "/hash (reply to a file)")
async def hash_cmd(update: Update, context: Ctx, user: User) -> None:
    src = await fetch_replied_file(update, context)
    if src is None:
        await need_media(update, "a file")
        return
    try:
        md5, sha1, sha256 = await asyncio.gather(
            *(asyncio.to_thread(file_digest, src, a) for a in ("md5", "sha1", "sha256"))
        )
        await reply(
            update,
            f"🔐 <b>{esc(src.name)}</b> · {human_size(src.stat().st_size)}\n"
            f"MD5: <code>{md5}</code>\nSHA-1: <code>{sha1}</code>\nSHA-256: <code>{sha256}</code>",
        )
    finally:
        shutil.rmtree(src.parent, ignore_errors=True)


@command("urls", "utils", "Extract every link from a message (reply to it)", "/urls (reply to a message)")
async def urls(update: Update, context: Ctx, user: User) -> None:
    msg = update.effective_message
    source = msg.reply_to_message or msg
    found = extract_urls((source.text or source.caption or "") + " " + arg_text(context))
    for ent in (source.entities or []) + (source.caption_entities or []):
        if ent.url and ent.url not in found:
            found.append(ent.url)
    if not found:
        await reply(update, "No links found. Reply to a message that contains links.")
        return
    await reply(
        update,
        f"🔗 <b>{len(found)} links</b>\n"
        + "\n".join(esc(u) for u in found[:100])
        + "\n\nDownload them all with /batch (reply to the same message).",
    )
