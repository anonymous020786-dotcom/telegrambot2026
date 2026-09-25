"""General and account commands."""

from __future__ import annotations

import platform
import time

import telegram
import yt_dlp
from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, Update

from .. import __version__
from ..db import User
from ..registry import GROUPS, REGISTRY, by_group, command
from ..services.downloader import adult_extractors, extractor_names, supported_extractor
from ..utils import ago, domain_of, esc, fmt_ts, human_duration, human_size
from .common import Ctx, arg_text, is_allowed, reply, svc, url_from, usage

WELCOME = (
    "👋 <b>Hi {name}!</b>\n\n"
    "Send me a link from almost any website and I'll download the <b>video, audio or images</b> in the "
    "quality you choose.\n\n"
    "• Paste a link to get a preview with buttons\n"
    "• <code>/mp3 URL</code> for audio, <code>/fhd URL</code> for 1080p\n"
    "• <code>/images URL</code> grabs every picture on a page\n"
    "• Reply to a file with <code>/compress</code>, <code>/convert mp4</code>, <code>/togif</code>…\n\n"
    "Tap a section below or send /help."
)


def main_menu(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [B("⬇️ Download", callback_data="help:download"), B("🎵 Audio", callback_data="help:audio")],
        [B("🖼 Images", callback_data="help:images"), B("🛠 Tools", callback_data="help:tools")],
        [B("📋 Queue", callback_data="menu:queue"), B("📚 History", callback_data="hist:0")],
        [B("⚙️ Settings", callback_data="set:open"), B("👤 My stats", callback_data="menu:me")],
        [B("❓ All commands", callback_data="help:all")],
    ]
    if admin:
        rows.append([B("🛡 Admin panel", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def help_groups_keyboard(admin: bool) -> InlineKeyboardMarkup:
    groups = list(by_group(include_admin=admin))
    rows, row = [], []
    for g in groups:
        row.append(B(GROUPS[g], callback_data=f"help:{g}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def help_text(group: str | None, admin: bool) -> str:
    groups = by_group(include_admin=admin)
    if group and group in groups:
        lines = [f"<b>{GROUPS[group]}</b>\n"]
        for cmd in groups[group]:
            lines.append(f"/{cmd.name} · {esc(cmd.description)}")
        return "\n".join(lines)
    total = sum(len(v) for v in groups.values())
    return (
        f"📖 <b>Help</b> · {total} commands\n\nPick a section, or send <code>/help command</code> for details "
        "about one command. Tip: just paste a link to get started."
    )


@command("start", "general", "Welcome message and main menu", public=True)
async def start(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    if context.args and context.args[0].startswith("inv_"):
        code = context.args[0][4:]
        if await s.db.redeem_invite(code, user.id):
            user.allowed = True
            await reply(update, "🎉 Invite accepted, you now have access!")
    if not is_allowed(s, user):
        from .common import access_denied

        await access_denied(update, context, user)
        return
    await reply(update, WELCOME.format(name=esc(user.first_name or "there")), reply_markup=main_menu(user.is_admin))


@command("help", "general", "Browse commands by section, or /help <command>", "/help [command]", public=True)
async def help_cmd(update: Update, context: Ctx, user: User) -> None:
    name = arg_text(context).lstrip("/").lower()
    if name:
        cmd = REGISTRY.get(name)
        if not cmd or (cmd.admin and not user.is_admin):
            await reply(update, f"Unknown command /{esc(name)}. Send /commands for the full list.")
            return
        await reply(
            update,
            f"<b>/{cmd.name}</b> · {GROUPS[cmd.group]}\n{esc(cmd.description)}\n\nUsage: <code>{esc(cmd.usage)}</code>",
        )
        return
    await reply(update, help_text(None, user.is_admin), reply_markup=help_groups_keyboard(user.is_admin))


@command("menu", "general", "Main menu with quick actions")
async def menu(update: Update, context: Ctx, user: User) -> None:
    await reply(update, "🏠 <b>Main menu</b>", reply_markup=main_menu(user.is_admin))


@command("about", "general", "What this bot can do", public=True)
async def about(update: Update, context: Ctx, user: User) -> None:
    n = len([c for c in REGISTRY.values() if not c.admin])
    await reply(
        update,
        (
            "🤖 <b>Media Downloader Bot</b>\n"
            f"A private bot with {n} commands for downloading public videos, audio and images from "
            "thousands of websites, plus editing tools, a download queue, history, subscriptions and scheduling.\n\n"
            "It only downloads publicly accessible media and does not bypass DRM or paywalls. "
            "Please respect copyright and each site's terms."
        ),
    )


@command("ping", "general", "Check the bot is alive and how fast it answers", public=True)
async def ping(update: Update, context: Ctx, user: User) -> None:
    t0 = time.perf_counter()
    msg = await reply(update, "🏓 Pong…")
    if msg:
        await msg.edit_text(
            f"🏓 Pong! {int((time.perf_counter() - t0) * 1000)} ms · up "
            f"{human_duration(time.time() - svc(context).started_at)}"
        )


@command("version", "general", "Versions of the bot and its engines")
async def version(update: Update, context: Ctx, user: User) -> None:
    import gallery_dl.version

    await reply(
        update,
        (
            f"🤖 Bot <code>{__version__}</code>\n"
            f"📥 yt-dlp <code>{yt_dlp.version.__version__}</code>\n"
            f"🖼 gallery-dl <code>{gallery_dl.version.__version__}</code>\n"
            f"🐍 Python <code>{platform.python_version()}</code> · PTB <code>{telegram.__version__}</code>"
        ),
    )


@command("commands", "general", "Full list of every command")
async def commands_cmd(update: Update, context: Ctx, user: User) -> None:
    chunks, current = [], ""
    for group, cmds in by_group(include_admin=user.is_admin).items():
        block = f"\n<b>{GROUPS[group]}</b>\n" + "\n".join(f"/{c.name} · {esc(c.description)}" for c in cmds) + "\n"
        if len(current) + len(block) > 3800:
            chunks.append(current)
            current = ""
        current += block
    chunks.append(current)
    for chunk in chunks:
        await reply(update, chunk.strip())


@command("privacy", "general", "What data the bot stores", public=True)
async def privacy(update: Update, context: Ctx, user: User) -> None:
    hours = svc(context).settings.file_retention_hours
    await reply(
        update,
        (
            "🔐 <b>Privacy</b>\n"
            "• Stored: your Telegram ID, name, settings, download history and daily usage counts.\n"
            f"• Downloaded files are deleted after delivery (large-file links after {hours:g} h).\n"
            "• Nothing is shared with third parties. Use /clearhistory to wipe your history."
        ),
    )


@command("feedback", "general", "Send a message to the bot owner", "/feedback <message>")
async def feedback(update: Update, context: Ctx, user: User) -> None:
    text = arg_text(context)
    if not text:
        await reply(update, usage("feedback"))
        return
    s = svc(context)
    await s.db.add_feedback(user.id, text[:2000])
    for admin in s.settings.admin_ids:
        try:
            await context.bot.send_message(
                admin,
                f"💬 Feedback from {esc(user.display)} (<code>{user.id}</code>):\n{esc(text[:2000])}",
                parse_mode="HTML",
            )
        except telegram.error.TelegramError:
            pass
    await reply(update, "🙏 Thanks! Your message was sent to the owner.")


@command("id", "general", "Show your Telegram user ID and this chat's ID", public=True)
async def my_id(update: Update, context: Ctx, user: User) -> None:
    chat = update.effective_chat
    await reply(update, f"🆔 Your ID: <code>{user.id}</code>\n💬 Chat ID: <code>{chat.id}</code> ({chat.type})")


# ------------------------------------------------------------------ account


@command("me", "account", "Your profile, role and settings summary")
async def me(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    used, used_bytes = await s.db.usage_today(user.id)
    left = await s.jobs.remaining_quota(user)
    await reply(
        update,
        (
            f"👤 <b>{esc(user.display)}</b> (<code>{user.id}</code>)\n"
            f"Role: {'🛡 admin' if user.is_admin else 'user'}\n"
            f"Member since: {fmt_ts(user.created_at, user.pref('timezone'))}\n"
            f"Today: {used} downloads · {human_size(used_bytes)}\n"
            f"Remaining today: {'unlimited' if left is None else left}\n"
            f"Default quality: {user.pref('quality')} · audio: {user.pref('audio_format')} · "
            f"delivery: {user.pref('delivery')}"
        ),
    )


@command("stats", "account", "Your download statistics")
async def stats(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    total, total_bytes = await s.db.usage_totals(user.id)
    today_count, today_bytes = await s.db.usage_today(user.id)
    favs = await s.db.count_history(user.id, favorites=True)
    history = await s.db.count_history(user.id)
    await reply(
        update,
        (
            "📊 <b>Your stats</b>\n"
            f"All time: {total} downloads · {human_size(total_bytes)}\n"
            f"Today: {today_count} · {human_size(today_bytes)}\n"
            f"History items: {history} · favorites: {favs}\n"
            f"Active jobs: {len(s.jobs.user_jobs(user.id))}"
        ),
    )


@command("quota", "account", "How many downloads you have left today")
async def quota(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    left = await s.jobs.remaining_quota(user)
    used, _ = await s.db.usage_today(user.id)
    if left is None:
        await reply(update, f"♾ Unlimited downloads. Used today: {used}.")
    else:
        await reply(update, f"🚦 {left} downloads left today (used {used}). Resets at 00:00 UTC.")


@command("limits", "account", "Bot limits: file sizes, queue, playlists")
async def limits(update: Update, context: Ctx, user: User) -> None:
    st = svc(context).settings
    await reply(
        update,
        (
            "📏 <b>Limits</b>\n"
            f"Telegram upload limit: {human_size(st.upload_limit_bytes)}\n"
            f"Max download size: {human_size(st.max_download_mb * 1024 * 1024)}\n"
            f"Parallel jobs: {st.max_concurrent_jobs} (you: {st.per_user_concurrent_jobs})\n"
            f"Playlist items per request: {st.max_playlist_items}\n"
            f"Images per request: {st.max_images_per_request}\n"
            f"Daily downloads: {st.daily_limit or 'unlimited'}\n"
            "Bigger files are split, or sent as a download link when link/S3 delivery is configured."
        ),
    )


@command("sites", "account", "Search the list of 1,800+ supported sites", "/sites [name]")
async def sites(update: Update, context: Ctx, user: User) -> None:
    names = extractor_names()
    query = arg_text(context).lower()
    if query == "adult":
        s = svc(context)
        if not await s.policy.adult_allowed(user):
            await reply(
                update, "🔞 Adult sites are hidden. If the admin allows it, adults can turn them on with /setadult."
            )
            return
        adult = adult_extractors()
        await reply(
            update,
            f"🔞 {len(adult)} adult sites have dedicated support:\n{esc(', '.join(adult))}\n\n"
            "Other sites often work too: the bot looks for the video stream in the page itself.",
        )
        return
    if query:
        hits = [n for n in names if query in n.lower()]
        body = ", ".join(hits[:120]) or "No dedicated extractor, but /dl may still work via the generic extractor."
        await reply(update, f"🔎 Sites matching “{esc(query)}” ({len(hits)}):\n{esc(body)}")
    else:
        await reply(
            update,
            f"🌐 {len(names)} sites have dedicated support, and any page with an embedded video or "
            "direct media link works too.\nSearch with <code>/sites youtube</code> (or <code>/sites adult</code>).",
        )


@command("supported", "account", "Check if a link is supported", "/supported <url>")
async def supported(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await reply(update, usage("supported"))
        return
    name = supported_extractor(url)
    if name:
        await reply(update, f"✅ <b>{esc(domain_of(url))}</b> is supported (extractor: <code>{esc(name)}</code>).")
    else:
        await reply(
            update,
            f"🟡 No dedicated extractor for <b>{esc(domain_of(url))}</b>. The generic extractor will "
            "try embedded videos, and /images works on any page.",
        )


@command("redeem", "account", "Activate access with an invite code", "/redeem <code>", public=True)
async def redeem(update: Update, context: Ctx, user: User) -> None:
    code = arg_text(context)
    if not code:
        await reply(update, usage("redeem"))
        return
    if await svc(context).db.redeem_invite(code, user.id):
        await reply(update, "🎉 Welcome! You now have access. Send /start to begin.")
    else:
        await reply(update, "❌ That invite code is invalid, used up or expired.")


@command("request", "account", "Ask the admins for access", public=True)
async def request_access(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    if is_allowed(s, user):
        await reply(update, "✅ You already have access.")
        return
    if not s.settings.access_requests:
        await reply(update, "Access requests are turned off. Ask the owner for an invite code.")
        return
    await send_access_request(context, user)
    await reply(update, "📨 Request sent! You'll get a message when an admin approves it.")


async def send_access_request(context: Ctx, user: User) -> None:
    s = svc(context)
    last = float(await s.db.get_kv(f"req:{user.id}", "0") or 0)
    if time.time() - last < 3600:
        return
    await s.db.set_kv(f"req:{user.id}", str(time.time()))
    markup = InlineKeyboardMarkup(
        [
            [
                B("✅ Approve", callback_data=f"access:approve:{user.id}"),
                B("⛔ Deny", callback_data=f"access:deny:{user.id}"),
            ]
        ]
    )
    for admin in s.settings.admin_ids:
        try:
            await context.bot.send_message(
                admin,
                f"🙋 Access request from {esc(user.display)} (<code>{user.id}</code>), "
                f"first seen {ago(user.created_at)}",
                parse_mode="HTML",
                reply_markup=markup,
            )
        except telegram.error.TelegramError:
            pass
