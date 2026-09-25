# Command reference

167 commands: 130 for users and 37 for admins.
This file is generated from the command registry; run `python -m scripts.gen_commands` after changes.

Tips: paste any link to get a preview with buttons. Media tools work when you reply to a file, or send
the file with the command as its caption.

## 🏠 General (10)

| Command | What it does | Usage |
| --- | --- | --- |
| `/start` | Welcome message and main menu | `/start` |
| `/help` | Browse commands by section, or /help <command> | `/help [command]` |
| `/menu` | Main menu with quick actions | `/menu` |
| `/about` | What this bot can do | `/about` |
| `/ping` | Check the bot is alive and how fast it answers | `/ping` |
| `/version` | Versions of the bot and its engines | `/version` |
| `/commands` | Full list of every command | `/commands` |
| `/privacy` | What data the bot stores | `/privacy` |
| `/feedback` | Send a message to the bot owner | `/feedback <message>` |
| `/id` | Show your Telegram user ID and this chat's ID | `/id` |

## ⬇️ Download (18)

| Command | What it does | Usage |
| --- | --- | --- |
| `/dl` | Smart download: video in your default quality, or images for image links | `/dl <url>` |
| `/video` | Download the video in your default quality | `/video <url>` |
| `/audio` | Download audio in your default audio format | `/audio <url> [bitrate]` |
| `/best` | Best available quality | `/best <url>` |
| `/worst` | Smallest file (lowest quality) | `/worst <url>` |
| `/hd` | 720p video | `/hd <url>` |
| `/fhd` | 1080p Full HD video | `/fhd <url>` |
| `/4k` | 2160p 4K video (when available) | `/4k <url>` |
| `/preview` | Quick low-res 360p copy | `/preview <url>` |
| `/q` | Pick a resolution: 144–2160 | `/q <height> <url>` |
| `/formats` | List every available format and pick one | `/formats <url>` |
| `/getformat` | Download a specific format ID (see /formats) | `/getformat <format_id> <url>` |
| `/clip` | Download only part of a video | `/clip <url> <start> <end>  (e.g. 1:05 2:30)` |
| `/playlist` | Download a playlist or channel (optionally a range) | `/playlist <url> [1-10] [mp3]` |
| `/batch` | Download many links at once (one per line) | `/batch <url1> <url2> … [mp3]` |
| `/direct` | Get direct media URLs (to stream or use elsewhere) | `/direct <url>` |
| `/doc` | Download and send as an uncompressed file | `/doc <url>` |
| `/gif` | Turn a video link into a GIF (first 30 s) | `/gif <url>` |

## 🎵 Audio (8)

| Command | What it does | Usage |
| --- | --- | --- |
| `/mp3` | Audio as MP3 (choose bitrate, e.g. /mp3 URL 320) | `/mp3 <url> [bitrate]` |
| `/m4a` | Audio as M4A/AAC (Apple-friendly) | `/m4a <url> [bitrate]` |
| `/opus` | Audio as Opus (small, high quality) | `/opus <url> [bitrate]` |
| `/flac` | Lossless FLAC audio | `/flac <url>` |
| `/wav` | Uncompressed WAV audio | `/wav <url>` |
| `/aac` | Raw AAC audio | `/aac <url> [bitrate]` |
| `/ogg` | Ogg Vorbis audio | `/ogg <url>` |
| `/voice` | Audio as a Telegram voice message | `/voice <url>` |

## 🧾 Media details (8)

| Command | What it does | Usage |
| --- | --- | --- |
| `/info` | Details: title, uploader, duration, qualities, subtitles… | `/info <url>` |
| `/thumb` | Get the thumbnail / cover image | `/thumb <url>` |
| `/subs` | List subtitle languages, or download one (srt) | `/subs <url> [lang\|all]` |
| `/desc` | Show the full description | `/desc <url>` |
| `/chapters` | List the video's chapters | `/chapters <url>` |
| `/splitchapters` | Download each chapter as a separate file | `/splitchapters <url> [mp3]` |
| `/meta` | All metadata as a JSON file | `/meta <url>` |
| `/size` | Estimated file size for each quality | `/size <url>` |

