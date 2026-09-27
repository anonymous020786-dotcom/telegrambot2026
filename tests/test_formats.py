"""Containers and codecs: lossless rewrapping, codec preference and direct-file details."""

import shutil
import subprocess
from pathlib import Path

from bot.config import Settings
from bot.services.downloader import Preset, build_options, fill_direct_file_details, rewrap
from tests.conftest import needs_ffmpeg


def _copy_as(sample: Path, tmp_path: Path, ext: str, extra: list[str] | None = None) -> Path:
    out = tmp_path / f"clip{ext}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(sample), *(extra or ["-c", "copy"]), str(out)]
    subprocess.run(cmd, check=True)
    return out


def _codecs(path: Path) -> list[str]:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.split()


@needs_ffmpeg
def test_mkv_flv_and_ts_are_rewrapped_into_mp4_without_reencoding(sample_video, tmp_path):
    for ext in (".mkv", ".flv", ".ts"):
        src = _copy_as(sample_video, tmp_path, ext)
        out = rewrap(src, "mp4")
        assert out.suffix == ".mp4" and out.exists() and not src.exists(), ext
        assert _codecs(out) == ["h264", "aac"], ext
        out.unlink()


@needs_ffmpeg
def test_mov_is_produced_on_request(sample_video, tmp_path):
    src = shutil.copy(sample_video, tmp_path / "clip.mp4")
    out = rewrap(Path(src), "mov")
    assert out.suffix == ".mov" and _codecs(out) == ["h264", "aac"]


@needs_ffmpeg
def test_codecs_that_do_not_fit_keep_the_original(sample_video, tmp_path):
    # MPEG-TS can carry MP2 audio + MPEG-2 video, which MOV can't hold as-is: the original must survive.
    src = _copy_as(sample_video, tmp_path, ".ts", ["-c:v", "mpeg2video", "-c:a", "mp2"])
    kept = rewrap(src, "mp4")
    assert kept.exists()


def test_rewrap_leaves_other_containers_alone(tmp_path):
    for name, container in (("a.webm", "mp4"), ("a.mp4", "mp4"), ("a.mkv", "mkv"), ("a.webm", "webm")):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert rewrap(path, container) == path


def test_codec_preference_sorts_by_resolution_first(tmp_path):
    settings = Settings(bot_token="1:x", data_dir=tmp_path)
    assert "format_sort" not in build_options(Preset(), settings, tmp_path)
    opts = build_options(Preset(codec="h264"), settings, tmp_path)
    assert opts["format_sort"] == ["res", "vcodec:h264"]
    assert "format_sort" not in build_options(Preset(mode="audio", codec="av1"), settings, tmp_path)
    assert Preset(quality="1080", codec="av1").label() == "1080p AV1 MP4"


@needs_ffmpeg
def test_direct_file_links_get_resolution_codecs_and_duration(web):
    info = {"formats": [{"format_id": "0", "url": f"{web}/sample.mp4", "protocol": "https", "ext": "mp4"}]}
    fill_direct_file_details(info, "test-agent")
    fmt = info["formats"][0]
    assert (fmt["width"], fmt["height"], fmt["vcodec"], fmt["acodec"], fmt["fps"]) == (320, 240, "h264", "aac", 25.0)
    assert round(info["duration"]) == 4


def test_direct_file_details_skip_formats_that_already_have_them():
    info = {"formats": [{"url": "https://x.test/v.mp4", "height": 720}]}
    assert fill_direct_file_details(info, "ua") == {"formats": [{"url": "https://x.test/v.mp4", "height": 720}]}
    many = {"formats": [{"url": "https://x.test/1.mp4"}, {"url": "https://x.test/2.mp4"}]}
    assert fill_direct_file_details(many, "ua") == many  # extractors with several formats already describe them
