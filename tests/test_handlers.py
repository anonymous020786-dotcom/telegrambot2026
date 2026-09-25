"""Command handlers through the real access guard, with fake Telegram objects."""

import pytest

from bot.app import build_services, load_handlers
from bot.handlers.common import guard
from bot.registry import REGISTRY
from tests.conftest import FakeBot, make_context, make_update

load_handlers()


@pytest.fixture
async def services(settings):
    s = build_services(settings)
    await s.db.connect()
    s.jobs.bot = FakeBot()
    yield s
    await s.db.close()


async def run(services, name, user_id=1, args=None, text=""):
    bot = FakeBot()
    services.last_command.clear()
    update = make_update(bot, user_id, text=text or f"/{name} {' '.join(args or [])}")
    await guard(REGISTRY[name])(update, make_context(services, bot, args))
    return bot


async def test_private_bot_blocks_strangers(services):
    bot = await run(services, "mp3", user_id=999, args=["https://x.com/v"])
    assert "private bot" in bot.texts()[0] and "999" in bot.texts()[0]
    bot = await run(services, "id", user_id=999)  # public command still works
    assert "Your ID: <code>999</code>" in bot.texts()[0]


async def test_admin_commands_need_admin(services):
    await services.db.set_allowed(5, True)
    bot = await run(services, "users", user_id=5)
    assert "admins only" in bot.texts()[0]
    bot = await run(services, "users", user_id=1)  # user 1 is in ADMIN_IDS
    assert "Users" in bot.texts()[0]


async def test_banned_and_maintenance(services):
    await services.db.set_allowed(6, True)
    await services.db.set_role(6, "banned")
    assert "banned" in (await run(services, "ping", user_id=6)).texts()[0]
    await services.db.set_role(6, "user")
    await services.db.set_kv("maintenance", "1")
    assert "maintenance" in (await run(services, "settings", user_id=6)).texts()[0]
    assert "Settings" in (await run(services, "settings", user_id=1)).texts()[0]  # admins bypass


async def test_settings_commands_persist(services):
    await services.db.set_allowed(7, True)
    await run(services, "setquality", 7, ["720"])
    await run(services, "setaudio", 7, ["flac"])
    await run(services, "setdoc", 7, ["on"])
    await run(services, "settz", 7, ["Asia/Kolkata"])
    bad = await run(services, "settz", 7, ["Mars/Base"])
    assert "IANA" in bad.texts()[0]
    user = await services.db.get_user(7)
    assert (user.pref("quality"), user.pref("audio_format"), user.pref("as_document"), user.pref("timezone")) == (
        "720",
        "flac",
        True,
        "Asia/Kolkata",
    )
    await run(services, "reset", 7)
    assert (await services.db.get_user(7)).pref("quality") == "best"


async def test_download_command_queues_a_job(services):
    await services.db.set_allowed(8, True)
    await services.db.update_settings(8, quality="480")
    await run(services, "mp3", 8, ["https://example.com/watch?v=1", "320"])
    await run(services, "fhd", 8, ["https://example.com/watch?v=2"])
    jobs = services.jobs.user_jobs(8)
    assert [j.preset.label() for j in jobs] == ["MP3 320k", "1080p MP4"]
    bot = await run(services, "mp3", 8, [])
    assert "Send a link" in bot.texts()[0]


async def test_clip_and_batch_validation(services):
    await services.db.set_allowed(9, True)
    bot = await run(services, "clip", 9, ["https://example.com/v", "2:00", "1:00"])
    assert "after the start" in bot.texts()[0]
    await run(services, "clip", 9, ["https://example.com/v", "0:10", "0:20"])
    assert services.jobs.user_jobs(9)[0].preset.section == (10.0, 20.0)
    await run(services, "batch", 9, text="/batch https://a.com/1 https://b.com/2.jpg https://c.com/3")
    kinds = [j.kind for j in services.jobs.user_jobs(9)]
    assert kinds.count("images") == 1 and kinds.count("media") == 3


async def test_help_history_queue_and_invites(services):
    await services.db.set_allowed(10, True)
    assert "Help" in (await run(services, "help", 10)).texts()[0]
    assert "/mp3" in (await run(services, "help", 10, ["mp3"])).texts()[0]
    assert "empty" in (await run(services, "queue", 10)).texts()[0]
    assert "Nothing here" in (await run(services, "history", 10)).texts()[0]
    admin_bot = await run(services, "invite", 1, ["2", "24"])
    code = admin_bot.texts()[0].split("<code>")[1].split("</code>")[0]
    assert "access" in (await run(services, "redeem", 555, [code])).texts()[0]
    assert (await services.db.get_user(555)).allowed


async def test_quota_message(services, settings):
    settings.daily_limit = 1
    await services.db.set_allowed(11, True)
    await services.db.add_usage(11, 1)
    bot = await run(services, "mp3", 11, ["https://example.com/v"])
    assert "daily limit" in bot.texts()[0]
