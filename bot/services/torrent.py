"""BitTorrent downloads (magnet links and .torrent files) through aria2.

Only video files are fetched: by default the largest one, or all of them, or the files the user picks. Seeding stops
the moment the download completes and uploads are rate-limited while it runs, because downloading a torrent also
shares its pieces with other peers (which matters for copyrighted material). Local peer discovery is off so the
client never goes looking on the server's own network.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..utils import VIDEO_EXTS, ext_of
from .netguard import request_hook

log = logging.getLogger(__name__)

MAGNET_RE = re.compile(r"magnet:\?[^\s<>\"']*xt=urn:bt[im]h:[A-Za-z0-9]+[^\s<>\"']*", re.I)
MAX_TORRENT_FILE = 10 * 1024 * 1024  # .torrent metadata is small; refuse anything bigger


class TorrentError(Exception):
    pass


@dataclass
class TorrentFile:
    index: int  # 1-based, as aria2's --select-file counts
    path: str
    size: int

    @property
    def is_video(self) -> bool:
        return ext_of(self.path) in VIDEO_EXTS and not re.search(r"(^|[/\\._ -])sample([/\\._ -]|$)", self.path, re.I)


@dataclass
class TorrentInfo:
    name: str
    info_hash: str
    files: list[TorrentFile] = field(default_factory=list)

    @property
    def videos(self) -> list[TorrentFile]:
        return [f for f in self.files if f.is_video]

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


# ------------------------------------------------------------------ .torrent parsing


def bdecode(data: bytes):
    """Decode bencoded data (the .torrent format). Returns (value, raw bytes of the 'info' dict)."""
    info_span: list[int] = []

    def parse(i: int, depth: int = 0):
        if depth > 64:
            raise ValueError("bencode nested too deeply")
        c = data[i : i + 1]
        if c == b"i":
            end = data.index(b"e", i)
            return int(data[i + 1 : end]), end + 1
        if c == b"l":
            i += 1
            out = []
            while data[i : i + 1] != b"e":
                v, i = parse(i, depth + 1)
                out.append(v)
            return out, i + 1
        if c == b"d":
            i += 1
            out = {}
            while data[i : i + 1] != b"e":
                k, i = parse(i, depth + 1)
                start = i
                v, i = parse(i, depth + 1)
                if k == b"info" and depth == 0:
                    info_span[:] = [start, i]
                out[k] = v
            return out, i + 1
        if c.isdigit():
            colon = data.index(b":", i)
            length = int(data[i:colon])
            start = colon + 1
            if length < 0 or start + length > len(data):
                raise ValueError("bencode string runs past the end")
            return data[start : start + length], start + length
        raise ValueError(f"bad bencode at byte {i}")

    value, _ = parse(0)
    raw_info = data[info_span[0] : info_span[1]] if info_span else b""
    return value, raw_info


def _text(value: bytes | None) -> str:
    return (value or b"").decode("utf-8", "replace")


def parse_torrent(path: Path) -> TorrentInfo:
    data = path.read_bytes()
    if len(data) > MAX_TORRENT_FILE:
        raise TorrentError("That .torrent file is too big to be real.")
    try:
        meta, raw_info = bdecode(data)
        info = meta[b"info"]
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise TorrentError("That isn't a valid .torrent file.") from exc
    name = _text(info.get(b"name.utf-8") or info.get(b"name")) or "torrent"
    files: list[TorrentFile] = []
    if b"files" in info:  # multi-file torrent: paths are relative to a folder named after the torrent
        for n, entry in enumerate(info[b"files"], start=1):
            parts = entry.get(b"path.utf-8") or entry.get(b"path") or []
            files.append(TorrentFile(n, "/".join([name, *(_text(p) for p in parts)]), int(entry.get(b"length", 0))))
    else:
        files.append(TorrentFile(1, name, int(info.get(b"length", 0))))
    # aria2 skips BEP 47 padding files in its numbering only if they're real entries; keep them but never pick them.
    return TorrentInfo(name=name, info_hash=hashlib.sha1(raw_info).hexdigest(), files=files)


def choose(info: TorrentInfo, spec: str = "auto") -> list[TorrentFile]:
    """Which files to fetch: 'auto' = the largest video, 'all' = every video, '1,3,5' = those file numbers."""
    spec = (spec or "auto").strip().lower()
    videos = info.videos
    if spec in ("auto", ""):
        if not videos:
            raise TorrentError("This torrent has no video files. Use /torrent <link> list to see what it contains.")
        return [max(videos, key=lambda f: f.size)]
    if spec == "all":
        if not videos:
            raise TorrentError("This torrent has no video files.")
        return videos
    try:
        wanted = {int(x) for x in re.split(r"[,\s]+", spec) if x}
    except ValueError as exc:
        raise TorrentError("Pick files by number, e.g. /torrent <link> 1,3 (see /torrent <link> list).") from exc
    picked = [f for f in info.files if f.index in wanted]
    if not picked:
        raise TorrentError("None of those file numbers exist in this torrent.")
    if not all(f.is_video for f in picked):
        raise TorrentError("Only video files can be downloaded from torrents.")
    return picked


# ------------------------------------------------------------------ getting the metadata


def aria2_available() -> bool:
    return shutil.which("aria2c") is not None


BASE_ARGS = [
    "--enable-color=false",
    "--console-log-level=warn",
    "--summary-interval=1",
    "--file-allocation=none",
    "--auto-file-renaming=false",
    "--allow-overwrite=true",
    "--bt-enable-lpd=false",  # no local peer discovery on the server's network
    "--enable-dht=true",
    "--seed-time=0",  # stop sharing as soon as the download completes
]


async def fetch_metadata(
    source: str, workdir: Path, user_agent: str, allow_private: bool = False, timeout: float = 120
) -> Path:
    """A local .torrent path for a magnet link, a .torrent URL, or an already-local .torrent file."""
    workdir.mkdir(parents=True, exist_ok=True)
    if not source.startswith(("magnet:", "http://", "https://")):
        path = Path(source)
        if not path.is_file():
            raise TorrentError("The .torrent file is gone; send it again.")
        return path
    if source.startswith("http"):
        hooks = {"request": [request_hook(allow_private)]}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, event_hooks=hooks) as client:
            res = await client.get(source, headers={"User-Agent": user_agent})
            res.raise_for_status()
            if len(res.content) > MAX_TORRENT_FILE:
                raise TorrentError("That link isn't a .torrent file.")
            path = workdir / "download.torrent"
            path.write_bytes(res.content)
            return path
    if not aria2_available():
        raise TorrentError("Torrent support needs aria2 (it's in the Docker image).")
    before = set(workdir.glob("*.torrent"))
    args = ["aria2c", *BASE_ARGS, "--bt-metadata-only=true", "--bt-save-metadata=true", f"--dir={workdir}", source]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise TorrentError("Couldn't get this magnet link's file list from any peer (nobody is sharing it).") from None
    found = [p for p in workdir.glob("*.torrent") if p not in before]
    if not found:
        raise TorrentError("Couldn't get this magnet link's file list from any peer.")
    return found[0]


# ------------------------------------------------------------------ downloading

SIZE_RE = r"([\d.]+)([KMGT]?i?B)"
READOUT_RE = re.compile(rf"\[#\w+\s+{SIZE_RE}/{SIZE_RE}\((\d+)%\)(?:.*?DL:{SIZE_RE})?(?:.*?ETA:([\dhms]+))?")
UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def _bytes(number: str, unit: str) -> int:
    return int(float(number) * UNITS.get(unit, 1))


def _seconds(eta: str | None) -> float | None:
    if not eta:
        return None
    total = 0
    for value, unit in re.findall(r"(\d+)([hms])", eta):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return float(total)


def parse_readout(line: str) -> dict | None:
    """aria2's one-line summary → {'downloaded', 'total', 'speed', 'eta'}."""
    m = READOUT_RE.search(line)
    if not m:
        return None
    speed = _bytes(m.group(6), m.group(7)) if m.group(6) else None
    return {
        "downloaded": _bytes(m.group(1), m.group(2)),
        "total": _bytes(m.group(3), m.group(4)),
        "speed": speed,
        "eta": _seconds(m.group(8)),
    }


