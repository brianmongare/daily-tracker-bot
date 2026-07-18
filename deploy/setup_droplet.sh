#!/bin/bash
# One-time setup script for a fresh Ubuntu droplet.
# Run this as root (or with sudo) right after creating the droplet.
#
# Usage: bash setup_droplet.sh <your-github-repo-url>
# Example: bash setup_droplet.sh https://github.com/brianmongare/daily-tracker-bot.git

set -e

REPO_URL="$1"
if [ -z "$REPO_URL" ]; then
  echo "Usage: bash setup_droplet.sh <github-repo-url>"
  exit 1
fi

echo "== Installing Python, pip, venv, and git =="
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git

echo "== Creating a dedicated, non-root user for the bot =="
if ! id "botuser" &>/dev/null; then
  adduser --disabled-password --gecos "" botuser
fi

echo "== Cloning your bot repo from GitHub =="
sudo -u botuser git clone "$REPO_URL" /home/botuser/daily-tracker-bot

echo "== Creating virtual environment and installing dependencies =="
sudo -u botuser python3 -m venv /home/botuser/daily-tracker-bot/venv
sudo -u botuser /home/botuser/daily-tracker-bot/venv/bin/pip install --upgrade pip
sudo -u botuser /home/botuser/daily-tracker-bot/venv/bin/pip install -r /home/botuser/daily-tracker-bot/requirements.txt

echo "== Setting up your .env file =="
if [ ! -f /home/botuser/daily-tracker-bot/.env ]; then
  cp /home/botuser/daily-tracker-bot/deploy/.env.example /home/botuser/daily-tracker-bot/.env
  chown botuser:botuser /home/botuser/daily-tracker-bot/.env
  chmod 600 /home/botuser/daily-tracker-bot/.env
  echo ""
  echo "  >>> IMPORTANT: edit /home/botuser/daily-tracker-bot/.env with your real secrets"
  echo "  >>> run: nano /home/botuser/daily-tracker-bot/.env"
  echo ""
fi

echo "== Installing the systemd service =="
cp /home/botuser/daily-tracker-bot/deploy/daily-tracker-bot.service /etc/systemd/system/daily-tracker-bot.service
systemctl daemon-reload
systemctl enable daily-tracker-bot

echo ""
echo "Setup complete. Next steps:"
echo "1. nano /home/botuser/daily-tracker-bot/.env   (fill in your real secrets)"
echo "2. systemctl start daily-tracker-bot"
echo "3. systemctl status daily-tracker-bot          (confirm it's running)"
echo "4. journalctl -u daily-tracker-bot -f           (watch live logs)"
