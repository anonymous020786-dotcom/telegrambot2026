# Deploying the bot

There are three ways to run the bot. Each keeps it running 24/7 and restarts it after crashes and reboots.

| Option | Best for | Time |
| --- | --- | --- |
| [A. Docker on any server](#a-docker-any-server-or-pc) | Any Linux VPS, a home server, or a PC with Docker | 5 min |
| [B. Your own server with systemd](#b-your-own-server-systemd-no-docker) | Ubuntu or Debian without Docker | 5 min |
| [C. AWS (CloudFormation)](#c-aws-one-command) | Managed AWS setup with S3 for big files | 10 min |

## 1. Create the bot (all options)

1. In Telegram, open **@BotFather** and send `/newbot`. Choose a name and a username, then copy the **token**.
2. Find your **Telegram user ID**: message **@userinfobot**, or start this bot later and send `/id`.
3. Optionally, send `/setprivacy` → *Disable* to BotFather if you want the bot to see links in groups. The bot
   installs its own command menu at startup, so you don't need `/setcommands`.
4. Optionally, turn on **inline mode** (`@yourbot <link>` in any chat): send `/setinline` to BotFather and pick a
   placeholder such as "link or search…", then `/setinlinefeedback` → *100%* so downloads start the moment a
   result is picked. Without feedback, users press the result's "▶️ Start download" button instead.

## A. Docker (any server or PC)

```bash
git clone https://github.com/anonymous020786-dotcom/telegrambot2026.git
cd telegrambot2026
cp .env.example .env
nano .env                    # set BOT_TOKEN and ADMIN_IDS
docker compose up -d --build
docker compose logs -f bot   # wait for "Bot @yourbot ready"
```

- **Restarts:** `restart: unless-stopped` brings the bot back after crashes and reboots, as long as Docker starts on
  boot.
- **Data:** the database and temporary files live in the `bot-data` volume.
- **Health check:** the container reports *unhealthy* if the bot stops responding. `docker ps` shows the status.

## B. Your own server (systemd, no Docker)

On Ubuntu 22.04+ or Debian 12+, run this from a clone of the repository:

```bash
git clone https://github.com/anonymous020786-dotcom/telegrambot2026.git
cd telegrambot2026
sudo bash deploy/install.sh          # asks for the token and your ID the first time
journalctl -u telegram-media-bot -f  # live logs
```

What the script sets up:
- **System packages:** it installs Python, ffmpeg and a virtualenv.
- **Service user:** it creates a locked-down `mediabot` user.
- **Locations:** the code goes to `/opt/telegram-media-bot` and data to `/var/lib/telegram-media-bot`.
- **systemd service:** it installs a hardened service that restarts automatically and starts on boot.

Configuration lives in `/opt/telegram-media-bot/.env`. After editing it, run
`sudo systemctl restart telegram-media-bot`. To update, `git pull` and run `sudo bash deploy/install.sh` again.

## C. AWS (one command)

`deploy/aws/cloudformation.yaml` creates:

- **EC2 instance:** Amazon Linux 2023 with Docker, an encrypted gp3 disk, and IMDSv2 required. There's no SSH port;
  you connect with **Session Manager** instead.
- **Secrets Manager secret:** holds the bot token.
- **S3 bucket:** private, encrypted and TLS-only. Big files are uploaded here and shared as presigned links. They are
  deleted automatically after `S3RetentionDays` (default 2).
- **IAM role:** it can only write to that bucket and read that secret.
- **Link-server port (optional):** 8080 opened for expiring download links (`EnableLinkServer=true`).

Requirements: the AWS CLI, configured with credentials that can create these resources.

```bash
BOT_TOKEN=123456:ABC... ADMIN_IDS=111111111 ./deploy/aws/deploy.sh telegram-media-bot us-east-1

# bigger instance, link server on, deploy a specific branch:
PARAMS="InstanceType=t3.medium EnableLinkServer=true RepoBranch=main" \
BOT_TOKEN=... ADMIN_IDS=... ./deploy/aws/deploy.sh
```

The stack deploys the code from `RepoUrl` / `RepoBranch` (default: this repository's `main` branch). The repository
must be reachable by the instance: either public, or a URL that contains a read-only token.

| Task | Command |
| --- | --- |
| Open a shell on the server | `aws ssm start-session --target <InstanceId>` (the stack output shows the exact command) |
| Follow logs | `cd /opt/telegram-media-bot && sudo docker compose logs -f bot` |
| Update to the latest code | `./deploy/aws/update.sh telegram-media-bot us-east-1` |
| Delete everything | `aws cloudformation delete-stack --stack-name telegram-media-bot` (empty the bucket first) |

Rough cost: a `t3.small` in us-east-1 is about US$15/month, plus the disk and any S3 or data transfer.

You can also do it in the web console: CloudFormation → *Create stack* → upload `cloudformation.yaml`.

## Big files

Telegram limits **bots** to 50 MB uploads (20 MB for files users send to the bot). Pick one or more of these:

### 1. Self-hosted Bot API server (up to 2000 MB, recommended)

1. At <https://my.telegram.org> → *API development tools*, create an app and copy the **api_id** and **api_hash**.
2. In `.env`, set:
   ```ini
   TELEGRAM_API_ID=...
   TELEGRAM_API_HASH=...
   BOT_API_BASE_URL=http://telegram-bot-api:8081/bot
   BOT_API_BASE_FILE_URL=http://telegram-bot-api:8081/file/bot
   BOT_API_LOCAL_MODE=true
   ```
3. Start it with `docker compose --profile local-api up -d`. The two containers share the data volume, so files are
   uploaded by path.
4. The first time you switch to it, log the bot out of the cloud API once by opening
   `https://api.telegram.org/bot<TOKEN>/logOut` in a browser.

### 2. Expiring download links from your server

```ini
LINK_SERVER_ENABLED=true
LINK_BASE_URL=https://files.example.com    # public address of port 8080
LINK_SECRET=<long random string>           # keep links valid across restarts
LINK_TTL_HOURS=6
```

Put HTTPS in front of it, for example with Caddy: `files.example.com { reverse_proxy localhost:8080 }`. Links are
HMAC-signed and stop working after the TTL. Files are deleted after `FILE_RETENTION_HOURS`.

### 3. Amazon S3

Set `S3_BUCKET` and `S3_REGION`. On EC2 the instance role supplies credentials; elsewhere, use the standard
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` variables. Add a lifecycle rule to the bucket that deletes old files
(the CloudFormation stack does this for you).

Each user picks how big files reach them with `/setdelivery auto|split|link|s3`. `auto` tries S3, then a link, then
splitting into playable parts.

## Keeping downloads working

Websites change often, and yt-dlp releases fixes quickly.

- **From Telegram:** an admin sends `/updateytdlp` and then `/restart`.
- **Docker:** `docker compose build --pull && docker compose up -d`.
- **systemd:** `sudo bash deploy/install.sh` (it reinstalls the latest compatible versions).

Updating or restarting never loses work: downloads that were waiting or running are saved in the database and
resume on the next start (users see "Resumed after a bot restart"). Keep `data/` on a persistent volume, as the
provided Docker and systemd setups do.

## Backups

Send `/backup` in the bot to receive a copy of the SQLite database. On the server, the database is at
`$DATA_DIR/bot.db`: `/var/lib/telegram-media-bot/bot.db`, or the `bot-data` Docker volume.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| The bot doesn't answer | Check the logs. `Unauthorized` means the token is wrong. |
| "This is a private bot" for you | Your ID isn't in `ADMIN_IDS`. Send `/id` and add it, then restart. |
| "Requested format is not available" | Send `/formats URL` to see what exists; try `/best`. |
| A site stopped working | `/sitecheck` to see what fails, then `/updateytdlp` and `/restart`. |
| "This media isn't public (it needs a login)" | Export **your own** browser cookies (Netscape `cookies.txt`, e.g. with the "Get cookies.txt LOCALLY" extension) and reply to the file with `/cookies`. |
| "The site is rate-limiting this server (429)" / YouTube asks to "confirm you're not a bot" | Common on cloud/datacenter IPs. Add your cookies with `/cookies`, lower `MAX_CONCURRENT_JOBS`, or set `PROXY` to a residential/home connection. |
| "The server's network or proxy blocked the connection" | The server's firewall/proxy doesn't allow that site. Check outbound HTTPS from the server. |
| "No downloadable video stream was found on this page" | Run `/pagedebug <url>`. If the stream URL is in the attached HTML, add `/siterule add <domain> <regex>` and check it with `/siterule test <url>`. |
| "DRM-protected streaming service" | Expected. The bot never bypasses DRM (Netflix, Prime Video, Disney+ and similar). |
| Files over 50 MB arrive in parts | Expected without a local Bot API server, links or S3. See [Big files](#big-files). |
| `ffmpeg isn't installed` | Docker already includes it; otherwise run `apt install ffmpeg`. |
