"""Subscriptions (auto-download new uploads) and scheduled downloads."""

from __future__ import annotations

import json

from telegram import Update

from ..db import User
from ..registry import command
from ..services.downloader import AUDIO_FORMATS, friendly_error
from ..services.watcher import check_watch, entry_ids, schedule_job
from ..utils import esc, fmt_ts, parse_when, truncate
from .common import Ctx, arg_text, need_url, non_url_args, preset_for, reply, svc, url_from, usage

MAX_WATCHES = 20


@command(
    "watch", "watch", "Auto-download new uploads from a channel or playlist", "/watch <channel or playlist url> [mp3]"
)
async def watch(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    if not url:
        await need_url(update, "watch")
        return
    s = svc(context)
    if len(await s.db.watches(user.id)) >= MAX_WATCHES and not user.is_admin:
        await reply(update, f"You can have up to {MAX_WATCHES} subscriptions. Remove one with /unwatch.")
        return
    try:
        info = await s.downloader.flat_entries(url, limit=15)
    except Exception as exc:  # noqa: BLE001
        await reply(update, f"⚠️ {esc(friendly_error(exc))}")
        return
    if not info.is_playlist:
        await reply(update, "That link isn't a channel or playlist. Send a channel, user or playlist page.")
        return
    fmt = next((a.lower() for a in non_url_args(context) if a.lower() in AUDIO_FORMATS), None)
    preset = preset_for(user, mode="audio", audio_format=fmt) if fmt else preset_for(user, mode="video")
    wid = await s.db.add_watch(user.id, update.effective_chat.id, url, preset.to_dict(), info.title, entry_ids(info))
    minutes = s.settings.watch_interval_minutes
    await reply(
        update,
        f"🔔 Subscribed to <b>{esc(truncate(info.title, 80))}</b> (#{wid}). I'll check every "
        f"{minutes} min and send new uploads as {esc(preset.label())}.",
    )


@command("watches", "watch", "Your subscriptions")
async def watches(update: Update, context: Ctx, user: User) -> None:
    rows = await svc(context).db.watches(user.id)
    if not rows:
        await reply(update, "No subscriptions. Add one with /watch.")
        return
    from ..services.downloader import Preset

    lines = ["🔔 <b>Subscriptions</b>"]
    for r in rows:
        label = Preset.from_dict(json.loads(r["preset"])).label()
        lines.append(
            f"#{r['id']} {esc(truncate(r['title'] or r['url'], 50))} · {esc(label)} · checked "
            f"{fmt_ts(r['last_check'], user.pref('timezone'))}"
        )
    lines.append("\n/unwatch ID to stop · /checknow to check immediately")
    await reply(update, "\n".join(lines))


@command("unwatch", "watch", "Stop a subscription", "/unwatch <id>")
async def unwatch(update: Update, context: Ctx, user: User) -> None:
    arg = arg_text(context).lstrip("#")
    if not arg.isdigit():
        await reply(update, usage("unwatch"))
        return
    ok = await svc(context).db.remove_watch(user.id, int(arg))
    await reply(update, "🔕 Subscription removed." if ok else "No subscription with that ID.")


@command("checknow", "watch", "Check your subscriptions for new uploads right now")
async def checknow(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    rows = await s.db.watches(user.id)
    if not rows:
        await reply(update, "No subscriptions to check.")
        return
    status = await reply(update, f"🔄 Checking {len(rows)} subscription{'s' if len(rows) != 1 else ''}…")
    total = 0
    for row in rows:
        try:
            total += await check_watch(context.application, row)
        except Exception as exc:  # noqa: BLE001
            await reply(update, f"⚠️ #{row['id']}: {esc(friendly_error(exc))}")
    if status:
        await status.edit_text(f"✅ Checked. {total} new download{'s' if total != 1 else ''} queued.")


@command("schedule", "watch", "Download later: in 30m, +2h, 18:30 or 2026-10-01 08:00", "/schedule <when> <url> [mp3]")
async def schedule(update: Update, context: Ctx, user: User) -> None:
    url = url_from(update, context)
    args = [a for a in non_url_args(context) if a.lower() not in AUDIO_FORMATS]
    if not url or not args:
        await reply(update, usage("schedule"))
        return
    when_text = " ".join(args)
    try:
        when = parse_when(when_text, user.pref("timezone"))
    except ValueError as exc:
        await reply(update, f"⏰ {esc(exc)}")
        return
    fmt = next((a.lower() for a in non_url_args(context) if a.lower() in AUDIO_FORMATS), None)
    preset = preset_for(user, mode="audio", audio_format=fmt) if fmt else preset_for(user, mode="video")
    s = svc(context)
    record = {"kind": "media", "preset": preset.to_dict(), "options": {}}
    sid = await s.db.add_schedule(user.id, update.effective_chat.id, url, record, when.timestamp())
    schedule_job(context.application, sid, when.timestamp())
    await reply(
        update,
        f"⏰ Scheduled #{sid} for <b>{fmt_ts(when.timestamp(), user.pref('timezone'))}</b> "
        f"({esc(user.pref('timezone'))}) · {esc(preset.label())}",
    )


@command("schedules", "watch", "Your pending scheduled downloads")
async def schedules(update: Update, context: Ctx, user: User) -> None:
    rows = await svc(context).db.pending_schedules(user.id)
    if not rows:
        await reply(update, "No scheduled downloads. Add one with /schedule.")
        return
    lines = ["⏰ <b>Scheduled</b>"]
    for r in rows:
        lines.append(f"#{r['id']} {fmt_ts(r['run_at'], user.pref('timezone'))} · {esc(truncate(r['url'], 50))}")
    lines.append("\n/unschedule ID to cancel")
    await reply(update, "\n".join(lines))


@command("unschedule", "watch", "Cancel a scheduled download", "/unschedule <id>")
async def unschedule(update: Update, context: Ctx, user: User) -> None:
    arg = arg_text(context).lstrip("#")
    if not arg.isdigit():
        await reply(update, usage("unschedule"))
        return
    s = svc(context)
    ok = await s.db.cancel_schedule(user.id, int(arg))
    for job in context.application.job_queue.get_jobs_by_name(f"schedule-{arg}") if ok else []:
        job.schedule_removal()
    await reply(update, "🗑 Scheduled download cancelled." if ok else "No pending schedule with that ID.")
