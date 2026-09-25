"""ffmpeg / ffprobe / Pillow tools used by the media commands and for splitting large files."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

from ..utils import kind_of_path

VIDEO_TARGETS = {"mp4", "mkv", "webm", "mov", "avi", "gif"}
AUDIO_TARGETS = {"mp3", "m4a", "aac", "opus", "ogg", "flac", "wav"}
IMAGE_TARGETS = {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tiff"}


class MediaError(Exception):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def run(cmd: list[str], timeout: float = 1800) -> str:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        raise MediaError("Processing took too long") from exc
    if proc.returncode != 0:
        tail = (err or b"").decode(errors="replace").strip().splitlines()[-3:]
        raise MediaError(" | ".join(tail) or f"{cmd[0]} failed")
    return (out or b"").decode(errors="replace")


async def ffmpeg(*args: str, timeout: float = 1800) -> None:
    await run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], timeout)


async def probe(path: Path) -> dict[str, Any]:
    out = await run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 120
    )
    return json.loads(out or "{}")


def summarize_probe(data: dict[str, Any]) -> dict[str, Any]:
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") not in ("0/0", "0"):
        num, den = video["avg_frame_rate"].split("/")
        fps = round(float(num) / float(den), 2) if float(den) else None
    return {
        "container": fmt.get("format_name"),
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "size": int(fmt["size"]) if fmt.get("size") else None,
        "bitrate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": fps,
        "vcodec": video.get("codec_name") if video else None,
        "acodec": audio.get("codec_name") if audio else None,
        "sample_rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": audio.get("channels") if audio else None,
        "streams": len(streams),
    }


async def duration_of(path: Path) -> float:
    info = summarize_probe(await probe(path))
    if not info["duration"]:
        raise MediaError("Couldn't read the media duration")
    return info["duration"]


def _out(src: Path, suffix: str, tag: str) -> Path:
    return src.with_name(f"{src.stem}_{tag}.{suffix.lstrip('.')}")


async def convert(src: Path, target: str) -> Path:
    target = target.lower().lstrip(".")
    kind = kind_of_path(src)
    if kind == "image" or (target in IMAGE_TARGETS and target != "gif"):
        return await asyncio.to_thread(image_convert, src, target)
    if target == "gif":
        return await to_gif(src)
    if target in AUDIO_TARGETS:
        return await extract_audio(src, target)
    if target not in VIDEO_TARGETS:
        raise MediaError(f"Unsupported target format: {target}")
    out = _out(src, target, "conv")
    if target == "webm":
        await ffmpeg("-i", str(src), "-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "0", "-c:a", "libopus", str(out))
    else:
        await ffmpeg(
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        )
    return out


async def compress(src: Path, target_bytes: int | None = None, crf: int = 28) -> Path:
    """Re-encode H.264/AAC. With target_bytes, uses a bitrate budget (two-pass-free approximation)."""
    out = _out(src, "mp4", "small")
    args = [
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
    ]
    if target_bytes:
        duration = await duration_of(src)
        total_kbps = (target_bytes * 8 / 1000) / max(duration, 1) * 0.95
        video_kbps = max(int(total_kbps - 96), 50)
        args += ["-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.2)}k", "-bufsize", f"{video_kbps * 2}k"]
    else:
        args += ["-crf", str(crf)]
    await ffmpeg(*args, str(out))
    return out


async def trim(src: Path, start: float, end: float | None) -> Path:
    if end is not None and end <= start:
        raise MediaError("End time must be after the start time")
    out = _out(src, src.suffix, "trim")
    args = ["-ss", f"{start:.3f}", "-i", str(src)]
    if end is not None:
        args += ["-t", f"{end - start:.3f}"]
    kind = kind_of_path(src)
    codec = (
        ["-c:a", "libmp3lame" if src.suffix.lower() == ".mp3" else "aac"]
        if kind == "audio"
        else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"]
    )
    await ffmpeg(*args, *codec, str(out))
    return out


async def extract_audio(src: Path, fmt: str = "mp3", bitrate: int = 192) -> Path:
    fmt = fmt.lower()
    codecs = {
        "mp3": ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"],
        "m4a": ["-c:a", "aac", "-b:a", f"{bitrate}k"],
        "aac": ["-c:a", "aac", "-b:a", f"{bitrate}k"],
        "opus": ["-c:a", "libopus", "-b:a", f"{min(bitrate, 256)}k"],
        "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
        "flac": ["-c:a", "flac"],
        "wav": ["-c:a", "pcm_s16le"],
    }
    if fmt not in codecs:
        raise MediaError(f"Unsupported audio format: {fmt}")
    out = _out(src, fmt, "audio")
    await ffmpeg("-i", str(src), "-vn", *codecs[fmt], str(out))
    return out


async def mute(src: Path) -> Path:
    out = _out(src, src.suffix, "muted")
    await ffmpeg("-i", str(src), "-c:v", "copy", "-an", str(out))
    return out


def _atempo_chain(factor: float) -> str:
    parts = []
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.4f}")
    return ",".join(parts)


async def speed(src: Path, factor: float) -> Path:
    if not 0.25 <= factor <= 4:
        raise MediaError("Speed must be between 0.25 and 4")
    out = _out(src, src.suffix, f"x{factor:g}")
    if kind_of_path(src) == "audio":
        await ffmpeg("-i", str(src), "-filter:a", _atempo_chain(factor), str(out))
    else:
        await ffmpeg(
            "-i",
            str(src),
            "-filter_complex",
            f"[0:v]setpts={1 / factor:.5f}*PTS[v];[0:a]{_atempo_chain(factor)}[a]",
            "-map",
            "[v]",
            "-map",
            "[a]?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            str(out),
        )
    return out


async def rotate(src: Path, degrees: int) -> Path:
    filters = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2", -90: "transpose=2"}
    if degrees not in filters:
        raise MediaError("Rotate by 90, 180 or 270 degrees")
    if kind_of_path(src) == "image":
        return await asyncio.to_thread(image_rotate, src, degrees)
    out = _out(src, src.suffix, f"rot{degrees}")
    await ffmpeg(
        "-i", str(src), "-vf", filters[degrees], "-c:v", "libx264", "-preset", "veryfast", "-c:a", "copy", str(out)
    )
    return out


async def resize(src: Path, height: int) -> Path:
    if not 16 <= height <= 4320:
        raise MediaError("Height must be between 16 and 4320")
    if kind_of_path(src) == "image":
        return await asyncio.to_thread(image_resize, src, height)
    out = _out(src, src.suffix, f"{height}p")
    await ffmpeg(
        "-i",
        str(src),
        "-vf",
        f"scale=-2:{height}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "copy",
        str(out),
    )
    return out


async def to_gif(src: Path, fps: int = 12, width: int = 480, max_seconds: float = 30) -> Path:
    out = _out(src, "gif", "gif")
    await ffmpeg(
        "-t",
        str(max_seconds),
        "-i",
        str(src),
        "-vf",
        f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        "-loop",
        "0",
        str(out),
    )
    return out


async def frame(src: Path, at: float) -> Path:
    out = _out(src, "jpg", f"frame{int(at)}")
    await ffmpeg("-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out))
    if not out.exists():
        raise MediaError("No frame at that time")
    return out


async def volume(src: Path, factor: float) -> Path:
    if not 0 < factor <= 10:
        raise MediaError("Volume factor must be between 0 and 10")
    out = _out(src, src.suffix, f"vol{factor:g}")
    video = ["-c:v", "copy"] if kind_of_path(src) == "video" else []
    await ffmpeg("-i", str(src), *video, "-filter:a", f"volume={factor}", str(out))
    return out


async def reverse(src: Path, max_seconds: float = 60) -> Path:
    if await duration_of(src) > max_seconds:
        raise MediaError(f"Reverse works on clips up to {int(max_seconds)} s (it needs a lot of memory)")
    out = _out(src, src.suffix, "rev")
    if kind_of_path(src) == "audio":
        await ffmpeg("-i", str(src), "-af", "areverse", str(out))
    else:
        await ffmpeg(
            "-i", str(src), "-vf", "reverse", "-af", "areverse", "-c:v", "libx264", "-preset", "veryfast", str(out)
        )
    return out


async def to_voice(src: Path) -> Path:
    """Telegram voice notes are OGG/Opus mono."""
    out = _out(src, "ogg", "voice")
    await ffmpeg("-i", str(src), "-vn", "-c:a", "libopus", "-b:a", "48k", "-ac", "1", str(out))
    return out


async def to_video_note(src: Path, size: int = 384, max_seconds: float = 60) -> Path:
    """Telegram round video notes are square, ≤ 60 s."""
    out = _out(src, "mp4", "note")
    await ffmpeg(
        "-t",
        str(max_seconds),
        "-i",
        str(src),
        "-vf",
        f"crop='min(iw,ih)':'min(iw,ih)',scale={size}:{size}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        str(out),
    )
    return out


async def split(src: Path, max_bytes: int) -> list[Path]:
    """Split media into playable parts under max_bytes, falling back to a raw byte split.

    1. Stream copy at time boundaries (fast, lossless) — works when keyframes are frequent.
    2. Re-encode with a keyframe forced at every cut, so each part is still playable.
    3. Raw .001/.002 byte split (always fits, but must be rejoined before playing).
    """
    size = src.stat().st_size
    if size <= max_bytes:
        return [src]
    if kind_of_path(src) in ("video", "audio") and ffmpeg_available():
        duration = await duration_of(src)
        base_parts = math.ceil(size / (max_bytes * 0.9))
        # One lossless stream-copy attempt, then re-encodes with progressively more (shorter) parts.
        for reencode, parts_needed in (
            (False, base_parts),
            (True, base_parts),
            (True, base_parts + 1),
            (True, base_parts * 2),
        ):
            segment = max(duration / parts_needed, 1.0)
            parts = await _segment(src, segment, reencode=reencode, max_bytes=max_bytes)
            if parts and all(p.stat().st_size <= max_bytes for p in parts):
                return parts
            for p in parts:
                p.unlink(missing_ok=True)
    return await asyncio.to_thread(byte_split, src, max_bytes)


async def _segment(src: Path, segment: float, reencode: bool, max_bytes: int) -> list[Path]:
    tag = "enc" if reencode else "part"
    pattern = src.with_name(f"{src.stem}_{tag}%03d{src.suffix}")
    if reencode:
        # Budget the bitrate so each segment lands under the limit, and force a keyframe at each cut.
        total_kbps = max_bytes * 8 / 1000 / segment * 0.85
        audio_kbps = int(min(96, max(24, total_kbps * 0.25)))
        video_kbps = max(int(total_kbps - audio_kbps), 32)
        codec = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            f"{video_kbps}k",
            "-maxrate",
            f"{video_kbps}k",
            "-bufsize",
            f"{video_kbps}k",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment:.3f})",
            "-c:a",
            "aac",
            "-b:a",
            f"{audio_kbps}k",
        ]
        if kind_of_path(src) == "audio":
            # Audio-only: the whole budget goes to audio (capped at a sensible quality).
            audio_only = int(min(192, max(32, total_kbps)))
            encoder = "libmp3lame" if src.suffix.lower() == ".mp3" else "aac"
            codec = ["-c:a", encoder, "-b:a", f"{audio_only}k"]
    else:
        codec = ["-c", "copy"]
    try:
        await ffmpeg(
            "-i",
            str(src),
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            *codec,
            "-f",
            "segment",
            "-segment_time",
            f"{segment:.3f}",
            "-reset_timestamps",
            "1",
            str(pattern),
        )
    except MediaError:
        return sorted(src.parent.glob(f"{src.stem}_{tag}*{src.suffix}"))
    return sorted(src.parent.glob(f"{src.stem}_{tag}*{src.suffix}"))


def byte_split(src: Path, max_bytes: int) -> list[Path]:
    """Raw split into .001, .002 … (rejoin with `cat file.* > file` or 7-Zip)."""
    parts = []
    chunk = max_bytes - 1024
    with open(src, "rb") as fh:
        index = 1
        while True:
            data = fh.read(chunk)
            if not data:
                break
            part = src.with_name(f"{src.name}.{index:03d}")
            part.write_bytes(data)
            parts.append(part)
            index += 1
    return parts


# ----------------------------------------------------------------- images (Pillow)


def image_info(src: Path) -> dict[str, Any]:
    with Image.open(src) as im:
        info: dict[str, Any] = {
            "format": im.format,
            "mode": im.mode,
            "width": im.width,
            "height": im.height,
            "frames": getattr(im, "n_frames", 1),
        }
        exif = {}
        try:
            raw = im.getexif()
            for tag, value in raw.items():
                name = ExifTags.TAGS.get(tag, str(tag))
                if isinstance(value, bytes):
                    continue
                exif[name] = str(value)[:80]
        except Exception:  # noqa: BLE001
            pass
        info["exif"] = exif
    info["size"] = src.stat().st_size
    return info


def image_convert(src: Path, target: str) -> Path:
    target = "jpg" if target == "jpeg" else target
    if target not in IMAGE_TARGETS:
        raise MediaError(f"Unsupported image format: {target}")
    out = _out(src, target, "conv")
    with Image.open(src) as im:
        img = im.convert("RGB") if target in ("jpg", "bmp") and im.mode not in ("RGB", "L") else im
        params = {"quality": 92} if target in ("jpg", "webp") else {}
        img.save(out, "JPEG" if target == "jpg" else target.upper(), **params)
    return out


def image_resize(src: Path, height: int) -> Path:
    out = _out(src, src.suffix, f"{height}p")
    with Image.open(src) as im:
        width = max(1, round(im.width * height / im.height))
        im.resize((width, height), Image.LANCZOS).save(out)
    return out


def image_rotate(src: Path, degrees: int) -> Path:
    out = _out(src, src.suffix, f"rot{degrees}")
    with Image.open(src) as im:
        im.rotate(-degrees, expand=True).save(out)
    return out
