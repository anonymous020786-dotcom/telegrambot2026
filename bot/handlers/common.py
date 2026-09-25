"""Helpers shared by all command handlers."""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..context import Services
from ..db import User
from ..registry import REGISTRY, Command
from ..services.downloader import Preset
from ..services.jobs import Job, QuotaExceeded
from ..utils import esc, extract_urls, is_http_url

log = logging.getLogger(__name__)

Ctx = ContextTypes.DEFAULT_TYPE
TG_DOWNLOAD_LIMIT_CLOUD = 20 * 1024 * 1024


def svc(context: Ctx) -> Services:
    return context.application.bot_data["svc"]


async def reply(update: Update, text: str, **kwargs: Any) -> Message | None:
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    kwargs.setdefault("disable_web_page_preview", True)
    msg = update.effective_message
    if msg is None:
        return None
    try:
        return await msg.reply_text(text, **kwargs)
    except TelegramError as exc:
        log.warning("reply failed: %s", exc)
        return None


def usage(name: str) -> str:
    cmd = REGISTRY.get(name)
    return f"Usage: <code>{esc(cmd.usage)}</code>\n{esc(cmd.description)}" if cmd else ""


def arg_text(context: Ctx) -> str:
    return " ".join(context.args or []).strip()


def url_from(update: Update, context: Ctx) -> str | None:
    """First URL in the command arguments, else in the message being replied to."""
    urls = extract_urls(arg_text(context))
    if urls:
        return urls[0]
    reply_to = update.effective_message.reply_to_message if update.effective_message else None
    if reply_to:
        urls = extract_urls(reply_to.text or reply_to.caption or "")
        for ent in (reply_to.entities or []) + (reply_to.caption_entities or []):
            if ent.url and is_http_url(ent.url):
                urls.append(ent.url)
        if urls:
            return urls[0]
    return None


def non_url_args(context: Ctx) -> list[str]:
    return [a for a in (context.args or []) if not is_http_url(a)]


