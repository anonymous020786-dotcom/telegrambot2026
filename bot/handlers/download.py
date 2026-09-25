"""Download, audio, media-detail and search commands, plus link cards and their buttons."""

from __future__ import annotations

import io
import json
import logging

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from yt_dlp.utils import DownloadError

from ..db import User
from ..registry import command
from ..services.downloader import AUDIO_FORMATS, QUALITIES, MediaInfo, friendly_error
from ..ui import cards
from ..utils import (
    esc,
    extract_urls,
    human_duration,
    human_size,
    looks_like_image_url,
    parse_range,
    parse_timestamp,
    truncate,
)
from .common import Ctx, enqueue, need_url, non_url_args, preset_for, reply, svc, url_from, usage

log = logging.getLogger(__name__)


async def _probe_or_explain(update: Update, context: Ctx, url: str, playlist: bool = False) -> MediaInfo | None:
    s = svc(context)
    try:
        return await s.probe(url, playlist=playlist)
    except DownloadError as exc:
        token = s.tokens.put(url)
        await reply(
            update,
            f"⚠️ {esc(friendly_error(exc))}",
            reply_markup=InlineKeyboardMarkup([[B("🖼 Get images from this page", callback_data=f"img:{token}")]]),
        )
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ Couldn't read that link: {esc(friendly_error(exc))}")
    return None


async def _video(update: Update, context: Ctx, user: User, name: str, **preset: object) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, name)
        return
    await enqueue(update, context, user, url, preset=preset_for(user, mode="video", **preset))


async def _audio(update: Update, context: Ctx, user: User, name: str, fmt: str) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, name)
        return
    bitrate = int(user.pref("audio_bitrate"))
    for arg in non_url_args(context):
        if arg.rstrip("k").isdigit() and 32 <= int(arg.rstrip("k")) <= 320:
            bitrate = int(arg.rstrip("k"))
    await enqueue(
        update, context, user, url, preset=preset_for(user, mode="audio", audio_format=fmt, audio_bitrate=bitrate)
    )


# ------------------------------------------------------------------ download


@command("dl", "download", "Smart download: video in your default quality, or images for image links", "/dl <url>")
async def dl(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "dl")
        return
    if looks_like_image_url(url):
        await enqueue(update, context, user, url, kind="images", options={"mode": "album"})
    else:
        await enqueue(update, context, user, url, preset=preset_for(user, mode="video"))


@command("video", "download", "Download the video in your default quality", "/video <url>")
async def video(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "video")


@command("audio", "download", "Download audio in your default audio format", "/audio <url> [bitrate]")
async def audio(update: Update, context: Ctx, user: User) -> None:
    await _audio(update, context, user, "audio", user.pref("audio_format"))


@command("best", "download", "Best available quality", "/best <url>")
async def best(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "best", quality="best")


@command("worst", "download", "Smallest file (lowest quality)", "/worst <url>")
async def worst(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "worst", quality="worst")


@command("hd", "download", "720p video", "/hd <url>")
async def hd(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "hd", quality="720")


@command("fhd", "download", "1080p Full HD video", "/fhd <url>")
async def fhd(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "fhd", quality="1080")


@command("4k", "download", "2160p 4K video (when available)", "/4k <url>")
async def uhd(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "4k", quality="2160")


@command("preview", "download", "Quick low-res 360p copy", "/preview <url>")
async def preview(update: Update, context: Ctx, user: User) -> None:
    await _video(update, context, user, "preview", quality="360")


@command("q", "download", "Pick a resolution: 144–2160", "/q <height> <url>")
async def q(update: Update, context: Ctx, user: User) -> None:
    heights = [a.rstrip("p") for a in non_url_args(context) if a.rstrip("p").isdigit()]
    if not heights or heights[0] not in QUALITIES:
        await reply(update, f"{usage('q')}\nHeights: {', '.join(x for x in QUALITIES if x.isdigit())}")
        return
    await _video(update, context, user, "q", quality=heights[0])


@command("formats", "download", "List every available format and pick one", "/formats <url>")
async def formats(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "formats")
        return
    info = await _probe_or_explain(update, context, url)
    if not info:
        return
    if not info.formats:
        await reply(update, "This link has a single format. Use /dl to download it.")
        return
    token = svc(context).tokens.put(url)
    await reply(update, cards.formats_table(info), reply_markup=cards.formats_keyboard(token, info))


