"""Admin-defined site rules and /pagedebug."""

import asyncio

import pytest

from bot.app import build_services, load_handlers
from bot.handlers.common import guard
from bot.registry import REGISTRY
from bot.services.jobs import Job
from bot.services.siterules import apply_rules, rules_for, validate_pattern
from tests.conftest import FakeBot, make_context, make_update, needs_ffmpeg

load_handlers()

RULE = r'"stream_path":\s*"([^"]+)"'


def test_validate_and_apply_rules():
    validate_pattern(RULE)
    for bad in ("", "(unclosed", "(a)(b)", "x" * 500):
        with pytest.raises(ValueError):
            validate_pattern(bad)
    html = '{"stream_path": "\\/media\\/1080p.m3u8"} {"stream_path": "https://cdn.x.test/v/480.mp4"}'
    found = apply_rules(html, "https://site.test/watch/9", [RULE])
    assert [c.url for c in found] == ["https://site.test/media/1080p.m3u8", "https://cdn.x.test/v/480.mp4"]
    assert found[0].kind == "hls" and found[0].height == 1080
    assert rules_for("https://www.site.test/a", {"site.test": [RULE], "other.test": ["x"]}) == [RULE]
    assert rules_for("https://notsite.test/a", {"site.test": [RULE]}) == []


@pytest.fixture
async def services(settings):
    s = build_services(settings)
    await s.db.connect()
    s.jobs.bot = FakeBot()
    yield s
    await s.jobs.stop()
    await s.db.close()


async def run(services, name, args, user_id=1):
    bot = FakeBot()
    services.last_command.clear()
    update = make_update(bot, user_id, text=f"/{name} " + " ".join(args))
    await guard(REGISTRY[name])(update, make_context(services, bot, args))
    return bot


async def test_siterule_commands(services, web):
    bot = await run(services, "siterule", ["add", "not_a_domain", RULE])
    assert "isn't a valid domain" in bot.texts()[0]
    bot = await run(services, "siterule", ["add", "example.test", "(bad"])
    assert "invalid regular expression" in bot.texts()[0]
    bot = await run(services, "siterule", ["add", "example.test", RULE])
    assert "Rule 1 added" in bot.texts()[0]
    bot = await run(services, "siterule", ["list"])
    assert "example.test" in bot.texts()[0] and "stream_path" in bot.texts()[0]
    bot = await run(services, "siterule", ["remove", "example.test"])
    assert "Removed 1 rule" in bot.texts()[0]
    bot = await run(services, "siterule", ["add", "x.test", RULE], user_id=77)
    assert "admins only" in bot.texts()[0]


async def test_pagedebug_reports_candidates(services, web):
    bot = await run(services, "pagedebug", [f"{web}/tube/1"])
    text = bot.texts()[0]
    assert "none (generic)" in text and "sample.mp4" in text and "Labels itself adult: no" in text
    assert bot.sent_files and bot.sent_files[0][0] == "document"  # the page HTML
    bot = await run(services, "pagedebug", [f"{web}/tube/adult"])
    assert "Labels itself adult: yes" in bot.texts()[0]


async def wait_done(job, timeout=60):
    for _ in range(int(timeout / 0.1)):
        if job.status in ("done", "failed", "cancelled"):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(job.status)


@needs_ffmpeg
async def test_site_rule_integrates_a_site_end_to_end(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(60, "r", "R")
    before = Job(user_id=60, chat_id=60, url=f"{web}/custom/1")
    await services.jobs.submit(before, user)
    await wait_done(before)
    assert before.status == "failed"  # nothing on the page is recognisable without a rule
    assert "/siterule" in before.error  # the error points admins at the fix

    added = await run(services, "siterule", ["add", "127.0.0.1", RULE])
    assert "Rule 1 added" in added.texts()[0]
    tested = await run(services, "siterule", ["test", f"{web}/custom/1"])
    assert "sample.mp4" in tested.texts()[0]

    after = Job(user_id=60, chat_id=60, url=f"{web}/custom/1")
    await services.jobs.submit(after, user)
    await wait_done(after)
    assert after.status == "done", after.error
    assert bot.sent_files[-1][0] == "video"
