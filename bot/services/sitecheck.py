"""Live health check: can this server read media info from the major platforms right now?

Default targets are the sample URLs yt-dlp's own extractors ship with, so they track upstream changes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import yt_dlp

from .downloader import Downloader, friendly_error

# Platform label → yt-dlp extractor key whose first real test URL is used.
PLATFORMS: dict[str, str] = {
    "YouTube": "Youtube",
    "Instagram": "Instagram",
    "TikTok": "TikTok",
    "X / Twitter": "Twitter",
    "Facebook": "Facebook",
    "Reddit": "Reddit",
    "Vimeo": "Vimeo",
    "Dailymotion": "Dailymotion",
    "SoundCloud": "Soundcloud",
    "Twitch": "TwitchVod",
    "Pinterest": "Pinterest",
    "Bilibili": "BiliBili",
    "Tumblr": "Tumblr",
    "Bluesky": "Bluesky",
}


# Category → {label: yt-dlp extractor key}
CATEGORIES: dict[str, dict[str, str]] = {
    "social": {
        "YouTube": "Youtube",
        "YouTube Shorts": "YoutubeTab",
        "Instagram": "Instagram",
        "Instagram Reels": "InstagramIOS",
        "TikTok": "TikTok",
        "Facebook": "Facebook",
        "Facebook Reels": "FacebookReel",
        "X / Twitter": "Twitter",
        "Reddit": "Reddit",
        "Pinterest": "Pinterest",
        "Tumblr": "Tumblr",
        "Bluesky": "Bluesky",
        "LinkedIn": "LinkedIn",
        "Snapchat Spotlight": "SnapchatSpotlight",
        "VK": "VK",
        "VK Video": "VKPlay",
        "Weibo": "Weibo",
        "Douyin": "Douyin",
        "Kuaishou": "Kuaishou",
        "Likee": "Likee",
        "Triller": "Triller",
        "Imgur": "Imgur",
        "9GAG": "NineGag",
        "Coub": "Coub",
        "Telegram": "TelegramEmbed",
        "Mastodon": "Mastodon",
        "Gab": "Gab",
        "Truth Social": "Truth",
        "Threads": "Threads",
        "Lemon8": "Lemon8",
    },
    "video": {
        "Vimeo": "Vimeo",
        "Dailymotion": "Dailymotion",
        "Twitch VOD": "TwitchVod",
        "Twitch Clips": "TwitchClips",
        "Kick": "KickVOD",
        "Kick Clips": "KickClip",
        "Rumble": "Rumble",
        "Odysee": "LBRY",
        "BitChute": "BitChute",
        "Streamable": "Streamable",
        "Bilibili": "BiliBili",
        "NicoNico": "Niconico",
        "Youku": "Youku",
        "iQiyi": "Iqiyi",
        "AcFun": "AcFunVideo",
        "Rutube": "Rutube",
        "PeerTube": "PeerTube",
        "Loom": "Loom",
        "Wistia": "Wistia",
        "Vidyard": "Vidyard",
        "JW Player": "JWPlatform",
        "Brightcove": "BrightcoveNew",
        "Kaltura": "Kaltura",
        "Medal": "MedalTV",
        "Nebula": "Nebula",
        "Floatplane": "Floatplane",
        "Patreon": "Patreon",
        "Substack": "Substack",
        "Archive.org": "ArchiveOrg",
        "Giphy": "Giphy",
        "Tenor": "Tenor",
        "Vbox7": "Vbox7",
    },
    "audio": {
        "SoundCloud": "Soundcloud",
        "Bandcamp": "Bandcamp",
        "Mixcloud": "Mixcloud",
        "Audiomack": "Audiomack",
        "Audius": "Audius",
        "Apple Podcasts": "ApplePodcasts",
        "Castbox": "CastBox",
        "Podbean": "PodbeanEpisode",
        "Spreaker": "Spreaker",
        "iHeartRadio": "IHeartRadio",
        "Jamendo": "Jamendo",
        "Deezer": "DeezerPlaylist",
        "Anghami": "Anghami",
        "Boomplay": "Boomplay",
        "Gaana": "Gaana",
        "JioSaavn": "JioSaavnSong",
        "Hearthis.at": "HearThisAt",
        "Vocaroo": "Vocaroo",
        "Clyp": "Clyp",
        "Radio Garden": "RadioGarden",
    },
    "news_tv": {
        "BBC": "BBC",
        "CNN": "CNN",
        "CBS News": "CBSNews",
        "NBC News": "NBCNews",
        "ABC News": "AbcNews",
        "Fox News": "FoxNews",
        "Reuters": "Reuters",
        "Bloomberg": "Bloomberg",
        "CNBC": "CNBCVideo",
        "Al Jazeera": "AlJazeera",
        "DW": "DW",
        "France 24": "France24",
        "Washington Post": "WashingtonPost",
        "NYTimes": "NYTimes",
        "The Guardian": "TheGuardianPodcast",
        "TED": "TedTalk",
        "Arte": "ArteTV",
        "ZDF": "ZDF",
        "ARD": "ARDBetaMediathek",
        "NHK": "NhkVod",
        "RaiPlay": "RaiPlay",
        "RTVE": "RTVEALaCarta",
        "NPO": "NPO",
        "SVT Play": "SVTPlay",
        "NRK": "NRK",
        "CBC": "CBCPlayer",
        "SBS": "SBS",
        "ABC iview": "ABCIView",
        "PBS": "PBS",
        "C-SPAN": "CSpan",
        "Pluto TV": "PlutoTV",
        "Tubi": "TubiTv",
        "Viki": "Viki",
        "ZEE5": "Zee5",
        "SonyLIV": "SonyLIV",
        "MX Player": "Mxplayer",
        "JioCinema": "JioCinema",
        "Globo": "Globo",
        "TV5Monde": "TV5MondePlus",
        "Euronews": "Euronews",
        "Sky News": "SkyNews",
        "India Today": "IndiaToday",
        "NDTV": "NDTV",
        "Times of India": "TimesOfIndia",
    },
    "sports": {
        "NFL": "NFL",
        "NBA": "NBA",
        "MLB": "MLB",
        "NHL": "NHL",
        "ESPN": "ESPN",
        "FIFA": "Fifa",
        "Olympics": "OlympicsReplay",
        "Eurosport": "Eurosport",
        "Red Bull TV": "RedBullTV",
        "Formula 1": "Formula1",
        "WWE": "WWE",
        "UFC": "UFCTV",
    },
    "education": {
        "Khan Academy": "KhanAcademy",
        "Coursera": "Coursera",
        "MIT OCW": "OCWMIT",
        "Udemy": "Udemy",
        "LinkedIn Learning": "LinkedInLearning",
        "Frontend Masters": "FrontendMasters",
        "egghead": "EggheadLesson",
        "Craftsy": "Craftsy",
    },
    "files_cloud": {
        "Google Drive": "GoogleDrive",
        "Dropbox": "Dropbox",
        "OneDrive": "OneDrive",
        "Box": "Box",
        "pCloud": "PCloud",
        "Mega": "Mega",
        "MediaFire": "MediaFire",
        "Gofile": "Gofile",
        "Pixeldrain": "Pixeldrain",
    },
    "adult": {
        "xHamster": "XHamster",
        "PornHub": "PornHub",
        "XVideos": "XVideos",
        "XNXX": "XNXX",
        "YouPorn": "YouPorn",
        "RedTube": "RedTube",
        "SpankBang": "SpankBang",
        "Eporner": "Eporner",
        "Beeg": "Beeg",
        "pat.com": "Pat",
        "TNAFlix": "TNAFlix",
        "ThisVid": "ThisVid",
        "RedGifs": "RedGifs",
        "Chaturbate": "Chaturbate",
        "Stripchat": "Stripchat",
        "Motherless": "Motherless",
        "Tube8": "Tube8",
        "YouJizz": "YouJizz",
        "SunPorno": "SunPorno",
        "4Tube": "FourTube",
        "PornerBros": "PornerBros",
        "Fux": "Fux",
        "PornTube": "PornTube",
        "DrTuber": "DrTuber",
        "Nuvid": "Nuvid",
        "Txxx": "Txxx",
        "PornFlip": "PornFlip",
        "ManyVids": "ManyVids",
        "EroProfile": "EroProfile",
        "HellPorno": "HellPorno",
        "Porn.com": "PornCom",
        "PornHD": "PornHd",
        "PornoXO": "PornoXO",
        "Pornotube": "Pornotube",
        "Tnaflix Empflix": "EMPFlix",
        "CamSoda": "Camsoda",
        "Xstream": "XStream",
        "Lovehomeporn": "LoveHomePorn",
        "Slutload": "Slutload",
        "Xtube": "XTube",
    },
}
# Our own plugins and Indian platforms that the catalogue above doesn't name.
CATEGORIES["social"].update({"ShareChat": "ShareChat", "Moj": "Moj"})
CATEGORIES["news_tv"].update({"Times of India": "TimesInternet"})


@dataclass
class CheckResult:
    name: str
    url: str
    ok: bool
    detail: str
    seconds: float


def sample_url(extractor_key: str) -> str | None:
    """First non-'only_matching' test URL of a yt-dlp extractor."""
    for ie in yt_dlp.extractor.gen_extractor_classes():
        if ie.ie_key() != extractor_key:
            continue
        for test in ie.get_testcases(include_onlymatching=False):
            if test.get("url", "").startswith("http"):
                return test["url"]
    return None


def default_targets() -> list[tuple[str, str]]:
    return [(name, url) for name, key in PLATFORMS.items() if (url := sample_url(key))]


def category_targets(names: list[str]) -> list[tuple[str, str]]:
    """(label, sample URL) for every site in the named categories ("all" = every category)."""
    wanted = list(CATEGORIES) if "all" in names else [n for n in names if n in CATEGORIES]
    return [(label, url) for cat in wanted for label, key in CATEGORIES[cat].items() if (url := sample_url(key))]


BLOCKED_HINTS = (
    "failed to connect",
    "connection refused",
    "failed to establish a new connection",
    "failed to resolve",
    "couldn't reach the site",
)


def network_blocked(detail: str) -> bool:
    """True when the failure means this server can't reach the site at all (ISP/firewall block or DNS)."""
    text = detail.lower()
    return any(hint in text for hint in BLOCKED_HINTS)


async def check(
    downloader: Downloader, targets: list[tuple[str, str]], timeout: float = 60, concurrency: int = 4
) -> list[CheckResult]:
    sem = asyncio.Semaphore(concurrency)

    async def one(name: str, url: str) -> CheckResult:
        async with sem:
            t0 = time.monotonic()
            try:
                info = await asyncio.wait_for(downloader.probe(url), timeout)
                detail = info.title if not info.is_playlist else f"{info.title} ({len(info.entries)} items)"
                return CheckResult(name, url, True, detail, time.monotonic() - t0)
            except TimeoutError:
                return CheckResult(name, url, False, "timed out", time.monotonic() - t0)
            except Exception as exc:  # noqa: BLE001 - report every failure kind
                return CheckResult(name, url, False, friendly_error(exc), time.monotonic() - t0)

    return list(await asyncio.gather(*(one(n, u) for n, u in targets)))
