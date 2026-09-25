"""Runs EVERY registered command through the real access guard with realistic input.

A command fails this test if it raises (the guard's "Something went wrong" reply), answers with nothing, or
queues a download job that doesn't finish successfully (except where failure is the correct outcome, e.g.
asking for subtitles of a video that has none). New commands must be added to CASES, or this test fails.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from bot.app import build_services, load_handlers
from bot.handlers.common import guard
from bot.registry import REGISTRY
from tests.conftest import FakeBot, FakeMessage, FakeTgMedia, make_full_context, make_update, needs_ffmpeg

load_handlers()
pytestmark = needs_ffmpeg

W = "{web}"
V = f"{W}/sample.mp4"
PAGE = f"{W}/gallery.html"

# fmt: off
# name: (args, replied media kind or None). Replied kinds: video, image, file, cookies, text.
CASES: dict[str, tuple[list[str], str | None]] = {
    # general
    "start": ([], None), "help": ([], None), "menu": ([], None), "about": ([], None), "ping": ([], None),
    "version": ([], None), "commands": ([], None), "privacy": ([], None), "feedback": (["Great bot"], None),
    "id": ([], None),
    # download
    "dl": ([V], None), "video": ([V], None), "audio": ([V], None), "best": ([V], None), "worst": ([V], None),
    "hd": ([V], None), "fhd": ([V], None), "4k": ([V], None), "preview": ([V], None), "q": (["480", V], None),
    "formats": ([V], None), "getformat": (["0", V], None), "clip": ([V, "0:01", "0:03"], None),
    "playlist": ([V], None), "batch": ([V, f"{W}/img/big.png"], None), "direct": ([V], None), "doc": ([V], None),
    "gif": ([V], None),
    # audio
    "mp3": ([V, "320"], None), "m4a": ([V], None), "opus": ([V], None), "flac": ([V], None), "wav": ([V], None),
    "aac": ([V], None), "ogg": ([V], None), "voice": ([V], None),
    # extras
    "info": ([V], None), "thumb": ([V], None), "subs": ([V, "en"], None), "desc": ([V], None),
    "chapters": ([V], None), "splitchapters": ([V], None), "meta": ([V], None), "size": ([V], None),
    # images
    "images": ([PAGE, "300"], None), "img": ([PAGE], None), "gallery": ([PAGE], None), "imgzip": ([PAGE], None),
    "album": ([PAGE], None), "og": ([PAGE], None), "favicon": ([PAGE], None), "imgcount": ([PAGE], None),
    # tools (reply to a file)
    "convert": (["webm"], "video"), "compress": (["30"], "video"), "trim": (["0:01", "0:03"], "video"),
    "extractaudio": (["mp3"], "video"), "mute": ([], "video"), "speed": (["2"], "video"),
    "rotate": (["90"], "video"), "resize": (["120"], "video"), "togif": ([], "video"), "frame": (["1"], "video"),
    "volume": (["1.5"], "video"), "reverse": ([], "video"), "tovoice": ([], "video"), "tonote": ([], "video"),
    "mediainfo": ([], "video"), "imgconvert": (["webp"], "image"), "imgresize": (["100"], "image"),
    "imginfo": ([], "image"),
    # search (network replaced by a fake search that returns the local sample)
    "yt": (["sample", "clip"], None), "sc": (["sample"], None), "ytmp3": (["sample"], None),
    # queue (phase 2)
    "queue": ([], None), "status": ([], None), "cancel": ([], None), "cancelall": ([], None), "pause": ([], None),
    "resume": ([], None), "retry": ([], None), "top": (["nope"], None),
    # library (phase 2)
    "history": ([], None), "favs": ([], None), "last": ([], None), "redo": (["1"], None), "resend": (["1"], None),
    "fav": (["1"], None), "unfav": (["1"], None), "find": (["sample"], None), "clearhistory": ([], None),
    "exporthistory": ([], None),
    # settings
    "settings": ([], None), "setquality": (["720"], None), "setformat": (["mkv"], None),
    "setaudio": (["m4a"], None), "setbitrate": (["256"], None), "setdelivery": (["split"], None),
    "setcaption": (["minimal"], None), "setplaylistlimit": (["10"], None), "setdoc": (["off"], None),
    "setsubs": (["off"], None), "setthumb": (["on"], None), "setmeta": (["on"], None), "setsublang": (["es"], None),
    "settz": (["Asia/Kolkata"], None), "setname": (["%(title).40B"], None), "reset": ([], None),
    "setadult": ([], None),
    # account
    "me": ([], None), "stats": ([], None), "quota": ([], None), "limits": ([], None), "sites": (["vimeo"], None),
    "supported": (["https://vimeo.com/76979871"], None), "redeem": (["not-a-code"], None), "request": ([], None),
    # watch
    "watch": ([V], None), "watches": ([], None), "unwatch": (["1"], None), "checknow": ([], None),
    "schedule": (["in", "2h", V], None), "schedules": ([], None), "unschedule": (["1"], None),
    # utilities
    "qr": (["hello world"], None), "unshorten": ([f"{W}/redirect"], None), "headers": ([V], None),
    "mime": ([V], None), "hash": ([], "file"), "urls": ([], "text"),
    # admin
    "admin": ([], None), "users": ([], None), "user": (["1"], None), "allow": (["555"], None),
    "deny": (["555"], None), "ban": (["556"], None), "unban": (["556"], None), "promote": (["557"], None),
    "demote": (["557"], None), "setlimit": (["558", "5"], None), "broadcast": (["Maintenance at 10"], None),
    "maintenance": (["off"], None), "invite": (["2", "24"], None), "invites": ([], None),
    "revoke": (["nope"], None), "sysinfo": ([], None), "disk": ([], None), "cleanup": ([], None),
    "logs": ([], None), "updateytdlp": ([], None), "backup": ([], None), "globalstats": ([], None),
    "jobs": ([], None), "killjob": (["nope"], None), "pauseall": ([], None), "resumeall": ([], None),
    "clearcache": ([], None), "feedbacks": ([], None), "restart": ([], None), "adult": (["optin"], None),
    "blocksite": (["blocked.example"], None), "unblocksite": (["blocked.example"], None),
    "blockedsites": ([], None), "cookies": ([], "cookies"), "sitecheck": ([], None), "pagedebug": ([PAGE], None),
    "siterule": (["list"], None),
    "inline": ([], None),
}

# Run after phase 1's downloads finish: they inspect or control the queue and history those produced.
PHASE2 = {
    "queue", "status", "cancel", "cancelall", "pause", "resume", "retry", "top", "history", "favs", "last", "redo",
    "resend", "fav", "unfav", "find", "clearhistory", "exporthistory", "me", "stats", "jobs", "killjob",
    "pauseall", "resumeall", "globalstats", "backup", "cleanup", "clearcache", "restart",
}
# fmt: on
# Commands whose job correctly fails on the local sample: a bare MP4 has no thumbnail or subtitles, and /retry
# re-runs the latest failed item (one of those). The failure must carry an accurate explanation.
EXPECTED_JOB_FAILURES = {"thumb": "no thumbnail", "subs": "no subtitles", "retry": "no "}


def test_every_command_has_a_case():
    missing = sorted(set(REGISTRY) - set(CASES))
    stale = sorted(set(CASES) - set(REGISTRY))
    assert not missing, f"add these commands to CASES: {missing}"
    assert not stale, f"these CASES no longer exist: {stale}"


@pytest.fixture
async def services(settings, monkeypatch, web):
    settings.daily_limit = 0
    s = build_services(settings)
    await s.db.connect()

    async def fake_search(query, count=8, site="yt"):
        return [{"title": f"Sample {site} result", "url": f"{web}/sample.mp4", "duration": 4, "uploader": "Local"}]

    monkeypatch.setattr(s.downloader, "search", fake_search)
    monkeypatch.setattr("bot.handlers.content.default_targets", lambda: [("Local sample", f"{web}/sample.mp4")])

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"Successfully installed yt-dlp-2099.1.1\n", None

    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*args, **kwargs):  # only /updateytdlp's pip call is faked; ffmpeg runs for real
        if "pip" in args:
            return FakeProc()
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    yield s
    await s.jobs.stop()
    await s.db.close()


def _replied(kind: str | None, bot: FakeBot, sample_video: Path, tmp: Path, web: str) -> FakeMessage | None:
    if kind is None:
        return None
    if kind == "video":
        return FakeMessage(bot, 1, video=FakeTgMedia(sample_video))
    if kind == "image":
        img = tmp / "photo.png"
        Image.new("RGB", (400, 300), (30, 120, 200)).save(img)
        return FakeMessage(bot, 1, document=FakeTgMedia(img, mime_type="image/png"))
    if kind == "file":
        return FakeMessage(bot, 1, document=FakeTgMedia(sample_video))
    if kind == "cookies":
        c = tmp / "cookies.txt"
        c.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tsession\tabc\n")
        return FakeMessage(bot, 1, document=FakeTgMedia(c), delete=lambda: asyncio.sleep(0))
    if kind == "text":
        return FakeMessage(bot, 1, text=f"links: {web}/a and {web}/b.jpg")
    raise ValueError(kind)


async def _run(services, name: str, web: str, sample_video: Path, tmp: Path) -> tuple[FakeBot, SimpleNamespace]:
    args_t, kind = CASES[name]
    args = [a.replace(W, web) for a in args_t]
    bot = FakeBot()
    services.last_command.clear()
    update = make_update(bot, 1, text=f"/{name} " + " ".join(args))
    update.effective_message.reply_to_message = _replied(kind, bot, sample_video, tmp, web)
    context = make_full_context(services, bot, args)
    await guard(REGISTRY[name])(update, context)
    return bot, context


async def _wait_jobs(services, timeout: float = 240) -> None:
    for _ in range(int(timeout / 0.2)):
        if not services.jobs.active():
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"jobs still running: {[j.describe() for j in services.jobs.active()]}")


async def test_every_command_works(services, web, sample_video, tmp_path):
    services.jobs.start(FakeBot())
    await services.db.upsert_user(1, "owner", "Owner")
    problems: list[str] = []
    job_owner: dict[str, str] = {}

    async def run_phase(names: list[str]) -> None:
        for name in names:
            before = set(services.jobs.jobs)
            try:
                bot, context = await _run(services, name, web, sample_video, tmp_path)
            except Exception as exc:  # noqa: BLE001 - report every broken command at once
                problems.append(f"/{name}: raised {exc!r}")
                continue
            for jid in set(services.jobs.jobs) - before:
                job_owner[jid] = name
            texts = " ".join(bot.texts())
            if "Something went wrong" in texts:
                problems.append(f"/{name}: {texts[:200]}")
            elif not bot.calls and not (set(services.jobs.jobs) - before):
                problems.append(f"/{name}: no reply and no job")
            if name == "restart":
                assert context.application.stopped

    phase1 = [n for n in sorted(REGISTRY) if n not in PHASE2]
    await run_phase(phase1)
    await _wait_jobs(services)
    await run_phase(sorted(PHASE2))
    services.jobs.paused_all = False
    await _wait_jobs(services)

    for jid, name in job_owner.items():
        job = services.jobs.jobs.get(jid)
        if job is None or job.status == "cancelled":
            continue  # /cancel and /cancelall legitimately cancel queued work
        if name in EXPECTED_JOB_FAILURES:
            if job.status != "failed" or EXPECTED_JOB_FAILURES[name] not in (job.error or "").lower():
                problems.append(f"/{name}: expected an explained failure, got {job.status}: {job.error}")
        elif job.status != "done":
            problems.append(f"/{name}: job {job.status}: {job.error}")
    assert not problems, "\n".join(problems)