## 🖼 Images (8)

| Command | What it does | Usage |
| --- | --- | --- |
| `/images` | Download every image on any web page (album or ZIP) | `/images <url> [min-width e.g. 400] [zip]` |
| `/img` | The largest image on a page (or a direct image link) | `/img <url>` |
| `/gallery` | Full-resolution galleries from 100+ sites (gallery-dl) | `/gallery <url> [limit=50]` |
| `/imgzip` | All images on a page as one ZIP file | `/imgzip <url> [min-width]` |
| `/album` | Images on a page as Telegram albums (10 per album) | `/album <url> [min-width]` |
| `/og` | The page's share preview image (Open Graph / Twitter card) | `/og <url>` |
| `/favicon` | The site's icons and favicons | `/favicon <url>` |
| `/imgcount` | Count the images on a page without downloading them | `/imgcount <url>` |

## 🛠 Media tools (18)

| Command | What it does | Usage |
| --- | --- | --- |
| `/convert` | Convert a file: mp4 mkv webm mov gif mp3 m4a flac wav opus ogg jpg png webp | `/convert <format>  (reply to a file)` |
| `/compress` | Shrink a video (quality 18–40, or a target size like 20MB) | `/compress [28 \| 20MB]  (reply to a video)` |
| `/trim` | Cut a video/audio between two times | `/trim <start> [end]  e.g. /trim 0:30 1:15` |
| `/extractaudio` | Extract the soundtrack of a video | `/extractaudio [mp3\|m4a\|flac\|wav\|opus]` |
| `/mute` | Remove the sound from a video | `/mute  (reply to a video)` |
| `/speed` | Change playback speed (0.25–4) | `/speed <factor>  e.g. /speed 1.5` |
| `/rotate` | Rotate a video or image by 90/180/270° | `/rotate <90\|180\|270>` |
| `/resize` | Resize a video or image to a height | `/resize <height>  e.g. /resize 720` |
| `/togif` | Turn a video into a GIF (up to 30 s) | `/togif [fps] [width]` |
| `/frame` | Grab a still image from a video | `/frame [time]  e.g. /frame 1:23` |
| `/volume` | Make audio louder or quieter (e.g. 1.5 or 0.5) | `/volume <factor>` |
| `/reverse` | Play a clip backwards (up to 60 s) | `/reverse` |
| `/tovoice` | Convert audio/video to a Telegram voice message | `/tovoice` |
| `/tonote` | Convert a video to a round video message (≤ 60 s) | `/tonote` |
| `/mediainfo` | Technical details: codecs, resolution, bitrate… | `/mediainfo (reply to a file)` |
| `/imgconvert` | Convert an image: jpg png webp bmp gif tiff | `/imgconvert <format>` |
| `/imgresize` | Resize an image to a height (keeps aspect ratio) | `/imgresize <height>` |
| `/imginfo` | Image size, format and EXIF data | `/imginfo (reply to an image file)` |

## 🔎 Search (4)

| Command | What it does | Usage |
| --- | --- | --- |
| `/yt` | Search YouTube and pick a result | `/yt <search words>` |
| `/sc` | Search SoundCloud and pick a track | `/sc <search words>` |
| `/ytmp3` | Search YouTube and download the top result as MP3 | `/ytmp3 <search words>` |
| `/inline` | Post videos or audio into any chat: type @bot <link or words> | `/inline` |

## 📋 Queue (8)

| Command | What it does | Usage |
| --- | --- | --- |
| `/queue` | Your active and waiting downloads | `/queue` |
| `/status` | Live progress of a job | `/status [job_id]` |
| `/cancel` | Cancel a job (or your newest one) | `/cancel [job_id]` |
| `/cancelall` | Cancel all your jobs | `/cancelall` |
| `/pause` | Hold your waiting jobs (running ones finish) | `/pause` |
| `/resume` | Release your held jobs | `/resume` |
| `/top` | Move a waiting job to the front | `/top <job_id>` |
| `/retry` | Retry a failed download from history | `/retry [history_id]` |

