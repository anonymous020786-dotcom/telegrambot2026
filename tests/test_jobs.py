"""Job queue behaviour, including full downloads delivered through a fake Telegram bot."""

import asyncio

import pytest

from bot.app import build_services
from bot.services.downloader import Preset
from bot.services.jobs import Job, QuotaExceeded
from tests.conftest import FakeBot, needs_ffmpeg


@pytest.fixture
async def services(settings):
    s = build_services(settings)
    await s.db.connect()
    yield s
    await s.jobs.stop()
    await s.db.close()


async def wait_for(predicate, timeout=60):
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("timed out")


@needs_ffmpeg
async def test_mp3_job_end_to_end_and_cache(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(10, "u", "U")
    job = Job(
        user_id=10,
        chat_id=10,
        url=f"{web}/sample.mp4",
        preset=Preset(mode="audio", audio_format="mp3", embed_thumbnail=False),
    )
    await services.jobs.submit(job, user)
    await wait_for(lambda: job.status in ("done", "failed"))
    assert job.status == "done", job.error
    assert bot.sent_files[0][0] == "audio" and bot.sent_files[0][1].endswith(".mp3")
    assert ("delete_message", job.message_id) in bot.calls  # progress message cleaned up
    hist = await services.db.last_history(10)
    assert hist["status"] == "done" and hist["file_id"] == "FILE_audio_1"
    assert await services.db.usage_today(10) == (1, hist["size"])
    assert not services.jobs.job_dir(job).exists()  # temp files removed

    again = Job(
        user_id=10,
        chat_id=10,
        url=f"{web}/sample.mp4",
        preset=Preset(mode="audio", audio_format="mp3", embed_thumbnail=False),
    )
    await services.jobs.submit(again, user)
    await wait_for(lambda: again.status == "done")
    assert len(bot.sent_files) == 2
    assert bot.sent_files[1] == ("audio", "FILE_audio_1")  # re-sent by file_id, no re-download


@needs_ffmpeg
async def test_images_job_album_and_zip(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(11, "i", "I")
    album = Job(
        user_id=11, chat_id=11, url=f"{web}/gallery.html", kind="images", options={"mode": "album", "min_width": 300}
    )
    await services.jobs.submit(album, user)
    await wait_for(lambda: album.status in ("done", "failed"))
    assert album.status == "done", album.error
    assert album.files_sent >= 5
    zipped = Job(user_id=11, chat_id=11, url=f"{web}/gallery.html", kind="images", options={"mode": "zip"})
    await services.jobs.submit(zipped, user)
    await wait_for(lambda: zipped.status in ("done", "failed"))
    assert zipped.status == "done"
    assert any(kind == "document" and name.endswith(".zip") for kind, name in bot.sent_files)


async def test_failed_job_records_error_and_offers_retry(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(12, "f", "F")
    job = Job(user_id=12, chat_id=12, url=f"{web}/missing.mp4", preset=Preset(embed_thumbnail=False))
    await services.jobs.submit(job, user)
    await wait_for(lambda: job.status in ("done", "failed"))
    assert job.status == "failed" and job.error
    assert (await services.db.last_history(12))["status"] == "failed"
    assert any("failed" in t.lower() for t in bot.texts())


async def test_queue_controls_without_workers(services):
    bot = FakeBot()
    services.jobs.bot = bot  # no workers started: jobs stay queued
    user = await services.db.upsert_user(13, "q", "Q")
    jobs = [Job(user_id=13, chat_id=13, url=f"https://example.com/{i}") for i in range(3)]
    for j in jobs:
        await services.jobs.submit(j, user)
    assert [services.jobs.position(j) for j in jobs] == [1, 2, 3]
    assert services.jobs.move_to_front(jobs[2].id, 13)
    assert services.jobs.position(jobs[2]) == 1
    assert services.jobs.hold(13) == 3 and all(j.status == "held" for j in jobs)
    assert services.jobs.release(13) == 3
    assert services.jobs.cancel(jobs[0].id, 13) is not None and jobs[0].status == "cancelled"
    assert services.jobs.cancel(jobs[0].id, 13) is None
    assert services.jobs.cancel(jobs[1].id, 999) is None  # someone else's job
    assert services.jobs.cancel_all(13) == 2
    assert services.jobs.user_jobs(13) == []


async def test_daily_quota(services, settings):
    services.jobs.bot = FakeBot()
    settings.daily_limit = 2
    user = await services.db.upsert_user(14, "l", "L")
    await services.db.add_usage(14, 1)
    await services.jobs.submit(Job(user_id=14, chat_id=14, url="https://example.com/a"), user)
    with pytest.raises(QuotaExceeded):
        await services.jobs.submit(Job(user_id=14, chat_id=14, url="https://example.com/b"), user)
    await services.db.set_daily_limit(14, 0)  # 0 = unlimited for this user
    user = await services.db.get_user(14)
    await services.jobs.submit(Job(user_id=14, chat_id=14, url="https://example.com/c"), user)
    admin = await services.db.upsert_user(1, "boss", "Boss")
    await services.db.set_role(1, "admin")
    admin = await services.db.get_user(1)
    assert await services.jobs.remaining_quota(admin) is None


async def test_per_user_concurrency(services, settings, monkeypatch):
    settings.per_user_concurrent_jobs = 1
    started: list[str] = []
    gate = asyncio.Event()

    async def fake_run(job):
        started.append(job.id)
        await gate.wait()
        job.status = "done"

    monkeypatch.setattr(services.jobs, "_run", fake_run)
    bot = FakeBot()
    services.jobs.start(bot)
    alice = await services.db.upsert_user(20, "a", "A")
    bob = await services.db.upsert_user(21, "b", "B")
    a1 = Job(user_id=20, chat_id=20, url="https://x/1")
    a2 = Job(user_id=20, chat_id=20, url="https://x/2")
    b1 = Job(user_id=21, chat_id=21, url="https://x/3")
    for j, u in ((a1, alice), (a2, alice), (b1, bob)):
        await services.jobs.submit(j, u)
    await wait_for(lambda: len(started) == 2, timeout=5)
    assert set(started) == {a1.id, b1.id}  # alice's second job waits for her first
    gate.set()
    await wait_for(lambda: len(started) == 3, timeout=5)
