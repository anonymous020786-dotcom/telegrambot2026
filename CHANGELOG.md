# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org/). Pushing a tag
`vX.Y.Z` publishes the Docker image `ghcr.io/anonymous020786-dotcom/telegrambot2026:X.Y.Z` and a GitHub Release
with the matching section below.

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