async def download(
    torrent: Path,
    files: list[TorrentFile],
    out_dir: Path,
    progress: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    stall_timeout: int = 300,
    upload_limit_kb: int = 100,
) -> list[Path]:
    """Fetch the chosen files into `out_dir`; returns their paths."""
    if not aria2_available():
        raise TorrentError("Torrent support needs aria2 (it's in the Docker image).")
    out_dir.mkdir(parents=True, exist_ok=True)
    args = [
        "aria2c",
        *BASE_ARGS,
        f"--dir={out_dir}",
        f"--select-file={','.join(str(f.index) for f in files)}",
        f"--bt-stop-timeout={stall_timeout}",  # give up when no data arrives for this long
        f"--max-overall-upload-limit={upload_limit_kb}K",
        "--bt-remove-unselected-file=true",
        "--follow-torrent=mem",
        str(torrent),
    ]
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    tail: list[str] = []
    assert proc.stdout is not None
    try:
        buffer = b""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            *lines, buffer = re.split(rb"[\r\n]", buffer)
            for raw in lines:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if (stats := parse_readout(line)) and progress:
                    progress(stats)
                elif not line.startswith(("[#", "***", "===", "FILE:", "---", "Download Results")):
                    tail = [*tail[-5:], line]
            if cancelled and cancelled():
                proc.kill()
                break
        code = await proc.wait()
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    if cancelled and cancelled():
        return []
    paths = [out_dir / f.path for f in files]
    missing = [p for p in paths if not p.is_file()]
    if code != 0 or missing:
        detail = " ".join(tail)[-300:]
        if code == 7 or "bt-stop-timeout" in detail.lower() or not detail:
            raise TorrentError("The torrent stalled: no peer sent data for a while (it may have no seeders).")
        raise TorrentError(f"The torrent download failed: {detail}")
    for leftover in out_dir.glob("*.aria2"):
        leftover.unlink(missing_ok=True)
    return paths
