"""Admin-only commands."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

import psutil
from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from ..db import User
from ..registry import command
from ..services.downloader import extractor_names
from ..utils import ago, esc, human_duration, human_size, truncate
from .common import Ctx, arg_text, reply, svc, usage


async def _target(update: Update, context: Ctx, name: str) -> User | None:
    ref = (context.args or [""])[0]
    msg = update.effective_message
    if not ref and msg.reply_to_message and msg.reply_to_message.forward_origin is not None:
        origin = msg.reply_to_message.forward_origin
        sender = getattr(origin, "sender_user", None)
        if sender:
            ref = str(sender.id)
    if not ref:
        await reply(update, usage(name))
        return None
    s = svc(context)
    user = await s.db.find_user(ref)
    if user is None and ref.lstrip("-").isdigit():
        user = await s.db.ensure_user(int(ref))
    if user is None:
        await reply(update, f"User {esc(ref)} not found (they must have messaged the bot, or use their numeric ID).")
    return user


async def _notify(context: Ctx, uid: int, text: str) -> None:
    try:
        await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


def admin_panel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [B("👥 Users", callback_data="adm:users"), B("📊 Stats", callback_data="adm:stats")],
            [B("🖥 System", callback_data="adm:sys"), B("📋 All jobs", callback_data="adm:jobs")],
            [B("🧹 Clean files", callback_data="adm:cleanup"), B("🎟 New invite", callback_data="adm:invite")],
        ]
    )


@command("admin", "admin", "Admin panel", admin=True)
async def admin(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    maint = await s.db.get_kv("maintenance") == "1"
    await reply(
        update,
        f"🛡 <b>Admin panel</b>\nUsers: {await s.db.count_users()} · active jobs: "
        f"{len(s.jobs.active())} · maintenance: {'on' if maint else 'off'} · queue "
        f"{'paused' if s.jobs.paused_all else 'running'}",
        reply_markup=admin_panel(),
    )


async def users_text(s, page: int = 0) -> str:
    users = await s.db.list_users(30, page * 30)
    total = await s.db.count_users()
    lines = [f"👥 <b>Users</b> · {total}"]
    for u in users:
        badge = "🛡" if u.is_admin else ("⛔" if u.is_banned else ("✅" if u.allowed else "🔒"))
        lines.append(f"{badge} <code>{u.id}</code> {esc(truncate(u.display, 25))} · {ago(u.last_seen)}")
    lines.append("\n🛡 admin · ✅ allowed · 🔒 no access · ⛔ banned")
    return "\n".join(lines)


@command("users", "admin", "List users (30 per page)", "/users [page]", admin=True)
async def users(update: Update, context: Ctx, user: User) -> None:
    page = int(arg_text(context)) - 1 if arg_text(context).isdigit() else 0
    await reply(update, await users_text(svc(context), max(page, 0)))


@command("user", "admin", "Details about one user", "/user <id|@username>", admin=True)
async def user_info(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "user")
    if not target:
        return
    s = svc(context)
    total, total_bytes = await s.db.usage_totals(target.id)
    today, _ = await s.db.usage_today(target.id)
    await reply(
        update,
        (
            f"👤 {esc(target.display)} (<code>{target.id}</code>)\nRole: {target.role} · allowed: {target.allowed}\n"
            f"Daily limit: {target.daily_limit if target.daily_limit is not None else 'default'}\n"
            f"Downloads: {total} ({human_size(total_bytes)}) · today {today}\n"
            f"First seen {ago(target.created_at)} · last seen {ago(target.last_seen)}\n"
            f"Active jobs: {len(s.jobs.user_jobs(target.id))}"
        ),
    )


@command("allow", "admin", "Give a user access", "/allow <id|@username>", admin=True)
async def allow(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "allow")
    if target:
        await svc(context).db.set_allowed(target.id, True)
        await reply(update, f"✅ {esc(target.display)} can now use the bot.")
        await _notify(context, target.id, "🎉 You've been given access! Send /start.")


@command("deny", "admin", "Remove a user's access", "/deny <id|@username>", admin=True)
async def deny(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "deny")
    if target:
        await svc(context).db.set_allowed(target.id, False)
        await reply(update, f"🔒 {esc(target.display)} no longer has access.")


@command("ban", "admin", "Ban a user and cancel their jobs", "/ban <id|@username>", admin=True)
async def ban(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "ban")
    if not target:
        return
    if target.id in svc(context).settings.admin_ids:
        await reply(update, "You can't ban an owner listed in ADMIN_IDS.")
        return
    s = svc(context)
    await s.db.set_role(target.id, "banned")
    n = s.jobs.cancel_all(target.id)
    await reply(update, f"⛔ Banned {esc(target.display)} ({n} jobs cancelled).")


@command("unban", "admin", "Lift a ban", "/unban <id|@username>", admin=True)
async def unban(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "unban")
    if target:
        await svc(context).db.set_role(target.id, "user")
        await reply(update, f"✅ Unbanned {esc(target.display)}.")


@command("promote", "admin", "Make a user an admin", "/promote <id|@username>", admin=True)
async def promote(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "promote")
    if target:
        await svc(context).db.set_role(target.id, "admin")
        await reply(update, f"🛡 {esc(target.display)} is now an admin.")


@command("demote", "admin", "Remove admin rights", "/demote <id|@username>", admin=True)
async def demote(update: Update, context: Ctx, user: User) -> None:
    target = await _target(update, context, "demote")
    if not target:
        return
    if target.id in svc(context).settings.admin_ids:
        await reply(update, "Owners listed in ADMIN_IDS stay admins. Remove them from the config instead.")
        return
    await svc(context).db.set_role(target.id, "user")
    await reply(update, f"👤 {esc(target.display)} is now a regular user.")


@command(
    "setlimit",
    "admin",
    "Set a user's daily download limit (0 = unlimited, default = global)",
    "/setlimit <id|@username> <number|default>",
    admin=True,
)
async def setlimit(update: Update, context: Ctx, user: User) -> None:
    if len(context.args or []) < 2:
        await reply(update, usage("setlimit"))
        return
    target = await _target(update, context, "setlimit")
    if not target:
        return
    raw = context.args[1].lower()
    limit = None if raw == "default" else int(raw) if raw.isdigit() else -1
    if limit == -1:
        await reply(update, usage("setlimit"))
        return
    await svc(context).db.set_daily_limit(target.id, limit)
    await reply(
        update, f"🚦 Daily limit for {esc(target.display)}: {'default' if limit is None else limit or 'unlimited'}"
    )


@command("broadcast", "admin", "Message every user who has access", "/broadcast <message>", admin=True)
async def broadcast(update: Update, context: Ctx, user: User) -> None:
    text = arg_text(context)
    if not text:
        await reply(update, usage("broadcast"))
        return
    s = svc(context)
    ids = await s.db.all_user_ids()
    status = await reply(update, f"📣 Sending to {len(ids)} users…")
    ok = failed = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, f"📣 {esc(text)}", parse_mode=ParseMode.HTML)
            ok += 1
        except Forbidden:
            failed += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)  # stay well under Telegram's broadcast rate limit
    if status:
        await status.edit_text(f"📣 Broadcast done: {ok} delivered, {failed} failed.")


@command("maintenance", "admin", "Block non-admin use while you work on the bot", "/maintenance <on|off>", admin=True)
async def maintenance(update: Update, context: Ctx, user: User) -> None:
    arg = arg_text(context).lower()
    s = svc(context)
    if arg not in ("on", "off"):
        state = await s.db.get_kv("maintenance") == "1"
        await reply(update, f"Maintenance is {'on' if state else 'off'}. {usage('maintenance')}")
        return
    await s.db.set_kv("maintenance", "1" if arg == "on" else "0")
    await reply(update, f"🛠 Maintenance mode {arg}.")


@command("invite", "admin", "Create an invite code (uses, hours valid)", "/invite [uses=1] [hours=72]", admin=True)
async def invite(update: Update, context: Ctx, user: User) -> None:
    args = context.args or []
    uses = int(args[0]) if args and args[0].isdigit() else 1
    hours = float(args[1]) if len(args) > 1 and args[1].replace(".", "").isdigit() else 72
    code = await svc(context).db.create_invite(user.id, uses, hours)
    me = await context.bot.get_me()
    await reply(
        update,
        f"🎟 Invite <code>{code}</code> · {uses} use{'s' if uses != 1 else ''} · {hours:g} h\n"
        f"Link: https://t.me/{me.username}?start=inv_{code}",
    )


@command("invites", "admin", "Active invite codes", admin=True)
async def invites(update: Update, context: Ctx, user: User) -> None:
    rows = await svc(context).db.invites()
    if not rows:
        await reply(update, "No active invites. Create one with /invite.")
        return
    lines = ["🎟 <b>Invites</b>"]
    for r in rows:
        exp = f"expires in {human_duration(max(0, r['expires_at'] - time.time()))}" if r["expires_at"] else "no expiry"
        lines.append(f"<code>{r['code']}</code> · {r['uses_left']} left · {exp}")
    await reply(update, "\n".join(lines))


@command("revoke", "admin", "Delete an invite code", "/revoke <code>", admin=True)
async def revoke(update: Update, context: Ctx, user: User) -> None:
    code = arg_text(context)
    if not code:
        await reply(update, usage("revoke"))
        return
    ok = await svc(context).db.revoke_invite(code)
    await reply(update, "🗑 Invite revoked." if ok else "No such invite.")


def sysinfo_text(s) -> str:
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(s.settings.data_dir)
    load = ", ".join(f"{x:.2f}" for x in os.getloadavg()) if hasattr(os, "getloadavg") else "n/a"
    proc = psutil.Process()
    return (
        "🖥 <b>System</b>\n"
        f"OS: {esc(platform.platform(terse=True))} · Python {platform.python_version()}\n"
        f"CPU: {psutil.cpu_count()} cores · {psutil.cpu_percent(interval=0.3):.0f}% · load {load}\n"
        f"RAM: {human_size(vm.used)} / {human_size(vm.total)} ({vm.percent:.0f}%)\n"
        f"Bot memory: {human_size(proc.memory_info().rss)}\n"
        f"Disk: {human_size(disk.used)} / {human_size(disk.total)} · free {human_size(disk.free)}\n"
        f"Uptime: {human_duration(time.time() - s.started_at)} · active jobs {len(s.jobs.active())}\n"
        f"ffmpeg: {'yes' if shutil.which('ffmpeg') else 'NO'} · extractors: {len(extractor_names())}"
    )


@command("sysinfo", "admin", "CPU, memory, disk and uptime", admin=True)
async def sysinfo(update: Update, context: Ctx, user: User) -> None:
    await reply(update, sysinfo_text(svc(context)))


@command("disk", "admin", "Space used by downloads and the database", admin=True)
async def disk(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)

    def measure() -> tuple[int, int]:
        files = [f for f in s.settings.download_dir.rglob("*") if f.is_file()]
        return len(files), sum(f.stat().st_size for f in files)

    count, size = await asyncio.to_thread(measure)
    db_size = s.settings.db_path.stat().st_size if s.settings.db_path.exists() else 0
    free = shutil.disk_usage(s.settings.data_dir).free
    await reply(
        update,
        f"💽 Downloads: {count} files · {human_size(size)}\nDatabase: {human_size(db_size)}\n"
        f"Free space: {human_size(free)}\nFiles older than {s.settings.file_retention_hours:g} h are "
        "removed automatically; /cleanup removes them now.",
    )


@command("cleanup", "admin", "Delete leftover download files now", "/cleanup [hours=0]", admin=True)
async def cleanup(update: Update, context: Ctx, user: User) -> None:
    arg = arg_text(context)
    hours = float(arg) if arg.replace(".", "").isdigit() else 0
    removed, freed = await asyncio.to_thread(svc(context).jobs.cleanup_old_files, hours)
    await reply(update, f"🧹 Removed {removed} folders, freed {human_size(freed)}.")


@command("logs", "admin", "The last lines of the bot log", "/logs [lines=60]", admin=True)
async def logs(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    n = min(int(arg_text(context)), 400) if arg_text(context).isdigit() else 60
    path = s.settings.log_dir / "bot.log"
    if not path.exists():
        await reply(update, "No log file yet.")
        return
    lines = path.read_text(errors="replace").splitlines()[-n:]
    text = "\n".join(lines)
    if len(text) > 3800:
        await update.effective_message.reply_document(InputFile(text.encode(), filename="bot-log.txt"))
    else:
        await reply(update, f"<pre>{esc(text) or '(empty)'}</pre>")


@command("updateytdlp", "admin", "Update yt-dlp to the latest version (sites change often)", admin=True)
async def updateytdlp(update: Update, context: Ctx, user: User) -> None:
    status = await reply(update, "⬆️ Updating yt-dlp…")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pip",
        "install",
        "-U",
        "--no-cache-dir",
        "yt-dlp[default]",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    tail = (out or b"").decode(errors="replace").strip().splitlines()[-2:]
    ok = proc.returncode == 0
    text = f"{'✅' if ok else '❌'} pip exited with {proc.returncode}\n<pre>{esc(chr(10).join(tail))}</pre>\n" + (
        "Send /restart to load the new version." if ok else ""
    )
    if status:
        await status.edit_text(text, parse_mode=ParseMode.HTML)


@command("backup", "admin", "Send a copy of the database", admin=True)
async def backup(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / f"bot-backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
        await asyncio.to_thread(s.db.backup_to, dest)
        with dest.open("rb") as fh:
            await update.effective_message.reply_document(
                InputFile(fh, filename=dest.name), caption="💾 Database backup (SQLite)"
            )


def stats_text(g: dict) -> str:
    kinds = ", ".join(f"{k}: {v}" for k, v in g["kinds"].items()) or "none"
    return (
        f"📊 <b>Global stats</b>\nUsers: {g['users']}\nJobs: {g['jobs']} (✅ {g['ok']} · ❌ {g['failed']})\n"
        f"Delivered: {human_size(g['bytes'])}\nBy type: {esc(kinds)}"
    )


@command("globalstats", "admin", "Totals across all users", admin=True)
async def globalstats(update: Update, context: Ctx, user: User) -> None:
    await reply(update, stats_text(await svc(context).db.global_stats()))


def jobs_text(s) -> str:
    jobs = s.jobs.active()
    if not jobs:
        return "📭 No active jobs."
    lines = [f"📋 <b>All jobs</b> · {len(jobs)}"]
    for j in jobs:
        lines.append(
            f"{'▶️' if j.status == 'running' else '🕒'} {j.describe()} · user <code>{j.user_id}</code> · {j.phase}"
        )
    return "\n".join(lines)


@command("jobs", "admin", "Every active job from every user", admin=True)
async def jobs(update: Update, context: Ctx, user: User) -> None:
    await reply(update, jobs_text(svc(context)))


@command("killjob", "admin", "Cancel any user's job", "/killjob <job_id>", admin=True)
async def killjob(update: Update, context: Ctx, user: User) -> None:
    jid = arg_text(context)
    job = svc(context).jobs.cancel(jid) if jid else None
    await reply(update, f"✖ Cancelled {esc(jid)}." if job else usage("killjob"))


@command("pauseall", "admin", "Stop starting new jobs for everyone", admin=True)
async def pauseall(update: Update, context: Ctx, user: User) -> None:
    svc(context).jobs.paused_all = True
    await reply(update, "⏸ Global queue paused. Running jobs finish; /resumeall to continue.")


@command("resumeall", "admin", "Resume the global queue", admin=True)
async def resumeall(update: Update, context: Ctx, user: User) -> None:
    s = svc(context)
    s.jobs.paused_all = False
    s.jobs._wake.set()
    await reply(update, "▶️ Global queue resumed.")


@command("clearcache", "admin", "Forget cached Telegram file IDs (forces fresh downloads)", admin=True)
async def clearcache(update: Update, context: Ctx, user: User) -> None:
    n = await svc(context).db.cache_clear()
    svc(context).probe_cache.clear()
    await reply(update, f"🧽 Cleared {n} cached files and the link-info cache.")


@command("feedbacks", "admin", "Recent feedback from users", admin=True)
async def feedbacks(update: Update, context: Ctx, user: User) -> None:
    rows = await svc(context).db.feedback(20)
    if not rows:
        await reply(update, "No feedback yet.")
        return
    lines = ["💬 <b>Feedback</b>"]
    for r in rows:
        lines.append(f"• <code>{r['user_id']}</code> {ago(r['created_at'])}: {esc(truncate(r['text'], 200))}")
    await reply(update, "\n".join(lines))


@command("restart", "admin", "Restart the bot process (Docker/systemd bring it back)", admin=True)
async def restart(update: Update, context: Ctx, user: User) -> None:
    await reply(update, "♻️ Restarting…")
    context.application.bot_data["restart"] = True
    context.application.stop_running()


# ------------------------------------------------------------------ callbacks


async def on_admin_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    if not user.is_admin:
        await query.answer("Admins only", show_alert=True)
        return
    s = svc(context)
    action = (query.data or "").split(":", 1)[1]
    await query.answer()
    chat = query.message.chat_id
    if action == "users":
        text = await users_text(s)
    elif action == "stats":
        text = stats_text(await s.db.global_stats())
    elif action == "sys":
        text = sysinfo_text(s)
    elif action == "jobs":
        text = jobs_text(s)
    elif action == "cleanup":
        removed, freed = await asyncio.to_thread(s.jobs.cleanup_old_files, 0)
        text = f"🧹 Removed {removed} folders, freed {human_size(freed)}."
    elif action == "invite":
        code = await s.db.create_invite(user.id, 1, 72)
        me = await context.bot.get_me()
        text = f"🎟 Invite <code>{code}</code> (1 use, 72 h)\nhttps://t.me/{me.username}?start=inv_{code}"
    else:
        return
    await context.bot.send_message(chat, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def on_access_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    s = svc(context)
    parts = (query.data or "").split(":")
    if parts[1] == "request":
        from .general import send_access_request

        await send_access_request(context, user)
        await query.answer("Request sent to the admins", show_alert=True)
        return
    if not user.is_admin:
        await query.answer("Admins only", show_alert=True)
        return
    uid = int(parts[2])
    if parts[1] == "approve":
        await s.db.set_allowed(uid, True)
        await _notify(context, uid, "🎉 Your access request was approved! Send /start.")
        await query.answer("Approved")
        await query.message.edit_text(
            f"✅ Approved <code>{uid}</code> (by {esc(user.display)})", parse_mode=ParseMode.HTML
        )
    else:
        await query.answer("Denied")
        await query.message.edit_text(
            f"⛔ Denied <code>{uid}</code> (by {esc(user.display)})", parse_mode=ParseMode.HTML
        )