@command("getformat", "download", "Download a specific format ID (see /formats)", "/getformat <format_id> <url>")
async def getformat(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    args = non_url_args(context)
    if not url or not args:
        await reply(update, usage("getformat"))
        return
    fid = args[0]
    has_audio = True
    info = await _probe_or_explain(update, context, url)
    if not info:
        return
    match = next((f for f in info.formats if f.format_id == fid), None)
    if match is None and "+" not in fid:
        await reply(update, f"Format <code>{esc(fid)}</code> isn't available. See /formats.")
        return
    if match is not None:
        has_audio = match.has_audio
    await enqueue(
        update,
        context,
        user,
        url,
        title=info.title,
        preset=preset_for(user, mode="format", format_id=fid, format_has_audio=has_audio),
    )


@command("clip", "download", "Download only part of a video", "/clip <url> <start> <end>  (e.g. 1:05 2:30)")
async def clip(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    args = non_url_args(context)
    if not url or len(args) < 2:
        await reply(update, usage("clip"))
        return
    try:
        start, end = parse_timestamp(args[0]), parse_timestamp(args[1])
    except ValueError as exc:
        await reply(update, f"⏱ {esc(exc)}")
        return
    if end <= start:
        await reply(update, "⏱ The end time must be after the start time.")
        return
    audio_only = any(a.lower() in ("audio", "mp3") for a in args[2:])
    preset = preset_for(user, mode="audio" if audio_only else "video", section=(start, end))
    await enqueue(update, context, user, url, preset=preset)


@command("playlist", "download", "Download a playlist or channel (optionally a range)", "/playlist <url> [1-10] [mp3]")
async def playlist(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "playlist")
        return
    s = svc(context)
    args = non_url_args(context)
    limit = min(int(user.pref("playlist_limit")), s.settings.max_playlist_items)
    items = None
    for arg in args:
        if arg[0].isdigit() or arg.startswith(":"):
            try:
                items = parse_range(arg, limit)
            except ValueError as exc:
                await reply(update, f"🔢 {esc(exc)}")
                return
    as_audio = any(a.lower() in AUDIO_FORMATS for a in args)
    fmt = next((a.lower() for a in args if a.lower() in AUDIO_FORMATS), user.pref("audio_format"))
    preset = preset_for(
        user,
        mode="audio" if as_audio else "video",
        audio_format=fmt,
        playlist=True,
        playlist_items=items or f"1-{limit}",
    )
    await enqueue(update, context, user, url, preset=preset)


@command("batch", "download", "Download many links at once (one per line)", "/batch <url1> <url2> … [mp3]")
async def batch(update: Update, context: Ctx, user: User) -> None:
    msg = update.effective_message
    text = (
        (msg.text or "")
        + "\n"
        + ((msg.reply_to_message.text or msg.reply_to_message.caption or "") if msg.reply_to_message else "")
    )
    urls = extract_urls(text)[:25]
    if not urls:
        await reply(update, usage("batch") + "\nYou can also reply to a message that contains links.")
        return
    fmt = next((a.lower() for a in non_url_args(context) if a.lower() in AUDIO_FORMATS), None)
    queued = 0
    for url in urls:
        if looks_like_image_url(url):
            job = await enqueue(update, context, user, url, kind="images", options={"mode": "album"})
        elif fmt:
            job = await enqueue(update, context, user, url, preset=preset_for(user, mode="audio", audio_format=fmt))
        else:
            job = await enqueue(update, context, user, url, preset=preset_for(user, mode="video"))
        if job is None:
            break
        queued += 1
    await reply(update, f"📦 Queued {queued} of {len(urls)} links. See /queue.")


@command("direct", "download", "Get direct media URLs (to stream or use elsewhere)", "/direct <url>")
async def direct(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "direct")
        return
    try:
        links = await svc(context).downloader.direct_urls(url, preset_for(user, mode="video"))
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ {esc(friendly_error(exc))}")
        return
    if not links:
        await reply(update, "No direct links found.")
        return
    lines = ["🔗 <b>Direct links</b> (they usually expire within hours):"]
    lines += [f'• <a href="{esc(u)}">{esc(label)}</a>' for label, u in links]
    await reply(update, "\n".join(lines))


@command("doc", "download", "Download and send as an uncompressed file", "/doc <url>")
async def doc(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "doc")
        return
    await enqueue(update, context, user, url, preset=preset_for(user, mode="video"), options={"as_document": True})


@command("gif", "download", "Turn a video link into a GIF (first 30 s)", "/gif <url>", needs_ffmpeg=True)
async def gif(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "gif")
        return
    await enqueue(
        update,
        context,
        user,
        url,
        preset=preset_for(user, mode="video", quality="480", section=(0, 30)),
        options={"force_kind": "gif"},
    )


# ------------------------------------------------------------------ audio


def _make_audio(fmt: str, desc: str):
    async def handler(update: Update, context: Ctx, user: User) -> None:
        await _audio(update, context, user, fmt, fmt)

    command(fmt, "audio", desc, f"/{fmt} <url>" + (" [bitrate]" if fmt in ("mp3", "m4a", "aac", "opus") else ""))(
        handler
    )


_make_audio("mp3", "Audio as MP3 (choose bitrate, e.g. /mp3 URL 320)")
_make_audio("m4a", "Audio as M4A/AAC (Apple-friendly)")
_make_audio("opus", "Audio as Opus (small, high quality)")
_make_audio("flac", "Lossless FLAC audio")
_make_audio("wav", "Uncompressed WAV audio")
_make_audio("aac", "Raw AAC audio")
_make_audio("ogg", "Ogg Vorbis audio")


@command("voice", "audio", "Audio as a Telegram voice message", "/voice <url>", needs_ffmpeg=True)
async def voice(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "voice")
        return
    await enqueue(
        update,
        context,
        user,
        url,
        preset=preset_for(user, mode="audio", audio_format="opus"),
        options={"force_kind": "voice"},
    )


# ------------------------------------------------------------------ extras


@command("info", "extras", "Details: title, uploader, duration, qualities, subtitles…", "/info <url>")
async def info_cmd(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "info")
        return
    info = await _probe_or_explain(update, context, url)
    if info:
        await reply(update, cards.details_text(info))


@command("thumb", "extras", "Get the thumbnail / cover image", "/thumb <url>")
async def thumb(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "thumb")
        return
    await enqueue(update, context, user, url, preset=preset_for(user, mode="thumbnail"))


@command("subs", "extras", "List subtitle languages, or download one (srt)", "/subs <url> [lang|all]")
async def subs(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "subs")
        return
    args = non_url_args(context)
    if args:
        await enqueue(update, context, user, url, preset=preset_for(user, mode="subs", sub_lang=args[0].lower()))
        return
    info = await _probe_or_explain(update, context, url)
    if not info:
        return
    if not info.subtitles and not info.auto_captions:
        await reply(update, "No subtitles available for this video.")
        return
    text = [f"📝 <b>Subtitles</b> · {esc(truncate(info.title, 80))}"]
    if info.subtitles:
        text.append("Manual: " + ", ".join(f"<code>{esc(x)}</code>" for x in info.subtitles[:40]))
    if info.auto_captions:
        text.append(
            f"Auto-generated: {len(info.auto_captions)} languages (e.g. "
            + ", ".join(f"<code>{esc(x)}</code>" for x in info.auto_captions[:12])
            + ")"
        )
    text.append(f"\nDownload with <code>/subs {esc(url)} en</code>")
    await reply(update, "\n".join(text))


@command("desc", "extras", "Show the full description", "/desc <url>")
async def desc(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "desc")
        return
    info = await _probe_or_explain(update, context, url)
    if info:
        await reply(
            update,
            f"📄 <b>{esc(truncate(info.title, 100))}</b>\n\n"
            f"{esc(truncate(info.description or 'No description.', 3600))}",
        )


@command("chapters", "extras", "List the video's chapters", "/chapters <url>")
async def chapters(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "chapters")
        return
    info = await _probe_or_explain(update, context, url)
    if info:
        await reply(update, cards.chapters_text(info))


@command("splitchapters", "extras", "Download each chapter as a separate file", "/splitchapters <url> [mp3]")
async def splitchapters(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "splitchapters")
        return
    as_audio = any(a.lower() in AUDIO_FORMATS for a in non_url_args(context))
    await enqueue(
        update,
        context,
        user,
        url,
        preset=preset_for(user, mode="audio" if as_audio else "video", split_chapters=True, embed_thumbnail=False),
    )


@command("meta", "extras", "All metadata as a JSON file", "/meta <url>")
async def meta(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "meta")
        return
    info = await _probe_or_explain(update, context, url)
    if not info:
        return
    raw = {
        k: v
        for k, v in info.raw.items()
        if k not in ("formats", "thumbnails", "automatic_captions", "requested_formats", "http_headers")
    }
    data = json.dumps(raw, indent=2, ensure_ascii=False, default=str).encode()
    await update.effective_message.reply_document(
        InputFile(io.BytesIO(data), filename="metadata.json"), caption=esc(truncate(info.title, 200))
    )


@command("size", "extras", "Estimated file size for each quality", "/size <url>")
async def size(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "size")
        return
    info = await _probe_or_explain(update, context, url)
    if not info:
        return
    lines = [f"📦 <b>Estimated sizes</b> · {esc(truncate(info.title, 80))}"]
    for h in info.heights[:12]:
        lines.append(f"{h}p: ~{human_size(info.estimate_size(h))}")
    audio = info.best_audio()
    if audio and audio.filesize:
        lines.append(f"Audio only: ~{human_size(audio.filesize)}")
    if len(lines) == 1:
        lines.append("The site doesn't report sizes in advance.")
    await reply(update, "\n".join(lines))


# ------------------------------------------------------------------ search


async def _search(update: Update, context: Ctx, site: str, name: str) -> None:
    query = " ".join(non_url_args(context))
    if not query:
        await reply(update, usage(name))
        return
    s = svc(context)
    try:
        results = await s.downloader.search(query, 8, site)
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ Search failed: {esc(friendly_error(exc))}")
        return
    if not results:
        await reply(update, "No results.")
        return
    lines = [f"🔎 <b>{esc(query)}</b>"]
    rows = []
    for i, r in enumerate(results, 1):
        dur = f" · {human_duration(r['duration'])}" if r.get("duration") else ""
        lines.append(f"{i}. {esc(truncate(r['title'], 70))}{dur}")
        token = s.tokens.put(r["url"], title=r["title"])
        rows.append(B(str(i), callback_data=f"pick:{token}"))
    await reply(update, "\n".join(lines), reply_markup=InlineKeyboardMarkup([rows[:4], rows[4:8]]))


@command("yt", "search", "Search YouTube and pick a result", "/yt <search words>")
async def yt(update: Update, context: Ctx, user: User) -> None:
    await _search(update, context, "yt", "yt")


@command("sc", "search", "Search SoundCloud and pick a track", "/sc <search words>")
async def sc(update: Update, context: Ctx, user: User) -> None:
    await _search(update, context, "sc", "sc")


@command("ytmp3", "search", "Search YouTube and download the top result as MP3", "/ytmp3 <search words>")
async def ytmp3(update: Update, context: Ctx, user: User) -> None:
    query = " ".join(non_url_args(context))
    if not query:
        await reply(update, usage("ytmp3"))
        return
    try:
        results = await svc(context).downloader.search(query, 1, "yt")
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ Search failed: {esc(friendly_error(exc))}")
        return
    if not results:
        await reply(update, "No results.")
        return
    top = results[0]
    await enqueue(
        update, context, user, top["url"], title=top["title"], preset=preset_for(user, mode="audio", audio_format="mp3")
    )


# ------------------------------------------------------------------ link messages & card buttons


async def show_card(update: Update, context: Ctx, url: str, edit_message=None) -> None:
    s = svc(context)
    status = edit_message or await reply(update, "🔎 Looking up the link…")
    playlist_hint = any(k in url for k in ("list=", "/playlist", "/sets/", "/album/", "/channel/", "/@"))
    try:
        info = await s.probe(url, playlist=playlist_hint)
    except DownloadError as exc:
        token = s.tokens.put(url)
        text = f"⚠️ {esc(friendly_error(exc))}"
        markup = InlineKeyboardMarkup(
            [
                [
                    B("🖼 Get images from this page", callback_data=f"img:{token}"),
                    B("🖼 Largest image", callback_data=f"img1:{token}"),
                ]
            ]
        )
        if status:
            await status.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        return
    except Exception as exc:  # noqa: BLE001
        if status:
            await status.edit_text(f"⚠️ {esc(friendly_error(exc))}", parse_mode=ParseMode.HTML)
        return
    token = s.tokens.put(url)
    text, markup = cards.media_card(info), cards.media_keyboard(token, info)
    if info.thumbnail and not info.is_playlist and status:
        try:
            await context.bot.send_photo(
                status.chat_id, info.thumbnail, caption=text, parse_mode=ParseMode.HTML, reply_markup=markup
            )
            await status.delete()
            return
        except TelegramError:
            pass
    if status:
        await status.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)


async def on_link_message(update: Update, context: Ctx, user: User) -> None:
    """Plain messages containing links: one link → preview card; several → bulk buttons."""
    msg = update.effective_message
    urls = extract_urls(msg.text or msg.caption or "")
    for ent in (msg.entities or []) + (msg.caption_entities or []):
        if ent.url and ent.url not in urls:
            urls.append(ent.url)
    if not urls:
        await reply(update, "Send me a link, or /help to see everything I can do.")
        return
    s = svc(context)
    if len(urls) == 1:
        url = urls[0]
        if looks_like_image_url(url):
            await enqueue(update, context, user, url, kind="images", options={"mode": "album"})
            return
        await show_card(update, context, url)
        return
    token = s.tokens.put("\n".join(urls[:25]))
    await reply(
        update,
        f"📦 Found {len(urls[:25])} links. What should I do?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    B("🎬 Download all (best)", callback_data=f"multi:{token}:video"),
                    B("🎵 All as MP3", callback_data=f"multi:{token}:mp3"),
                ],
                [B("🖼 Images from all", callback_data=f"multi:{token}:images"), B("✖ Close", callback_data="close")],
            ]
        ),
    )