def preset_for(user: User, **overrides: Any) -> Preset:
    base = Preset(
        quality=str(user.pref("quality")),
        container=user.pref("container"),
        audio_format=user.pref("audio_format"),
        audio_bitrate=int(user.pref("audio_bitrate")),
        sub_lang=user.pref("sub_lang"),
        embed_subs=bool(user.pref("subs")),
        embed_thumbnail=bool(user.pref("embed_thumbnail")),
        embed_metadata=bool(user.pref("embed_metadata")),
        filename=user.pref("filename"),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


async def enqueue(
    update: Update,
    context: Ctx,
    user: User,
    url: str,
    *,
    kind: str = "media",
    preset: Preset | None = None,
    options: dict[str, Any] | None = None,
    title: str | None = None,
    chat_id: int | None = None,
) -> Job | None:
    s = svc(context)
    if s.jobs.paused_all and not user.is_admin:
        await reply(update, "⏸ Downloads are paused by the admin right now. Please try again later.")
        return None
    msg = update.effective_message
    job = Job(
        user_id=user.id,
        chat_id=chat_id or update.effective_chat.id,
        url=url,
        kind=kind,
        preset=preset or preset_for(user),
        options=options or {},
        reply_to=msg.message_id if msg else None,
        title=title,
        priority=1 if user.is_admin else 5,
    )
    try:
        await s.jobs.submit(job, user)
    except QuotaExceeded:
        limit = user.daily_limit if user.daily_limit is not None else s.settings.daily_limit
        await reply(update, f"🚦 You've reached your daily limit of {limit} downloads. It resets at 00:00 UTC.")
        return None
    return job


async def fetch_replied_file(
    update: Update, context: Ctx, want: tuple[str, ...] = ("video", "audio", "image", "file")
) -> Path | None:
    """Download the media in the replied-to message (or this message) to a temp folder."""
    msg = update.effective_message
    target = msg.reply_to_message if msg and msg.reply_to_message else msg
    if target is None:
        return None
    obj = None
    name = "file"
    if target.video and "video" in want:
        obj, name = target.video, target.video.file_name or "video.mp4"
    elif target.animation and "video" in want:
        obj, name = target.animation, target.animation.file_name or "animation.mp4"
    elif target.video_note and "video" in want:
        obj, name = target.video_note, "video_note.mp4"
    elif target.audio and "audio" in want:
        obj, name = target.audio, target.audio.file_name or "audio.mp3"
    elif target.voice and "audio" in want:
        obj, name = target.voice, "voice.ogg"
    elif target.photo and "image" in want:
        obj, name = target.photo[-1], "photo.jpg"
    elif target.document and ("file" in want or _doc_kind(target.document.file_name) in want):
        obj, name = target.document, target.document.file_name or "file"
    if obj is None:
        return None
    s = svc(context)
    if not s.settings.bot_api_base_url and (obj.file_size or 0) > TG_DOWNLOAD_LIMIT_CLOUD:
        await reply(
            update,
            "📦 Telegram only lets bots download files up to 20 MB (a self-hosted Bot API server lifts this to 2 GB).",
        )
        return None
    folder = Path(tempfile.mkdtemp(prefix="tool_", dir=s.settings.download_dir))
    tg_file = await obj.get_file(read_timeout=300)
    path = folder / Path(name).name
    await tg_file.download_to_drive(path, read_timeout=600)
    return path


def _doc_kind(filename: str | None) -> str:
    from ..utils import kind_of_path

    return kind_of_path(filename or "")


async def need_media(update: Update, what: str = "a video, audio file or photo") -> None:
    await reply(update, f"↩️ Reply to {what} with this command (or send it with the command as its caption).")


async def need_url(update: Update, name: str) -> None:
    await reply(update, f"🔗 Send a link with the command.\n{usage(name)}")


def paginate_keyboard(prefix: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}:{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{max(pages, 1)}", callback_data="noop"))
    if page + 1 < pages:
        row.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


def is_allowed(s: Services, user: User) -> bool:
    if user.is_banned:
        return False
    return (
        s.settings.public_mode
        or user.is_admin
        or user.allowed
        or user.id in s.settings.admin_ids
        or user.id in s.settings.allowed_user_ids
    )


async def access_denied(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    if user.is_banned:
        await reply(update, "⛔ You are banned from this bot.")
        return
    text = (
        f"🔒 <b>This is a private bot.</b>\nYour Telegram ID is <code>{user.id}</code>.\n\n"
        "Ask the owner for an invite code and send <code>/redeem CODE</code>"
    )
    markup = None
    if s.settings.access_requests:
        text += ", or request access below."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🙋 Request access", callback_data="access:request")]])
    await reply(update, text, reply_markup=markup)


def guard(cmd: Command):
    """Wraps a command handler with access control, maintenance mode, cooldown and error handling."""

    async def wrapper(update: Update, context: Ctx) -> None:
        tg_user = update.effective_user
        if tg_user is None or update.effective_message is None:
            return
        s = svc(context)
        user = await s.db.upsert_user(tg_user.id, tg_user.username, tg_user.first_name)
        if tg_user.id in s.settings.admin_ids and not user.is_admin:
            await s.db.set_role(tg_user.id, "admin")
            user.role = "admin"
        if user.is_banned:  # bans apply to every command, including public ones
            await reply(update, "⛔ You are banned from this bot.")
            return
        if cmd.admin and not user.is_admin:
            await reply(update, "🛡 That command is for admins only.")
            return
        if not cmd.public and not is_allowed(s, user):
            await access_denied(update, context, user)
            return
        if not user.is_admin and await s.db.get_kv("maintenance") == "1" and not cmd.public:
            await reply(update, "🛠 The bot is under maintenance. Please try again later.")
            return
        now = time.monotonic()
        if not user.is_admin and now - s.last_command.get(user.id, 0) < s.settings.command_cooldown_seconds:
            return
        s.last_command[user.id] = now
        if cmd.needs_ffmpeg:
            from ..services.media import ffmpeg_available

            if not ffmpeg_available():
                await reply(update, "⚠️ ffmpeg isn't installed on the server, so media tools are unavailable.")
                return
        try:
            await cmd.handler(update, context, user)
        except Exception as exc:
            log.exception("/%s failed", cmd.name)
            await reply(update, f"⚠️ Something went wrong: <code>{esc(str(exc)[:300])}</code>")

    wrapper.__name__ = f"cmd_{cmd.name}"
    return wrapper
