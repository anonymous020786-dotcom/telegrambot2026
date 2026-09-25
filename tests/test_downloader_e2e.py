"""Real yt-dlp downloads against a local web server (no internet needed)."""

import pytest
from yt_dlp.utils import DownloadError

from bot.services import media
from bot.services.downloader import Cancelled, Downloader, Preset
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


async def test_probe_direct_video(settings, web):
    info = await Downloader(settings).probe(f"{web}/sample.mp4")
    assert info.extractor.lower() == "generic"
    assert not info.is_playlist


async def test_download_video_with_progress(settings, web, tmp_path):
    events = []
    _, files = await Downloader(settings).download(
        f"{web}/sample.mp4", Preset(embed_thumbnail=False), tmp_path / "v", events.append
    )
    assert len(files) == 1 and files[0].suffix == ".mp4"
    assert files[0].stat().st_size > 10_000
    assert any(e.get("status") == "finished" for e in events)


async def test_download_as_mp3(settings, web, tmp_path):
    _, files = await Downloader(settings).download(
        f"{web}/sample.mp4", Preset(mode="audio", audio_format="mp3", embed_thumbnail=False), tmp_path / "a"
    )
    assert [f.suffix for f in files] == [".mp3"]
    assert (media.summarize_probe(await media.probe(files[0])))["acodec"] == "mp3"


async def test_download_clip_section(settings, web, tmp_path):
    _, files = await Downloader(settings).download(
        f"{web}/sample.mp4", Preset(section=(1, 3), embed_thumbnail=False), tmp_path / "c"
    )
    duration = media.summarize_probe(await media.probe(files[0]))["duration"]
    assert 1.5 < duration < 2.6


async def test_cancel_from_progress_hook(settings, web, tmp_path):
    from yt_dlp.utils import DownloadCancelled

    def hook(_d):
        raise DownloadCancelled()

    with pytest.raises(Cancelled):
        await Downloader(settings).download(f"{web}/sample.mp4", Preset(embed_thumbnail=False), tmp_path / "x", hook)


async def test_missing_media_raises(settings, web, tmp_path):
    with pytest.raises(DownloadError):
        await Downloader(settings).download(f"{web}/nope.mp4", Preset(embed_thumbnail=False), tmp_path / "n")


@pytest.mark.parametrize(
    ("mode", "expected"), [("thumbnail", "no thumbnail"), ("subs", "No subtitles found for language 'en'")]
)
async def test_missing_extras_are_explained(settings, web, tmp_path, mode, expected):
    from bot.services.downloader import friendly_error

    with pytest.raises(DownloadError) as info:
        await Downloader(settings).download(f"{web}/sample.mp4", Preset(mode=mode, sub_lang="en"), tmp_path / mode)
    assert expected in str(info.value)
    assert "larger" not in friendly_error(info.value)  # never mislabelled as a size-limit problem
