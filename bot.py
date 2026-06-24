import logging
import json
import os
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from notion_client import Client

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID"))
NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NAIROBI_TZ         = pytz.timezone("Africa/Nairobi")

# ── Activities ────────────────────────────────────────────────────────────────
ACTIVITIES = [
    {"id": 1, "label": "🐍 Python Backend Study",      "category": "Learning",   "target": "30–60 min"},
    {"id": 2, "label": "🧩 DSA / Problem Solving",     "category": "Learning",   "target": "20 min"},
    {"id": 3, "label": "🔨 Work on a Mini Project",    "category": "Learning",   "target": "30 min"},
    {"id": 4, "label": "📖 Read Docs / Articles",      "category": "Learning",   "target": "15 min"},
    {"id": 5, "label": "🎮 Something Fun (your pick)", "category": "Fun",        "target": "Free"},
    {"id": 6, "label": "📝 Daily Reflection / Notes",  "category": "Wind-down",  "target": "5 min"},
]

# ── State ─────────────────────────────────────────────────────────────────────
# Stores today's checked activity IDs
daily_state: dict[str, set] = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

notion = Client(auth=NOTION_TOKEN)


# ── Helpers ───────────────────────────────────────────────────────────────────

def today_key() -> str:
    return datetime.now(NAIROBI_TZ).strftime("%Y-%m-%d")


def get_today_checked() -> set:
    return daily_state.get(today_key(), set())


def set_today_checked(ids: set):
    daily_state[today_key()] = ids


def build_activity_list(checked: set) -> str:
    lines = []
    for a in ACTIVITIES:
        tick = "✅" if a["id"] in checked else "⬜"
        lines.append(f"{tick} {a['id']}. {a['label']} ({a['target']})")
    return "\n".join(lines)


def parse_numbers(text: str) -> set:
    """Extract valid activity IDs from a message like '1 3 5' or '1,3,5'."""
    valid_ids = {a["id"] for a in ACTIVITIES}
    nums = set()
    for token in text.replace(",", " ").split():
        try:
            n = int(token)
            if n in valid_ids:
                nums.add(n)
        except ValueError:
            pass
    return nums


async def log_to_notion(date_str: str, checked: set, score_pct: int):
    """Write a daily summary row to Notion."""
    done_labels = [a["label"] for a in ACTIVITIES if a["id"] in checked]
    missed_labels = [a["label"] for a in ACTIVITIES if a["id"] not in checked]

    try:
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {
                    "title": [{"text": {"content": f"Daily Log — {date_str}"}}]
                },
                "Date": {
                    "date": {"start": date_str}
                },
                "Score": {
                    "number": score_pct
                },
                "Completed": {
                    "rich_text": [{"text": {"content": "\n".join(done_labels) or "None"}}]
                },
                "Missed": {
                    "rich_text": [{"text": {"content": "\n".join(missed_labels) or "None"}}]
                },
            },
        )
        logger.info(f"Logged to Notion: {date_str} — {score_pct}%")
    except Exception as e:
        logger.error(f"Notion log failed: {e}")


# ── Scheduled messages ────────────────────────────────────────────────────────

async def send_morning_message(app: Application):
    checked = get_today_checked()
    date_str = datetime.now(NAIROBI_TZ).strftime("%A, %d %B %Y")
    msg = (
        f"☀️ *Good morning Brian!*\n"
        f"_{date_str}_\n\n"
        f"Here are your activities for today:\n\n"
        f"{build_activity_list(checked)}\n\n"
        f"Reply with the numbers of activities you complete during the day.\n"
        f"e.g. `1 3 5` to mark those as done.\n\n"
        f"You've got this 💪"
    )
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )


async def send_evening_summary(app: Application):
    checked = get_today_checked()
    date_str = today_key()
    total = len(ACTIVITIES)
    done = len(checked)
    pct = round((done / total) * 100) if total > 0 else 0

    if pct == 100:
        verdict = "🏆 Perfect day! You crushed every single one."
    elif pct >= 70:
        verdict = "💪 Solid effort today. Keep pushing."
    elif pct >= 40:
        verdict = "📈 Decent start. Tomorrow aim a little higher."
    else:
        verdict = "🔄 Rough day — that's okay. Fresh start tomorrow."

    msg = (
        f"🌙 *Evening Check-in*\n\n"
        f"{build_activity_list(checked)}\n\n"
        f"*Score: {done}/{total} ({pct}%)*\n\n"
        f"{verdict}\n\n"
        f"_Logging to Notion..._"
    )
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )

    await log_to_notion(date_str, checked, pct)

    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text="✅ Logged to Notion. See you tomorrow at 6 AM!",
    )

    # Reset for next day
    daily_state[date_str] = set()


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Brian's Daily Tracker Bot*\n\n"
        "I'll message you every morning at 6 AM with your activities.\n"
        "Reply with numbers to check them off.\n\n"
        "Commands:\n"
        "/status — see today's progress\n"
        "/done 1 3 5 — mark activities as done\n"
        "/reset — clear today's checks\n"
        "/summary — trigger evening summary now\n"
        "/morning — trigger morning message now",
        parse_mode="Markdown"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    checked = get_today_checked()
    done = len(checked)
    total = len(ACTIVITIES)
    pct = round((done / total) * 100) if total > 0 else 0
    msg = (
        f"📊 *Today's Progress — {pct}%*\n\n"
        f"{build_activity_list(checked)}\n\n"
        f"Reply with numbers to check off more, e.g. `2 4`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    ids = parse_numbers(text)
    if not ids:
        await update.message.reply_text("Send numbers like: /done 1 3 5")
        return
    checked = get_today_checked()
    checked.update(ids)
    set_today_checked(checked)
    done = len(checked)
    total = len(ACTIVITIES)
    pct = round((done / total) * 100)
    await update.message.reply_text(
        f"✅ Marked! *{done}/{total} done ({pct}%)*\n\n{build_activity_list(checked)}",
        parse_mode="Markdown"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_today_checked(set())
    await update.message.reply_text("🔄 Today's progress reset.")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_evening_summary(context.application)


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_morning_message(context.application)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain number replies like '1 3 5'."""
    text = update.message.text
    ids = parse_numbers(text)
    if ids:
        checked = get_today_checked()
        checked.update(ids)
        set_today_checked(checked)
        done = len(checked)
        total = len(ACTIVITIES)
        pct = round((done / total) * 100)
        if pct == 100:
            msg = f"🎉 *100%! You nailed every activity today Brian!*\n\n{build_activity_list(checked)}"
        else:
            msg = f"✅ Got it! *{done}/{total} ({pct}%)*\n\n{build_activity_list(checked)}"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "Reply with numbers to check off activities, e.g. `1 3 5`\n"
            "Or use /status to see today's list.",
            parse_mode="Markdown"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("done",    cmd_done))
    app.add_handler(CommandHandler("reset",   cmd_reset))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler
    scheduler = AsyncIOScheduler(timezone=NAIROBI_TZ)
    scheduler.add_job(
        send_morning_message,
        CronTrigger(hour=6, minute=0, timezone=NAIROBI_TZ),
        args=[app],
        id="morning"
    )
    scheduler.add_job(
        send_evening_summary,
        CronTrigger(hour=21, minute=0, timezone=NAIROBI_TZ),
        args=[app],
        id="evening"
    )
    scheduler.start()

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