## 📚 History & favorites (10)

| Command | What it does | Usage |
| --- | --- | --- |
| `/history` | Your recent downloads (paged) | `/history [page]` |
| `/favs` | Your favorite downloads | `/favs [page]` |
| `/last` | Your most recent download, with actions | `/last` |
| `/redo` | Download a history item again | `/redo <id>` |
| `/resend` | Instantly resend a previous file (no re-download) | `/resend <id>` |
| `/fav` | Add a history item to favorites | `/fav <id>` |
| `/unfav` | Remove from favorites | `/unfav <id>` |
| `/find` | Search your history by title or link | `/find <words>` |
| `/clearhistory` | Delete your history (favorites kept unless 'all') | `/clearhistory [all]` |
| `/exporthistory` | Download your history as a CSV file | `/exporthistory` |

## ⚙️ Settings (17)

| Command | What it does | Usage |
| --- | --- | --- |
| `/settings` | Open the settings menu | `/settings` |
| `/setquality` | Default video quality | `/setquality <best\|1080\|720\|480\|…\|worst>` |
| `/setformat` | Video container: mp4, mkv or webm | `/setformat <mp4\|mkv\|webm>` |
| `/setaudio` | Default audio format | `/setaudio <mp3\|m4a\|opus\|flac\|wav\|aac\|ogg>` |
| `/setbitrate` | Audio bitrate for MP3/M4A/Opus | `/setbitrate <128\|160\|192\|256\|320>` |
| `/setdelivery` | How to send files over the Telegram limit | `/setdelivery <auto\|telegram\|split\|link\|s3>` |
| `/setcaption` | Caption style for sent files | `/setcaption <full\|minimal\|off>` |
| `/setplaylistlimit` | Default number of playlist items | `/setplaylistlimit <5\|10\|25\|50\|100>` |
| `/setdoc` | Always send files uncompressed as documents | `/setdoc [on\|off]` |
| `/setsubs` | Embed subtitles into downloaded videos | `/setsubs [on\|off]` |
| `/setthumb` | Embed the cover image into files | `/setthumb [on\|off]` |
| `/setmeta` | Embed title/artist tags and chapters | `/setmeta [on\|off]` |
| `/setsublang` | Preferred subtitle language code | `/setsublang <en\|es\|de\|fr\|…\|all>` |
| `/settz` | Your time zone (for schedules and dates) | `/settz <Europe/Berlin\|Asia/Kolkata\|…>` |
| `/setname` | File name template (yt-dlp fields) | `/setname <template>  e.g. %(uploader)s - %(title)s` |
| `/reset` | Restore all settings to defaults | `/reset` |
| `/setadult` | Allow 18+ media for yourself (needs admin permission and age confirmation) | `/setadult [on\|off]` |

## 👤 Account (8)

| Command | What it does | Usage |
| --- | --- | --- |
| `/me` | Your profile, role and settings summary | `/me` |
| `/stats` | Your download statistics | `/stats` |
| `/quota` | How many downloads you have left today | `/quota` |
| `/limits` | Bot limits: file sizes, queue, playlists | `/limits` |
| `/sites` | Search the list of 1,800+ supported sites | `/sites [name]` |
| `/supported` | Check if a link is supported | `/supported <url>` |
| `/redeem` | Activate access with an invite code | `/redeem <code>` |
| `/request` | Ask the admins for access | `/request` |

## ⏰ Subscriptions & schedules (7)

| Command | What it does | Usage |
| --- | --- | --- |
| `/watch` | Auto-download new uploads from a channel or playlist | `/watch <channel or playlist url> [mp3]` |
| `/watches` | Your subscriptions | `/watches` |
| `/unwatch` | Stop a subscription | `/unwatch <id>` |
| `/checknow` | Check your subscriptions for new uploads right now | `/checknow` |
| `/schedule` | Download later: in 30m, +2h, 18:30 or 2026-10-01 08:00 | `/schedule <when> <url> [mp3]` |
| `/schedules` | Your pending scheduled downloads | `/schedules` |
| `/unschedule` | Cancel a scheduled download | `/unschedule <id>` |

