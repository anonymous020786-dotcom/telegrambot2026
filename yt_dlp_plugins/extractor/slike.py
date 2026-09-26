"""yt-dlp plugin extractors for Times Internet video pages (Times of India and sister sites) served by Slike.

The page's JSON-LD points at t.sli.ke/v.<slike id>.mp4, which redirects to a "not available" placeholder when
fetched directly, so the generic extractor downloads a 4-second stub. The player instead reads
tvid.in/api/mediainfo/<id[2:4]>/<id[4:6]>/<id>/<id>.json, which either names a YouTube embed ("yt::<id>") or
lists the native streams (HLS master plus MP4 files per height).
"""

from typing import ClassVar

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError, float_or_none, int_or_none, traverse_obj


class _SlikeBaseIE(InfoExtractor):
    _MEDIAINFO = "https://tvid.in/api/mediainfo/{0}/{1}/{2}/{2}.json?skiptracking=1"

    def _extract_slike(self, slike_id, display_id, referer):
        info = self._download_json(
            self._MEDIAINFO.format(slike_id[2:4], slike_id[4:6], slike_id),
            display_id,
            "Downloading Slike media info",
            headers={"Referer": referer},
        )
        embed = info.get("embed") or ""
        if embed.startswith("yt::"):
            return self.url_result(f"https://www.youtube.com/watch?v={embed[4:]}", "Youtube", embed[4:])

        formats = []
        for flavor in info.get("flavors") or []:
            url = flavor.get("url")
            if not url:
                continue
            if flavor.get("type") == "hls":
                formats.extend(self._extract_m3u8_formats(url, display_id, "mp4", m3u8_id="hls", fatal=False))
            elif flavor.get("type") == "dash":
                formats.extend(self._extract_mpd_formats(url, display_id, mpd_id="dash", fatal=False))
            else:
                height = int_or_none(flavor.get("height"))
                formats.append(
                    {
                        "format_id": f"mp4-{height}p" if height else "mp4",
                        "url": url,
                        "ext": "mp4",
                        "width": int_or_none(flavor.get("width")),
                        "height": height,
                        "tbr": int_or_none(flavor.get("bitrate")),
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                    }
                )
        if not formats:
            raise ExtractorError("This video has no playable stream (it may be geo-blocked or removed)", expected=True)

        return {
            "id": slike_id,
            "display_id": display_id,
            "title": info.get("name") or display_id,
            "thumbnail": info.get("poster") or info.get("image"),
            "duration": float_or_none(info.get("duration")),
            "formats": formats,
        }


class TimesInternetIE(_SlikeBaseIE):
    IE_NAME = "timesinternet"
    _VALID_URL = r"https?://(?:[a-z]+\.)?indiatimes\.com/(?:[^/?#]+/)*videoshow/(?P<id>\d+)\.cms"
    _TESTS: ClassVar[list[dict]] = [
        {
            # native Slike stream (HLS + MP4 up to 1080p)
            "url": "https://timesofindia.indiatimes.com/videos/education/choosing-auckland-to-study-abroad-dos-and-donts-of-student-life-explained/videoshow/126203083.cms",
            "info_dict": {"id": "1xyr5rs9z6", "ext": "mp4", "title": str},
            "params": {"skip_download": True},
        },
        {
            # the page's player is a YouTube embed
            "url": "https://timesofindia.indiatimes.com/videos/aiq/ai-driving-the-next-wave-of-business-transformation/videoshow/133712746.cms",
            "only_matching": True,
        },
    ]

    def _real_extract(self, url):
        display_id = self._match_id(url)
        webpage = self._download_webpage(url, display_id)
        slike_id = self._search_regex(
            (r"t\.sli\.ke/v\.([a-z0-9]{8,12})\.mp4", r'data-slikeid="([a-z0-9]{8,12})"'), webpage, "slike id"
        )
        result = self._extract_slike(slike_id, display_id, url)
        if result.get("_type") == "url":
            return result
        ld = self._search_json_ld(webpage, display_id, default={}, expected_type="VideoObject")
        return {
            **result,
            "title": ld.get("title") or result["title"],
            "description": ld.get("description"),
            "timestamp": ld.get("timestamp"),
            "thumbnail": result.get("thumbnail") or traverse_obj(ld, ("thumbnails", 0, "url")),
        }
