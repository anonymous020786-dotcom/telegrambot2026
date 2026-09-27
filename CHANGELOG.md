# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/). Pushing a tag
`vX.Y.Z` publishes the Docker image `ghcr.io/anonymous020786-dotcom/telegrambot2026:X.Y.Z` and a GitHub Release
with the matching section below.

## [Unreleased]

### Added
- **pat.com support** through a new yt-dlp plugin (`yt_dlp_plugins/extractor/pat.py`): every quality from 240p to
  1080p in H.264, H.265 and AV1, plus the direct MP4 files, with H.264 preferred at each resolution. It is rated
  18+, so it follows the adult-content setting like other adult sites.
- **ShareChat and Moj support** (`yt_dlp_plugins/extractor/sharechat.py`): video posts with the clean original,
  the H.264/H.265 re-encodes and the watermarked copy as separate formats, plus views, likes and comments.
- **Times of India and other `indiatimes.com` video pages** (`yt_dlp_plugins/extractor/slike.py`). The generic
  extractor downloaded a 4-second "not available" stub; now native videos come with every HLS quality and the
  MP4 files (up to 1080p), and pages whose player is a YouTube embed hand over to YouTube.
- **Script-built pages (React/Vue/Angular SPAs):** when yt-dlp and the page scan find nothing, the bot opens the
  page in headless Chromium, presses play, and downloads the HLS/DASH/Smooth Streaming manifest or video file the
  player requests (the main player's video first, not the feed previews). `BROWSER_FALLBACK`,
  `BROWSER_TIMEOUT_SECONDS`; build with `INSTALL_BROWSER=false` to leave Chromium out of the image.
- **Codec preference:** `/setcodec <auto|h264|h265|av1|vp9>` and a settings button. The chosen resolution stays
  first; at that resolution the chosen codec wins (H.264 plays on every Telegram client).
- **MOV output** (`/setformat mov`), and lossless rewrapping: when MP4 was asked for but the download arrived as MKV,
  FLV or MPEG-TS (HLS with unknown audio codecs, RTMP, live streams), the tracks are copied into MP4 so Telegram
  plays it inline. If the codecs don't fit, the original file is sent.
- **RTMP** (`rtmp://`) downloads: `rtmpdump` is now in the image.
- Preview cards for direct video file links show the real resolution, codecs, frame rate and duration (ffprobe
  reads the file header).
- **WebRTC live-stream recording.** WHEP playback links (the open WebRTC viewing standard used by Cloudflare
  Stream, Dolby/Millicast, MediaMTX, Janus and others) are recorded directly; pages whose live player uses WebRTC
  are recorded from the player by the headless browser. New `/record <url> [seconds|1:30] [token=…]` (default
  `WEBRTC_RECORD_SECONDS`, up to `WEBRTC_MAX_RECORD_SECONDS`), a 🔴 progress bar in seconds, Cancel support, and
  MP4 output at the stream's own resolution. Recordings are never served from the cache.
- **`/sitecheck <category>`**: admins test a whole category from Telegram (`social`, `video`, `audio`, `news_tv`,
  `sports`, `education`, `files_cloud`, `adult`, or `all` ~200 sites). Long reports list problems first, span
  several messages, and mark sites this server can't reach at all (🚫, fix with `PROXY`) apart from broken ones.
- `scripts/format_check.py` downloads public test streams (HLS with TS and fMP4/CMAF chunks, DASH, Smooth
  Streaming, H.264/H.265/AV1/VP9 files, MKV/WebM/MOV) through the bot's download code.
- `scripts/link_check.py URL…` checks real links the way the bot handles them (yt-dlp, the page scan, a browser).
- The bot loads yt-dlp plugins at startup, so extractors in `yt_dlp_plugins/` count as supported sites.
- `scripts/site_survey.py` checks about 200 popular sites by category using each extractor's sample video
  (`python -m scripts.site_survey [category …]` inside the container).

## [1.1.0] - 2026-09-25

### Security
- **SSRF protection.** Links that resolve to localhost, private networks, link-local, carrier-grade NAT or cloud
  metadata addresses are refused, including through redirects and numeric tricks such as `http://2130706433/`.
  Previously `/pagedebug`, `/headers`, `/unshorten` and `/mime` could return responses from the server's own
  network. Self-hosters can allow their LAN with `ALLOW_PRIVATE_URLS=true`.
- Cookie files are written atomically with `0600` permissions, and each download works on a private copy, so the
  admin's cookie file is never rewritten.

### Added
- **Inline mode:** `@bot <link>` or `@bot <search words>` in any chat. Cached files post instantly; others show a
  placeholder that is replaced by the video or audio. New `/inline` command (167 commands in total).
- **Restart-safe queue:** waiting and interrupted downloads survive `/restart`, `/updateytdlp`, redeploys and crashes
  and resume automatically (same job ID, so partial downloads continue). Pauses are kept too.
- **Automatic retries** for rate limits (HTTP 429), server errors and network drops, with backoff
  (`TRANSIENT_RETRIES`, `RETRY_DELAY_SECONDS`).
- **Duplicate protection:** the same download is never queued twice; users see where the existing job is.
- **Release pipeline:** tagged releases publish a multi-architecture Docker image (amd64 + arm64) to GitHub
  Container Registry and a GitHub Release.
- A test that runs every command end to end through the real access guard.

### Fixed
- `/gallery` now falls back to scraping the page when gallery-dl doesn't support the site.
- `/thumb` and `/subs` no longer report "the file is larger than the maximum size" when a video simply has no
  thumbnail or subtitles.
- Uploaded cookie files without the Netscape header (or with a UTF-8 BOM) no longer break every download.
- Concurrent downloads could read a half-written cookie file and fail.

## [1.0.0] - 2026-09-25

### Added
- Private Telegram bot that downloads public videos, audio and images from 1,800+ sites (yt-dlp, gallery-dl, a
  universal page-video fallback and admin-defined site rules), with 150+ commands and button menus.
- Every format and quality, clips, playlists, subtitles, thumbnails, chapters, 18 media tools, image scraping,
  watches and schedules, history and favorites, per-user settings, quotas and an admin panel.
- Content policy: DRM services refused, admin domain blocklist, opt-in 18+ mode, user-supplied cookies.
- Large files: split parts, expiring download links, S3 presigned links, or a local Bot API server (2 GB).
- Deployment: Docker Compose, a systemd installer for your own server, and an AWS CloudFormation stack; CI for lint,
  tests, CloudFormation, shell scripts and the Docker image.

[1.1.0]: https://github.com/anonymous020786-dotcom/telegrambot2026/compare/d4d22a8...v1.1.0
[1.0.0]: https://github.com/anonymous020786-dotcom/telegrambot2026/commit/d4d22a8
