# Telegram Media Downloader Bot

[![CI](https://github.com/anonymous020786-dotcom/telegrambot2026/actions/workflows/ci.yml/badge.svg)](https://github.com/anonymous020786-dotcom/telegrambot2026/actions/workflows/ci.yml)

A **private** Telegram bot, written in Python, that downloads **public videos, audio and images from almost any
website**, in any available format or quality. It has **167 commands**, 130 for users and 37 for admins, plus
button menus throughout.

- **Videos and audio from 1,800+ sites** via [yt-dlp](https://github.com/yt-dlp/yt-dlp): every resolution from
  144p to 4K, any single format ID, MP4, MKV or WebM, and MP3/M4A/Opus/FLAC/WAV/AAC/OGG at a bitrate you choose.
  It also handles clips (time ranges), playlists and channels, subtitles, thumbnails and one file per chapter.
- **Images from any web page**: `srcset` (largest version), lazy-loaded images, `<picture>`, CSS backgrounds,
  Open Graph and Twitter cards, JSON-LD and favicons. Hotlink-protected images work too. You can filter by minimum
  width and get the result as albums or a ZIP. [gallery-dl](https://github.com/mikf/gallery-dl) handles 100+
  gallery sites.
- **Interface**: paste a link to get a preview card (thumbnail, title, length, views) with buttons for best quality,
  720p/1080p, MP3/M4A/FLAC, all qualities with estimated sizes, formats, subtitles, GIF and "send as file". There are
  live progress bars with speed, ETA and a Cancel button, paged history, and a settings menu you change by tapping.
- **18 media tools** (ffmpeg/Pillow) that work on files you send: convert, compress (to a target size), trim, speed,
  rotate, resize, GIF, frame capture, volume, reverse, voice message, round video message, media info, and image
  convert/resize/EXIF.
- **Inline mode**: type `@yourbot <link>` or `@yourbot <search words>` in any chat, group or channel and pick
  🎬 Video or 🎵 Audio. Files downloaded before post instantly; others show "⏳ Downloading…" and are replaced by
  the file when it's ready. See `/inline`.
- **Download queue**: parallel workers with a per-user limit and daily quotas, pause and resume, move to front,
  retry, and instant re-sending of files already downloaded (cached by Telegram file ID). The queue survives
  restarts, updates and crashes: waiting and interrupted downloads resume automatically, and pauses are kept.
  Temporary failures (rate limits, server errors, network drops) are retried automatically with backoff, and the
  same download is never queued twice.
- **Library**: history, favorites, search, one-tap re-download, CSV export.
- **Subscriptions**: watch a channel or playlist and get new uploads automatically. You can also **schedule**
  downloads ("in 2h", "18:30", "2026-10-01 08:00", in your time zone).
- **Large files**: files over Telegram's 50 MB bot limit are split into playable parts, sent as expiring download
  links from the bot's own server, uploaded to S3 with presigned links, or sent through a self-hosted Bot API server
  (up to 2 GB).
- **Private by default**: an allowlist plus admins. Strangers can request access (admins get Approve/Deny buttons)
  or redeem invite codes. Admins can ban users, set per-user limits, broadcast, turn on maintenance mode, and
  inspect system, disk, logs and backups.

> It only downloads **publicly accessible** media. It does not bypass DRM, paywalls or logins. Respect copyright and
> each website's terms of service.

## Supported sites and content rules

| Kind of site | Works? | Notes |
| --- | --- | --- |
| **Social media**: YouTube (incl. Shorts), Instagram (reels, posts, stories you can see), TikTok, X/Twitter, Facebook, Reddit, Threads*, Pinterest, Snapchat Spotlight, Tumblr, Bluesky, LinkedIn, Twitch, Kick, VK, Weibo, Bilibili… | ✅ | Dedicated yt-dlp extractors. Photo and carousel posts are fetched with gallery-dl automatically. Sites that require a login even for public posts work once an admin adds their **own** cookies with `/cookies`. |
| **Video and audio platforms**: Vimeo, Dailymotion, SoundCloud, Bandcamp, Rumble, Odysee, archive.org, news sites, direct media links, pages with embedded players | ✅ | 1,800+ sites. Anything else with an embedded video or a direct link goes through the generic extractor. |
| **Adult sites** hosting legal content | ⚙️ Opt-in | **61 sites with dedicated extractors** (xHamster, PornHub, XVideos, XNXX, YouPorn, RedTube, SpankBang, Eporner, Beeg, TNAFlix, ThisVid, RedGifs, Chaturbate, Stripchat…; `/sites adult` lists them all). Other sites usually work through the page-video fallback (below). Off by default: an admin enables it with `/adult optin` (or `ADULT_CONTENT=optin`), then each user confirms they are 18+ with `/setadult`. Media rated 18+ by the site, and pages that label themselves adult (RTA tag), are refused for everyone else, including the preview thumbnail. |
| **Subscription OTT and streaming** (Netflix, Prime Video, Disney+, Hotstar Premium, Max, Hulu, Apple TV+, Spotify…) | ❌ | These services encrypt their streams with DRM. The bot **does not bypass DRM** and tells the user why. DRM-free clips, trailers and free catch-up TV that yt-dlp supports do work. |

\* Threads is handled by gallery-dl rather than yt-dlp.

**Any other website:** when yt-dlp has no extractor for a site, the bot reads the page itself. It looks for
`<video>`/`<source>` tags, `og:video`, JSON-LD `VideoObject`, links to MP4/WebM/HLS/DASH files, stream URLs
inside the player's scripts, and embedded players that yt-dlp supports. It then downloads the best stream it
finds, sending the page as Referer. This works for most sites that serve an unencrypted stream. It can't work for
DRM-encrypted streams, or for sites that build the stream URL with obfuscated code at play time.

**Integrating a new site without code changes:** an admin sends `/pagedebug <url>` to see what the bot finds on
the page (extractor, adult label, every stream candidate), along with the page's HTML. If the stream is in the HTML
but not recognised, `/siterule add <domain> <regex>` adds a pattern whose first capture group is the stream URL.
Rules are tried before the generic scan; `/siterule test <url>` checks one.

Admins can check live from the server which platforms currently work with **`/sitecheck`**. It tests the sample
links yt-dlp maintains for each platform. They can also block any domain with `/blocksite`. When a site changes,
`/updateytdlp` followed by `/restart` usually fixes it. YouTube needs a JavaScript runtime and hardened sites need
browser impersonation; both are installed automatically (`yt-dlp[default,curl-cffi,deno]`).

## Quick start (Docker)

```bash
git clone https://github.com/anonymous020786-dotcom/telegrambot2026.git
cd telegrambot2026
cp .env.example .env        # set BOT_TOKEN (from @BotFather) and ADMIN_IDS (your Telegram ID)
docker compose up -d
docker compose logs -f bot
```

Open your bot in Telegram and send `/start`. Don't know your ID? Start the bot with any `ADMIN_IDS`, send `/id`,
put the number in `.env` and restart.

**Other ways to run it:**
- **Prebuilt Docker image** (amd64 and arm64, published on every release): set
  `BOT_IMAGE=ghcr.io/anonymous020786-dotcom/telegrambot2026:latest` in `.env`, then
  `docker compose pull bot && docker compose up -d`
- **Your own Linux server with systemd:** `sudo bash deploy/install.sh`
- **AWS (EC2 + S3 + Secrets Manager) in one command:** `BOT_TOKEN=... ADMIN_IDS=... ./deploy/aws/deploy.sh`
- **Locally without Docker:** `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python -m bot`
  (needs `ffmpeg` installed)

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for step-by-step instructions, 2 GB uploads, download links, S3, updates,
security and releasing. What changed in each version is in **[CHANGELOG.md](CHANGELOG.md)**.

## Using the bot

| You send | You get |
| --- | --- |
| Any link | A preview card with buttons for quality, audio, formats, subtitles and more |
| `/mp3 URL 320` | 320 kbps MP3 with cover art and tags |
| `/fhd URL`, `/hd URL`, `/4k URL`, `/q 480 URL` | That resolution |
| `/clip URL 1:05 2:30` | Just that part |
| `/playlist URL 1-10 mp3` | Items 1–10 of a playlist as MP3 |
| `/images URL 600 zip` | Every image at least 600 px wide on the page, as a ZIP |
| A video with the caption `/compress 20MB` | The video re-encoded to about 20 MB |
| `/watch CHANNEL_URL` | New uploads sent to you automatically |
| `/schedule 18:30 URL` | The download starts at 18:30 your time |

The full list of all 167 commands is in **[COMMANDS.md](COMMANDS.md)**, which is generated from the code.

## Configuration

All settings are environment variables (see [.env.example](.env.example)). The most important ones:

| Variable | Default | Meaning |
| --- | --- | --- |
| `BOT_TOKEN` | – | Token from @BotFather (required) |
| `ADMIN_IDS` | – | Owner Telegram IDs, comma-separated |
| `ALLOWED_USER_IDS` | – | Extra users allowed without an invite |
| `PUBLIC_MODE` | `false` | Let anyone use the bot |
| `DAILY_LIMIT` | `200` | Downloads per user per day (`0` = unlimited) |
| `MAX_CONCURRENT_JOBS` / `PER_USER_CONCURRENT_JOBS` | `3` / `2` | Parallel downloads |
| `ALLOW_PRIVATE_URLS` | `false` | SSRF protection: links that resolve to localhost, private networks (10.x, 192.168.x…), link-local or cloud-metadata addresses are refused, including via redirects. Set `true` only to download from your own LAN |
| `TRANSIENT_RETRIES` / `RETRY_DELAY_SECONDS` | `2` / `15` | Automatic retries after HTTP 429/5xx or network errors; each wait is 4× the previous (15 s, 60 s) |
| `MAX_DOWNLOAD_MB` | `4000` | Refuse larger source files |
| `BOT_API_BASE_URL` | – | Self-hosted Bot API server (2 GB uploads) |
| `LINK_SERVER_ENABLED` / `LINK_BASE_URL` | `false` / – | Expiring download links for big files |
| `S3_BUCKET` / `S3_REGION` | – | S3 delivery for big files |
| `PROXY` | – | HTTP/SOCKS proxy for downloads |
| `COOKIES_FILE` | – | Your own browser cookies (Netscape format), for media your account can see (or upload with `/cookies`) |
| `ADULT_CONTENT` | `off` | `off`, or `optin`, where adults confirm with `/setadult` |
| `BLOCKED_DOMAINS` | – | Never download from these domains |

## Architecture

```
bot/
  app.py            wiring: handlers, callbacks, background jobs, startup/shutdown
  registry.py       single source of truth for every command (help, menus, docs, tests)
  config.py  db.py  settings from env · SQLite (users, history, cache, quotas, invites, watches, schedules)
  handlers/         general · download/audio/details/search · images · tools · library · settings · watch · utilities · admin
  services/
    downloader.py   yt-dlp presets, probing, progress, cancellation, search
    images.py       HTML image extraction + gallery-dl
    media.py        ffmpeg/Pillow tools, playable splitting
    jobs.py         priority queue, workers, quotas, progress messages, caching
    delivery.py     Telegram uploads + split / link / S3 fallbacks
    linkserver.py   signed expiring download links (aiohttp)
    s3.py           S3 multipart upload + presigned URLs
    watcher.py      subscriptions and schedules
  ui/cards.py       preview cards and keyboards
deploy/             systemd unit, Ubuntu installer, AWS CloudFormation + scripts
```

## Development

```bash
pip install -r requirements-dev.txt     # plus ffmpeg on your PATH
python -m pytest -q                     # 128 tests
ruff check . && ruff format --check .
python -m scripts.gen_commands          # regenerate COMMANDS.md after changing commands
python -m scripts.gen_commands --botfather   # command list to paste into @BotFather
```

The tests need no internet or Telegram account. They cover:
- **Downloads:** real yt-dlp downloads (video, MP3, clips, cancellation) from a local web server.
- **Media tools:** every ffmpeg tool, run on a generated video.
- **Images:** image scraping with a hotlink-protected image.
- **Queue and handlers:** the job queue delivering through a fake Telegram bot, and every access rule.
- **Full bot:** a **boot test** that starts the real bot process against a fake Telegram API and checks its replies.

## License

GPL-3.0. See [LICENSE](LICENSE).
