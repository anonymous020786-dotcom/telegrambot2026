"""Message texts and inline keyboards (the bot's visual layer)."""

from __future__ import annotations

from datetime import datetime

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup

from ..services.downloader import MediaInfo
from ..utils import esc, human_duration, human_size, truncate


def _count(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def _date(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%d %b %Y")
    except ValueError:
        return None


def media_card(info: MediaInfo) -> str:
    lines = [f"<b>{esc(truncate(info.title, 180))}</b>"]
    meta = []
    if info.uploader:
        meta.append(f"👤 {esc(truncate(info.uploader, 40))}")
    if info.is_playlist:
        meta.append(f"📜 {len(info.entries)} items")
    elif info.duration:
        meta.append(f"⏱ {human_duration(info.duration)}")
    if info.view_count:
        meta.append(f"👁 {_count(info.view_count)}")
    if info.like_count:
        meta.append(f"❤️ {_count(info.like_count)}")
    if date := _date(info.upload_date):
        meta.append(f"📅 {date}")
    if meta:
        lines.append(" · ".join(meta))
    if info.heights:
        best = info.heights[0]
        est = info.estimate_size(best)
        lines.append(f"🎞 up to {best}p{' · ~' + human_size(est) if est else ''} · {esc(info.extractor)}")
    elif not info.is_playlist:
        lines.append(f"🌐 {esc(info.extractor)}")
    if info.is_live:
        lines.append("🔴 Live now: it can be downloaded once the stream ends")
    lines.append("\nChoose what to download:")
    return "\n".join(lines)


def media_keyboard(token: str, info: MediaInfo) -> InlineKeyboardMarkup:
    rows: list[list[B]] = []
    if info.is_playlist:
        n = len(info.entries)
        rows.append(
            [
                B(f"📜 Download all ({n})", callback_data=f"dl:{token}:pl:all"),
                B("🔢 First 10", callback_data=f"dl:{token}:pl:10"),
            ]
        )
        rows.append(
            [B("🎵 All as MP3", callback_data=f"dl:{token}:pl:mp3"), B("📋 List items", callback_data=f"list:{token}")]
        )
        return InlineKeyboardMarkup(rows)
    video_row = [B("🎬 Best", callback_data=f"dl:{token}:best")]
    heights = info.heights
    for h in (1080, 720, 360):
        if not heights or any(x >= h for x in heights):
            if heights and h > heights[0]:
                continue
            video_row.append(B(f"{h}p", callback_data=f"dl:{token}:q{h}"))
    rows.append(video_row[:4])
    rows.append(
        [
            B("🎵 MP3", callback_data=f"dl:{token}:a:mp3"),
            B("🎧 M4A", callback_data=f"dl:{token}:a:m4a"),
            B("🎼 FLAC", callback_data=f"dl:{token}:a:flac"),
        ]
    )
    rows.append([B("📐 All qualities", callback_data=f"qual:{token}"), B("🧩 Formats", callback_data=f"fmts:{token}")])
    extra = [B("🖼 Thumbnail", callback_data=f"dl:{token}:thumb")]
    if info.subtitles or info.auto_captions:
        extra.append(B("📝 Subtitles", callback_data=f"dl:{token}:subs"))
    extra.append(B("ℹ️ Details", callback_data=f"info:{token}"))
    rows.append(extra)
    rows.append(
        [
            B("📄 As file", callback_data=f"dl:{token}:doc"),
            B("🎞 GIF", callback_data=f"dl:{token}:gif"),
            B("✖ Close", callback_data="close"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def quality_keyboard(token: str, info: MediaInfo) -> InlineKeyboardMarkup:
    rows: list[list[B]] = []
    row: list[B] = []
    for h in info.heights[:12]:
        est = info.estimate_size(h)
        label = f"{h}p" + (f" · {human_size(est)}" if est else "")
        row.append(B(label, callback_data=f"dl:{token}:q{h}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            B("🎵 MP3 320k", callback_data=f"dl:{token}:a:mp3:320"),
            B("🔊 Opus", callback_data=f"dl:{token}:a:opus"),
            B("💿 WAV", callback_data=f"dl:{token}:a:wav"),
        ]
    )
    rows.append([B("⬅️ Back", callback_data=f"card:{token}")])
    return InlineKeyboardMarkup(rows)


def formats_keyboard(token: str, info: MediaInfo, limit: int = 30) -> InlineKeyboardMarkup:
    fmts = sorted(info.formats, key=lambda f: ((f.height or 0), f.tbr or 0), reverse=True)[:limit]
    rows = []
    row: list[B] = []
    for f in fmts:
        kind = "🎬" if f.has_video and f.has_audio else ("📺" if f.has_video else "🎵")
        label = f"{kind} {f.height or ''}{'p' if f.height else f.ext} {f.ext}"
        if f.filesize:
            label += f" {human_size(f.filesize)}"
        row.append(B(label[:40], callback_data=f"fmt:{token}:{f.format_id[:30]}:{int(f.has_audio)}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([B("⬅️ Back", callback_data=f"card:{token}")])
    return InlineKeyboardMarkup(rows)


def formats_table(info: MediaInfo, limit: int = 40) -> str:
    fmts = sorted(info.formats, key=lambda f: ((f.height or 0), f.tbr or 0), reverse=True)[:limit]
    lines = [f"<b>{esc(truncate(info.title, 120))}</b>", "<pre>ID          EXT   RES    SIZE     CODECS"]
    for f in fmts:
        res = f"{f.height}p" if f.height else ("audio" if f.has_audio else "?")
        codecs = "+".join(c.split(".")[0] for c in (f.vcodec, f.acodec) if c and c != "none")
        lines.append(f"{f.format_id[:11]:<11} {f.ext:<5} {res:<6} {human_size(f.filesize):<8} {codecs[:18]}")
    lines.append("</pre>Use <code>/getformat ID URL</code> or tap a format below.")
    return "\n".join(lines)


def details_text(info: MediaInfo) -> str:
    rows = [
        ("Title", info.title),
        ("Uploader", info.uploader),
        ("Site", info.extractor),
        ("Duration", human_duration(info.duration) if info.duration else None),
        ("Uploaded", _date(info.upload_date)),
        ("Views", _count(info.view_count) if info.view_count else None),
        ("Likes", _count(info.like_count) if info.like_count else None),
        ("Qualities", ", ".join(f"{h}p" for h in info.heights[:10]) or None),
        ("Formats", str(len(info.formats)) if info.formats else None),
        ("Chapters", str(len(info.chapters)) if info.chapters else None),
        ("Subtitles", ", ".join(info.subtitles[:15]) or None),
        ("Auto captions", f"{len(info.auto_captions)} languages" if info.auto_captions else None),
        ("URL", info.url),
    ]
    out = ["ℹ️ <b>Details</b>"]
    out += [f"<b>{k}:</b> {esc(truncate(str(v), 300))}" for k, v in rows if v]
    return "\n".join(out)


def chapters_text(info: MediaInfo) -> str:
    if not info.chapters:
        return "This video has no chapters."
    lines = [f"📑 <b>Chapters</b> · {esc(truncate(info.title, 80))}"]
    for i, ch in enumerate(info.chapters[:80], 1):
        lines.append(f"{i:>2}. <code>{human_duration(ch.get('start_time'))}</code> {esc(ch.get('title') or '')}")
    return "\n".join(lines)
