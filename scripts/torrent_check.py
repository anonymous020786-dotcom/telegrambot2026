"""End-to-end check of torrent downloads through the real job pipeline, with legal (Creative Commons) torrents.

Needs aria2 (in the Docker image) and the test suite (FakeBot). Run from the repo root:  python -m scripts.torrent_check
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from bot.app import build_services
from bot.config import Settings
from bot.services.jobs import Job
from tests.conftest import FakeBot

CASES = [
    # Big Buck Bunny (Blender Foundation, CC-BY) from the Internet Archive; file 11 is the 62 MB MP4
    (".torrent link, file 11", "https://archive.org/download/BigBuckBunny_124/BigBuckBunny_124_archive.torrent", "11"),
    # Sintel (Blender Foundation, CC-BY) as a magnet link; "auto" picks the largest video
    (
        "magnet link, auto",
        "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"
        "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce&tr=udp%3A%2F%2Fopen.demonii.com%3A1337%2Fannounce"
        "&ws=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2F",
        "auto",
    ),
]


async def main() -> None:
    settings = Settings(bot_token="1:x", admin_ids=[1], data_dir=Path(tempfile.mkdtemp()), torrent_stall_seconds=120)
    settings.ensure_dirs()
    services = build_services(settings)
    await services.db.connect()
    bot = FakeBot()
    services.jobs.start(bot)
    await services.db.upsert_user(1, "admin", "Admin")
    await services.db.set_role(1, "admin")  # what the bot does for ADMIN_IDS on their first message
    user = await services.db.get_user(1)
    try:
        for name, url, files in CASES:
            job = Job(user_id=1, chat_id=1, url=url, kind="torrent", options={"files": files})
            before = len(bot.sent_files)
            await services.jobs.submit(job, user)
            peak = 0
            for _ in range(3000):
                if job.status in ("done", "failed", "cancelled"):
                    break
                peak = max(peak, job.downloaded)
                await asyncio.sleep(0.2)
            if job.status == "done":
                kind, sent = bot.sent_files[before][:2]
                print(f"OK   {name:<24} '{job.title}' sent as {kind} ({sent}), progress seen up to {peak / 1e6:.0f} MB")
            else:
                print(f"FAIL {name:<24} {job.status}: {job.error}")
    finally:
        await services.jobs.stop()
        await services.db.close()


if __name__ == "__main__":
    asyncio.run(main())
