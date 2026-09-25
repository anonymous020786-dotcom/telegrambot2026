"""Sending results to Telegram, with fallbacks for files above the upload limit."""

from __future__ import annotations

import logging
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram import Bot, InputMediaDocument, InputMediaPhoto, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

from ..config import Settings
from ..utils import esc, human_size, kind_of_path
from . import media
from .linkserver import LinkServer
from .s3 import S3Store

log = logging.getLogger(__name__)

PHOTO_MAX = 10 * 1024 * 1024
TIMEOUTS = {"read_timeout": 900, "write_timeout": 900, "connect_timeout": 60, "pool_timeout": 60}


@dataclass
class Sent:
    file_id: str | None
    kind: str
    message: Message | None = None
    link: str | None = None


def choose_mode(size: int, limit: int, preferred: str, links: bool, s3: bool) -> str:
    """How to deliver a file of `size` bytes. Pure function (unit-tested)."""
    if size <= limit and preferred != "link" and preferred != "s3":
        return "telegram"
    if preferred == "link" and links:
        return "link"
    if preferred == "s3" and s3:
        return "s3"
    if size <= limit:
        return "telegram"
    if preferred == "split":
        return "split"
    if s3:
        return "s3"
    if links:
        return "link"
    return "split"


def make_zip(files: list[Path], dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    return dest


def _file_id(msg: Message) -> tuple[str | None, str]:
    for attr in ("video", "audio", "animation", "voice", "video_note", "document"):
        obj = getattr(msg, attr, None)
        if obj is not None:
            return obj.file_id, attr
    if msg.photo:
        return msg.photo[-1].file_id, "photo"
    return None, "unknown"


class Delivery:
    def __init__(self, settings: Settings, links: LinkServer | None = None, s3: S3Store | None = None):
        self.settings = settings
        self.links = links
        self.s3 = s3

    @property
    def limit(self) -> int:
        return self.settings.upload_limit_bytes

    def _input(self, path: Path, stack: ExitStack) -> Any:
        # With a local Bot API server sharing our filesystem, pass the path (uploaded by the server).
        if self.settings.bot_api_local_mode:
            return path
        return stack.enter_context(path.open("rb"))

    async def _thumbnail(self, path: Path) -> Path | None:
        if not media.ffmpeg_available():
            return None
        thumb = path.with_name(f".{path.stem}_thumb.jpg")
        try:
            await media.ffmpeg(
                "-ss",
                "1",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-q:v",
                "5",
                str(thumb),
                timeout=60,
            )
        except media.MediaError:
            try:
                await media.ffmpeg("-i", str(path), "-frames:v", "1", "-vf", "scale=320:-2", str(thumb), timeout=60)
            except media.MediaError:
                return None
        return thumb if thumb.exists() and thumb.stat().st_size < 200_000 else None

    async def send_one(
        self,
        bot: Bot,
        chat_id: int,
        path: Path,
        caption: str | None = None,
        *,
        as_document: bool = False,
        force_kind: str | None = None,
        reply_to: int | None = None,
        title: str | None = None,
        performer: str | None = None,
    ) -> Sent:
        """Upload a single file that fits within the limit."""
        with ExitStack() as stack:
            kind = force_kind or kind_of_path(path)
            common: dict[str, Any] = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": ParseMode.HTML,
                "reply_to_message_id": reply_to,
                **TIMEOUTS,
            }
            size = path.stat().st_size
            msg: Message
            if as_document and kind not in ("voice", "video_note"):
                msg = await bot.send_document(document=self._input(path, stack), filename=path.name, **common)
            elif kind == "video" and path.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv", ".webm"):
                info = {}
                if media.ffmpeg_available():
                    try:
                        info = media.summarize_probe(await media.probe(path))
                    except media.MediaError:
                        info = {}
                thumb = await self._thumbnail(path)
                try:
                    msg = await bot.send_video(
                        video=self._input(path, stack),
                        supports_streaming=True,
                        filename=path.name,
                        duration=int(info["duration"]) if info.get("duration") else None,
                        width=info.get("width"),
                        height=info.get("height"),
                        thumbnail=stack.enter_context(thumb.open("rb")) if thumb else None,
                        **common,
                    )
                finally:
                    if thumb:
                        thumb.unlink(missing_ok=True)
            elif kind == "video" and path.suffix.lower() == ".gif":
                msg = await bot.send_animation(animation=self._input(path, stack), filename=path.name, **common)
            elif kind == "audio" and force_kind != "voice":
                duration = None
                if media.ffmpeg_available():
                    try:
                        duration = int((media.summarize_probe(await media.probe(path)))["duration"] or 0) or None
                    except media.MediaError:
                        pass
                msg = await bot.send_audio(
                    audio=self._input(path, stack),
                    filename=path.name,
                    duration=duration,
                    title=title,
                    performer=performer,
                    **common,
                )
            elif kind == "voice":
                msg = await bot.send_voice(voice=self._input(path, stack), **common)
            elif kind == "video_note":
                common.pop("caption"), common.pop("parse_mode")
                msg = await bot.send_video_note(video_note=self._input(path, stack), **common)
            elif kind == "image" and path.suffix.lower() == ".gif":
                msg = await bot.send_animation(animation=self._input(path, stack), filename=path.name, **common)
            elif kind == "image" and size <= PHOTO_MAX and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                try:
                    msg = await bot.send_photo(photo=self._input(path, stack), **common)
                except BadRequest:  # e.g. extreme aspect ratio → send the original as a file
                    msg = await bot.send_document(document=self._input(path, stack), filename=path.name, **common)
            else:
                msg = await bot.send_document(document=self._input(path, stack), filename=path.name, **common)
        file_id, sent_kind = _file_id(msg)
        return Sent(file_id, sent_kind, msg)

    async def deliver(
        self,
        bot: Bot,
        chat_id: int,
        path: Path,
        caption: str | None,
        *,
        user_id: int,
        preferred: str = "auto",
        as_document: bool = False,
        force_kind: str | None = None,
        reply_to: int | None = None,
        title: str | None = None,
        performer: str | None = None,
    ) -> list[Sent]:
        size = path.stat().st_size
        mode = choose_mode(size, self.limit, preferred, bool(self.links), bool(self.s3))
        if mode == "telegram":
            return [
                await self.send_one(
                    bot,
                    chat_id,
                    path,
                    caption,
                    as_document=as_document,
                    force_kind=force_kind,
                    reply_to=reply_to,
                    title=title,
                    performer=performer,
                )
            ]
        if mode == "link" and self.links:
            url = self.links.link_for(path)
            hours = self.settings.link_ttl_hours
            text = (
                f"{caption + chr(10) + chr(10) if caption else ''}📦 <b>{esc(path.name)}</b> · {human_size(size)}\n"
                f'🔗 <a href="{esc(url)}">Download</a> (link expires in {hours:g} h)'
            )
            msg = await bot.send_message(
                chat_id, text, parse_mode=ParseMode.HTML, reply_to_message_id=reply_to, disable_web_page_preview=True
            )
            return [Sent(None, "link", msg, url)]
        if mode == "s3" and self.s3:
            url = await self.s3.upload(path, user_id)
            hours = self.settings.s3_url_ttl_hours
            text = (
                f"{caption + chr(10) + chr(10) if caption else ''}☁️ <b>{esc(path.name)}</b> · {human_size(size)}\n"
                f'🔗 <a href="{esc(url)}">Download from S3</a> (expires in {hours:g} h)'
            )
            msg = await bot.send_message(
                chat_id, text, parse_mode=ParseMode.HTML, reply_to_message_id=reply_to, disable_web_page_preview=True
            )
            return [Sent(None, "s3", msg, url)]
        # split
        parts = await media.split(path, self.limit)
        results = []
        raw = parts and parts[0].suffix[1:].isdigit()
        for i, part in enumerate(parts, 1):
            note = f"Part {i}/{len(parts)}"
            if raw and i == 1:
                note += " · rejoin with <code>cat file.* &gt; file</code> or 7-Zip"
            cap = f"{caption}\n{note}" if caption and i == 1 else note
            results.append(
                await self.send_one(
                    bot, chat_id, part, cap, as_document=raw or as_document, reply_to=reply_to if i == 1 else None
                )
            )
        return results

    async def send_album(
        self, bot: Bot, chat_id: int, files: list[Path], caption: str | None = None, as_document: bool = False
    ) -> int:
        """Send files as media groups of up to 10. Returns the number of files sent."""
        sent = 0
        photos_ok = not as_document
        for start in range(0, len(files), 10):
            chunk = files[start : start + 10]
            group = []
            for j, f in enumerate(chunk):
                cap = caption if (start == 0 and j == 0) else None
                use_photo = (
                    photos_ok
                    and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                    and f.stat().st_size <= PHOTO_MAX
                )
                if use_photo:
                    group.append(InputMediaPhoto(f.read_bytes(), caption=cap, parse_mode=ParseMode.HTML))
                else:
                    group.append(
                        InputMediaDocument(f.read_bytes(), filename=f.name, caption=cap, parse_mode=ParseMode.HTML)
                    )
            kinds = {type(m) for m in group}
            try:
                if len(group) == 1 or len(kinds) > 1:
                    for f in chunk:
                        await self.send_one(bot, chat_id, f, None, as_document=as_document)
                else:
                    await bot.send_media_group(chat_id, group, **TIMEOUTS)
            except BadRequest as exc:
                log.warning("album chunk failed (%s); sending individually", exc)
                for f in chunk:
                    try:
                        await self.send_one(bot, chat_id, f, None, as_document=True)
                    except BadRequest:
                        continue
            sent += len(chunk)
        return sent

    async def send_cached(
        self, bot: Bot, chat_id: int, kind: str, file_id: str, caption: str | None, reply_to: int | None = None
    ) -> Message:
        send = {
            "video": bot.send_video,
            "audio": bot.send_audio,
            "animation": bot.send_animation,
            "voice": bot.send_voice,
            "photo": bot.send_photo,
            "document": bot.send_document,
            "video_note": bot.send_video_note,
        }.get(kind, bot.send_document)
        field = {
            "video": "video",
            "audio": "audio",
            "animation": "animation",
            "voice": "voice",
            "photo": "photo",
            "video_note": "video_note",
        }.get(kind, "document")
        kwargs: dict[str, Any] = {"chat_id": chat_id, field: file_id, "reply_to_message_id": reply_to}
        if kind != "video_note":
            kwargs.update(caption=caption, parse_mode=ParseMode.HTML)
        return await send(**kwargs)
