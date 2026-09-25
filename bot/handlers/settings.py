"""Per-user settings: an inline menu plus one command per setting."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

from ..db import User
from ..registry import command
from ..services.downloader import AUDIO_FORMATS, CONTAINERS, QUALITIES
from ..utils import esc
from .common import Ctx, arg_text, reply, svc

CHOICES: dict[str, tuple[Any, ...]] = {
    "quality": QUALITIES,
    "container": CONTAINERS,
    "audio_format": AUDIO_FORMATS,
    "audio_bitrate": (128, 160, 192, 256, 320),
    "delivery": ("auto", "telegram", "split", "link", "s3"),
    "caption": ("full", "minimal", "off"),
    "playlist_limit": (5, 10, 25, 50, 100),
}
TOGGLES = ("as_document", "subs", "embed_thumbnail", "embed_metadata")
LABELS = {
    "quality": "🎞 Quality",
    "container": "📦 Container",
    "audio_format": "🎵 Audio",
    "audio_bitrate": "🎚 Bitrate",
    "delivery": "🚚 Big files",
    "caption": "💬 Captions",
    "playlist_limit": "📜 Playlist max",
    "as_document": "📄 Send as file",
    "subs": "📝 Embed subtitles",
    "embed_thumbnail": "🖼 Embed cover",
    "embed_metadata": "🏷 Embed tags",
    "sub_lang": "🌐 Subtitle language",
    "timezone": "🕰 Time zone",
    "filename": "🏷 File names",
}


def _fmt(key: str, value: Any) -> str:
    if key in TOGGLES:
        return "on" if value else "off"
    if key == "quality" and str(value).isdigit():
        return f"{value}p"
    if key == "audio_bitrate":
        return f"{value}k"
    return str(value)


def settings_text(user: User) -> str:
    lines = ["⚙️ <b>Settings</b> · tap to change"]
    for key in (*CHOICES, *TOGGLES, "sub_lang", "timezone", "filename"):
        lines.append(f"{LABELS[key]}: <b>{esc(_fmt(key, user.pref(key)))}</b>")
    return "\n".join(lines)


def settings_keyboard(user: User) -> InlineKeyboardMarkup:
    buttons = [B(f"{LABELS[k]}: {_fmt(k, user.pref(k))}", callback_data=f"set:{k}") for k in (*CHOICES, *TOGGLES)]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([B("♻️ Reset all", callback_data="set:__reset"), B("✖ Close", callback_data="close")])
    return InlineKeyboardMarkup(rows)


def next_value(key: str, current: Any) -> Any:
    if key in TOGGLES:
        return not current
    options = CHOICES[key]
    normalized = [str(o) for o in options]
    idx = normalized.index(str(current)) if str(current) in normalized else -1
    return options[(idx + 1) % len(options)]


async def _set(update: Update, context: Ctx, user: User, key: str, value: Any) -> None:
    await svc(context).db.update_settings(user.id, **{key: value})
    await reply(update, f"✅ {LABELS[key]} set to <b>{esc(_fmt(key, value))}</b>.")


async def _choice(update: Update, context: Ctx, user: User, key: str, name: str) -> None:
    raw = arg_text(context).lower().rstrip("pk")
    options = CHOICES[key]
    match = next((o for o in options if str(o).lower() == raw), None)
    if match is None:
        await reply(
            update,
            f"{LABELS[key]} is <b>{esc(_fmt(key, user.pref(key)))}</b>.\n"
            f"Options: {', '.join(_fmt(key, o) for o in options)}\nExample: <code>/{name} "
            f"{esc(str(options[1]))}</code>",
        )
        return
    await _set(update, context, user, key, match)


async def _toggle(update: Update, context: Ctx, user: User, key: str) -> None:
    raw = arg_text(context).lower()
    if raw in ("on", "yes", "true", "1"):
        value = True
    elif raw in ("off", "no", "false", "0"):
        value = False
    else:
        value = not user.pref(key)
    await _set(update, context, user, key, value)


@command("settings", "settings", "Open the settings menu")
async def settings(update: Update, context: Ctx, user: User) -> None:
    await reply(update, settings_text(user), reply_markup=settings_keyboard(user))


@command("setquality", "settings", "Default video quality", "/setquality <best|1080|720|480|…|worst>")
async def setquality(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "quality", "setquality")


@command("setformat", "settings", "Video container: mp4, mkv or webm", "/setformat <mp4|mkv|webm>")
async def setformat(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "container", "setformat")


@command("setaudio", "settings", "Default audio format", "/setaudio <mp3|m4a|opus|flac|wav|aac|ogg>")
async def setaudio(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "audio_format", "setaudio")


@command("setbitrate", "settings", "Audio bitrate for MP3/M4A/Opus", "/setbitrate <128|160|192|256|320>")
async def setbitrate(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "audio_bitrate", "setbitrate")


@command(
    "setdelivery", "settings", "How to send files over the Telegram limit", "/setdelivery <auto|telegram|split|link|s3>"
)
async def setdelivery(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "delivery", "setdelivery")


@command("setcaption", "settings", "Caption style for sent files", "/setcaption <full|minimal|off>")
async def setcaption(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "caption", "setcaption")


@command("setplaylistlimit", "settings", "Default number of playlist items", "/setplaylistlimit <5|10|25|50|100>")
async def setplaylistlimit(update: Update, context: Ctx, user: User) -> None:
    await _choice(update, context, user, "playlist_limit", "setplaylistlimit")


@command("setdoc", "settings", "Always send files uncompressed as documents", "/setdoc [on|off]")
async def setdoc(update: Update, context: Ctx, user: User) -> None:
    await _toggle(update, context, user, "as_document")


@command("setsubs", "settings", "Embed subtitles into downloaded videos", "/setsubs [on|off]")
async def setsubs(update: Update, context: Ctx, user: User) -> None:
    await _toggle(update, context, user, "subs")


@command("setthumb", "settings", "Embed the cover image into files", "/setthumb [on|off]")
async def setthumb(update: Update, context: Ctx, user: User) -> None:
    await _toggle(update, context, user, "embed_thumbnail")


@command("setmeta", "settings", "Embed title/artist tags and chapters", "/setmeta [on|off]")
async def setmeta(update: Update, context: Ctx, user: User) -> None:
    await _toggle(update, context, user, "embed_metadata")


@command("setsublang", "settings", "Preferred subtitle language code", "/setsublang <en|es|de|fr|…|all>")
async def setsublang(update: Update, context: Ctx, user: User) -> None:
    lang = arg_text(context).lower()
    if not lang or not (lang == "all" or (2 <= len(lang) <= 8 and lang.replace("-", "").isalpha())):
        await reply(
            update, f"Subtitle language is <b>{esc(user.pref('sub_lang'))}</b>. Example: <code>/setsublang es</code>"
        )
        return
    await _set(update, context, user, "sub_lang", lang)


@command("settz", "settings", "Your time zone (for schedules and dates)", "/settz <Europe/Berlin|Asia/Kolkata|…>")
async def settz(update: Update, context: Ctx, user: User) -> None:
    tz = arg_text(context)
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        await reply(
            update,
            f"Time zone is <b>{esc(user.pref('timezone'))}</b>. Use an IANA name like <code>/settz Asia/Kolkata</code>",
        )
        return
    await _set(update, context, user, "timezone", tz)


@command(
    "setname", "settings", "File name template (yt-dlp fields)", "/setname <template>  e.g. %(uploader)s - %(title)s"
)
async def setname(update: Update, context: Ctx, user: User) -> None:
    template = arg_text(context)
    if not template or "%(" not in template or "/" in template or ".." in template:
        await reply(
            update,
            f"Current: <code>{esc(user.pref('filename'))}</code>\nExample: "
            "<code>/setname %(uploader)s - %(title).60B</code>\nFields: title, uploader, id, upload_date, "
            "resolution, ext…",
        )
        return
    await _set(update, context, user, "filename", template[:120])


@command("reset", "settings", "Restore all settings to defaults")
async def reset(update: Update, context: Ctx, user: User) -> None:
    await svc(context).db.reset_settings(user.id)
    await reply(update, "♻️ Settings restored to defaults.")


async def on_settings_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    key = (query.data or "").split(":", 1)[1]
    s = svc(context)
    if key == "open":
        await query.answer()
        await context.bot.send_message(
            query.message.chat_id, settings_text(user), parse_mode=ParseMode.HTML, reply_markup=settings_keyboard(user)
        )
        return
    if key == "__reset":
        await s.db.reset_settings(user.id)
        await query.answer("Settings reset")
    elif key in CHOICES or key in TOGGLES:
        value = next_value(key, user.pref(key))
        if key == "delivery" and value == "link" and not s.settings.links_enabled:
            value = next_value(key, value)
        if key == "delivery" and value == "s3" and not s.settings.s3_enabled:
            value = next_value(key, value)
        await s.db.update_settings(user.id, **{key: value})
        await query.answer(f"{LABELS[key]}: {_fmt(key, value)}")
    else:
        await query.answer()
        return
    fresh = await s.db.get_user(user.id)
    try:
        await query.message.edit_text(
            settings_text(fresh), parse_mode=ParseMode.HTML, reply_markup=settings_keyboard(fresh)
        )
    except BadRequest:
        pass
