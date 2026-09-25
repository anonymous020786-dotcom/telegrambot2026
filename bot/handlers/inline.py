"""Inline mode: type @bot <link or search words> in any chat to post a video or audio there.

Telegram can only put a file into an inline message if it already has it (a file_id). Cached downloads are
offered as real files and post instantly. Anything else posts a "⏳ Downloading…" placeholder; the bot downloads
it through the normal queue into the user's private chat (which gives it a file_id) and then swaps the
placeholder for the media. The download starts when the result is chosen (needs inline feedback enabled in
@BotFather) or when the placeholder's "Start download" button is pressed, so it works either way.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from telegram import InlineKeyboardButton as B
from telegram import (
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedVideo,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..db import User
from ..registry import command
from ..services.downloader import Preset
from ..services.jobs import Job, PolicyBlocked, QuotaExceeded
from ..utils import cache_key, domain_of, esc, extract_urls, human_duration, truncate
from .common import Ctx, is_allowed, preset_for, reply, svc

MAX_TOKENS = 5000
TOKEN_TTL = 6 * 3600
SEARCH_MIN_CHARS = 3
MODES = {"v": "🎬 Video", "a": "🎵 Audio"}


@dataclass
class InlineTarget:
    url: str
    mode: str  # v | a
    title: str
    user_id: int
    created: float
    started: bool = False


def _targets(context: Ctx) -> dict[str, InlineTarget]:
    return context.application.bot_data.setdefault("inline_targets", {})


def remember(context: Ctx, url: str, mode: str, title: str, user_id: int) -> str:
    """Result ids are limited to 64 bytes, so long URLs are kept here under a short token."""
    targets = _targets(context)
    now = time.time()
    if len(targets) >= MAX_TOKENS:
        for key in [k for k, t in targets.items() if now - t.created > TOKEN_TTL] or list(targets)[: MAX_TOKENS // 5]:
            targets.pop(key, None)
    token = secrets.token_urlsafe(9)
    targets[token] = InlineTarget(url, mode, title, user_id, now)
    return token


def inline_preset(user: User, mode: str) -> Preset:
    return preset_for(user, mode="audio") if mode == "a" else preset_for(user)


def _placeholder(token: str, url: str, title: str, mode: str) -> tuple[InputTextMessageContent, InlineKeyboardMarkup]:
    text = f"⏳ <b>Downloading {MODES[mode].split()[1].lower()}…</b>\n{esc(truncate(title, 90))}"
    markup = InlineKeyboardMarkup([[B("▶️ Start download", callback_data=f"inl:{token}"), B("🔗 Source", url=url)]])
    return InputTextMessageContent(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True), markup


async def _results_for_url(context: Ctx, user: User, url: str) -> list:
    s = svc(context)
    if reason := await s.policy.refusal(url):
        return [
            InlineQueryResultArticle(
                id="refused",
                title="Can't download this link",
                description=truncate(reason, 120),
                input_message_content=InputTextMessageContent(esc(reason), parse_mode=ParseMode.HTML),
            )
        ]
    results = []
    title = domain_of(url)
    for mode, label in MODES.items():
        preset = inline_preset(user, mode)
        cached = await s.db.cache_get(cache_key(url, preset.to_dict()))
        token = remember(context, url, mode, cached["title"] if cached else title, user.id)
        if cached:
            caption = s.jobs.caption(user, cached["title"], url, f"{preset.label()} · ⚡ cached")
            common = {"id": f"c:{token}", "caption": caption, "parse_mode": ParseMode.HTML}
            if cached["kind"] == "video":
                results.append(
                    InlineQueryResultCachedVideo(
                        video_file_id=cached["file_id"], title=cached["title"] or label, **common
                    )
                )
                continue
            if cached["kind"] == "audio":
                results.append(InlineQueryResultCachedAudio(audio_file_id=cached["file_id"], **common))
                continue
            if cached["kind"] == "document":
                results.append(
                    InlineQueryResultCachedDocument(
                        document_file_id=cached["file_id"], title=cached["title"] or label, **common
                    )
                )
                continue
        content, markup = _placeholder(token, url, title, mode)
        results.append(
            InlineQueryResultArticle(
                id=f"{mode}:{token}",
                title=f"{label} · {preset.label()}",
                description=f"Download and post here · {truncate(url, 60)}",
                input_message_content=content,
                reply_markup=markup,
            )
        )
    return results


async def _results_for_search(context: Ctx, user: User, query: str) -> list:
    s = svc(context)
    try:
        found = await s.downloader.search(query, 8, "yt")
    except Exception:  # noqa: BLE001 - a failed search just shows nothing
        return []
    results = []
    for item in found:
        url, title = item.get("url"), item.get("title") or "Untitled"
        if not url or await s.policy.refusal(url):
            continue
        token = remember(context, url, "v", title, user.id)
        content, markup = _placeholder(token, url, title, "v")
        details = " · ".join(
            x for x in (item.get("uploader"), human_duration(item["duration"]) if item.get("duration") else None) if x
        )
        results.append(
            InlineQueryResultArticle(
                id=f"v:{token}",
                title=truncate(title, 100),
                description=details or url,
                thumbnail_url=item.get("thumbnail"),
                input_message_content=content,
                reply_markup=markup,
            )
        )
    return results


async def _inline_user(update: Update, context: Ctx) -> User | None:
    s = svc(context)
    tg = update.effective_user
    if tg is None:
        return None
    user = await s.db.upsert_user(tg.id, tg.username, tg.first_name)
    if tg.id in s.settings.admin_ids and not user.is_admin:
        await s.db.set_role(tg.id, "admin")
        user.role = "admin"
    if not is_allowed(s, user):  # also covers bans
        return None
    if not user.is_admin and await s.db.get_kv("maintenance") == "1":
        return None
    return user


async def on_inline_query(update: Update, context: Ctx) -> None:
    query = update.inline_query
    user = await _inline_user(update, context)
    if user is None:
        await query.answer(
            [],
            cache_time=60,
            is_personal=True,
            button=InlineQueryResultsButton("🔒 Private bot · open it", start_parameter="inline"),
        )
        return
    text = (query.query or "").strip()
    urls = extract_urls(text)
    if urls:
        results = await _results_for_url(context, user, urls[0])
    elif len(text) >= SEARCH_MIN_CHARS:
        results = await _results_for_search(context, user, text)
    else:
        results = []
    await query.answer(
        results,
        cache_time=5,
        is_personal=True,
        button=None
        if results
        else InlineQueryResultsButton("Paste a link or type to search", start_parameter="inline"),
    )


async def start_inline_download(context: Ctx, user: User, token: str, inline_message_id: str) -> str | None:
    """Queue the download behind an inline placeholder. Returns a problem to show, or None when queued."""
    s = svc(context)
    target = _targets(context).get(token)
    if target is None:
        return "This result expired (the bot restarted). Type the link again."
    if target.user_id != user.id:
        return "Only the person who posted this can start it."
    if target.started:
        return "Already downloading. The file appears here when it's ready."
    if s.jobs.paused_all and not user.is_admin:
        return "Downloads are paused by the admin right now."
    job = Job(
        user_id=user.id,
        chat_id=user.id,  # delivered privately first; that gives the file_id the inline message needs
        url=target.url,
        preset=inline_preset(user, target.mode),
        options={"inline_message_id": inline_message_id},
        title=target.title if target.title != domain_of(target.url) else None,
        priority=1 if user.is_admin else 5,
    )
    try:
        await s.jobs.submit(job, user)
    except PolicyBlocked as exc:
        return str(exc)
    except QuotaExceeded:
        return "You've reached your daily download limit."
    target.started = True
    return None


async def _show_problem(context: Ctx, inline_message_id: str, problem: str) -> None:
    try:
        await context.bot.edit_message_text(
            f"⚠️ {esc(problem)}", inline_message_id=inline_message_id, parse_mode=ParseMode.HTML
        )
    except TelegramError:
        pass


async def on_chosen_inline_result(update: Update, context: Ctx) -> None:
    chosen = update.chosen_inline_result
    mode, _, token = chosen.result_id.partition(":")
    if mode not in MODES or not chosen.inline_message_id:
        return  # cached files were posted as-is; nothing to do
    user = await _inline_user(update, context)
    if user is None:
        return
    problem = await start_inline_download(context, user, token, chosen.inline_message_id)
    if problem and not problem.startswith("Already"):
        await _show_problem(context, chosen.inline_message_id, problem)


async def on_inline_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    token = (query.data or "").split(":", 1)[1]
    if not query.inline_message_id:
        await query.answer()
        return
    problem = await start_inline_download(context, user, token, query.inline_message_id)
    await query.answer(problem or "⬇️ Download started. It appears here when ready.", show_alert=bool(problem))


@command("inline", "search", "Post videos or audio into any chat: type @bot <link or words>")
async def inline_help(update: Update, context: Ctx, user: User) -> None:
    name = context.bot.username or "this_bot"
    await reply(
        update,
        f"💬 <b>Inline mode</b>: works in any chat, group or channel, without adding the bot there.\n\n"
        f"• <code>@{esc(name)} https://…</code> → pick 🎬 Video or 🎵 Audio\n"
        f"• <code>@{esc(name)} lofi hip hop</code> → pick a YouTube result\n\n"
        "Files you've downloaded before post instantly (⚡). Others show “⏳ Downloading…”, and the file replaces "
        "it when ready (you also get a copy here). Your quality and format settings apply.\n\n"
        "<i>Admins: enable it once in @BotFather with /setinline, and /setinlinefeedback (100%) so downloads "
        "start the moment a result is picked.</i>",
        reply_markup=InlineKeyboardMarkup(
            [[B("Try it here", switch_inline_query_current_chat=""), B("Try in another chat", switch_inline_query="")]]
        ),
    )
