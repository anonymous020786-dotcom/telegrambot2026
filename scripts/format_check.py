"""Download public test streams through the bot's real download code and report what came out.

Covers the streaming protocols (HLS with .ts and fMP4/CMAF chunks, MPEG-DASH, Smooth Streaming, RTMP), the
codecs (H.264, H.265, AV1, VP9) and the output containers (mp4, mkv, webm, mov) the bot offers.
Run inside the bot container:  python -m scripts.format_check [name-filter]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bot.config import Settings
from bot.services.downloader import Downloader, Preset

TV = "https://test-videos.co.uk/vids/bigbuckbunny"
CLIP = (0.0, 20.0)  # only the first 20 s of long streams

TESTS: list[tuple[str, str, Preset]] = [
    (
        "HLS .ts chunks",
        "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
        Preset(quality="480", container="mp4", embed_thumbnail=False, section=CLIP),
    ),
    (
        "HLS fMP4/CMAF",
        "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8",
        Preset(quality="480", container="mp4", embed_thumbnail=False, section=CLIP),
    ),
    (
        "DASH -> mkv",
        "https://dash.akamaized.net/akamai/bbb_30fps/bbb_30fps.mpd",
        Preset(quality="360", container="mkv", embed_thumbnail=False, section=CLIP),
    ),
    (
        "DASH VP9 -> webm",
        "https://storage.googleapis.com/shaka-demo-assets/angel-one/dash.mpd",
        Preset(quality="360", container="webm", embed_thumbnail=False),
    ),
    (
        "Smooth Streaming",
        "https://playready.directtaps.net/smoothstreaming/SSWSS720H264/SuperSpeedway_720.ism/Manifest",
        Preset(quality="360", container="mp4", embed_thumbnail=False),  # this format can't be clipped
    ),
    ("file H.264 mp4", f"{TV}/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4", Preset(embed_thumbnail=False)),
    ("file H.265 mp4", f"{TV}/mp4/h265/360/Big_Buck_Bunny_360_10s_1MB.mp4", Preset(embed_thumbnail=False)),
    ("file AV1 mp4", f"{TV}/mp4/av1/360/Big_Buck_Bunny_360_10s_1MB.mp4", Preset(embed_thumbnail=False)),
    ("file VP9 webm", f"{TV}/webm/vp9/360/Big_Buck_Bunny_360_10s_1MB.webm", Preset(embed_thumbnail=False)),
    ("file mkv", f"{TV}/mkv/360/Big_Buck_Bunny_360_10s_1MB.mkv", Preset(embed_thumbnail=False)),
    (
        "file -> mov",
        f"{TV}/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4",
        Preset(container="mov", embed_thumbnail=False),
    ),
]


def describe(path: Path) -> str:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    data = json.loads(out or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float((data.get("format") or {}).get("duration") or 0)
    return (
        f"{path.suffix} {video.get('codec_name')} {video.get('width')}x{video.get('height')} "
        f"audio={audio.get('codec_name')} {duration:.0f}s {path.stat().st_size // 1024}KB"
    )


def main() -> None:
    wanted = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    downloader = Downloader(Settings())
    for name, url, preset in TESTS:
        if wanted and wanted not in name.lower():
            continue
        out = Path(tempfile.mkdtemp())
        t0 = time.monotonic()
        try:
            _, files = downloader.download_sync(url, preset, out)
            print(f"OK   {name:<18} -> {describe(files[0])} ({time.monotonic() - t0:.0f}s)", flush=True)
        except Exception as exc:  # noqa: BLE001 - report every failure
            print(f"FAIL {name:<18} {str(exc).replace('ERROR: ', '')[:160]}", flush=True)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
