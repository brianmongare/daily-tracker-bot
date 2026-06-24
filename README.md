# Brian's Daily Tracker Bot

A Telegram bot that messages you every morning with your daily activities,
lets you check them off by replying with numbers, and logs everything to Notion at 9 PM.

---

## Schedule

- **6:00 AM EAT** — Morning message with activity list
- **Reply anytime** — Send numbers like `1 3 5` to mark done
- **9:00 PM EAT** — Evening summary + logged to Notion automatically

## Commands

| Command       | What it does                              |
| ------------- | ----------------------------------------- |
| `/start`      | Welcome message                           |
| `/status`     | See today's progress                      |
| `/done 1 3 5` | Mark activities as done                   |
| `/reset`      | Clear today's progress                    |
| `/morning`    | Trigger morning message now (for testing) |
| `/summary`    | Trigger evening summary now (for testing) |

---

## Step 1 — Connect Notion Integration

Before deploying, connect the integration to your database:

1. Open the Daily Tracker database in Notion
2. Click `...` (top right) → **Connections** → **Add connections**
3. Search **Daily Tracker** → select it
4. Run: `python setup_notion.py` to create the database columns

---

## Step 2 — Deploy to Railway

1. Go to [railway.app](https://railway.app) and sign up (free)
2. Click **New Project** → **Deploy from GitHub repo**
3. Push this folder to a GitHub repo first (see below)
4. Add these environment variables in Railway dashboard:

```
TELEGRAM_BOT_TOKEN=telegram_bot_token
TELEGRAM_CHAT_ID=telegram_chat_id
NOTION_TOKEN=your_notion_token
NOTION_DATABASE_ID=your_notion_database_id
```

5. Railway will auto-detect the Procfile and run the bot as a worker

---

## Push to GitHub (run these commands)

```bash
git init
git add .
git commit -m "Initial daily tracker bot"
# Create a repo on github.com then:
git remote add origin https://github.com/YOUR_USERNAME/daily-tracker-bot.git
git push -u origin main
```

---

## Local testing

```bash
pip install -r requirements.txt
cp .env .env.local
python bot.py
```

Then in Telegram send `/morning` to test the morning message immediately.