async def on_card_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    s = svc(context)
    data = query.data or ""
    kind, _, rest = data.partition(":")
    token, _, action = rest.partition(":")
    url = s.tokens.url(token)
    if url is None:
        await query.answer("This button has expired. Send the link again.", show_alert=True)
        return
    await query.answer()
    msg = query.message
    if kind == "dl":
        preset = preset_for(user, mode="video")
        options: dict[str, object] = {}
        if action == "best":
            preset.quality = "best"
        elif action.startswith("q"):
            preset.quality = action[1:]
        elif action.startswith("a:"):
            parts = action.split(":")
            preset = preset_for(user, mode="audio", audio_format=parts[1])
            if len(parts) > 2:
                preset.audio_bitrate = int(parts[2])
        elif action == "thumb":
            preset = preset_for(user, mode="thumbnail")
        elif action == "subs":
            preset = preset_for(user, mode="subs")
        elif action == "doc":
            options["as_document"] = True
        elif action == "gif":
            preset = preset_for(user, mode="video", quality="480", section=(0, 30))
            options["force_kind"] = "gif"
        elif action.startswith("pl:"):
            what = action[3:]
            limit = min(int(user.pref("playlist_limit")), s.settings.max_playlist_items)
            if what == "mp3":
                preset = preset_for(user, mode="audio", audio_format="mp3", playlist=True, playlist_items=f"1-{limit}")
            else:
                n = 10 if what == "10" else limit
                preset = preset_for(user, mode="video", playlist=True, playlist_items=f"1-{n}")
        await enqueue(update, context, user, url, preset=preset, options=options, chat_id=msg.chat_id)
    elif kind == "qual":
        info = await s.probe(url)
        await _edit_markup(msg, cards.quality_keyboard(token, info))
    elif kind == "fmts":
        info = await s.probe(url)
        await _edit_markup(msg, cards.formats_keyboard(token, info))
    elif kind == "card":
        info = await s.probe(url)
        await _edit_markup(msg, cards.media_keyboard(token, info))
    elif kind == "fmt":
        fid, _, has_audio = action.partition(":")
        await enqueue(
            update,
            context,
            user,
            url,
            chat_id=msg.chat_id,
            preset=preset_for(user, mode="format", format_id=fid, format_has_audio=has_audio == "1"),
        )
    elif kind == "info":
        info = await s.probe(url)
        await context.bot.send_message(
            msg.chat_id, cards.details_text(info), parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    elif kind == "list":
        info = await s.probe(url, playlist=True)
        lines = [f"📜 <b>{esc(truncate(info.title, 80))}</b>"]
        for i, e in enumerate(info.entries[:50], 1):
            lines.append(f"{i}. {esc(truncate(e['title'] or '', 70))}")
        if len(info.entries) > 50:
            lines.append(f"… and {len(info.entries) - 50} more")
        lines.append("\nDownload a range with <code>/playlist URL 1-10</code>")
        await context.bot.send_message(msg.chat_id, "\n".join(lines), parse_mode=ParseMode.HTML)
    elif kind == "pick":
        await show_card(update, context, url, edit_message=None if msg.photo else msg)
    elif kind in ("img", "img1"):
        options = {"mode": "album"} if kind == "img" else {"mode": "album", "pick": "largest"}
        await enqueue(update, context, user, url, kind="images", options=options, chat_id=msg.chat_id)
    elif kind == "multi":
        queued = 0
        for u in url.split("\n"):
            if action == "images":
                job = await enqueue(
                    update, context, user, u, kind="images", chat_id=msg.chat_id, options={"mode": "album"}
                )
            elif action == "mp3":
                job = await enqueue(
                    update,
                    context,
                    user,
                    u,
                    chat_id=msg.chat_id,
                    preset=preset_for(user, mode="audio", audio_format="mp3"),
                )
            else:
                job = await enqueue(update, context, user, u, chat_id=msg.chat_id, preset=preset_for(user))
            if job is None:
                break
            queued += 1
        await context.bot.send_message(msg.chat_id, f"📦 Queued {queued} downloads. See /queue.")


async def _edit_markup(msg, markup) -> None:
    try:
        await msg.edit_reply_markup(markup)
    except BadRequest:
        pass
