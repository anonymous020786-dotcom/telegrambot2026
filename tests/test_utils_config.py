from datetime import UTC, datetime

import pytest

from bot.config import Settings
from bot.utils import (
    cache_key,
    extract_urls,
    human_duration,
    human_size,
    kind_of_path,
    looks_like_image_url,
    parse_range,
    parse_size,
    parse_timestamp,
    parse_when,
    progress_bar,
    safe_filename,
)


def test_extract_urls_dedupes_and_strips_punctuation():
    text = "watch https://youtu.be/abc, then (https://x.com/a/status/1). again https://youtu.be/abc!"
    assert extract_urls(text) == ["https://youtu.be/abc", "https://x.com/a/status/1"]
    assert extract_urls(None) == []


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("83", 83),
        ("1:23", 83),
        ("01:02:03", 3723),
        ("1:02:03.5", 3723.5),
        ("1h2m3s", 3723),
        ("90s", 90),
        ("2m", 120),
    ],
)
def test_parse_timestamp(value, seconds):
    assert parse_timestamp(value) == seconds


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "-5"])
def test_parse_timestamp_rejects(bad):
    with pytest.raises(ValueError):
        parse_timestamp(bad)


def test_parse_size():
    assert parse_size("25MB") == 25 * 1024**2
    assert parse_size("1.5g") == int(1.5 * 1024**3)
    assert parse_size("800k") == 800 * 1024
    with pytest.raises(ValueError):
        parse_size("lots")


def test_parse_when_relative_and_clock():
    now = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    assert parse_when("in 30m", "UTC", now) == datetime(2026, 9, 25, 10, 30, tzinfo=UTC)
    assert parse_when("+2h", "UTC", now) == datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert parse_when("18:30", "UTC", now) == datetime(2026, 9, 25, 18, 30, tzinfo=UTC)
    assert parse_when("09:00", "UTC", now) == datetime(2026, 9, 26, 9, 0, tzinfo=UTC)  # tomorrow
    # 18:30 in Kolkata (UTC+5:30) is 13:00 UTC
    assert parse_when("18:30", "Asia/Kolkata", now) == datetime(2026, 9, 25, 13, 0, tzinfo=UTC)
    assert parse_when("2026-10-01 08:00", "UTC", now) == datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_when("2020-01-01 08:00", "UTC", now)
    with pytest.raises(ValueError):
        parse_when("tomorrowish", "UTC", now)


def test_formatting_helpers():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(None) == "?"
    assert human_duration(83) == "1:23"
    assert human_duration(3723) == "1:02:03"
    assert progress_bar(0.5, 10) == "▰▰▰▰▰▱▱▱▱▱"
    assert progress_bar(2, 4) == "▰▰▰▰"


def test_kinds_and_names():
    assert kind_of_path("a.MP4") == "video"
    assert kind_of_path("a.flac") == "audio"
    assert kind_of_path("a.webp") == "image"
    assert kind_of_path("a.srt") == "subtitle"
    assert kind_of_path("a.zip") == "file"
    assert looks_like_image_url("https://x.com/a/b.JPG?w=1")
    assert not looks_like_image_url("https://x.com/watch?v=1")
    assert safe_filename('a<b>:"c"/d?.mp4') == "a_b_c_d_.mp4"
    assert safe_filename("...") == "file"


def test_parse_range():
    assert parse_range("1-10", 25) == "1-10"
    assert parse_range("1, 3, 5", 25) == "1,3,5"
    with pytest.raises(ValueError):
        parse_range("1-100", 25)
    with pytest.raises(ValueError):
        parse_range("abc", 25)


def test_cache_key_is_stable_and_specific():
    a = cache_key("https://x", {"q": "720", "m": "video"})
    assert a == cache_key("https://x", {"m": "video", "q": "720"})
    assert a != cache_key("https://x", {"q": "1080", "m": "video"})


def test_settings_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("ADMIN_IDS", "11, 22;33")
    monkeypatch.setenv("ALLOWED_USER_IDS", "")
    monkeypatch.setenv("LINK_BASE_URL", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings(_env_file=None)
    assert s.admin_ids == [11, 22, 33]
    assert s.allowed_user_ids == []
    assert s.link_base_url is None
    assert s.upload_limit_bytes == 50 * 1024**2
    assert len(s.link_secret) == 64  # random secret generated
    monkeypatch.setenv("BOT_API_BASE_URL", "http://tg:8081/bot")
    assert Settings(_env_file=None).upload_limit_bytes == 2000 * 1024**2
    monkeypatch.setenv("UPLOAD_LIMIT_MB", "100")
    assert Settings(_env_file=None).upload_limit_bytes == 100 * 1024**2
