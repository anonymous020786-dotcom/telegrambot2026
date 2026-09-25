"""SSRF protection: user links must not reach the server's own network (unless ALLOW_PRIVATE_URLS)."""

import httpx
import pytest
from yt_dlp.utils import DownloadError

from bot.app import build_services, load_handlers
from bot.handlers.common import guard
from bot.registry import REGISTRY
from bot.services.downloader import is_transient
from bot.services.images import ImageService
from bot.services.jobs import Job, PolicyBlocked
from bot.services.netguard import BLOCKED_MESSAGE, BlockedAddress, check_url, is_public_ip, request_hook
from tests.conftest import FakeBot, make_context, make_update

load_handlers()


@pytest.mark.parametrize(
    ("ip", "public"),
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.4.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1%eth0", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
    ],
)
def test_public_address_rules(ip, public):
    assert is_public_ip(ip) is public


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8081/bot123/getMe",
        "http://localhost/",
        "http://2130706433/",  # 127.0.0.1 written as one number
        "http://0x7f.1/",
        "http://[::1]:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "https://10.1.2.3/admin",
    ],
)
async def test_private_links_are_refused(url):
    assert await check_url(url) == BLOCKED_MESSAGE
    assert await check_url(url, allow_private=True) is None  # the self-hoster opt-in


async def test_public_and_other_links_pass():
    assert await check_url("http://8.8.8.8/") is None
    assert await check_url("ytsearch5:lofi") is None  # not a network URL
    assert await check_url("http:///nohost") is not None


async def test_every_redirect_hop_is_checked():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:8081/secret"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, event_hooks={"request": [request_hook(False)]}
    ) as client:
        with pytest.raises(BlockedAddress):
            await client.get("http://8.8.8.8/start")
    assert seen == ["http://8.8.8.8/start"]  # the internal address was never requested
    assert not is_transient(BlockedAddress("x"))  # refusals are final, never retried


# ------------------------------------------------------------------ the bot, with the default (guard on)


@pytest.fixture
async def services(settings):
    settings.allow_private_urls = False
    s = build_services(settings)
    await s.db.connect()
    yield s
    await s.jobs.stop()
    await s.db.close()


async def test_downloads_and_fetches_are_refused(services, settings, web):
    assert await services.policy.refusal(f"{web}/sample.mp4") == BLOCKED_MESSAGE
    user = await services.db.upsert_user(1, "owner", "O")
    with pytest.raises(PolicyBlocked):
        await services.jobs.submit(Job(user_id=1, chat_id=1, url=f"{web}/sample.mp4"), user)
    with pytest.raises(DownloadError, match="private or local"):
        await services.downloader.probe(f"{web}/sample.mp4")
    with pytest.raises(BlockedAddress):
        await ImageService(settings).fetch_page(f"{web}/gallery.html")


@pytest.mark.parametrize("name", ["pagedebug", "headers", "unshorten", "mime", "dl", "info", "images"])
async def test_commands_never_echo_internal_content(services, web, name):
    bot = FakeBot()
    services.last_command.clear()
    url = f"{web}/gallery.html"
    await guard(REGISTRY[name])(make_update(bot, 1, text=f"/{name} {url}"), make_context(services, bot, [url]))
    text = " ".join(bot.texts())
    assert "private or local" in text, text
    assert "<html" not in text.lower() and "og:image" not in text
    assert not bot.sent_files and not services.jobs.jobs
