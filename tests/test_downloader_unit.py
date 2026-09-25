from pathlib import Path

from yt_dlp.utils import DownloadError

from bot.services.downloader import (
    Preset,
    build_options,
    collect_outputs,
    format_selector,
    friendly_error,
    media_info_from,
)

FAKE_INFO = {
    "id": "abc",
    "title": "Test video",
    "extractor_key": "Youtube",
    "uploader": "Chan",
    "duration": 125,
    "view_count": 1234567,
    "upload_date": "20260101",
    "subtitles": {"en": [], "de": []},
    "automatic_captions": {"fr": []},
    "chapters": [{"start_time": 0, "title": "Intro"}, {"start_time": 60, "title": "Main"}],
    "formats": [
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 2_000_000, "tbr": 128},
        {
            "format_id": "137",
            "ext": "mp4",
            "height": 1080,
            "vcodec": "avc1",
            "acodec": "none",
            "filesize": 50_000_000,
            "tbr": 4000,
        },
        {
            "format_id": "22",
            "ext": "mp4",
            "height": 720,
            "vcodec": "avc1",
            "acodec": "mp4a",
            "filesize_approx": 20_000_000,
            "tbr": 1500,
        },
        {"format_id": "sb0", "ext": "mhtml", "format_note": "storyboard"},
    ],
}


def test_media_info_from_fake_info():
    info = media_info_from(FAKE_INFO, "https://youtu.be/abc")
    assert info.title == "Test video" and info.extractor == "Youtube"
    assert info.heights == [1080, 720]
    assert len(info.formats) == 3  # storyboard dropped
    assert info.best_audio().format_id == "140"
    assert info.estimate_size(1080) == 52_000_000  # video-only + best audio
    assert info.estimate_size(720) == 20_000_000  # muxed format
    assert info.subtitles == ["de", "en"] and info.auto_captions == ["fr"]
    assert not info.is_playlist


def test_playlist_info():
    info = media_info_from(
        {
            "_type": "playlist",
            "title": "PL",
            "entries": [{"id": "1", "title": "One", "url": "https://x/1"}, None, {"id": "2", "url": "https://x/2"}],
        },
        "https://x",
    )
    assert info.is_playlist and [e["id"] for e in info.entries] == ["1", "2"]


def test_format_selectors():
    assert format_selector(Preset(mode="audio")) == "ba/b"
    assert format_selector(Preset(quality="worst")) == "wv*+wa/w"
    sel = format_selector(Preset(quality="720", container="mp4"))
    assert sel.startswith("bv*[height<=720][ext=mp4]+ba[ext=m4a]") and sel.endswith("/bv*+ba/b")
    assert format_selector(Preset(quality="best", container="mkv")) == "bv*+ba/b"
    assert format_selector(Preset(mode="format", format_id="137", format_has_audio=False)) == "137+ba/137"
    assert format_selector(Preset(mode="format", format_id="22", format_has_audio=True)) == "22"


def _pp_keys(opts):
    return [p["key"] for p in opts["postprocessors"]]


