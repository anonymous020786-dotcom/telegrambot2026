"""yt-dlp plugin extractor for ShareChat (sharechat.com) video posts.

The post page embeds the main video as JSON-LD (VideoObject.contentUrl, the clean H.264 file). The same content
folder on the CDN also holds an H.265 encode and a watermarked "attributed" copy; those URLs appear in the page too.
"""

import re
from typing import ClassVar

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, parse_duration, unified_timestamp, urljoin


class ShareChatIE(InfoExtractor):
    IE_NAME = "sharechat"
    _VALID_URL = r"https?://(?:www\.)?sharechat\.com/(?:[^/?#]+/)*(?:post|video)/(?P<id>[A-Za-z0-9]+)"
    # variant folder on the CDN -> (format label, quality rank, description)
    _VARIANTS: ClassVar[dict[str, tuple[str, int, str]]] = {
        "compressed": ("h264", 10, "original"),
        "slow_h265_v3": ("h265", 5, "original"),
        "h265_v3": ("h265", 5, "original"),
        "attributed": ("h264-watermarked", -10, "with ShareChat watermark"),
    }
    _TESTS: ClassVar[list[dict]] = [
        {
            "url": "https://sharechat.com/post/m3QqPD0",
            "info_dict": {
                "id": "m3QqPD0",
                "ext": "mp4",
                "title": str,
                "uploader": str,
                "duration": 5,
            },
            "params": {"skip_download": True},
        }
    ]

    def _real_extract(self, url):
        post_id = self._match_id(url)
        webpage = self._download_webpage(url, post_id)

        video = None
        for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', webpage, re.S):
            data = self._parse_json(blob, post_id, fatal=False) or {}
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict) and item.get("@type") == "VideoObject" and item.get("contentUrl"):
                    video = item
                    break
            if video:
                break
        if not video:
            raise ExtractorError("This ShareChat post has no video (image posts: use /images)", expected=True)

        main_url = video["contentUrl"]
        folder = self._search_regex(r"(/contents/sc_\d+/)", main_url, "content folder", default=None)
        urls = {main_url.split("?")[0]}
        if folder:
            urls.update(re.findall(rf'(https://[^"\s]+{re.escape(folder)}[^"\s?&]+\.mp4)', webpage))

        formats = []
        for media_url in sorted(urls):
            variant = self._search_regex(r"/contents/sc_\d+/([^/]+)/", media_url, "variant", default="compressed")
            label, quality, note = self._VARIANTS.get(variant, (variant, 0, variant))
            formats.append(
                {
                    "format_id": label,
                    "format_note": note,
                    "url": media_url + "?tenant=sc",
                    "ext": "mp4",
                    "vcodec": "hevc" if "265" in variant else "avc1",
                    "acodec": "aac",
                    "quality": quality,
                }
            )

        author = video.get("author") or {}
        name = (video.get("name") or "").strip()
        return {
            "id": post_id,
            "title": name or f"ShareChat {post_id}",
            "description": video.get("description"),
            "uploader": (author.get("name") or "").strip() or None,
            "uploader_url": urljoin("https://sharechat.com", author.get("url")),
            "thumbnail": video.get("thumbnailUrl"),
            "timestamp": unified_timestamp(video.get("uploadDate")),
            "duration": parse_duration(video.get("duration")),
            "formats": formats,
            # Clean original first, then H.265, then the watermarked copy.
            "_format_sort_fields": ("quality",),
        }
