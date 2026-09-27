"""Torrent downloads: /torrent, pasted magnet links and uploaded .torrent files. Only video files are fetched."""

from __future__ import annotations

import secrets
from pathlib import Path

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup, Update

from ..db import User
from ..registry import command
from ..services.torrent import MAGNET_RE, TorrentError, TorrentInfo, choose, fetch_metadata, parse_torrent
from ..utils import esc, extract_urls, human_size, truncate
from .common import Ctx, enqueue, fetch_replied_file, reply, svc, usage

LIST_LIMIT = 40


def torrent_source(text: str) -> str | None:
    """A magnet link, or an http(s) link to a .torrent file, in the message text."""
    if m := MAGNET_RE.search(text or ""):
        return m.group(0)
    return next((u for u in extract_urls(text or "") if u.lower().split("?")[0].endswith(".torrent")), None)


def file_list(info: TorrentInfo) -> str:
    lines = [f"🧲 <b>{esc(truncate(info.name, 80))}</b> · {len(info.files)} files · {human_size(info.total_size)}"]
    for f in info.files[:LIST_LIMIT]:
        mark = "🎬" if f.is_video else "▫️"
        lines.append(f"{mark} <code>{f.index}</code> · {human_size(f.size)} · {esc(truncate(Path(f.path).name, 60))}")
    if len(info.files) > LIST_LIMIT:
        lines.append(f"… and {len(info.files) - LIST_LIMIT} more")
    return "\n".join(lines)


def choice_keyboard(token: str, info: TorrentInfo) -> InlineKeyboardMarkup:
    rows = []
    if info.videos:
        largest = max(info.videos, key=lambda f: f.size)
        rows.append([B(f"⬇️ Largest video ({human_size(largest.size)})", callback_data=f"tor:{token}:auto")])
        if len(info.videos) > 1:
            total = sum(f.size for f in info.videos)
            rows.append([B(f"⬇️ All {len(info.videos)} videos ({human_size(total)})", callback_data=f"tor:{token}:all")])
    rows.append([B("✖ Close", callback_data="close")])
    return InlineKeyboardMarkup(rows)


async def _allowed(update: Update, context: Ctx, user: User) -> bool:
    s = svc(context)
    if s.settings.torrents_allowed(user.is_admin):
        return True
    await reply(update, "🧲 Torrent downloads are turned off for your account.")
    return False


async def _saved_torrent(update: Update, context: Ctx) -> Path | None:
    """An uploaded .torrent (in this message or the one replied to), kept in the data folder for the queue."""
    path = await fetch_replied_file(update, context, want=("file",))
    if path is None or path.suffix.lower() != ".torrent":
        return None
    keep = svc(context).settings.data_dir / "torrents"
    keep.mkdir(parents=True, exist_ok=True)
    dest = keep / f"{secrets.token_hex(8)}.torrent"
    dest.write_bytes(path.read_bytes())
    return dest


async def _list(update: Update, context: Ctx, source: str) -> None:
    s = svc(context)
    status = await reply(update, "🧲 Reading the torrent's file list…")
    try:
        work = s.settings.data_dir / "torrents" / "meta" / secrets.token_hex(6)
        info = parse_torrent(await fetch_metadata(source, work, s.settings.user_agent, s.settings.allow_private_urls))
    except TorrentError as exc:
        text = f"🧲 {esc(exc)}"
    except Exception as exc:  # noqa: BLE001 - report network errors to the user
        text = f"🧲 Couldn't read that torrent: {esc(truncate(str(exc), 200))}"
    else:
        text = (
            file_list(info)
            + "\n\nDownload: <code>/torrent &lt;link&gt; auto</code> · <code>all</code> · <code>1,3</code>"
        )
    if status:
        await status.edit_text(text, parse_mode="HTML")


@command(
    "torrent",
    "download",
    "Download videos from a torrent (magnet link, .torrent link, or reply to a .torrent file)",
    "/torrent <magnet|link> [auto|all|list|1,3]",
)
async def torrent(update: Update, context: Ctx, user: User) -> None:
    if not await _allowed(update, context, user):
        return
    msg = update.effective_message
    text = " ".join(context.args or [])
    source = torrent_source(text) or torrent_source((msg.reply_to_message.text or "") if msg.reply_to_message else "")
    torrent_file = None if source else await _saved_torrent(update, context)
    if not source and not torrent_file:
        await reply(update, usage("torrent") + "\nOr send me a .torrent file.")
        return
    words = [w.lower() for w in text.split() if not w.lower().startswith(("magnet:", "http"))]
    spec = next((w for w in words if w in ("auto", "all", "list") or w[:1].isdigit()), "auto")
    if spec == "list":
        await _list(update, context, source or str(torrent_file))
        return
    if torrent_file and spec != "list":
        try:
            choose(parse_torrent(torrent_file), spec)  # check the choice now rather than in the queue
        except TorrentError as exc:
            await reply(update, f"🧲 {esc(exc)}")
            return
    await _queue(update, context, user, source, torrent_file, spec)


async def _queue(
    update: Update, context: Ctx, user: User, source: str | None, torrent_file: Path | None, spec: str
) -> None:
    title = None
    url = source or ""
    options: dict = {"files": spec}
    if torrent_file:
        info = parse_torrent(torrent_file)
        url = f"magnet:?xt=urn:btih:{info.info_hash}"  # identifies the torrent in history and duplicate checks
        options["torrent_file"] = str(torrent_file)
        title = info.name
    if spec != "auto":
        url += f"&x.files={spec}"  # different file choices are different jobs
    await enqueue(update, context, user, url, kind="torrent", options=options, title=title)


async def on_magnet_message(update: Update, context: Ctx, user: User, magnet: str) -> None:
    """A magnet link pasted as a plain message: download its largest video."""
    if await _allowed(update, context, user):
        await _queue(update, context, user, magnet, None, "auto")


async def on_torrent_document(update: Update, context: Ctx, user: User) -> None:
    """A .torrent file sent to the bot: show its files and offer the downloads."""
    if not await _allowed(update, context, user):
        return
    path = await _saved_torrent(update, context)
    if path is None:
        return
    try:
        info = parse_torrent(path)
    except TorrentError as exc:
        await reply(update, f"🧲 {esc(exc)}")
        return
    token = svc(context).tokens.put(f"torrent:{path}", torrent_file=str(path))
    note = "" if info.videos else "\n\nThere are no video files in this torrent."
    await reply(update, file_list(info) + note, reply_markup=choice_keyboard(token, info))


async def on_torrent_button(update: Update, context: Ctx, user: User) -> None:
    query = update.callback_query
    _, token, spec = (query.data or "").split(":", 2)
    item = svc(context).tokens.get(token)
    await query.answer()
    if not item or not Path(item.get("torrent_file", "")).is_file():
        await query.message.reply_text("⌛ That button expired. Send the .torrent file again.")
        return
    if not await _allowed(update, context, user):
        return
    await _queue(update, context, user, None, Path(item["torrent_file"]), spec)
