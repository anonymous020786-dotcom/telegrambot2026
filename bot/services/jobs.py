"""Download job queue with live progress, cancellation, per-user limits and caching."""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from yt_dlp.utils import DownloadCancelled, DownloadError

from ..config import Settings
from ..db import Database, User
from ..utils import cache_key, domain_of, esc, human_duration, human_size, progress_bar, truncate
from .delivery import Delivery, make_zip
from .downloader import AdultBlocked, Cancelled, Downloader, Preset, friendly_error
from .images import FoundImage, ImageService
from .policy import ADULT_BLOCKED_MESSAGE, Policy

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 2.5


@dataclass
class Job:
    user_id: int
    chat_id: int
    url: str
    kind: str = "media"  # media | images | gallery | imagelist
    preset: Preset = field(default_factory=Preset)
    options: dict[str, Any] = field(default_factory=dict)
    priority: int = 5
    reply_to: int | None = None
    id: str = field(default_factory=lambda: secrets.token_hex(3))
    status: str = "queued"  # queued | held | running | done | failed | cancelled
    phase: str = "queued"
    title: str | None = None
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: float | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    message_id: int | None = None
    error: str | None = None
    cancelled: bool = False
    files_sent: int = 0
    bytes_sent: int = 0

    @property
    def fraction(self) -> float:
        return self.downloaded / self.total if self.total else 0.0

    def describe(self) -> str:
        what = self.preset.label() if self.kind == "media" else self.kind
        return f"<code>{self.id}</code> · {what} · {esc(truncate(self.title or domain_of(self.url), 40))}"


def progress_text(job: Job) -> str:
    icon = {"queued": "🕒", "downloading": "⬇️", "processing": "⚙️", "uploading": "⬆️"}.get(job.phase, "⏳")
    phase = {
        "queued": "Queued",
        "downloading": "Downloading",
        "processing": "Processing",
        "uploading": "Uploading",
    }.get(job.phase, job.phase.title())
    what = (
        job.preset.label()
        if job.kind == "media"
        else {"images": "images", "gallery": "gallery", "imagelist": "images"}.get(job.kind, job.kind)
    )
    lines = [f"{icon} <b>{phase}</b> · {esc(what)}", f"<b>{esc(truncate(job.title or job.url, 90))}</b>"]
    if job.phase == "downloading":
        if job.total:
            lines.append(f"{progress_bar(job.fraction)} {job.fraction * 100:4.1f}%")
            detail = f"{human_size(job.downloaded)} / {human_size(job.total)}"
        else:
            detail = human_size(job.downloaded)
        if job.speed:
            detail += f" · {human_size(job.speed)}/s"
        if job.eta:
            detail += f" · ETA {human_duration(job.eta)}"
        lines.append(detail)
    elif job.phase == "processing":
        lines.append("Merging / converting with ffmpeg…")
    lines.append(f"<i>Job {job.id}</i>")
    return "\n".join(lines)


def cancel_keyboard(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data=f"job:cancel:{job.id}")]])


class QuotaExceeded(Exception):
    pass


class PolicyBlocked(Exception):
    """The URL is refused by content policy (DRM service or admin-blocked domain)."""


# Social sites where a post can be photos only; yt-dlp then finds "no video" and gallery-dl takes over.
PHOTO_POST_SITES = (
    "instagram.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "facebook.com",
    "threads.net",
    "threads.com",
    "pinterest.com",
    "reddit.com",
    "tumblr.com",
    "bsky.app",
    "vk.com",
    "weibo.com",
    "imgur.com",
    "flickr.com",
    "deviantart.com",
    "artstation.com",
)
NO_VIDEO_HINTS = (
    "no video formats",
    "no video in this",
    "there's no video",
    "no media found",
    "no video could be found",
    "unsupported url",
    "nothing was downloaded",
)


class JobManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        downloader: Downloader,
        images: ImageService,
        delivery: Delivery,
        policy: Policy | None = None,
    ):
        self.settings = settings
        self.policy = policy or Policy(db, settings)
        self.db = db
        self.downloader = downloader
        self.images = images
        self.delivery = delivery
        self.bot: Bot | None = None
        self.jobs: dict[str, Job] = {}
        self._heap: list[tuple[int, int, str]] = []
        self._seq = itertools.count()
        self._wake = asyncio.Event()
        self._workers: list[asyncio.Task] = []
        self.held_users: set[int] = set()
        self.paused_all = False

    # ------------------------------------------------------------------ lifecycle
    def start(self, bot: Bot) -> None:
        self.bot = bot
        for i in range(self.settings.max_concurrent_jobs):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"job-worker-{i}"))

    async def stop(self) -> None:
        for job in self.jobs.values():
            job.cancelled = True
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()

    # ------------------------------------------------------------------ queries
    def user_jobs(self, uid: int) -> list[Job]:
        return [j for j in self.jobs.values() if j.user_id == uid and j.status in ("queued", "held", "running")]

    def active(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status in ("queued", "held", "running")]

    def position(self, job: Job) -> int:
        order = sorted((p, s, jid) for p, s, jid in self._heap if self.jobs.get(jid, job).status == "queued")
        ids = [jid for _, _, jid in order]
        return ids.index(job.id) + 1 if job.id in ids else 0

    def running_count(self, uid: int | None = None) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "running" and (uid is None or j.user_id == uid))

    # ------------------------------------------------------------------ submit / control
    async def remaining_quota(self, user: User) -> int | None:
        """Downloads left today (None = unlimited)."""
        limit = user.daily_limit if user.daily_limit is not None else self.settings.daily_limit
        if user.is_admin or limit == 0:
            return None
        used, _ = await self.db.usage_today(user.id)
        pending = len(self.user_jobs(user.id))
        return max(0, limit - used - pending)

    async def submit(self, job: Job, user: User) -> Job:
        if reason := await self.policy.refusal(job.url):
            raise PolicyBlocked(reason)
        left = await self.remaining_quota(user)
        if left is not None and left <= 0:
            raise QuotaExceeded()
        self.jobs[job.id] = job
        if user.id in self.held_users:
            job.status = "held"
        else:
            heapq.heappush(self._heap, (job.priority, next(self._seq), job.id))
            self._wake.set()
        if self.bot:
            try:
                msg = await self.bot.send_message(
                    job.chat_id,
                    progress_text(job),
                    parse_mode=ParseMode.HTML,
                    reply_markup=cancel_keyboard(job),
                    reply_to_message_id=job.reply_to,
                )
                job.message_id = msg.message_id
            except TelegramError as exc:
                log.warning("could not send progress message: %s", exc)
        return job

    def cancel(self, job_id: str, uid: int | None = None) -> Job | None:
        job = self.jobs.get(job_id)
        if not job or (uid is not None and job.user_id != uid) or job.status not in ("queued", "held", "running"):
            return None
        job.cancelled = True
        if job.status in ("queued", "held"):
            job.status = "cancelled"
            asyncio.get_running_loop().create_task(self._finish_message(job, "✖ Cancelled"))
        return job

    def cancel_all(self, uid: int) -> int:
        return sum(1 for j in list(self.user_jobs(uid)) if self.cancel(j.id, uid))

    def hold(self, uid: int) -> int:
        self.held_users.add(uid)
        n = 0
        for j in self.user_jobs(uid):
            if j.status == "queued":
                j.status = "held"
                n += 1
        return n

    def release(self, uid: int) -> int:
        self.held_users.discard(uid)
        n = 0
        for j in self.user_jobs(uid):
            if j.status == "held":
                j.status = "queued"
                heapq.heappush(self._heap, (j.priority, next(self._seq), j.id))
                n += 1
        self._wake.set()
        return n

    def move_to_front(self, job_id: str, uid: int | None = None) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status != "queued" or (uid is not None and job.user_id != uid):
            return False
        job.priority = 0
        heapq.heappush(self._heap, (0, next(self._seq), job.id))
        self._wake.set()
        return True

    # ------------------------------------------------------------------ worker loop
    async def _next_job(self) -> Job:
        while True:
            deferred = []
            chosen = None
            while self._heap and not self.paused_all:
                prio, seq, jid = heapq.heappop(self._heap)
                job = self.jobs.get(jid)
                # Stale entries (moved to front, cancelled, already started) are skipped by status.
                if not job or job.status != "queued":
                    continue
                if self.running_count(job.user_id) >= self.settings.per_user_concurrent_jobs:
                    deferred.append((prio, seq, jid))
                    continue
                chosen = job
                break
            for item in deferred:
                heapq.heappush(self._heap, item)
            if chosen:
                chosen.status = "running"
                return chosen
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)

    async def _worker(self, index: int) -> None:
        while True:
            job = await self._next_job()
            try:
                await self._run(job)
            except Exception:
                log.exception("job %s crashed", job.id)
                job.status = "failed"
                job.error = "Internal error"
                await self._finish_message(job, "❌ Internal error", retry=True)
            finally:
                job.finished_at = time.time()
                self._wake.set()
                self._forget_later(job)

    def _forget_later(self, job: Job) -> None:
        async def forget() -> None:
            await asyncio.sleep(3600)
            self.jobs.pop(job.id, None)

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(forget())

    # ------------------------------------------------------------------ progress reporting
    def _hook(self, job: Job):
        def hook(d: dict[str, Any]) -> None:
            if job.cancelled:
                raise DownloadCancelled()
            status = d.get("status")
            if "postprocessor" in d:
                if status == "started":
                    job.phase = "processing"
                return
            if status == "downloading":
                job.phase = "downloading"
                job.downloaded = d.get("downloaded_bytes") or 0
                job.total = d.get("total_bytes") or d.get("total_bytes_estimate") or job.total
                job.speed = d.get("speed")
                job.eta = d.get("eta")
                info = d.get("info_dict") or {}
                job.title = job.title or info.get("title")
            elif status == "finished":
                job.phase = "processing"

        return hook

    async def _progress_loop(self, job: Job) -> None:
        last = ""
        while job.status == "running":
            await asyncio.sleep(PROGRESS_INTERVAL)
            text = progress_text(job)
            if text != last and job.message_id and self.bot:
                last = text
                try:
                    await self.bot.edit_message_text(
                        text, job.chat_id, job.message_id, parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard(job)
                    )
                except BadRequest:
                    pass
                except TelegramError as exc:
                    log.debug("progress edit failed: %s", exc)

    async def _finish_message(self, job: Job, text: str, retry: bool = False, delete: bool = False) -> None:
        if not (self.bot and job.message_id):
            return
        try:
            if delete:
                await self.bot.delete_message(job.chat_id, job.message_id)
                return
            markup = None
            if retry:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Retry", callback_data=f"job:retry:{job.id}")]])
            body = f"{text}\n<b>{esc(truncate(job.title or job.url, 90))}</b>"
            if job.error and not text.endswith(job.error):
                body += f"\n<i>{esc(job.error)}</i>"
            await self.bot.edit_message_text(
                body,
                job.chat_id,
                job.message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except TelegramError:
            pass

    # ------------------------------------------------------------------ execution
    def job_dir(self, job: Job) -> Path:
        return self.settings.download_dir / f"{int(job.created_at)}_{job.user_id}_{job.id}"

    async def _run(self, job: Job) -> None:
        assert self.bot is not None
        job.started_at = time.time()
        job.phase = "downloading"
        user = await self.db.ensure_user(job.user_id)
        reporter = asyncio.create_task(self._progress_loop(job))
        out_dir = self.job_dir(job)
        used_link = False
        try:
            if job.kind == "media":
                try:
                    used_link = await self._run_media(job, user, out_dir)
                except DownloadError as exc:
                    if not self._photo_post_fallback(job, exc):
                        raise
                    # A photo/carousel post on a social site: fetch the pictures with gallery-dl instead.
                    shutil.rmtree(out_dir, ignore_errors=True)
                    job.kind, job.options = "gallery", {**job.options, "mode": "auto", "limit": 50}
                    used_link = await self._run_images(job, user, out_dir)
            else:
                used_link = await self._run_images(job, user, out_dir)
            job.status = "done"
            await self._finish_message(job, "", delete=True)
        except (Cancelled, DownloadCancelled):
            job.status = "cancelled"
            await self._finish_message(job, "✖ Cancelled")
        except AdultBlocked:
            job.status = "failed"
            job.error = ADULT_BLOCKED_MESSAGE
            await self._finish_message(job, "🔞 Not downloaded")
        except DownloadError as exc:
            job.status = "failed"
            job.error = friendly_error(exc)
            await self._finish_message(job, "❌ Download failed", retry=True)
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.status = "failed"
            job.error = friendly_error(exc)
            await self._finish_message(job, "❌ Failed", retry=True)
        finally:
            reporter.cancel()
            if job.status in ("failed", "cancelled"):
                await self.db.add_history(
                    job.user_id,
                    job.url,
                    title=job.title,
                    kind=job.kind,
                    preset=self._preset_record(job),
                    status=job.status,
                    error=job.error,
                )
            if not used_link:
                shutil.rmtree(out_dir, ignore_errors=True)

    @staticmethod
    def _photo_post_fallback(job: Job, exc: Exception) -> bool:
        domain = domain_of(job.url)
        on_social = any(domain == d or domain.endswith("." + d) for d in PHOTO_POST_SITES)
        return (
            on_social
            and job.preset.mode in ("video", "format")
            and any(hint in str(exc).lower() for hint in NO_VIDEO_HINTS)
        )

    def _preset_record(self, job: Job) -> dict[str, Any]:
        return {"kind": job.kind, "preset": job.preset.to_dict(), "options": job.options}

    def caption(self, user: User, title: str | None, url: str, extra: str = "") -> str | None:
        style = user.pref("caption")
        if style == "off":
            return None
        title_line = f"<b>{esc(truncate(title or 'Download', 200))}</b>"
        if style == "minimal":
            return title_line
        link = f'<a href="{esc(url)}">{esc(domain_of(url) or "source")}</a>'
        return f"{title_line}\n{link}{' · ' + extra if extra else ''}"

    async def _run_media(self, job: Job, user: User, out_dir: Path) -> bool:
        assert self.bot is not None
        key = cache_key(job.url, job.preset.to_dict())
        as_document = bool(job.options.get("as_document", user.pref("as_document")))
        force_kind = job.options.get("force_kind")
        cached = None if (job.preset.playlist or as_document or force_kind) else await self.db.cache_get(key)
        if cached:
            job.title = cached["title"]
            caption = self.caption(user, cached["title"], job.url, f"{job.preset.label()} · ⚡ cached")
            await self.delivery.send_cached(
                self.bot, job.chat_id, cached["kind"], cached["file_id"], caption, reply_to=job.reply_to
            )
            await self.db.add_history(
                job.user_id,
                job.url,
                title=cached["title"],
                kind=cached["kind"],
                preset=self._preset_record(job),
                status="done",
                size=cached["size"],
                file_id=cached["file_id"],
            )
            await self.db.add_usage(job.user_id, 1, 0)
            return False

        allow_adult = await self.policy.adult_allowed(user)
        info, files = await self.downloader.download(job.url, job.preset, out_dir, self._hook(job), allow_adult)
        if job.cancelled:
            raise Cancelled()
        job.title = (info or {}).get("title") or job.title or files[0].stem
        performer = (info or {}).get("artist") or (info or {}).get("uploader")
        job.phase = "uploading"
        used_link = False
        first_file_id = first_kind = None
        total_size = 0
        for index, path in enumerate(files):
            if job.cancelled:
                raise Cancelled()
            if force_kind == "gif":
                from . import media

                path = await media.to_gif(path)
            elif force_kind == "voice":
                from . import media

                path = await media.to_voice(path)
            elif force_kind == "video_note":
                from . import media

                path = await media.to_video_note(path)
            size = path.stat().st_size
            total_size += size
            title = job.title if len(files) == 1 else path.stem
            extra = f"{job.preset.label()} · {human_size(size)}"
            if len(files) > 1:
                extra += f" · {index + 1}/{len(files)}"
            sent = await self.delivery.deliver(
                self.bot,
                job.chat_id,
                path,
                self.caption(user, title, job.url, extra),
                user_id=job.user_id,
                preferred=user.pref("delivery"),
                as_document=as_document,
                force_kind={"voice": "voice", "video_note": "video_note"}.get(force_kind or ""),
                reply_to=job.reply_to if index == 0 else None,
                title=title,
                performer=performer,
            )
            used_link = used_link or any(s.kind == "link" for s in sent)
            if len(sent) == 1 and sent[0].file_id and first_file_id is None:
                first_file_id, first_kind = sent[0].file_id, sent[0].kind
            job.files_sent += 1
        job.bytes_sent = total_size
        if first_file_id and len(files) == 1 and not force_kind and not as_document:
            await self.db.cache_put(key, first_file_id, first_kind or "document", job.title, total_size)
        await self.db.add_history(
            job.user_id,
            job.url,
            title=job.title,
            kind=first_kind or "media",
            preset=self._preset_record(job),
            status="done",
            size=total_size,
            file_id=first_file_id if len(files) == 1 else None,
        )
        await self.db.add_usage(job.user_id, 1, total_size)
        return used_link

    async def _run_images(self, job: Job, user: User, out_dir: Path) -> bool:
        assert self.bot is not None
        opts = job.options
        limit = int(opts.get("limit") or self.settings.max_images_per_request)
        if job.kind == "gallery":
            job.phase = "downloading"
            files = await self.images.gallery_dl(job.url, out_dir, limit=limit)
            job.title = job.title or domain_of(job.url)
        else:
            if job.kind == "imagelist":
                found = [FoundImage(u, "direct") for u in opts.get("urls", [])]
                job.title = job.title or f"{len(found)} images"
            else:
                found, title = await self.images.find(job.url)
                job.title = title
            if opts.get("pick") == "largest":
                found.sort(key=lambda f: f.width, reverse=True)
            saved = await self.images.download(
                found, out_dir, referer=job.url, min_width=int(opts.get("min_width", 0)), limit=limit
            )
            if opts.get("pick") == "largest" and saved:
                saved = [max(saved, key=lambda s: s.width * s.height)]
            files = [s.path for s in saved]
        if job.cancelled:
            raise Cancelled()
        if not files:
            raise DownloadError("No images found (or all were smaller than your filter)")
        job.phase = "uploading"
        total = sum(f.stat().st_size for f in files)
        caption = self.caption(
            user, job.title, job.url, f"{len(files)} file{'s' if len(files) != 1 else ''} · {human_size(total)}"
        )
        used_link = False
        mode = opts.get("mode", "auto")
        if mode == "zip" or (mode == "auto" and len(files) > 30):
            archive = make_zip(files, out_dir / f"{(job.title or 'images')[:60].strip() or 'images'}.zip")
            sent = await self.delivery.deliver(
                self.bot,
                job.chat_id,
                archive,
                caption,
                user_id=job.user_id,
                preferred=user.pref("delivery"),
                as_document=True,
                reply_to=job.reply_to,
            )
            used_link = any(s.kind == "link" for s in sent)
        else:
            await self.delivery.send_album(
                self.bot,
                job.chat_id,
                files,
                caption,
                as_document=bool(opts.get("as_document", user.pref("as_document"))),
            )
        job.files_sent = len(files)
        job.bytes_sent = total
        await self.db.add_history(
            job.user_id,
            job.url,
            title=job.title,
            kind="images",
            preset=self._preset_record(job),
            status="done",
            size=total,
        )
        await self.db.add_usage(job.user_id, 1, total)
        return used_link

    # ------------------------------------------------------------------ housekeeping
    def cleanup_old_files(self, max_age_hours: float | None = None) -> tuple[int, int]:
        """Delete job folders older than the retention period. Returns (folders, bytes)."""
        max_age = (max_age_hours if max_age_hours is not None else self.settings.file_retention_hours) * 3600
        cutoff = time.time() - max_age
        running_dirs = {self.job_dir(j) for j in self.active()}
        removed = freed = 0
        for d in self.settings.download_dir.glob("*"):
            if d in running_dirs:
                continue
            try:
                if d.stat().st_mtime < cutoff:
                    size = (
                        sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.is_dir() else d.stat().st_size
                    )
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True)
                    else:
                        d.unlink(missing_ok=True)
                    removed += 1
                    freed += size
            except OSError:
                continue
        return removed, freed
