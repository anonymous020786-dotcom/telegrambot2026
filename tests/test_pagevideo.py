"""Universal page-video fallback and adult-site coverage."""

import asyncio

import pytest

from bot.app import build_services
from bot.services.downloader import adult_extractors, supported_extractor
from bot.services.jobs import Job
from bot.services.pagevideo import find_videos, page_is_adult
from tests.conftest import FakeBot, needs_ffmpeg

PAGE = """<html><head>
<meta property="og:video" content="https://cdn.site.test/og/clip.mp4">
<script type="application/ld+json">{"@type":"VideoObject","contentUrl":"https://cdn.site.test/ld/1080p.mp4",
 "height":1080,"embedUrl":"https://www.youtube.com/embed/jNQXAC9IVRw"}</script></head><body>
<video src="https://cdn.site.test/video/360.mp4"><source src="https://cdn.site.test/video/720.mp4" label="720p"></video>
<a href="/files/download_480p.mp4">Download</a>
<script>var cfg = {"hls": "https:\\/\\/stream.site.test\\/master.m3u8", "preview": "https://cdn.site.test/preview/1.mp4"};
</script>
<iframe src="https://www.youtube.com/embed/jNQXAC9IVRw"></iframe>
<iframe src="https://ads.example/banner.html"></iframe>
</body></html>"""


def test_find_videos_collects_every_kind_of_stream():
    cands = find_videos(PAGE, "https://site.test/watch/1", embed_supported=lambda u: supported_extractor(u) is not None)
    urls = [c.url for c in cands]
    assert "https://www.youtube.com/embed/jNQXAC9IVRw" in urls  # embedded supported player
    assert "https://stream.site.test/master.m3u8" in urls  # escaped URL inside a script
    assert "https://cdn.site.test/ld/1080p.mp4" in urls  # JSON-LD
    assert "https://cdn.site.test/og/clip.mp4" in urls  # og:video
    assert "https://cdn.site.test/video/720.mp4" in urls  # <source>
    assert "https://site.test/files/download_480p.mp4" in urls  # link, made absolute
    assert not any("preview" in u for u in urls)  # previews/trailers dropped when real streams exist
    assert not any("ads.example" in u for u in urls)  # unsupported iframes ignored
    assert cands[0].kind == "embed"
    files = [c for c in cands if c.kind == "file"]
    assert files[0].height == 1080  # highest quality first among direct files
    assert {c.kind for c in cands} == {"embed", "hls", "file"}


def test_find_videos_without_embed_support():
    cands = find_videos(PAGE, "https://site.test/watch/1")
    assert all(c.kind != "embed" for c in cands)
    assert find_videos("<html><body>No video here</body></html>", "https://x.test/") == []


def test_page_is_adult():
    assert page_is_adult('<meta name="RATING" content="RTA-5042-1996-1400-1577-RTA">')
    assert page_is_adult('<meta name="rating" content="adult">')
    assert page_is_adult('<meta property="og:restrictions:age" content="18+">')
    assert not page_is_adult('<meta name="rating" content="general">')
    assert not page_is_adult("<html><body>hello</body></html>")


def test_adult_extractor_catalogue():
    names = {n.lower() for n in adult_extractors()}
    assert len(names) >= 50
    for site in ("xhamster", "pornhub", "xvideos", "xnxx", "youporn", "redtube", "spankbang", "eporner", "beeg"):
        assert site in names, site
    assert "youtube" not in names and "reddit" not in names  # mainstream sites aren't misclassified


@pytest.mark.parametrize(
    "key",
    [
        "XHamster",
        "PornHub",
        "XVideos",
        "XNXX",
        "YouPorn",
        "RedTube",
        "SpankBang",
        "Eporner",
        "Beeg",
        "TNAFlix",
        "ThisVid",
        "RedGifs",
    ],
)
def test_adult_sites_route_to_their_extractors(key):
    import yt_dlp

    ie = next(i for i in yt_dlp.extractor.gen_extractor_classes() if i.ie_key() == key)
    url = next(t["url"] for t in ie.get_testcases(include_onlymatching=False))
    assert supported_extractor(url) == key


# ------------------------------------------------------------------ end to end through the job queue


@pytest.fixture
async def services(settings):
    s = build_services(settings)
    await s.db.connect()
    yield s
    await s.jobs.stop()
    await s.db.close()


async def wait_done(job, timeout=60):
    for _ in range(int(timeout / 0.1)):
        if job.status in ("done", "failed", "cancelled"):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(job.status)


@needs_ffmpeg
async def test_unsupported_site_video_found_in_page(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(50, "v", "V")
    job = Job(user_id=50, chat_id=50, url=f"{web}/tube/1")
    await services.jobs.submit(job, user)
    await wait_done(job)
    assert job.status == "done", job.error
    assert bot.sent_files and bot.sent_files[0][0] == "video"
    assert (await services.db.last_history(50))["url"] == f"{web}/tube/1"  # history keeps the page URL


@needs_ffmpeg
async def test_adult_labelled_page_needs_opt_in(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(51, "w", "W")
    blocked = Job(user_id=51, chat_id=51, url=f"{web}/tube/adult")
    await services.jobs.submit(blocked, user)
    await wait_done(blocked)
    assert blocked.status == "failed" and "18+" in blocked.error
    assert not bot.sent_files

    await services.db.set_kv("adult_mode", "optin")
    await services.db.update_settings(51, adult_ok=True)
    allowed = Job(user_id=51, chat_id=51, url=f"{web}/tube/adult")
    await services.jobs.submit(allowed, await services.db.get_user(51))
    await wait_done(allowed)
    assert allowed.status == "done", allowed.error
    assert bot.sent_files[0][0] == "video"
