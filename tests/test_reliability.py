"""Automatic retries after transient failures, and duplicate-download protection."""

import asyncio
import secrets

import pytest
from yt_dlp.utils import DownloadError

from bot.app import build_services, load_handlers
from bot.handlers.common import guard
from bot.registry import REGISTRY
from bot.services.downloader import Preset
from bot.services.jobs import DuplicateJob, Job
from tests.conftest import FakeBot, make_context, make_update, needs_ffmpeg

load_handlers()


@pytest.fixture
async def services(settings):
    settings.retry_delay_seconds = 0.2
    s = build_services(settings)
    await s.db.connect()
    yield s
    await s.jobs.stop()
    await s.db.close()


async def finished(job, timeout=60):
    for _ in range(int(timeout / 0.05)):
        if job.status in ("done", "failed", "cancelled"):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"job still {job.status}")


def flaky(web, fail):
    return f"{web}/flaky/{secrets.token_hex(4)}/sample.mp4?fail={fail}"


@needs_ffmpeg
async def test_a_server_error_is_retried_automatically(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(30, "r", "R")
    job = await services.jobs.submit(Job(user_id=30, chat_id=30, url=flaky(web, 1)), user)
    await finished(job)
    assert job.status == "done", job.error
    assert job.attempt == 1
    assert any("Waiting to retry" in t and "Temporary problem" in t for t in bot.texts())
    assert [f[0] for f in bot.sent_files] == ["video"]  # delivered exactly once
    history = await services.db.history(30, 10)
    assert [r["status"] for r in history] == ["done"]  # the failed attempt isn't recorded as a failure


@needs_ffmpeg
async def test_retries_give_up_with_a_clear_error(services, web):
    services.settings.transient_retries = 1
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(31, "r", "R")
    job = await services.jobs.submit(Job(user_id=31, chat_id=31, url=flaky(web, 99)), user)
    await finished(job)
    assert job.status == "failed" and job.attempt == 1
    assert "503" in job.error or "unavailable" in job.error.lower()
    assert [r["status"] for r in await services.db.history(31, 10)] == ["failed"]
    await asyncio.sleep(0.3)
    assert job.status == "failed"  # not re-queued after the last attempt


async def test_permanent_errors_are_not_retried(services, monkeypatch):
    calls = []

    async def not_found(*args, **kwargs):
        calls.append(1)
        raise DownloadError("ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found")

    monkeypatch.setattr(services.downloader, "download", not_found)
    services.jobs.start(FakeBot())
    user = await services.db.upsert_user(32, "r", "R")
    job = await services.jobs.submit(Job(user_id=32, chat_id=32, url="https://video.example/v/404"), user)
    await finished(job)
    assert job.status == "failed" and job.attempt == 0 and calls == [1]


async def test_a_job_waiting_to_retry_can_be_cancelled(services, monkeypatch):
    services.settings.retry_delay_seconds = 30
    calls = []

    async def rate_limited(*args, **kwargs):
        calls.append(1)
        raise DownloadError("ERROR: [youtube] x: HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(services.downloader, "download", rate_limited)
    services.jobs.start(FakeBot())
    user = await services.db.upsert_user(33, "r", "R")
    job = await services.jobs.submit(Job(user_id=33, chat_id=33, url="https://video.example/v/busy"), user)
    for _ in range(200):
        if job.phase == "retry":
            break
        await asyncio.sleep(0.05)
    assert job.status == "queued" and job.phase == "retry" and job.not_before > 0
    assert [j.id for j in services.jobs.user_jobs(33)] == [job.id]  # visible in /queue while it waits
    assert services.jobs.cancel(job.id, 33) is job
    await asyncio.sleep(0.3)
    assert job.status == "cancelled" and calls == [1]


async def test_the_same_download_is_not_queued_twice(services):
    user = await services.db.upsert_user(34, "d", "D")
    await services.jobs.set_paused_all(True)  # keep jobs waiting
    first = await services.jobs.submit(Job(user_id=34, chat_id=34, url="https://video.example/v/1"), user)
    with pytest.raises(DuplicateJob) as dup:
        await services.jobs.submit(Job(user_id=34, chat_id=34, url="https://video.example/v/1"), user)
    assert dup.value.existing is first
    # A different quality, a different link or another user is a different download.
    await services.jobs.submit(
        Job(user_id=34, chat_id=34, url="https://video.example/v/1", preset=Preset(mode="audio")), user
    )
    await services.jobs.submit(Job(user_id=34, chat_id=34, url="https://video.example/v/2"), user)
    other = await services.db.upsert_user(35, "e", "E")
    await services.jobs.submit(Job(user_id=35, chat_id=35, url="https://video.example/v/1"), other)
    services.jobs.cancel(first.id, 34)
    await services.jobs.submit(Job(user_id=34, chat_id=34, url="https://video.example/v/1"), user)  # allowed again


async def test_duplicate_command_explains_where_the_job_is(services):
    await services.db.upsert_user(1, "owner", "O")
    await services.jobs.set_paused_all(True)

    async def dl():
        bot = FakeBot()
        services.last_command.clear()
        update = make_update(bot, 1, text="/dl https://video.example/v/9")
        await guard(REGISTRY["dl"])(update, make_context(services, bot, ["https://video.example/v/9"]))
        return bot

    await dl()
    again = await dl()
    assert "Already on it" in again.texts()[0] and "#1 in the queue" in again.texts()[0]
    assert len(services.jobs.jobs) == 1
