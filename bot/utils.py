"""Pure helpers (no Telegram / network) — unit-tested."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".tif", ".tiff", ".heic", ".jxl", ".svg"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".3gp", ".wmv", ".ogv"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".wma", ".alac", ".aiff"}
SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".ttml", ".lrc"}


def extract_urls(text: str | None) -> list[str]:
    """All http(s) URLs in text, in order, de-duplicated, with trailing punctuation removed."""
    if not text:
        return []
    out: list[str] = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)]}>»")
        if url not in out:
            out.append(url)
    return out


def is_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def ext_of(path: str | Path) -> str:
    return Path(str(path).split("?")[0]).suffix.lower()


def kind_of_path(path: str | Path) -> str:
    ext = ext_of(path)
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in SUB_EXTS:
        return "subtitle"
    return "file"


def looks_like_image_url(url: str) -> bool:
    return ext_of(urlparse(url).path) in IMAGE_EXTS


def human_size(num: float | int | None) -> str:
    if num is None:
        return "?"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    seconds = round(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_timestamp(value: str) -> float:
    """'83', '1:23', '01:02:03.5', '1h2m3s', '90s' → seconds. Raises ValueError."""
    v = value.strip().lower()
    if not v:
        raise ValueError("empty time")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", v)
    if m and any(m.groups()):
        h, mi, s = (float(x) if x else 0.0 for x in m.groups())
        return h * 3600 + mi * 60 + s
    parts = v.split(":")
    if len(parts) > 3:
        raise ValueError(f"bad time: {value}")
    total = 0.0
    for part in parts:
        if not re.fullmatch(r"\d+(?:\.\d+)?", part):
            raise ValueError(f"bad time: {value}")
        total = total * 60 + float(part)
    return total


def parse_size(value: str) -> int:
    """'25MB', '1.5g', '800k', '1000' (bytes) → bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*", value.lower())
    if not m:
        raise ValueError(f"bad size: {value}")
    mult = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[m.group(2)]
    return int(float(m.group(1)) * mult)


def parse_when(value: str, tz: str = "UTC", now: datetime | None = None) -> datetime:
    """'in 30m' / '+2h' / '18:30' / '2026-10-01 08:00' → aware UTC datetime in the future."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    now = now or datetime.now(UTC)
    v = value.strip().lower()
    rel = re.fullmatch(r"(?:in\s+|\+)(.+)", v)
    if rel:
        return now + timedelta(seconds=parse_timestamp(rel.group(1).replace(" ", "")))
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
    if m:
        local_now = now.astimezone(zone)
        target = local_now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if target <= local_now:
            target += timedelta(days=1)
        return target.astimezone(UTC)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}):(\d{2})", v)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        target = datetime(y, mo, d, h, mi, tzinfo=zone).astimezone(UTC)
        if target <= now:
            raise ValueError("that time is in the past")
        return target
    raise ValueError("use 'in 30m', '+2h', 'HH:MM' or 'YYYY-MM-DD HH:MM'")


def progress_bar(fraction: float, width: int = 12) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "▰" * filled + "▱" * (width - filled)


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=False)


def truncate(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def safe_filename(name: str, limit: int = 120) -> str:
    cleaned = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "_", name).strip(" .")
    return (cleaned or "file")[:limit]


def cache_key(url: str, preset: dict) -> str:
    raw = json.dumps({"u": url, "p": preset}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def file_digest(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_ts(ts: float | None, tz: str = "UTC") -> str:
    if not ts:
        return "never"
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return datetime.fromtimestamp(ts, zone).strftime("%Y-%m-%d %H:%M")


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    delta = max(0, time.time() - ts)
    for unit, secs in (("d", 86400), ("h", 3600), ("m", 60)):
        if delta >= secs:
            return f"{int(delta // secs)}{unit} ago"
    return "just now"


def parse_range(value: str, limit: int) -> str:
    """Validate a yt-dlp playlist range like '1-10', '3,5,7', '5:' → normalized string."""
    v = value.replace(" ", "")
    if not re.fullmatch(r"(\d+(-\d+)?|\d+:\d*|:\d+)(,(\d+(-\d+)?))*", v):
        raise ValueError("use a range like 1-10 or 1,3,5")
    nums = [int(x) for x in re.findall(r"\d+", v)]
    if nums and max(nums) > 10000:
        raise ValueError("range too large")
    if nums and v.count("-") == 1 and "," not in v:
        a, b = nums[0], nums[-1]
        if b - a + 1 > limit:
            raise ValueError(f"at most {limit} items at once")
    return v
