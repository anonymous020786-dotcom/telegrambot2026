"""Application wiring: handlers, background jobs, startup/shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
import time
from logging.handlers import RotatingFileHandler

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings, get_settings
from .context import Services
from .db import Database
from .registry import REGISTRY, menu_commands
from .services.delivery import Delivery
from .services.downloader import Downloader
from .services.images import ImageService
from .services.jobs import JobManager
from .services.linkserver import LinkServer
from .services.policy import Policy

log = logging.getLogger("bot")


def load_handlers() -> None:
    """Import handler modules so their @command decorators register."""
    from .handlers import (  # noqa: F401
        admin,
        content,
        download,
        general,
        images,
        library,
        settings,
        tools,
        utilities,
        watch,
    )


def setup_logging(settings: Settings) -> None:
    settings.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(
        settings.log_dir / "bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file_handler)
    for noisy in ("httpx", "httpcore", "telegram.ext.Application", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_services(settings: Settings) -> Services:
    db = Database(settings.db_path)
    links = None
    if settings.links_enabled:
        links = LinkServer(
            settings.download_dir,
            settings.link_secret,
            settings.link_base_url or "",
            settings.link_host,
            settings.link_port,
            settings.link_ttl_hours,
        )
    s3 = None
    if settings.s3_enabled:
        from .services.s3 import S3Store

        s3 = S3Store(settings)
    downloader = Downloader(settings)
    images = ImageService(settings)
    delivery = Delivery(settings, links, s3)
    policy = Policy(db, settings)
    jobs = JobManager(settings, db, downloader, images, delivery, policy)
    return Services(settings, db, downloader, images, delivery, jobs, links, policy=policy)


# ------------------------------------------------------------------ non-command updates


async def _as_user(update: Update, context: ContextTypes.DEFAULT_TYPE, public: bool = False):
    """Resolve the DB user and enforce access for non-command updates. Returns None if denied."""
    from .handlers.common import access_denied, is_allowed

    s: Services = context.application.bot_data["svc"]
    tg = update.effective_user
    if tg is None:
        return None
    user = await s.db.upsert_user(tg.id, tg.username, tg.first_name)
    if tg.id in s.settings.admin_ids and not user.is_admin:
        await s.db.set_role(tg.id, "admin")
        user.role = "admin"
    if user.is_banned:
        if update.callback_query:
            await update.callback_query.answer("⛔ You are banned", show_alert=True)
        return None
    if not public and not is_allowed(s, user):
        if update.callback_query:
            await update.callback_query.answer("🔒 Private bot", show_alert=True)
        else:
            await access_denied(update, context, user)
        return None
    if not user.is_admin and not public and await s.db.get_kv("maintenance") == "1":
        if update.callback_query:
            await update.callback_query.answer("🛠 Under maintenance", show_alert=True)
        return None
    return user


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .handlers.download import on_link_message

    user = await _as_user(update, context)
    if user:
        await on_link_message(update, context, user)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Media with a /command caption runs that command; otherwise offer quick tools."""
    from .handlers.common import guard
    from .handlers.tools import on_media_message

    msg = update.effective_message
    caption = (msg.caption or "").strip()
    if caption.startswith("/"):
        parts = shlex.split(caption[1:]) if caption.count('"') % 2 == 0 else caption[1:].split()
        name = parts[0].split("@")[0].lower() if parts else ""
        cmd = REGISTRY.get(name)
        if cmd:
            context.args = parts[1:]
            await guard(cmd)(update, context)
            return
    if msg.chat.type != ChatType.PRIVATE:
        return
    user = await _as_user(update, context)
    if user:
        await on_media_message(update, context, user)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .handlers import admin, content, download, general, library, settings, tools

    query = update.callback_query
    data = query.data or ""
    prefix = data.split(":", 1)[0]
    if data == "noop":
        await query.answer()
        return
    if data == "close":
        await query.answer()
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return
    user = await _as_user(update, context, public=prefix == "access")
    if user is None:
        return
    s: Services = context.application.bot_data["svc"]
    if prefix in ("dl", "qual", "fmts", "fmt", "card", "info", "list", "pick", "img", "img1", "multi"):
        await download.on_card_button(update, context, user)
    elif prefix in ("hist", "favs", "clearh", "job", "res", "redo", "favt"):
        await library.on_library_button(update, context, user)
    elif prefix == "set":
        await settings.on_settings_button(update, context, user)
    elif prefix == "tool":
        await tools.on_tool_button(update, context, user)
    elif prefix == "adm":
        await admin.on_admin_button(update, context, user)
    elif prefix == "access":
        await admin.on_access_button(update, context, user)
    elif prefix == "adult":
        await content.on_adult_button(update, context, user)
    elif prefix == "help":
        group = data.split(":", 1)[1]
        await query.answer()
        if group == "all":
            text, markup = general.help_text(None, user.is_admin), general.help_groups_keyboard(user.is_admin)
        else:
            text, markup = general.help_text(group, user.is_admin), general.help_groups_keyboard(user.is_admin)
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramError:
            await context.bot.send_message(query.message.chat_id, text, parse_mode="HTML", reply_markup=markup)
    elif prefix == "menu":
        what = data.split(":", 1)[1]
        await query.answer()
        if what == "queue":
            jobs = s.jobs.user_jobs(user.id)
            text, markup = library.queue_text(jobs, s), library.queue_keyboard(jobs)
            try:
                await query.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
            except TelegramError:
                await context.bot.send_message(query.message.chat_id, text, parse_mode="HTML", reply_markup=markup)
        elif what == "me":
            used, _ = await s.db.usage_today(user.id)
            left = await s.jobs.remaining_quota(user)
            await context.bot.send_message(
                query.message.chat_id,
                f"👤 {user.display} · today {used} downloads · left: {'unlimited' if left is None else left}",
            )
        elif what == "admin" and user.is_admin:
            await context.bot.send_message(query.message.chat_id, "🛡 Admin panel", reply_markup=admin.admin_panel())
    else:
        await query.answer()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error: %s", context.error, exc_info=context.error)


