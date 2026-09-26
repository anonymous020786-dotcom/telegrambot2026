"""yt-dlp plugin extractors for ShareChat (sharechat.com) and Moj (mojapp.in), both run by Mohalla Tech.

The page embeds the main video as JSON-LD (VideoObject.contentUrl). The same content folder on the CDN
(/contents/sc_<n>/ or /contents/moj_<n>/) holds several encodes: the clean original ("compressed"), H.264/H.265
re-encodes and a watermarked "attributed" copy. Their URLs appear in the page, so every one becomes a format.
"""

import re
from typing import ClassVar

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, int_or_none, parse_duration, traverse_obj, unified_timestamp, urljoin


class _MohallaBaseIE(InfoExtractor):
    _SITE = ""
    _HOME = ""
    _TENANT = ""
    # variant folder on the CDN -> (format label, quality rank, note)
    _VARIANTS: ClassVar[dict[str, tuple[str, int, str]]] = {
        "compressed": ("h264", 10, "original"),
        "slow_h264": ("h264-hq", 8, "re-encode"),
        "slow_h265_v3": ("h265", 5, "re-encode"),
        "h265_v3": ("h265", 5, "re-encode"),
        "attributed": ("h264-watermarked", -10, "with watermark"),
    }

    @staticmethod
    def _stat(video, action):
        for stat in video.get("interactionStatistic") or []:
            if action in str(traverse_obj(stat, ("interactionType", "@type")) or ""):
                return int_or_none(stat.get("userInteractionCount"))
        return None

    def _real_extract(self, url):
        video_id = self._match_id(url)
        webpage = self._download_webpage(url, video_id)

        video = None
        for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', webpage, re.S):
            data = self._parse_json(blob, video_id, fatal=False) or {}
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict) and item.get("@type") == "VideoObject" and item.get("contentUrl"):
                    video = item
                    break
            if video:
                break
        if not video:
            raise ExtractorError(f"This {self._SITE} post has no video (image posts: use /images)", expected=True)

        main_url = video["contentUrl"].replace("&amp;", "&")
        folder = self._search_regex(r"(/contents/(?:sc|moj)_\d+/)", main_url, "content folder", default=None)
        urls = {main_url.split("?")[0]}
        if folder:
            urls.update(re.findall(rf'(https://[^"\s]+{re.escape(folder)}[^"\s?&]+\.mp4)', webpage))

        formats = []
        for media_url in sorted(urls):
            variant = self._search_regex(
                r"/contents/(?:sc|moj)_\d+/([^/]+)/", media_url, "variant", default="compressed"
            )
            label, quality, note = self._VARIANTS.get(variant, (variant, 0, variant))
            formats.append(
                {
                    "format_id": label,
                    "format_note": note,
                    "url": f"{media_url}?tenant={self._TENANT}",
                    "ext": "mp4",
                    "vcodec": "hevc" if "265" in variant else "avc1",
                    "acodec": "aac",
                    "quality": quality,
                }
            )

        person = video.get("author") or video.get("creator") or {}
        thumbnail = video.get("thumbnailUrl")
        if isinstance(thumbnail, list):
            thumbnail = next((t for t in thumbnail if t), None)
        name = (video.get("name") or "").strip()
        description = video.get("description") or ""
        return {
            "id": video_id,
            "title": name if name and name != person.get("name") else (description.split("\n")[0] or name),
            "description": description or None,
            "uploader": (person.get("name") or "").strip() or None,
            "uploader_url": urljoin(self._HOME, person.get("url")),
            "thumbnail": thumbnail,
            "timestamp": unified_timestamp(video.get("uploadDate")),
            "duration": parse_duration(video.get("duration")),
            "view_count": self._stat(video, "Watch"),
            "like_count": self._stat(video, "Like"),
            "comment_count": int_or_none(video.get("commentCount")),
            "formats": formats,
            # Clean original first, then re-encodes, then the watermarked copy.
            "_format_sort_fields": ("quality",),
        }


class ShareChatIE(_MohallaBaseIE):
    IE_NAME = "sharechat"
    _VALID_URL = r"https?://(?:www\.)?sharechat\.com/(?:[^/?#]+/)*(?:post|video)/(?P<id>[A-Za-z0-9]+)"
    _SITE, _HOME, _TENANT = "ShareChat", "https://sharechat.com", "sc"
    _TESTS: ClassVar[list[dict]] = [
        {
            "url": "https://sharechat.com/post/m3QqPD0",
            "info_dict": {"id": "m3QqPD0", "ext": "mp4", "title": str, "uploader": str, "duration": 5},
            "params": {"skip_download": True},
        }
    ]


class MojIE(_MohallaBaseIE):
    IE_NAME = "moj"
    _VALID_URL = r"https?://(?:www\.)?mojapp\.in/(?:@[^/?#]+/)?video/(?P<id>\d+)"
    _SITE, _HOME, _TENANT = "Moj", "https://mojapp.in", "moj"
    _TESTS: ClassVar[list[dict]] = [
        {
            "url": "https://mojapp.in/@moj/video/3098969342",
            "info_dict": {"id": "3098969342", "ext": "mp4", "title": str, "uploader": "moj", "duration": 25},
            "params": {"skip_download": True},
        }
    ]
