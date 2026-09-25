"""Queue control, history and favorites."""

from __future__ import annotations

import csv
import io
import json

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

from ..db import User
from ..registry import command
from ..services.downloader import Preset
from ..services.jobs import Job, progress_text
from ..utils import ago, domain_of, esc, fmt_ts, human_size, truncate
from .common import Ctx, arg_text, enqueue, paginate_keyboard, reply, svc, usage

PAGE = 8
STATUS_ICON = {"done": "✅", "failed": "❌", "cancelled": "✖"}


# ------------------------------------------------------------------ queue


def queue_text(jobs: list[Job], s) -> str:
    if not jobs:
        return "📭 Your queue is empty. Send a link to start a download."
    lines = [f"📋 <b>Your queue</b> · {len(jobs)} active"]
    for j in jobs:
        if j.status == "running":
            pct = f" {j.fraction * 100:.0f}%" if j.total else ""
            lines.append(f"▶️ {j.describe()} · {j.phase}{pct}")
        elif j.status == "held":
            lines.append(f"⏸ {j.describe()}")
        else:
            lines.append(f"🕒 {j.describe()} · #{s.jobs.position(j)} in line")
    return "\n".join(lines)


def queue_keyboard(jobs: list[Job]) -> InlineKeyboardMarkup | None:
    if not jobs:
        return None
    rows = [
        [B(f"✖ {j.id}", callback_data=f"job:cancel:{j.id}") for j in jobs[i : i + 4]]
        for i in range(0, min(len(jobs), 8), 4)
    ]
    rows.append([B("🔄 Refresh", callback_data="menu:queue"), B("✖ Cancel all", callback_data="job:cancelall")])
    return InlineKeyboardMarkup(rows)


