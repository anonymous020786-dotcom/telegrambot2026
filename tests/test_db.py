import time

from bot.db import DEFAULT_USER_SETTINGS


async def test_users_roles_and_settings(db):
    u = await db.upsert_user(5, "alice", "Alice")
    assert u.role == "user" and not u.allowed and u.pref("quality") == DEFAULT_USER_SETTINGS["quality"]
    await db.set_allowed(5, True)
    await db.set_role(5, "admin")
    u = await db.get_user(5)
    assert u.allowed and u.is_admin
    assert (await db.find_user("@ALICE")).id == 5
    assert (await db.find_user("5")).id == 5
    merged = await db.update_settings(5, quality="720", audio_format="flac")
    assert merged["quality"] == "720" and merged["audio_format"] == "flac"
    # Values equal to the defaults aren't stored, so later default changes apply.
    await db.update_settings(5, quality=DEFAULT_USER_SETTINGS["quality"])
    raw = await db._one("SELECT settings FROM users WHERE id=5")
    assert "quality" not in raw["settings"]
    await db.reset_settings(5)
    assert (await db.get_user(5)).pref("audio_format") == DEFAULT_USER_SETTINGS["audio_format"]
    await db.set_role(5, "banned")
    assert (await db.get_user(5)).is_banned
    assert 5 not in await db.all_user_ids()


async def test_usage_and_history(db):
    await db.upsert_user(7, None, "Bob")
    await db.add_usage(7, 1, 1000)
    await db.add_usage(7, 2, 500)
    assert await db.usage_today(7) == (3, 1500)
    assert await db.usage_totals(7) == (3, 1500)
    ids = []
    for i in range(12):
        ids.append(
            await db.add_history(
                7,
                f"https://x.com/{i}",
                title=f"Video {i}",
                kind="video",
                preset={"kind": "media"},
                status="done" if i % 3 else "failed",
                size=10,
            )
        )
    assert await db.count_history(7) == 12
    page = await db.history(7, 5, 0)
    assert page[0]["title"] == "Video 11"
    assert await db.set_favorite(7, ids[2], True)
    assert await db.count_history(7, favorites=True) == 1
    assert len(await db.search_history(7, "Video 1")) == 3  # 1, 10, 11
    assert (await db.last_history(7))["title"] == "Video 11"
    assert await db.clear_history(7, keep_favorites=True) == 11
    assert await db.count_history(7) == 1
    stats = await db.global_stats()
    assert stats["users"] == 1 and stats["jobs"] == 1


async def test_cache_invites_watches_schedules_feedback(db, tmp_path):
    await db.cache_put("k1", "FILEID", "video", "T", 99)
    assert (await db.cache_get("k1"))["file_id"] == "FILEID"
    assert await db.cache_clear() == 1

    code = await db.create_invite(1, uses=2, ttl_hours=1)
    assert await db.redeem_invite(code, 50)
    assert (await db.get_user(50)).allowed
    assert await db.redeem_invite(code, 51)
    assert not await db.redeem_invite(code, 52)  # used up
    expired = await db.create_invite(1, uses=1, ttl_hours=1)
    await db._exec("UPDATE invites SET expires_at=? WHERE code=?", (time.time() - 1, expired))
    assert not await db.redeem_invite(expired, 53)
    keep = await db.create_invite(1)
    assert await db.revoke_invite(keep)

    wid = await db.add_watch(1, 1, "https://yt/c", {"mode": "video"}, "Chan", ["a", "b"])
    assert len(await db.watches(1)) == 1 and len(await db.watches()) == 1
    await db.update_watch_seen(wid, ["a", "b", "c"])
    assert '"c"' in (await db.watches(1))[0]["seen"]
    assert await db.remove_watch(1, wid)
    assert await db.watches(1) == []

    sid = await db.add_schedule(1, 1, "https://x", {"kind": "media"}, time.time() + 60)
    assert len(await db.pending_schedules(1)) == 1
    assert await db.cancel_schedule(1, sid)
    assert not await db.cancel_schedule(1, sid)
    assert await db.pending_schedules() == []

    await db.add_feedback(1, "great bot")
    assert (await db.feedback())[0]["text"] == "great bot"
    await db.set_kv("maintenance", "1")
    assert await db.get_kv("maintenance") == "1"

    backup = db.backup_to(tmp_path / "copy.db")
    assert backup.stat().st_size > 0
