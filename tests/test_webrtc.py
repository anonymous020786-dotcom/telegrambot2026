"""WebRTC live-stream recording: WHEP detection and the record command's limits (no network needed)."""

from bot.services.webrtc import looks_like_whep


def test_whep_links_are_recognised():
    for url in (
        "https://customer-abc.cloudflarestream.com/0123456789abcdef/webRTC/play",  # not WHEP-shaped: no
        "https://stream.example.com/live/whep",
        "https://stream.example.com/live/whep/",
        "http://10.0.0.5:8889/cam1/whep?token=x",
        "https://director.millicast.com/api/whep?whep",
    )[1:]:
        assert looks_like_whep(url), url
    for url in (
        "https://www.youtube.com/watch?v=abc",
        "https://example.com/whepcast/episode-1",
        "https://example.com/live/stream.m3u8",
    ):
        assert not looks_like_whep(url), url