# ------------------------------------------------------------------ background jobs


async def heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    s: Services = context.application.bot_data["svc"]
    (s.settings.data_dir / "heartbeat").write_text(str(int(time.time())))


async def cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    s: Services = context.application.bot_data["svc"]
    removed, freed = await asyncio.to_thread(s.jobs.cleanup_old_files)
    if removed:
        log.info("cleanup removed %s folders (%s bytes)", removed, freed)


async def set_menus(app: Application, settings: Settings) -> None:
    def cmds(admin: bool) -> list[BotCommand]:
        return [BotCommand(c.name, c.description[:256]) for c in menu_commands(admin=admin)]

    try:
        await app.bot.set_my_commands(cmds(False), scope=BotCommandScopeDefault())
        for admin_id in settings.admin_ids:
            try:
                await app.bot.set_my_commands(cmds(True), scope=BotCommandScopeChat(admin_id))
            except TelegramError:
                pass  # admin hasn't started the bot yet
    except TelegramError as exc:
        log.warning("could not set command menu: %s", exc)


async def post_init(app: Application) -> None:
    from .services.watcher import check_all_watches, restore_schedules

    s: Services = app.bot_data["svc"]
    await s.db.connect()
    for admin_id in s.settings.admin_ids:
        await s.db.ensure_user(admin_id)
        await s.db.set_role(admin_id, "admin")
    for uid in s.settings.allowed_user_ids:
        await s.db.set_allowed(uid, True)
    s.jobs.start(app.bot)
    if s.links:
        await s.links.start()
    jq = app.job_queue
    jq.run_repeating(heartbeat, interval=30, first=1, name="heartbeat")
    jq.run_repeating(cleanup_job, interval=1800, first=300, name="cleanup")
    jq.run_repeating(check_all_watches, interval=s.settings.watch_interval_minutes * 60, first=120, name="watches")
    restored = await restore_schedules(app)
    resumed = await s.jobs.restore()
    await set_menus(app, s.settings)
    me = await app.bot.get_me()
    log.info(
        "Bot @%s ready · %d commands · %d schedules restored · %d downloads resumed · upload limit %d MB",
        me.username,
        len(REGISTRY),
        restored,
        resumed,
        s.settings.upload_limit_bytes // (1024 * 1024),
    )


async def post_shutdown(app: Application) -> None:
    s: Services = app.bot_data["svc"]
    await s.jobs.stop()
    if s.links:
        await s.links.stop()
    await s.db.close()


def build_application(settings: Settings) -> Application:
    load_handlers()
    from .handlers.common import guard

    builder: ApplicationBuilder = (
        Application.builder()
        .token(settings.bot_token)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .read_timeout(60)
        .write_timeout(600)
        .media_write_timeout(900)
    )
    if settings.bot_api_base_url:
        builder = builder.base_url(settings.bot_api_base_url).local_mode(settings.bot_api_local_mode)
        if settings.bot_api_base_file_url:
            builder = builder.base_file_url(settings.bot_api_base_file_url)
    app = builder.build()
    app.bot_data["svc"] = build_services(settings)

    for cmd in REGISTRY.values():
        app.add_handler(CommandHandler(cmd.name, guard(cmd)))
    app.add_handler(CallbackQueryHandler(on_callback))
    media_filter = (
        filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.PHOTO
        | filters.Document.ALL
        | filters.ANIMATION
        | filters.VIDEO_NOTE
    )
    app.add_handler(MessageHandler(media_filter, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Entity("url") & ~filters.ChatType.PRIVATE, on_text)
    )
    app.add_error_handler(on_error)
    return app


def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    if not settings.admin_ids and not settings.public_mode:
        log.warning("ADMIN_IDS is empty: nobody can use this private bot. Set ADMIN_IDS to your Telegram ID.")
    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    if app.bot_data.get("restart"):
        log.info("Restarting…")
        os.execv(sys.executable, [sys.executable, "-m", "bot"])