@command("queue", "queue", "Your active and waiting downloads")
async def queue(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    jobs = s.jobs.user_jobs(user.id)
    await reply(update, queue_text(jobs, s), reply_markup=queue_keyboard(jobs))


@command("status", "queue", "Live progress of a job", "/status [job_id]")
async def status(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    jid = arg_text(context)
    jobs = s.jobs.user_jobs(user.id)
    job = s.jobs.jobs.get(jid) if jid else (jobs[0] if jobs else None)
    if not job or (job.user_id != user.id and not user.is_admin):
        await reply(update, "No such job. See /queue.")
        return
    extra = f"\nStatus: {job.status}" + (f" · error: {esc(job.error)}" if job.error else "")
    await reply(update, progress_text(job) + extra)


@command("cancel", "queue", "Cancel a job (or your newest one)", "/cancel [job_id]")
async def cancel(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    jid = arg_text(context)
    if not jid:
        jobs = s.jobs.user_jobs(user.id)
        if not jobs:
            await reply(update, "Nothing to cancel.")
            return
        jid = jobs[-1].id
    job = s.jobs.cancel(jid, None if user.is_admin else user.id)
    await reply(update, f"✖ Cancelled job <code>{esc(jid)}</code>." if job else "No such active job.")


@command("cancelall", "queue", "Cancel all your jobs")
async def cancelall(update: Update, context: Ctx, user: User) -> None:
    n = svc(context).jobs.cancel_all(user.id)
    await reply(update, f"✖ Cancelled {n} job{'s' if n != 1 else ''}.")


@command("pause", "queue", "Hold your waiting jobs (running ones finish)")
async def pause(update: Update, context: Ctx, user: User) -> None:
    n = svc(context).jobs.hold(user.id)
    await reply(
        update,
        f"⏸ Paused. {n} waiting job{'s' if n != 1 else ''} on hold. New jobs will wait too; /resume to continue.",
    )


@command("resume", "queue", "Release your held jobs")
async def resume(update: Update, context: Ctx, user: User) -> None:
    n = svc(context).jobs.release(user.id)
    await reply(update, f"▶️ Resumed {n} job{'s' if n != 1 else ''}.")


@command("top", "queue", "Move a waiting job to the front", "/top <job_id>")
async def top(update: Update, context: Ctx, user: User) -> None:
    jid = arg_text(context)
    if not jid:
        await reply(update, usage("top"))
        return
    ok = svc(context).jobs.move_to_front(jid, None if user.is_admin else user.id)
    await reply(update, "⏫ Moved to the front." if ok else "That job isn't waiting in the queue.")


async def requeue(update: Update, context: Ctx, user: User, url: str, record: dict, chat_id: int | None = None):
    kind = record.get("kind", "media")
    preset = Preset.from_dict(record.get("preset", {}))
    return await enqueue(
        update, context, user, url, kind=kind, preset=preset, options=record.get("options") or {}, chat_id=chat_id
    )


@command("retry", "queue", "Retry a failed download from history", "/retry [history_id]")
async def retry(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    arg = arg_text(context)
    row = None
    if arg.isdigit():
        row = await s.db.history_item(user.id, int(arg))
    else:
        for r in await s.db.history(user.id, 20):
            if r["status"] in ("failed", "cancelled"):
                row = r
                break
    if not row:
        await reply(update, "Nothing to retry. Use /history to find an item ID.")
        return
    await requeue(update, context, user, row["url"], json.loads(row["preset"] or "{}"))


# ------------------------------------------------------------------ history


def history_line(r) -> str:
    icon = STATUS_ICON.get(r["status"], "•") + ("⭐" if r["favorite"] else "")
    title = esc(truncate(r["title"] or domain_of(r["url"]), 45))
    size = f" · {human_size(r['size'])}" if r["size"] else ""
    return f"{icon} <code>{r['id']}</code> {title}{size} · {ago(r['created_at'])}"


async def history_page(s, user: User, page: int, favorites: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    total = await s.db.count_history(user.id, favorites)
    pages = max(1, -(-total // PAGE))
    page = max(0, min(page, pages - 1))
    rows = await s.db.history(user.id, PAGE, page * PAGE, favorites)
    title = "⭐ <b>Favorites</b>" if favorites else "📚 <b>History</b>"
    if not rows:
        return f"{title}\nNothing here yet.", None
    text = f"{title} · {total} items\n" + "\n".join(history_line(r) for r in rows)
    text += "\n\n<i>/resend ID · /redo ID · /fav ID</i>"
    return text, InlineKeyboardMarkup([paginate_keyboard("favs" if favorites else "hist", page, pages)])


@command("history", "library", "Your recent downloads (paged)", "/history [page]")
async def history(update: Update, context: Ctx, user: User) -> None:
    page = int(arg_text(context)) - 1 if arg_text(context).isdigit() else 0
    text, markup = await history_page(svc(context), user, page)
    await reply(update, text, reply_markup=markup)


@command("favs", "library", "Your favorite downloads", "/favs [page]")
async def favs(update: Update, context: Ctx, user: User) -> None:
    page = int(arg_text(context)) - 1 if arg_text(context).isdigit() else 0
    text, markup = await history_page(svc(context), user, page, favorites=True)
    await reply(update, text, reply_markup=markup)


def item_keyboard(r) -> InlineKeyboardMarkup:
    row = []
    if r["file_id"]:
        row.append(B("📤 Resend", callback_data=f"res:{r['id']}"))
    row.append(B("🔁 Download again", callback_data=f"redo:{r['id']}"))
    row.append(B("⭐ Unfavorite" if r["favorite"] else "⭐ Favorite", callback_data=f"favt:{r['id']}"))
    return InlineKeyboardMarkup([row])


def item_text(r, tz: str) -> str:
    rec = json.loads(r["preset"] or "{}")
    label = Preset.from_dict(rec.get("preset", {})).label() if rec.get("kind", "media") == "media" else rec.get("kind")
    return (
        f"{STATUS_ICON.get(r['status'], '•')} <b>{esc(truncate(r['title'] or 'Untitled', 150))}</b>\n"
        f"ID <code>{r['id']}</code> · {esc(label)} · {human_size(r['size']) if r['size'] else '—'}\n"
        f'{fmt_ts(r["created_at"], tz)} · <a href="{esc(r["url"])}">{esc(domain_of(r["url"]))}</a>'
        + (f"\n<i>{esc(r['error'])}</i>" if r["error"] else "")
    )


@command("last", "library", "Your most recent download, with actions")
async def last(update: Update, context: Ctx, user: User) -> None:
    r = await svc(context).db.last_history(user.id)
    if not r:
        await reply(update, "No downloads yet.")
        return
    await reply(update, item_text(r, user.pref("timezone")), reply_markup=item_keyboard(r))


async def _item(update: Update, context: Ctx, user: User, name: str):
    arg = arg_text(context)
    if not arg.isdigit():
        await reply(update, usage(name))
        return None
    r = await svc(context).db.history_item(user.id, int(arg))
    if not r:
        await reply(update, "No history item with that ID. See /history.")
    return r


@command("redo", "library", "Download a history item again", "/redo <id>")
async def redo(update: Update, context: Ctx, user: User) -> None:
    r = await _item(update, context, user, "redo")
    if r:
        await requeue(update, context, user, r["url"], json.loads(r["preset"] or "{}"))


@command("resend", "library", "Instantly resend a previous file (no re-download)", "/resend <id>")
async def resend(update: Update, context: Ctx, user: User) -> None:
    r = await _item(update, context, user, "resend")
    if not r:
        return
    if not r["file_id"]:
        await reply(update, "That item can't be resent instantly (it was a link, album or multi-file). Try /redo.")
        return
    await svc(context).delivery.send_cached(
        context.bot, update.effective_chat.id, r["kind"], r["file_id"], f"<b>{esc(truncate(r['title'] or '', 200))}</b>"
    )


@command("fav", "library", "Add a history item to favorites", "/fav <id>")
async def fav(update: Update, context: Ctx, user: User) -> None:
    r = await _item(update, context, user, "fav")
    if r:
        await svc(context).db.set_favorite(user.id, r["id"], True)
        await reply(update, "⭐ Added to favorites (/favs).")


@command("unfav", "library", "Remove from favorites", "/unfav <id>")
async def unfav(update: Update, context: Ctx, user: User) -> None:
    r = await _item(update, context, user, "unfav")
    if r:
        await svc(context).db.set_favorite(user.id, r["id"], False)
        await reply(update, "☆ Removed from favorites.")


@command("find", "library", "Search your history by title or link", "/find <words>")
async def find(update: Update, context: Ctx, user: User) -> None:
    q = arg_text(context)
    if not q:
        await reply(update, usage("find"))
        return
    rows = await svc(context).db.search_history(user.id, q)
    if not rows:
        await reply(update, f"No history matches “{esc(q)}”.")
        return
    await reply(update, f"🔎 <b>{len(rows)} matches</b>\n" + "\n".join(history_line(r) for r in rows))


@command("clearhistory", "library", "Delete your history (favorites kept unless 'all')", "/clearhistory [all]")
async def clearhistory(update: Update, context: Ctx, user: User) -> None:
    everything = arg_text(context).lower() == "all"
    what = "your entire history, including favorites" if everything else "your history (favorites are kept)"
    await reply(
        update,
        f"🗑 Delete {what}?",
        reply_markup=InlineKeyboardMarkup(
            [[B("Yes, delete", callback_data=f"clearh:{int(everything)}"), B("Cancel", callback_data="close")]]
        ),
    )


@command("exporthistory", "library", "Download your history as a CSV file")
async def exporthistory(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    rows = await s.db.history(user.id, 100000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "date", "status", "kind", "title", "url", "size_bytes", "favorite", "error"])
    for r in rows:
        writer.writerow(
            [
                r["id"],
                fmt_ts(r["created_at"], user.pref("timezone")),
                r["status"],
                r["kind"],
                r["title"],
                r["url"],
                r["size"],
                r["favorite"],
                r["error"],
            ]
        )
    await update.effective_message.reply_document(
        InputFile(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename="download-history.csv"),
        caption=f"📚 {len(rows)} history items",
    )


# ------------------------------------------------------------------ callbacks


async def on_library_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    s = svc(context)
    kind, _, arg = (query.data or "").partition(":")
    if kind in ("hist", "favs"):
        await query.answer()
        text, markup = await history_page(s, user, int(arg or 0), favorites=kind == "favs")
        try:
            await query.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True
            )
        except BadRequest:
            pass
        return
    if kind == "clearh":
        n = await s.db.clear_history(user.id, keep_favorites=arg != "1")
        await query.answer(f"Deleted {n} items")
        await query.message.edit_text(f"🗑 Deleted {n} history items.")
        return
    if kind == "job":
        action, _, jid = arg.partition(":")
        if action == "cancel":
            job = s.jobs.cancel(jid, None if user.is_admin else user.id)
            await query.answer("Cancelling…" if job else "Already finished", show_alert=False)
        elif action == "cancelall":
            n = s.jobs.cancel_all(user.id)
            await query.answer(f"Cancelled {n} jobs")
        elif action == "retry":
            job = s.jobs.jobs.get(jid)
            if not job or job.user_id != user.id:
                await query.answer("This job is too old to retry. Use /retry.", show_alert=True)
                return
            await query.answer("Retrying…")
            await enqueue(
                update,
                context,
                user,
                job.url,
                kind=job.kind,
                preset=job.preset,
                options=job.options,
                chat_id=query.message.chat_id,
            )
        return
    item = await s.db.history_item(user.id, int(arg)) if arg.isdigit() else None
    if not item:
        await query.answer("Item not found", show_alert=True)
        return
    if kind == "res" and item["file_id"]:
        await query.answer()
        await s.delivery.send_cached(
            context.bot,
            query.message.chat_id,
            item["kind"],
            item["file_id"],
            f"<b>{esc(truncate(item['title'] or '', 200))}</b>",
        )
    elif kind == "redo":
        await query.answer("Queued")
        await requeue(
            update, context, user, item["url"], json.loads(item["preset"] or "{}"), chat_id=query.message.chat_id
        )
    elif kind == "favt":
        await s.db.set_favorite(user.id, item["id"], not item["favorite"])
        await query.answer("Favorites updated")
        item = await s.db.history_item(user.id, item["id"])
        try:
            await query.message.edit_reply_markup(item_keyboard(item))
        except BadRequest:
            pass
