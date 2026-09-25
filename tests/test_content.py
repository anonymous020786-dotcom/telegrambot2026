"""Content policy, social-media support, adult opt-in, cookies and site checks."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from yt_dlp.utils import DownloadError

from bot.app import build_services, load_handlers
from bot.handlers.common import guard
from bot.handlers.content import on_adult_button, parse_cookie_file
from bot.registry import REGISTRY
from bot.services import downloader as dl_module
from bot.services.downloader import (
    AdultBlocked,
    Downloader,
    Preset,
    build_options,
    js_runtime_opts,
    supported_extractor,
)
from bot.services.jobs import Job, PolicyBlocked
from bot.services.policy import drm_service, is_adult, is_blocked, normalize_domain
from bot.services.sitecheck import PLATFORMS, default_targets
from tests.conftest import FakeBot, make_context, make_update

load_handlers()


@pytest.fixture
async def services(settings):
    s = build_services(settings)
    await s.db.connect()
    s.jobs.bot = FakeBot()
    yield s
    await s.jobs.stop()
    await s.db.close()


# ------------------------------------------------------------------ pure policy helpers


def test_drm_services_are_recognised():
    assert drm_service("https://www.netflix.com/watch/80100172") == "Netflix"
    assert drm_service("https://www.primevideo.com/detail/0ABC") == "Prime Video"
    assert drm_service("https://www.amazon.com/gp/video/detail/B0ABC") == "Prime Video"
    assert drm_service("https://apps.disneyplus.com/video/x") == "Disney+"
    assert drm_service("https://www.amazon.com/dp/B0ABC") is None  # a normal shop page
    assert drm_service("https://www.youtube.com/watch?v=x") is None


def test_domain_rules():
    assert normalize_domain("https://www.Example.com/path") == "example.com"
    assert normalize_domain("sub.example.org") == "sub.example.org"
    assert normalize_domain("not a domain") is None
    assert is_blocked("https://cdn.example.com/v.mp4", ["example.com"]) == "example.com"
    assert is_blocked("https://notexample.com/v.mp4", ["example.com"]) is None
    assert is_adult({"age_limit": 18}) and not is_adult({"age_limit": 0}) and not is_adult(None)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "https://youtu.be/jNQXAC9IVRw",
        "https://www.youtube.com/shorts/abcdefghijk",
        "https://www.instagram.com/reel/C1a2B3c4D5e/",
        "https://www.instagram.com/p/C1a2B3c4D5e/",
        "https://www.tiktok.com/@someone/video/7234567890123456789",
        "https://x.com/someone/status/1234567890123456789",
        "https://twitter.com/someone/status/1234567890123456789",
        "https://www.facebook.com/watch/?v=1234567890",
        "https://www.reddit.com/r/videos/comments/abc123/some_title/",
        "https://vimeo.com/76979871",
        "https://www.dailymotion.com/video/x7tgad0",
        "https://www.twitch.tv/videos/1234567890",
        "https://www.pinterest.com/pin/123456789012345678/",
        "https://soundcloud.com/artist/track-name",
        "https://bsky.app/profile/someone.bsky.social/post/3l4omssdl632g",
    ],
)
def test_major_social_platforms_have_dedicated_extractors(url):
    assert supported_extractor(url), url


def test_site_check_targets_come_from_yt_dlp():
    targets = default_targets()
    assert len(targets) == len(PLATFORMS)
    assert all(u.startswith("http") for _, u in targets)


def test_js_runtime_for_youtube_is_available():
    opts = js_runtime_opts()
    assert Path(opts["js_runtimes"]["deno"]["path"]).exists()


def test_cookie_file_validation():
    good = (
        "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc\n"
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\txyz\n"
    )
    assert parse_cookie_file(good) == ["instagram.com", "youtube.com"]
    with pytest.raises(ValueError):
        parse_cookie_file("sessionid=abc; other=def")
    with pytest.raises(ValueError):
        parse_cookie_file("# Netscape HTTP Cookie File\n")


# ------------------------------------------------------------------ adult content enforcement


def test_age_limit_option(settings, tmp_path):
    assert build_options(Preset(), settings, tmp_path, allow_adult=False)["age_limit"] == 17
    assert "age_limit" not in build_options(Preset(), settings, tmp_path, allow_adult=True)


def test_download_raises_adult_blocked(settings, tmp_path, monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return {"id": "x", "title": "rated", "age_limit": 18}  # skipped by age_limit: no files written

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(dl_module.yt_dlp, "YoutubeDL", FakeYDL)
    d = Downloader(settings)
    with pytest.raises(AdultBlocked):
        d.download_sync("https://adult.example/v/1", Preset(), tmp_path / "a", allow_adult=False)
    with pytest.raises(DownloadError):  # allowed, but nothing to download → ordinary error
        d.download_sync("https://adult.example/v/1", Preset(), tmp_path / "b", allow_adult=True)


async def test_policy_switches(services, settings):
    user = await services.db.upsert_user(30, "a", "A")
    assert await services.policy.adult_mode() == "off"
    await services.db.update_settings(30, adult_ok=True)
    user = await services.db.get_user(30)
    assert not await services.policy.adult_allowed(user)  # admin hasn't enabled it
    await services.db.set_kv("adult_mode", "optin")
    assert await services.policy.adult_allowed(user)
    settings.blocked_domains = ["env-blocked.com"]
    await services.policy.set_blocked(["kv-blocked.org"])
    assert await services.policy.blocked_domains() == ["env-blocked.com", "kv-blocked.org"]
    assert "blocked" in await services.policy.refusal("https://www.kv-blocked.org/x")
    assert "DRM" in await services.policy.refusal("https://www.netflix.com/title/1")
    assert await services.policy.refusal("https://vimeo.com/1") is None


async def test_submit_refuses_drm_and_blocked(services):
    user = await services.db.upsert_user(31, "b", "B")
    with pytest.raises(PolicyBlocked, match="Netflix"):
        await services.jobs.submit(Job(user_id=31, chat_id=31, url="https://www.netflix.com/watch/1"), user)
    await services.policy.set_blocked(["example.net"])
    with pytest.raises(PolicyBlocked):
        await services.jobs.submit(Job(user_id=31, chat_id=31, url="https://example.net/v"), user)
    assert services.jobs.user_jobs(31) == []


async def wait_done(job, timeout=30):
    for _ in range(int(timeout / 0.05)):
        if job.status in ("done", "failed", "cancelled"):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(job.status)


async def test_job_passes_adult_permission_and_reports_block(services, monkeypatch):
    seen = []

    async def fake_download(url, preset, out_dir, hook=None, allow_adult=True, referer=None):
        seen.append(allow_adult)
        raise AdultBlocked()

    monkeypatch.setattr(services.downloader, "download", fake_download)
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(32, "c", "C")
    job = Job(user_id=32, chat_id=32, url="https://adult.example/v/1")
    await services.jobs.submit(job, user)
    await wait_done(job)
    assert job.status == "failed" and "18+" in job.error and seen == [False]
    assert any("Not downloaded" in t for t in bot.texts())

    await services.db.set_kv("adult_mode", "optin")
    await services.db.update_settings(32, adult_ok=True)
    job2 = Job(user_id=32, chat_id=32, url="https://adult.example/v/2")
    await services.jobs.submit(job2, await services.db.get_user(32))
    await wait_done(job2)
    assert seen == [False, True]


# ------------------------------------------------------------------ social photo-post fallback


async def test_photo_post_falls_back_to_gallery_dl(services, monkeypatch, tmp_path):
    async def no_video(*a, **k):
        raise DownloadError("ERROR: [Instagram] C1a2B3c4D5e: No video formats found!")

    async def fake_gallery(url, dest, limit=50, timeout=600):
        dest.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(3):
            p = dest / f"photo{i}.jpg"
            Image.new("RGB", (640, 480), (i * 60, 90, 120)).save(p)
            paths.append(p)
        return paths

    monkeypatch.setattr(services.downloader, "download", no_video)
    monkeypatch.setattr(services.images, "gallery_dl", fake_gallery)
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(33, "d", "D")
    job = Job(user_id=33, chat_id=33, url="https://www.instagram.com/p/C1a2B3c4D5e/")
    await services.jobs.submit(job, user)
    await wait_done(job)
    assert job.status == "done", job.error
    assert job.kind == "gallery" and job.files_sent == 3
    assert ("send_media_group", 3) in bot.calls

    other = Job(user_id=33, chat_id=33, url="https://some-blog.example/post/1")
    await services.jobs.submit(other, user)
    await wait_done(other)
    assert other.status == "failed"  # no fallback outside social sites


# ------------------------------------------------------------------ commands


async def run(services, name, user_id, args=None):
    bot = FakeBot()
    services.last_command.clear()
    update = make_update(bot, user_id, text=f"/{name}")
    await guard(REGISTRY[name])(update, make_context(services, bot, args))
    return bot, update


async def test_setadult_flow(services):
    await services.db.set_allowed(40, True)
    bot, _ = await run(services, "setadult", 40)
    assert "disabled" in bot.texts()[0]
    bot, _ = await run(services, "adult", 40, ["optin"])
    assert "admins only" in bot.texts()[0]
    bot, _ = await run(services, "adult", 1, ["optin"])
    assert "optin" in bot.texts()[0]
    bot, _ = await run(services, "setadult", 40)
    assert "Only continue if you are an adult" in bot.texts()[0]

    msg = SimpleNamespace(edit_text=lambda *a, **k: asyncio.sleep(0))
    answers = []

    async def answer(*a, **k):
        answers.append(a)

    update = SimpleNamespace(callback_query=SimpleNamespace(data="adult:confirm", message=msg, answer=answer))
    user = await services.db.get_user(40)
    await on_adult_button(update, make_context(services, FakeBot()), user)
    assert (await services.db.get_user(40)).pref("adult_ok") is True
    await run(services, "setadult", 40, ["off"])
    assert (await services.db.get_user(40)).pref("adult_ok") is False


async def test_blocksite_commands_and_enforcement(services):
    await services.db.set_allowed(41, True)
    bot, _ = await run(services, "blocksite", 1, ["https://www.bad-site.test/x"])
    assert "bad-site.test" in bot.texts()[0]
    bot, _ = await run(services, "mp3", 41, ["https://cdn.bad-site.test/a.mp4"])
    assert "blocked by the bot's admin" in bot.texts()[0]
    bot, _ = await run(services, "blockedsites", 1)
    assert "bad-site.test" in bot.texts()[0]
    await run(services, "unblocksite", 1, ["bad-site.test"])
    assert await services.policy.blocked_domains() == []
    bot, _ = await run(services, "mp3", 41, ["https://www.netflix.com/watch/1"])
    assert "DRM" in bot.texts()[0]


async def test_cookies_status(services):
    bot, _ = await run(services, "cookies", 1)
    assert "Cookies: <b>none</b>" in bot.texts()[0]
    services.settings.uploaded_cookies.write_text(
        "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc\n"
    )
    bot, _ = await run(services, "cookies", 1)
    assert "active" in bot.texts()[0] and "1 domains" in bot.texts()[0]
    assert services.settings.cookies_path() == services.settings.uploaded_cookies
    bot, _ = await run(services, "cookies", 1, ["clear"])
    assert services.settings.cookies_path() is None
