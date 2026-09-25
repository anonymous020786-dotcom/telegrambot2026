import shutil

import pytest
from PIL import Image

from bot.services import media
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture
def video(sample_video, tmp_path):
    dst = tmp_path / "clip.mp4"
    shutil.copy(sample_video, dst)
    return dst


async def info(path):
    return media.summarize_probe(await media.probe(path))


async def test_probe(video):
    i = await info(video)
    assert (i["width"], i["height"]) == (320, 240)
    assert i["vcodec"] == "h264" and i["acodec"] == "aac"
    assert 3.9 < i["duration"] < 4.2


async def test_convert_to_webm_mkv_and_audio(video):
    webm = await media.convert(video, "webm")
    assert (await info(webm))["vcodec"] == "vp9"
    mkv = await media.convert(video, "mkv")
    assert "matroska" in (await info(mkv))["container"]
    mp3 = await media.convert(video, "mp3")
    i = await info(mp3)
    assert i["acodec"] == "mp3" and i["vcodec"] is None


@pytest.mark.parametrize(
    ("fmt", "codec"), [("m4a", "aac"), ("opus", "opus"), ("flac", "flac"), ("wav", "pcm_s16le"), ("ogg", "vorbis")]
)
async def test_extract_audio_formats(video, fmt, codec):
    out = await media.extract_audio(video, fmt)
    assert out.suffix == f".{fmt}" and (await info(out))["acodec"] == codec


async def test_compress_to_target_size(video):
    out = await media.compress(video, target_bytes=60_000)
    assert out.stat().st_size < 90_000  # within ~50% of a tiny target
    assert (await info(out))["vcodec"] == "h264"


async def test_trim_speed_reverse(video):
    trimmed = await media.trim(video, 1, 3)
    assert 1.8 < (await info(trimmed))["duration"] < 2.3
    fast = await media.speed(video, 2)
    assert 1.8 < (await info(fast))["duration"] < 2.3
    rev = await media.reverse(video)
    assert 3.8 < (await info(rev))["duration"] < 4.3
    with pytest.raises(media.MediaError):
        await media.trim(video, 3, 1)
    with pytest.raises(media.MediaError):
        await media.speed(video, 10)


async def test_mute_rotate_resize(video):
    assert (await info(await media.mute(video)))["acodec"] is None
    r = await info(await media.rotate(video, 90))
    assert (r["width"], r["height"]) == (240, 320)
    assert (await info(await media.resize(video, 120)))["height"] == 120


async def test_gif_frame_volume_voice_note(video):
    gif = await media.to_gif(video, fps=5, width=160)
    with Image.open(gif) as im:
        assert im.format == "GIF" and im.width == 160 and im.n_frames > 5
    frame = await media.frame(video, 2)
    with Image.open(frame) as im:
        assert im.size == (320, 240)
    assert (await info(await media.volume(video, 0.5)))["acodec"] == "aac"
    voice = await media.to_voice(video)
    v = await info(voice)
    assert v["acodec"] == "opus" and v["channels"] == 1
    note = await info(await media.to_video_note(video))
    assert note["width"] == note["height"] == 384


async def test_split_media_and_bytes(video, tmp_path):
    size = video.stat().st_size
    parts = await media.split(video, max(size // 2, 40_000))
    assert len(parts) >= 2
    assert all(p.stat().st_size <= max(size // 2, 40_000) for p in parts)
    for p in parts:  # every part is a playable file
        assert (await info(p))["duration"] > 0
    blob = tmp_path / "data.bin"
    blob.write_bytes(b"x" * 250_000)
    raw = await media.split(blob, 100_000)
    assert [p.name for p in raw] == ["data.bin.001", "data.bin.002", "data.bin.003"]
    assert b"".join(p.read_bytes() for p in raw) == blob.read_bytes()
    assert await media.split(blob, 10**9) == [blob]


def test_image_tools(tmp_path):
    src = tmp_path / "pic.png"
    Image.new("RGBA", (400, 200), (10, 20, 30, 128)).save(src)
    jpg = media.image_convert(src, "jpeg")
    with Image.open(jpg) as im:
        assert im.format == "JPEG" and im.mode == "RGB"
    webp = media.image_convert(src, "webp")
    assert webp.suffix == ".webp"
    small = media.image_resize(src, 100)
    with Image.open(small) as im:
        assert im.size == (200, 100)
    rotated = media.image_rotate(src, 90)
    with Image.open(rotated) as im:
        assert im.size == (200, 400)
    details = media.image_info(src)
    assert details["width"] == 400 and details["format"] == "PNG"
    with pytest.raises(media.MediaError):
        media.image_convert(src, "exe")
