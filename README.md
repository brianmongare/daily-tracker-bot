# Brian's Daily Tracker Bot

A Telegram bot that messages you every morning with your daily activities,
lets you check them off by replying with numbers, tracks goals with a deadline,
and logs everything to Notion at the end of each day (12:00 AM).

---

## Schedule

- **6:00 AM EAT** — Morning message with activity list + active goal status
- **Reply anytime** — Send numbers like `1 3 5` to mark done, or `/resource` to log a resource
- **12:00 AM EAT (23:59 job)** — Day-end check-in, logged to Notion automatically, goal deadline check

## Commands

| Command                  | What it does                                        |
| ------------------------ | --------------------------------------------------- |
| `/start`                 | Welcome message                                     |
| `/status`                | See today's progress                                |
| `/done 1 3 5`            | Mark activities as done                             |
| `/reset`                 | Clear today's progress and logged resources         |
| `/resource <note>`       | Log a resource (article/tutorial) you used today    |
| `/morning`               | Trigger morning message now (for testing)           |
| `/summary`               | Trigger the day-end check now (for testing)         |
| `/digest`                | Trigger the weekly digest now                       |
| `/streak`                | See current and longest streak                      |
| `/quote`                 | Get a random motivational quote                     |
| `/article <topic>`       | Find a real, current article on a topic             |
| `/learn <topic>`         | Study-buddy explanation of a Python/FastAPI concept |
| `/goal <text> \| <days>` | Set a goal with a deadline (default 7 days)         |
| `/goalstatus`            | See your current goal's progress                    |
| `/goaldone`              | Mark the active goal achieved                       |
| `/goalfail`              | Mark the active goal not achieved                   |
| `/goalextend <days>`     | Keep the active goal going longer                   |
| `/ask <question>`        | Ask anything, including about your own progress     |

### How goals work

Set a goal with `/goal Build a FastAPI auth system | 7` (the `| 7` sets a 7-day
deadline — leave it off to default to 7 days). Each morning message and every
day-end check-in shows which day you're on. Once the deadline is reached, the
bot will keep nudging you at day-end to resolve it with `/goaldone`,
`/goalfail`, or `/goalextend <days>` — **you can't start a new goal until the
current one is resolved.** The bot also weaves your current goal into `/ask`
and `/learn` answers so its advice stays relevant to what you're working on.

Goal state is stored in `goal_state.json` next to `bot.py` so it survives
restarts.

---

## Step 1 — Connect Notion Integration

Before deploying, connect the integration to your database:

1. Open the Daily Tracker database in Notion
2. Click `...` (top right) → **Connections** → **Add connections**
3. Search **Daily Tracker** → select it
4. Run: `python setup_notion.py` to create the database columns

> If you already ran this before, run it again — it now also adds `Resources`
> and `Goal` columns to your existing database (safe to re-run, it won't touch existing rows).

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
