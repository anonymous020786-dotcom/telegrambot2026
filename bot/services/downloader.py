"""yt-dlp wrapper: probing, format selection, downloading with progress and cancellation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, download_range_func

from ..config import Settings
from ..utils import AUDIO_EXTS, IMAGE_EXTS, SUB_EXTS, VIDEO_EXTS, ext_of

log = logging.getLogger(__name__)

AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav", "aac", "ogg")
LOSSLESS = {"flac", "wav"}
QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360", "240", "144", "worst")
CONTAINERS = ("mp4", "mkv", "webm")
TEMP_SUFFIXES = (".part", ".ytdl", ".temp", ".frag")


class Cancelled(Exception):
    """Raised when the user cancels a download."""


@dataclass
class Preset:
    """Everything that determines what gets downloaded (also used as a cache key)."""

    mode: str = "video"  # video | audio | format | thumbnail | subs
    quality: str = "best"
    container: str = "mp4"
    audio_format: str = "mp3"
    audio_bitrate: int = 192
    format_id: str | None = None
    format_has_audio: bool = True
    section: tuple[float, float] | None = None
    playlist: bool = False
    playlist_items: str | None = None
    sub_lang: str = "en"
    embed_subs: bool = False
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    split_chapters: bool = False
    filename: str = "%(title).80B"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.section:
            d["section"] = list(self.section)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Preset:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if known.get("section"):
            known["section"] = tuple(known["section"])
        return cls(**known)

    def label(self) -> str:
        if self.mode == "audio":
            rate = "" if self.audio_format in LOSSLESS else f" {self.audio_bitrate}k"
            return f"{self.audio_format.upper()}{rate}"
        if self.mode == "format":
            return f"format {self.format_id}"
        if self.mode == "thumbnail":
            return "thumbnail"
        if self.mode == "subs":
            return f"subtitles ({self.sub_lang})"
        q = self.quality if self.quality in ("best", "worst") else f"{self.quality}p"
        extra = " clip" if self.section else ""
        return f"{q} {self.container.upper()}{extra}"


@dataclass
class FormatInfo:
    format_id: str
    ext: str
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    filesize: int | None
    tbr: float | None
    note: str

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec) and self.vcodec != "none"

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec) and self.acodec != "none"


@dataclass
class MediaInfo:
    url: str
    title: str
    extractor: str
    uploader: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    description: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    upload_date: str | None = None
    is_live: bool = False
    is_playlist: bool = False
    entries: list[dict[str, Any]] = field(default_factory=list)
    formats: list[FormatInfo] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)
    subtitles: list[str] = field(default_factory=list)
    auto_captions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def heights(self) -> list[int]:
        return sorted({f.height for f in self.formats if f.has_video and f.height}, reverse=True)

    def best_audio(self) -> FormatInfo | None:
        audio = [f for f in self.formats if f.has_audio and not f.has_video]
        return max(audio, key=lambda f: f.tbr or 0, default=None)

    def estimate_size(self, height: int | None) -> int | None:
        """Rough size of best video ≤ height plus best audio."""
        vids = [f for f in self.formats if f.has_video and (height is None or (f.height or 0) <= height)]
        if not vids:
            return None
        best = max(vids, key=lambda f: ((f.height or 0), f.tbr or 0))
        size = best.filesize or 0
        if not best.has_audio:
            audio = self.best_audio()
            size += (audio.filesize or 0) if audio else 0
        return size or None


def _simplify_formats(info: dict[str, Any]) -> list[FormatInfo]:
    out = []
    for f in info.get("formats") or []:
        if f.get("format_note") == "storyboard" or (f.get("ext") == "mhtml"):
            continue
        out.append(
            FormatInfo(
                format_id=str(f.get("format_id")),
                ext=f.get("ext") or "?",
                height=f.get("height"),
                fps=f.get("fps"),
                vcodec=f.get("vcodec"),
                acodec=f.get("acodec"),
                filesize=f.get("filesize") or f.get("filesize_approx"),
                tbr=f.get("tbr"),
                note=f.get("format_note") or f.get("format") or "",
            )
        )
    return out


def media_info_from(info: dict[str, Any], url: str) -> MediaInfo:
    is_playlist = info.get("_type") in ("playlist", "multi_video") or bool(info.get("entries"))
    entries = []
    if is_playlist:
        for e in info.get("entries") or []:
            if not e:
                continue
            entries.append(
                {
                    "id": e.get("id"),
                    "title": e.get("title") or e.get("id"),
                    "url": e.get("url") or e.get("webpage_url"),
                    "duration": e.get("duration"),
                }
            )
    return MediaInfo(
        url=info.get("webpage_url") or url,
        title=info.get("title") or info.get("id") or "untitled",
        extractor=info.get("extractor_key") or info.get("extractor") or "generic",
        uploader=info.get("uploader") or info.get("channel") or info.get("creator"),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        description=info.get("description"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        upload_date=info.get("upload_date"),
        is_live=bool(info.get("is_live")),
        is_playlist=is_playlist,
        entries=entries,
        formats=_simplify_formats(info),
        chapters=list(info.get("chapters") or []),
        subtitles=sorted((info.get("subtitles") or {}).keys()),
        auto_captions=sorted((info.get("automatic_captions") or {}).keys()),
        raw=info,
    )


def format_selector(preset: Preset) -> str:
    """yt-dlp format string for a preset."""
    if preset.mode == "audio":
        return "ba/b"
    if preset.mode == "format" and preset.format_id:
        fid = preset.format_id
        return fid if preset.format_has_audio else f"{fid}+ba/{fid}"
    if preset.quality == "worst":
        return "wv*+wa/w"
    height = f"[height<={preset.quality}]" if preset.quality.isdigit() else ""
    if preset.container == "mp4":
        return f"bv*{height}[ext=mp4]+ba[ext=m4a]/bv*{height}+ba/b{height}" + ("/bv*+ba/b" if height else "")
    if preset.container == "webm":
        return f"bv*{height}[ext=webm]+ba[ext=webm]/bv*{height}+ba/b{height}" + ("/bv*+ba/b" if height else "")
    return f"bv*{height}+ba/b{height}" + ("/bv*+ba/b" if height else "")


def build_options(
    preset: Preset,
    settings: Settings,
    out_dir: Path,
    hook: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "outtmpl": {
            "default": str(out_dir / f"{preset.filename} [%(id)s].%(ext)s"),
            "chapter": str(out_dir / "%(title).60B - %(section_number)03d %(section_title).60B.%(ext)s"),
            "thumbnail": str(out_dir / f"{preset.filename} [%(id)s].%(ext)s"),
        },
        "paths": {"home": str(out_dir), "temp": str(out_dir)},
        "format": format_selector(preset),
        "noplaylist": not preset.playlist,
        "max_filesize": settings.max_download_mb * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "ignoreerrors": "only_download" if preset.playlist else False,
        "overwrites": True,
        "postprocessors": [],
        "http_headers": {"User-Agent": settings.user_agent},
    }
    if settings.proxy:
        opts["proxy"] = settings.proxy
    if settings.cookies_file and Path(settings.cookies_file).is_file():
        opts["cookiefile"] = str(settings.cookies_file)
    if preset.playlist:
        opts["playlist_items"] = preset.playlist_items or f"1-{settings.max_playlist_items}"
    if hook:
        opts["progress_hooks"] = [hook]
        opts["postprocessor_hooks"] = [hook]

    pps: list[dict[str, Any]] = opts["postprocessors"]

    if preset.mode == "thumbnail":
        opts.update(skip_download=True, writethumbnail=True)
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        return opts

    if preset.mode == "subs":
        opts.update(
            skip_download=True,
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=[preset.sub_lang, f"{preset.sub_lang}-.*"] if preset.sub_lang != "all" else ["all"],
            subtitlesformat="srt/vtt/best",
        )
        pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})
        return opts

    if preset.section:
        start, end = preset.section
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = True

    if preset.mode == "audio":
        codec = "vorbis" if preset.audio_format == "ogg" else preset.audio_format
        pps.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": codec,
                "preferredquality": "0" if preset.audio_format in LOSSLESS else str(preset.audio_bitrate),
            }
        )
    else:
        opts["merge_output_format"] = preset.container if preset.container != "mp4" else "mp4/mkv"
        if preset.embed_subs and preset.container in ("mp4", "mkv"):
            opts.update(writesubtitles=True, subtitleslangs=[preset.sub_lang, f"{preset.sub_lang}-.*"])
            pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

    if preset.embed_metadata:
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
    thumb_ok = preset.mode == "audio" and preset.audio_format in ("mp3", "m4a", "opus", "flac", "ogg")
    thumb_ok = thumb_ok or (preset.mode != "audio" and preset.container in ("mp4", "mkv"))
    if preset.embed_thumbnail and thumb_ok and not preset.section:
        opts["writethumbnail"] = True
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    if preset.split_chapters:
        pps.append({"key": "FFmpegSplitChapters", "force_keyframes": False})
    return opts


def collect_outputs(out_dir: Path, preset: Preset) -> list[Path]:
    """Finished files in a job directory, filtered to what the preset produces."""
    files = [
        p
        for p in sorted(out_dir.rglob("*"))
        if p.is_file() and not p.name.endswith(TEMP_SUFFIXES) and not p.name.startswith(".")
    ]
    if preset.mode == "thumbnail":
        wanted = IMAGE_EXTS
    elif preset.mode == "subs":
        wanted = SUB_EXTS
    elif preset.mode == "audio":
        wanted = AUDIO_EXTS
    else:
        wanted = VIDEO_EXTS | AUDIO_EXTS
    chosen = [p for p in files if ext_of(p) in wanted]
    if preset.split_chapters:
        # When splitting, send the chapter files rather than the full-length original.
        chapters = [p for p in chosen if " - " in p.stem and p.stem.rsplit(" - ", 1)[-1][:3].isdigit()]
        chosen = chapters or chosen
    return chosen or [p for p in files if ext_of(p) not in IMAGE_EXTS | SUB_EXTS | {".json"}]


class Downloader:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 30,
            "http_headers": {"User-Agent": self.settings.user_agent},
        }
        if self.settings.proxy:
            opts["proxy"] = self.settings.proxy
        if self.settings.cookies_file and Path(self.settings.cookies_file).is_file():
            opts["cookiefile"] = str(self.settings.cookies_file)
        return opts

    def _extract(self, url: str, **extra: Any) -> dict[str, Any]:
        with yt_dlp.YoutubeDL({**self._base_opts(), **extra}) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise DownloadError("No media found")
            return ydl.sanitize_info(info)

    async def probe(self, url: str, playlist: bool = False) -> MediaInfo:
        extra: dict[str, Any] = {"noplaylist": not playlist}
        if playlist:
            extra.update(extract_flat="in_playlist", playlistend=self.settings.max_playlist_items * 4)
        info = await asyncio.to_thread(self._extract, url, **extra)
        return media_info_from(info, url)

    async def flat_entries(self, url: str, limit: int = 15) -> MediaInfo:
        info = await asyncio.to_thread(self._extract, url, extract_flat="in_playlist", playlistend=limit)
        return media_info_from(info, url)

    async def search(self, query: str, count: int = 8, site: str = "yt") -> list[dict[str, Any]]:
        prefix = {"yt": "ytsearch", "sc": "scsearch"}[site]
        info = await asyncio.to_thread(self._extract, f"{prefix}{count}:{query}", extract_flat="in_playlist")
        results = []
        for e in info.get("entries") or []:
            if not e:
                continue
            url = e.get("url") or e.get("webpage_url")
            if site == "yt" and url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={e.get('id')}"
            results.append(
                {
                    "title": e.get("title") or "untitled",
                    "url": url,
                    "duration": e.get("duration"),
                    "uploader": e.get("uploader") or e.get("channel"),
                }
            )
        return results

    async def direct_urls(self, url: str, preset: Preset) -> list[tuple[str, str]]:
        """(label, direct media URL) pairs for the selected formats (links usually expire)."""
        info = await asyncio.to_thread(self._extract, url, format=format_selector(preset), noplaylist=True)
        out = []
        for f in info.get("requested_formats") or [info]:
            if f.get("url"):
                kind = "video" if f.get("vcodec", "none") != "none" else "audio"
                out.append((f"{kind} {f.get('format_id')} · {f.get('ext')}", f["url"]))
        return out

    def download_sync(
        self, url: str, preset: Preset, out_dir: Path, hook: Callable[[dict[str, Any]], None] | None = None
    ) -> tuple[dict[str, Any] | None, list[Path]]:
        out_dir.mkdir(parents=True, exist_ok=True)
        opts = build_options(preset, self.settings, out_dir, hook)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                info = ydl.sanitize_info(info) if info else None
        except DownloadCancelled as exc:
            raise Cancelled() from exc
        files = collect_outputs(out_dir, preset)
        if not files:
            if info and info.get("is_live"):
                raise DownloadError("Live streams can't be downloaded while they are live")
            raise DownloadError("Nothing was downloaded (the format may be unavailable or too large)")
        return info, files

    async def download(
        self, url: str, preset: Preset, out_dir: Path, hook: Callable[[dict[str, Any]], None] | None = None
    ) -> tuple[dict[str, Any] | None, list[Path]]:
        return await asyncio.to_thread(self.download_sync, url, preset, out_dir, hook)


def supported_extractor(url: str) -> str | None:
    """Name of the dedicated yt-dlp extractor for a URL (None = only the generic extractor)."""
    for ie in yt_dlp.extractor.gen_extractor_classes():
        name = ie.ie_key()
        if name == "Generic":
            continue
        try:
            if ie.suitable(url) and ie.working():
                return name
        except Exception:  # noqa: BLE001 - defensive: some extractors raise on odd URLs
            continue
    return None


def extractor_names() -> list[str]:
    names = set()
    for ie in yt_dlp.extractor.gen_extractor_classes():
        if ie.working() and ie.ie_key() != "Generic":
            names.add(getattr(ie, "IE_NAME", ie.ie_key()).split(":")[0])
    return sorted(names, key=str.lower)


def friendly_error(exc: BaseException) -> str:
    msg = str(exc)
    msg = msg.replace("ERROR: ", "").split("\n")[0]
    lowered = msg.lower()
    if "unsupported url" in lowered:
        return "This link isn't a supported media page. Try /images for pictures on any web page."
    if "drm" in lowered:
        return "This video is DRM-protected and can't be downloaded."
    if "private" in lowered or "login" in lowered or "sign in" in lowered:
        return "This media isn't public (it needs a login)."
    if "file is larger than max-filesize" in lowered or "too large" in lowered:
        return "The file is larger than the bot's maximum download size."
    if "requested format is not available" in lowered:
        return "That quality/format isn't available. Try /formats to see what exists."
    if "http error 404" in lowered:
        return "The page or media wasn't found (404)."
    if "http error 403" in lowered:
        return "The site refused the request (403). It may block downloads or need cookies."
    return msg[:300] or exc.__class__.__name__
