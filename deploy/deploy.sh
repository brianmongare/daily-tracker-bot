#!/bin/bash
# Run this on the droplet whenever you push new code to GitHub,
# to pull the update and restart the bot.
#
# Usage: bash deploy.sh

set -e

cd /home/botuser/daily-tracker-bot
sudo -u botuser git pull
sudo -u botuser /home/botuser/daily-tracker-bot/venv/bin/pip install -r requirements.txt
systemctl restart daily-tracker-bot

echo "Deployed latest code and restarted the bot."
systemctl status daily-tracker-bot --no-pager