## 🧰 Utilities (6)

| Command | What it does | Usage |
| --- | --- | --- |
| `/qr` | Make a QR code from text or a link | `/qr <text>` |
| `/unshorten` | Reveal where a short link really goes | `/unshorten <url>` |
| `/headers` | HTTP status and response headers of a link | `/headers <url>` |
| `/mime` | File type and size of a link (without downloading it) | `/mime <url>` |
| `/hash` | MD5 / SHA-1 / SHA-256 of a file | `/hash (reply to a file)` |
| `/urls` | Extract every link from a message (reply to it) | `/urls (reply to a message)` |

## 🛡 Admin (37)

| Command | What it does | Usage |
| --- | --- | --- |
| `/adult` | Adult content for the whole bot: off, or opt-in per user | `/adult <off\|optin>` |
| `/blocksite` | Block all downloads from a domain | `/blocksite <domain>` |
| `/unblocksite` | Remove a domain from the blocklist | `/unblocksite <domain>` |
| `/blockedsites` | Domains that are blocked | `/blockedsites` |
| `/cookies` | Use your own logged-in cookies for sites that need a login (reply to cookies.txt) | `/cookies [clear]  (reply to a cookies.txt file)` |
| `/sitecheck` | Test right now which major platforms this server can read | `/sitecheck [url …]` |
| `/pagedebug` | Show what the bot can find on a page (streams, extractor, adult label) + its HTML | `/pagedebug <url>` |
| `/siterule` | Custom stream-extraction rules for sites without built-in support | `/siterule list \| add <domain> <regex> \| remove <domain> [n] \| test <url>` |
| `/admin` | Admin panel | `/admin` |
| `/users` | List users (30 per page) | `/users [page]` |
| `/user` | Details about one user | `/user <id\|@username>` |
| `/allow` | Give a user access | `/allow <id\|@username>` |
| `/deny` | Remove a user's access | `/deny <id\|@username>` |
| `/ban` | Ban a user and cancel their jobs | `/ban <id\|@username>` |
| `/unban` | Lift a ban | `/unban <id\|@username>` |
| `/promote` | Make a user an admin | `/promote <id\|@username>` |
| `/demote` | Remove admin rights | `/demote <id\|@username>` |
| `/setlimit` | Set a user's daily download limit (0 = unlimited, default = global) | `/setlimit <id\|@username> <number\|default>` |
| `/broadcast` | Message every user who has access | `/broadcast <message>` |
| `/maintenance` | Block non-admin use while you work on the bot | `/maintenance <on\|off>` |
| `/invite` | Create an invite code (uses, hours valid) | `/invite [uses=1] [hours=72]` |
| `/invites` | Active invite codes | `/invites` |
| `/revoke` | Delete an invite code | `/revoke <code>` |
| `/sysinfo` | CPU, memory, disk and uptime | `/sysinfo` |
| `/disk` | Space used by downloads and the database | `/disk` |
| `/cleanup` | Delete leftover download files now | `/cleanup [hours=0]` |
| `/logs` | The last lines of the bot log | `/logs [lines=60]` |
| `/updateytdlp` | Update yt-dlp to the latest version (sites change often) | `/updateytdlp` |
| `/backup` | Send a copy of the database | `/backup` |
| `/globalstats` | Totals across all users | `/globalstats` |
| `/jobs` | Every active job from every user | `/jobs` |
| `/killjob` | Cancel any user's job | `/killjob <job_id>` |
| `/pauseall` | Stop starting new jobs for everyone | `/pauseall` |
| `/resumeall` | Resume the global queue | `/resumeall` |
| `/clearcache` | Forget cached Telegram file IDs (forces fresh downloads) | `/clearcache` |
| `/feedbacks` | Recent feedback from users | `/feedbacks` |
| `/restart` | Restart the bot process (Docker/systemd bring it back) | `/restart` |
