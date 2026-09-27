"""End-to-end check of WebRTC recording through the real job pipeline, against a local MediaMTX server.

Needs the MediaMTX binary (https://github.com/bluenviron/mediamtx) at $MEDIAMTX and the test suite (FakeBot).
Run from the repo root:  MEDIAMTX=/path/to/mediamtx python -m scripts.webrtc_check
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from bot.app import build_services
from bot.config import Settings
from bot.services.jobs import Job
from tests.conftest import FakeBot

PUBLISH = [
    "ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-f", "lavfi",
    "-i", "sine=frequency=440:sample_rate=48000", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
    "-profile:v", "baseline", "-bf", "0", "-g", "60", "-pix_fmt", "yuv420p", "-c:a", "libopus",
    "-f", "rtsp", "rtsp://127.0.0.1:8554/live",
]  # fmt: skip


def describe(path: Path) -> str:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration", "-of", "json"]
    data = json.loads(subprocess.run([*cmd, str(path)], capture_output=True, text=True, check=False).stdout or "{}")
    streams = [(s["codec_name"], s.get("width"), s.get("height")) for s in data.get("streams", [])]
    return f"{streams} {float(data.get('format', {}).get('duration', 0)):.1f}s"


async def run_job(services, bot: FakeBot, user, url: str, kind: str, options: dict) -> str:
    job = Job(user_id=user.id, chat_id=user.id, url=url, kind=kind, options=options)
    before = len(bot.sent_files)
    await services.jobs.submit(job, user)
    for _ in range(900):
        if job.status in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)
    if job.status != "done":
        return f"FAIL {job.status}: {job.error}"
    kind_sent, path = bot.sent_files[before][:2]
    return f"OK   sent as {kind_sent} -> {describe(Path(path)) if Path(path).exists() else path}"


async def main() -> None:
    data = Path(tempfile.mkdtemp())
    settings = Settings(
        bot_token="1:x", admin_ids=[1], data_dir=data, allow_private_urls=True, browser_timeout_seconds=30
    )
    settings.ensure_dirs()
    services = build_services(settings)
    await services.db.connect()
    bot = FakeBot()
    services.jobs.start(bot)
    user = await services.db.upsert_user(1, "admin", "Admin")
    try:
        cases = [
            ("WHEP link pasted", "http://127.0.0.1:8889/live/whep", "media", {}),
            ("/record player page", "http://127.0.0.1:8889/live", "record", {"seconds": 10}),
            ("player page pasted", "http://127.0.0.1:8889/live", "media", {}),
        ]
        settings.webrtc_record_seconds = 8
        for name, url, kind, options in cases:
            print(f"{name:<22} {await run_job(services, bot, user, url, kind, options)}", flush=True)
    finally:
        await services.jobs.stop()
        await services.db.close()


def serve_live_stream() -> list[subprocess.Popen]:
    """Start MediaMTX and publish a live H.264/Opus test pattern to it."""
    binary = Path(os.environ["MEDIAMTX"])
    server = subprocess.Popen([str(binary)], cwd=binary.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    publisher = subprocess.Popen(PUBLISH)
    time.sleep(3)
    return [server, publisher]


if __name__ == "__main__":
    processes = serve_live_stream()
    try:
        asyncio.run(main())
    finally:
        for process in processes:
            process.kill()
