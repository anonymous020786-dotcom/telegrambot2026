"""Background checks for subscriptions (new uploads) and scheduled downloads."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from ..utils import esc, truncate
from .downloader import Preset
from .jobs import Job, QuotaExceeded

log = logging.getLogger(__name__)
MAX_NEW_PER_CHECK = 5


def entry_ids(info) -> list[str]:
    return [str(e.get("id") or e.get("url")) for e in info.entries if e.get("id") or e.get("url")]


async def check_watch(app: Application, row) -> int:
    """Queue downloads for items that appeared since the last check. Returns how many were queued."""
    svc = app.bot_data["svc"]
    info = await svc.downloader.flat_entries(row["url"], limit=15)
    seen = json.loads(row["seen"] or "[]")
    new = [e for e in info.entries if str(e.get("id") or e.get("url")) not in seen and (e.get("url") or e.get("id"))]
    await svc.db.update_watch_seen(row["id"], seen + [str(e.get("id") or e.get("url")) for e in new])
    if not new:
        return 0
    user = await svc.db.ensure_user(row["user_id"])
    preset = Preset.from_dict(json.loads(row["preset"]))
    queued = 0
    for entry in list(reversed(new))[:MAX_NEW_PER_CHECK]:
        url = entry.get("url") or entry.get("id")
        if url and not str(url).startswith("http") and "youtube" in info.extractor.lower():
            url = f"https://www.youtube.com/watch?v={entry.get('id')}"
        job = Job(user_id=user.id, chat_id=row["chat_id"], url=url, preset=preset, title=entry.get("title"), priority=6)
        try:
            await svc.jobs.submit(job, user)
            queued += 1
        except QuotaExceeded:
            break
    try:
        await app.bot.send_message(
            row["chat_id"],
            f"🆕 {len(new)} new item{'s' if len(new) != 1 else ''} in "
            f"<b>{esc(truncate(row['title'] or info.title, 80))}</b> · {queued} queued",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass
    return queued


async def check_all_watches(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    for row in await svc.db.watches():
        try:
            await check_watch(context.application, row)
        except Exception as exc:  # noqa: BLE001
            log.warning("watch %s failed: %s", row["id"], exc)


async def run_schedule(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = context.application.bot_data["svc"]
    sched_id = context.job.data
    rows = [r for r in await svc.db.pending_schedules() if r["id"] == sched_id]
    if not rows:
        return  # cancelled
    row = rows[0]
    await svc.db.finish_schedule(sched_id)
    user = await svc.db.ensure_user(row["user_id"])
    record = json.loads(row["preset"])
    job = Job(
        user_id=user.id,
        chat_id=row["chat_id"],
        url=row["url"],
        kind=record.get("kind", "media"),
        preset=Preset.from_dict(record.get("preset", {})),
        options=record.get("options") or {},
    )
    try:
        await svc.jobs.submit(job, user)
    except QuotaExceeded:
        await context.bot.send_message(row["chat_id"], "⏰ Scheduled download skipped: daily limit reached.")


def schedule_job(app: Application, sched_id: int, run_at: float) -> None:
    when = datetime.fromtimestamp(max(run_at, time.time() + 1), UTC)
    app.job_queue.run_once(run_schedule, when=when, data=sched_id, name=f"schedule-{sched_id}")


async def restore_schedules(app: Application) -> int:
    svc = app.bot_data["svc"]
    rows = await svc.db.pending_schedules()
    for row in rows:
        schedule_job(app, row["id"], row["run_at"])
    return len(rows)
