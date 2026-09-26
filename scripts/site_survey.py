"""Survey which popular sites this server can read right now, by category.

Uses each yt-dlp extractor's own sample video (like /sitecheck) and reports extractor, formats and the best
quality found. Run inside the bot container:  python -m scripts.site_survey [--json out.json] [category ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import yt_dlp

from bot.services.downloader import load_all_plugins
from bot.services.sitecheck import sample_url

# Category → {label: yt-dlp extractor key}
SITES: dict[str, dict[str, str]] = {
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


def classes_by_key() -> dict[str, type]:
    load_all_plugins()
    return {ie.ie_key(): ie for ie in yt_dlp.extractor.gen_extractor_classes()}


def probe(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 30, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    info = info or {}
    if info.get("entries"):
        info = next((e for e in info["entries"] if e), info)
    fmts = info.get("formats") or []
    heights = [f.get("height") for f in fmts if f.get("height")]
    return {
        "title": (info.get("title") or "")[:60],
        "formats": len(fmts),
        "best_height": max(heights) if heights else None,
        "audio_only": bool(fmts) and not heights,
    }


async def run(categories: list[str], timeout: float, concurrency: int) -> list[dict]:
    classes = classes_by_key()
    sem = asyncio.Semaphore(concurrency)

    async def one(cat: str, label: str, key: str) -> dict:
        row = {"category": cat, "site": label, "extractor": key}
        ie = classes.get(key)
        if ie is None:
            return {**row, "status": "no_extractor"}
        if not ie.working():
            return {**row, "status": "marked_broken"}
        url = sample_url(key)
        if not url:
            return {**row, "status": "no_sample"}
        async with sem:
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(asyncio.to_thread(probe, url), timeout)
                status = "ok" if res["formats"] or res["title"] else "empty"
                return {**row, "status": status, "url": url, **res, "secs": round(time.monotonic() - t0, 1)}
            except TimeoutError:
                return {**row, "status": "timeout", "url": url}
            except Exception as exc:  # noqa: BLE001 - record every failure
                msg = str(exc).replace("ERROR: ", "").split("\n")[0][:160]
                return {**row, "status": "error", "url": url, "error": msg}

    jobs = [one(c, label, key) for c in categories for label, key in SITES[c].items()]
    return list(await asyncio.gather(*jobs))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("categories", nargs="*", default=list(SITES))
    ap.add_argument("--json")
    ap.add_argument("--timeout", type=float, default=75)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    rows = asyncio.run(run(args.categories, args.timeout, args.concurrency))
    for r in rows:
        extra = (
            f"{r.get('formats')} fmts, best {r.get('best_height') or 'audio'}"
            if r["status"] == "ok"
            else r.get("error", "")
        )
        print(f"{r['status']:<13} {r['category']:<11} {r['site']:<20} {extra}")
    total = len(rows)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"\n{ok}/{total} OK")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
