"""The browser fallback's media filter and ranking (no browser needed)."""

from bot.services.browser import classify, rank


def test_manifests_are_recognised_by_type_or_extension():
    assert classify("https://cdn.test/a/master.m3u8?token=1", "", None) == "hls"
    assert classify("https://cdn.test/play", "application/vnd.apple.mpegurl", None) == "hls"
    assert classify("https://cdn.test/a/manifest.mpd", "", None) == "dash"
    assert classify("https://cdn.test/play", "application/dash+xml; charset=utf-8", None) == "dash"
    assert classify("https://cdn.test/a/video.ism/Manifest", "", None) == "ism"


def test_whole_files_count_but_segments_previews_and_ads_do_not():
    assert classify("https://cdn.test/v/1080p.mp4", "video/mp4", 5_000_000) == "file"
    assert classify("https://cdn.test/v/clip.webm", "", None) == "file"
    assert classify("https://cdn.test/v/seg-12.ts", "video/mp2t", 900_000) is None
    assert classify("https://cdn.test/v/chunk_3.m4s", "video/iso.segment", 900_000) is None
    assert classify("https://cdn.test/v/hover.mp4", "video/mp4", 40_000) is None  # tiny preview
    assert classify("https://pubads.g.doubleclick.net/x.mp4", "video/mp4", 5_000_000) is None
    assert classify("blob:https://site.test/abc", "", None) is None
    assert classify("https://site.test/api/feed", "application/json", 2000) is None


def test_rank_prefers_manifests_then_the_main_player_then_request_order():
    found = {
        "https://cdn.test/next-video.mp4": "file",  # a feed preloads the next video first
        "https://cdn.test/main.mp4": "file",
        "https://cdn.test/master.m3u8": "hls",
    }
    urls = [c.url for c in rank(found, playing=["https://cdn.test/main.mp4"])]
    assert urls == ["https://cdn.test/master.m3u8", "https://cdn.test/main.mp4", "https://cdn.test/next-video.mp4"]
    assert [c.url for c in rank({"https://a.test/1.mp4": "file", "https://a.test/2.mp4": "file"})] == [
        "https://a.test/1.mp4",
        "https://a.test/2.mp4",
    ]
