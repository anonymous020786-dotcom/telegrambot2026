"""yt-dlp plugin extractor for pat.com.

pat.com runs on the same backend as beeg.com (store.externulls.com). Page links look like
https://pat.com/-0<file id> (or /embed/0<file id>); the "-0" prefix is not part of the id the API takes.
The API returns a public, unencrypted HLS master playlist served from video.pat.com.
"""

from typing import ClassVar

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import int_or_none, traverse_obj, unified_timestamp


class PatIE(InfoExtractor):
    IE_NAME = "pat"
    _VALID_URL = r"https?://(?:www\.)?pat\.com/(?:-0|embed/0)(?P<id>\d+)"
    _API = "https://store.externulls.com/facts/file/{}"
    _CDN = "https://video.pat.com/"
    _THUMBS = "https://thumbs.externulls.com/videos/{}/{}.webp"
    _CODECS: ClassVar[dict[str, str]] = {"avc1": "h264", "hvc1": "h265", "hev1": "h265", "av01": "av1"}
    _TESTS: ClassVar[list[dict]] = [
        {
            "url": "https://pat.com/-0297132863926995",
            "info_dict": {
                "id": "297132863926995",
                "ext": "mp4",
                "title": str,
                "duration": 790,
                "age_limit": 18,
            },
            "params": {"skip_download": True},
        }
    ]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        headers = {"Referer": "https://pat.com/", "Origin": "https://pat.com"}
        video = self._download_json(self._API.format(video_id), video_id, headers=headers)

        file = video.get("file") or {}
        facts = video.get("fc_facts") or []
        first_fact = min(facts, key=lambda f: f.get("id") or 0, default={})
        stuff = {d.get("cd_column"): d.get("cd_value") for d in file.get("data") or [] if d.get("cd_column")}

        formats = []
        # HLS master playlist: every quality (240p-1080p) in H.264, H.265 and AV1, audio muxed in.
        for uri in (file.get("hls_resources") or file.get("hls_resources_tmp") or {}).values():
            if uri:
                for f in self._extract_m3u8_formats(
                    self._CDN + uri.lstrip("/"), video_id, "mp4", m3u8_id="hls", headers=headers, fatal=False
                ):
                    codec = self._CODECS.get((f.get("vcodec") or "")[:4], "h264")
                    if f.get("height"):
                        f["format_id"] = f"hls-{codec}-{f['height']}p"
                    formats.append(f)
        # Progressive MP4 files (a small preview quality and the player's fallback file).
        progressive = {**(file.get("resources") or {}), "fallback": file.get("fallback")}
        for key, uri in progressive.items():
            height = int_or_none(self._search_regex(r"/(\d+)p/", uri or "", "height", default=None))
            if uri and height:
                formats.append(
                    {
                        "format_id": f"mp4-{height}p" + ("-fallback" if key == "fallback" else ""),
                        "url": self._CDN + uri.lstrip("/"),
                        "ext": "mp4",
                        "height": height,
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                    }
                )
        if not formats:
            self.raise_no_formats("No playable streams found for this video", expected=True)
        for f in formats:
            f.setdefault("http_headers", {}).update(headers)

        thumbs = first_fact.get("fc_thumbs") or []
        thumbnails = [{"url": self._THUMBS.format(video_id, t)} for t in thumbs[len(thumbs) // 2 :][:1]]

        return {
            "id": video_id,
            "display_id": str(first_fact["id"]) if first_fact.get("id") else None,
            "title": stuff.get("sf_name") or f"pat.com video {video_id}",
            "description": stuff.get("sf_story"),
            "timestamp": unified_timestamp(first_fact.get("fc_created")),
            "duration": int_or_none(file.get("fl_duration")),
            "width": int_or_none(file.get("fl_width")),
            "height": int_or_none(file.get("fl_height")),
            "tags": traverse_obj(video, ("tags", ..., "tg_name")),
            "thumbnails": thumbnails,
            "formats": formats,
            # Best resolution first; at equal resolution prefer H.264, which every Telegram client plays.
            "_format_sort_fields": ("res", "vcodec:h264"),
            "age_limit": 18,
        }