def test_build_options_audio_video_and_extras(settings, tmp_path):
    mp3 = build_options(Preset(mode="audio", audio_format="mp3", audio_bitrate=320), settings, tmp_path)
    extract = next(p for p in mp3["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}
    assert "EmbedThumbnail" in _pp_keys(mp3) and mp3["writethumbnail"]

    flac = build_options(Preset(mode="audio", audio_format="flac"), settings, tmp_path)
    assert next(p for p in flac["postprocessors"] if p["key"] == "FFmpegExtractAudio")["preferredquality"] == "0"
    ogg = build_options(Preset(mode="audio", audio_format="ogg"), settings, tmp_path)
    assert next(p for p in ogg["postprocessors"] if p["key"] == "FFmpegExtractAudio")["preferredcodec"] == "vorbis"

    video = build_options(Preset(quality="1080", embed_subs=True), settings, tmp_path)
    assert video["merge_output_format"] == "mp4/mkv" and video["noplaylist"]
    assert "FFmpegEmbedSubtitle" in _pp_keys(video) and "FFmpegMetadata" in _pp_keys(video)
    webm = build_options(Preset(container="webm"), settings, tmp_path)
    assert "EmbedThumbnail" not in _pp_keys(webm)  # webm can't hold a cover image

    clip = build_options(Preset(section=(10, 20)), settings, tmp_path)
    assert clip["force_keyframes_at_cuts"] and callable(clip["download_ranges"])

    pl = build_options(Preset(playlist=True, playlist_items="1-5"), settings, tmp_path)
    assert pl["playlist_items"] == "1-5" and not pl["noplaylist"] and pl["ignoreerrors"] == "only_download"

    thumb = build_options(Preset(mode="thumbnail"), settings, tmp_path)
    assert thumb["skip_download"] and thumb["writethumbnail"]
    subs = build_options(Preset(mode="subs", sub_lang="de"), settings, tmp_path)
    assert subs["subtitleslangs"] == ["de", "de-.*"] and subs["writeautomaticsub"]
    chapters = build_options(Preset(split_chapters=True), settings, tmp_path)
    assert "FFmpegSplitChapters" in _pp_keys(chapters)
    assert build_options(Preset(), settings, tmp_path)["max_filesize"] == settings.max_download_mb * 1024 * 1024


def test_preset_roundtrip_and_labels():
    p = Preset(mode="video", quality="720", section=(1.5, 9))
    again = Preset.from_dict(p.to_dict())
    assert again == p and again.section == (1.5, 9)
    assert Preset.from_dict({"mode": "audio", "unknown_key": 1}).mode == "audio"
    assert p.label() == "720p MP4 clip"
    assert Preset(mode="audio", audio_format="flac").label() == "FLAC"
    assert Preset(mode="audio", audio_bitrate=320).label() == "MP3 320k"


def test_collect_outputs_filters(tmp_path: Path):
    for name in ("a [x].mp4", "a [x].jpg", "a [x].en.srt", "a [x].mp4.part", "b [y].mp3"):
        (tmp_path / name).write_bytes(b"x")
    assert [p.name for p in collect_outputs(tmp_path, Preset())] == ["a [x].mp4", "b [y].mp3"]
    assert [p.name for p in collect_outputs(tmp_path, Preset(mode="audio"))] == ["b [y].mp3"]
    assert [p.name for p in collect_outputs(tmp_path, Preset(mode="thumbnail"))] == ["a [x].jpg"]
    assert [p.name for p in collect_outputs(tmp_path, Preset(mode="subs"))] == ["a [x].en.srt"]


def test_friendly_errors():
    assert "supported media page" in friendly_error(DownloadError("ERROR: Unsupported URL: https://x"))
    assert "DRM" in friendly_error(DownloadError("This video is DRM protected"))
    assert "public" in friendly_error(DownloadError("ERROR: This video is private"))
    assert "404" in friendly_error(DownloadError("HTTP Error 404: Not Found"))
    assert friendly_error(RuntimeError("")) == "RuntimeError"


def test_friendly_errors_for_network_and_age():
    proxy = (
        "ERROR: [youtube] x: Unable to download API page: ('Unable to connect to proxy', OSError('Tunnel "
        "connection failed: 403 Forbidden')) (caused by ProxyError('y')); please report this issue on https://x"
    )
    assert friendly_error(DownloadError(proxy)) == "The server's network or proxy blocked the connection to this site."
    assert "age-restricted" in friendly_error(DownloadError("ERROR: Sign in to confirm your age"))
    assert "rate-limiting" in friendly_error(DownloadError("ERROR: HTTP Error 429: Too Many Requests"))
    assert "country" in friendly_error(DownloadError("ERROR: This video is not available in your country"))
    tail = friendly_error(DownloadError("ERROR: [x] 1: New failure; please report this issue on https://x"))
    assert tail == "[x] 1: New failure"
