"""Inline mode: @bot <link or words> in any chat, end to end through the real job queue."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram import InlineQueryResultArticle, InlineQueryResultCachedAudio, InlineQueryResultCachedVideo

from bot.app import build_services, load_handlers, on_callback
from bot.handlers.inline import on_chosen_inline_result, on_inline_query
from tests.conftest import FakeBot, make_full_context, needs_ffmpeg

load_handlers()


@pytest.fixture
async def services(settings):
    s = build_services(settings)
    await s.db.connect()
    yield s
    await s.jobs.stop()
    await s.db.close()


class FakeInlineQuery(SimpleNamespace):
    async def answer(self, results, **kw):
        self.results, self.kw = results, kw


def tg_user(uid):
    return SimpleNamespace(id=uid, username=f"u{uid}", first_name="U")


async def ask(services, bot, uid, text):
    query = FakeInlineQuery(query=text, from_user=tg_user(uid))
    update = SimpleNamespace(effective_user=tg_user(uid), inline_query=query, effective_message=None)
    context = make_full_context(services, bot)
    await on_inline_query(update, context)
    return query, context


async def choose(services, bot, context, uid, result_id, inline_id="INLINE1"):
    chosen = SimpleNamespace(result_id=result_id, inline_message_id=inline_id, from_user=tg_user(uid))
    update = SimpleNamespace(effective_user=tg_user(uid), chosen_inline_result=chosen, effective_message=None)
    await on_chosen_inline_result(update, context)


async def press(context, uid, data, inline_id="INLINE1"):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append(text)

    query = SimpleNamespace(data=data, inline_message_id=inline_id, message=None, answer=answer)
    update = SimpleNamespace(effective_user=tg_user(uid), callback_query=query, effective_message=None)
    await on_callback(update, context)
    return answers


async def settle(services, timeout=60):
    for _ in range(int(timeout / 0.1)):
        if not services.jobs.active():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("jobs still running")


@needs_ffmpeg
async def test_link_downloads_into_the_inline_message_then_posts_instantly(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    await services.db.upsert_user(1, "owner", "O")  # admin from settings
    url = f"{web}/sample.mp4"
    query, context = await ask(services, bot, 1, f"save this {url}")
    ids = [r.id for r in query.results]
    assert [i.split(":")[0] for i in ids] == ["v", "a"]
    assert all(isinstance(r, InlineQueryResultArticle) for r in query.results)
    assert "Downloading video" in query.results[0].input_message_content.message_text
    assert query.kw["is_personal"] is True

    await choose(services, bot, context, 1, ids[0])
    await settle(services)
    assert bot.sent_files[0][0] == "video"  # delivered privately first (that's where the file_id comes from)
    assert bot.inline_edits == [("INLINE1", "InputMediaVideo", "FILE_video_1")]  # placeholder → the video

    again, _ = await ask(services, bot, 1, url)
    assert isinstance(again.results[0], InlineQueryResultCachedVideo)  # now it posts instantly
    assert again.results[0].video_file_id == "FILE_video_1"
    assert isinstance(again.results[1], InlineQueryResultArticle)  # audio isn't cached yet


@needs_ffmpeg
async def test_start_button_works_without_inline_feedback(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    await services.db.upsert_user(1, "owner", "O")
    query, context = await ask(services, bot, 1, f"{web}/sample.mp4")
    token = query.results[1].id.split(":")[1]  # audio
    assert await press(context, 1, f"inl:{token}", "INLINE2") == ["⬇️ Download started. It appears here when ready."]
    assert "Already downloading" in (await press(context, 1, f"inl:{token}", "INLINE2"))[0]
    await settle(services)
    assert bot.inline_edits[-1][:2] == ("INLINE2", "InputMediaAudio")
    cached, _ = await ask(services, bot, 1, f"{web}/sample.mp4")
    assert isinstance(cached.results[1], InlineQueryResultCachedAudio)


async def test_only_the_poster_can_start_it(services, web):
    bot = FakeBot()
    await services.db.upsert_user(1, "owner", "O")
    await services.db.upsert_user(2, "friend", "F")
    await services.db.set_allowed(2, True)
    query, context = await ask(services, bot, 1, f"{web}/sample.mp4")
    answers = await press(context, 2, f"inl:{query.results[0].id.split(':')[1]}")
    assert "Only the person who posted this" in answers[0]
    assert not services.jobs.jobs


async def test_private_bot_refuses_strangers(services):
    bot = FakeBot()
    query, context = await ask(services, bot, 777, "https://example.com/video")
    assert query.results == [] and "Private bot" in query.kw["button"].text
    await choose(services, bot, context, 777, "v:whatever")
    assert not services.jobs.jobs and not bot.calls


async def test_search_words_offer_youtube_results(services, monkeypatch):
    async def fake_search(q, count=8, site="yt"):
        assert (q, site) == ("lofi beats", "yt")
        return [
            {"title": "Lofi mix", "url": "https://www.youtube.com/watch?v=abc", "duration": 3600, "uploader": "Chill"},
            {"title": "No url"},
        ]

    monkeypatch.setattr(services.downloader, "search", fake_search)
    bot = FakeBot()
    await services.db.upsert_user(1, "owner", "O")
    query, _ = await ask(services, bot, 1, "lofi beats")
    assert [r.title for r in query.results] == ["Lofi mix"]
    assert query.results[0].description == "Chill · 1:00:00"
    short, _ = await ask(services, bot, 1, "lo")
    assert short.results == [] and short.kw["button"].text == "Paste a link or type to search"


async def test_refused_links_and_failures_explain_themselves(services, web):
    bot = FakeBot()
    services.jobs.start(bot)
    await services.db.upsert_user(1, "owner", "O")
    drm, _ = await ask(services, bot, 1, "https://www.netflix.com/watch/80100172")
    assert [r.id for r in drm.results] == ["refused"] and "DRM" in drm.results[0].description

    query, context = await ask(services, bot, 1, f"{web}/missing.mp4")
    await choose(services, bot, context, 1, query.results[0].id, "INLINE3")
    await settle(services)
    inline_id, kind, text = bot.inline_edits[-1]
    assert (inline_id, kind) == ("INLINE3", "text") and text.startswith("❌")

    await choose(services, bot, context, 1, "v:expired-token", "INLINE4")
    assert "expired" in bot.inline_edits[-1][2]
