#!/usr/bin/env bash
# Installs the bot on your own Ubuntu/Debian server as a systemd service.
#
#   sudo bash deploy/install.sh              # run from a clone of this repository
#
# Re-running updates the code and dependencies and restarts the service.
set -euo pipefail

APP_DIR=/opt/telegram-media-bot
DATA_DIR=/var/lib/telegram-media-bot
SERVICE=telegram-media-bot
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash deploy/install.sh" >&2
  exit 1
fi

echo "==> Installing system packages (python3, venv, ffmpeg)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip ffmpeg fonts-dejavu-core \
  ca-certificates rsync >/dev/null

echo "==> Creating the service user and folders"
id -u mediabot >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin mediabot
mkdir -p "$APP_DIR" "$DATA_DIR"

echo "==> Copying the application to $APP_DIR"
rsync -a --delete --exclude .git --exclude .venv --exclude data --exclude .env "$SRC_DIR"/ "$APP_DIR"/

echo "==> Creating the virtualenv and installing Python packages"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  read -rp "Bot token from @BotFather: " token
  read -rp "Your Telegram user ID (admin): " admin
  sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${token}|; s|^ADMIN_IDS=.*|ADMIN_IDS=${admin}|" "$APP_DIR/.env"
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed -i "s|^LINK_SECRET=.*|LINK_SECRET=${secret}|" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"
chown -R mediabot:mediabot "$APP_DIR" "$DATA_DIR"

echo "==> Installing the systemd service"
install -m 644 "$APP_DIR/deploy/systemd/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl restart "$SERVICE"

sleep 3
systemctl --no-pager --lines=5 status "$SERVICE" || true
echo
echo "Done. Useful commands:"
echo "  journalctl -u $SERVICE -f          # live logs"
echo "  sudo systemctl restart $SERVICE    # restart after editing $APP_DIR/.env"
echo "  sudo bash deploy/install.sh        # update after a git pull"
