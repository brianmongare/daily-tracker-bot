import logging
import os
import random
import httpx
from collections import Counter
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from notion_client import Client
from google import genai
from google.genai import types

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID"))
NOTION_TOKEN       = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
NAIROBI_TZ         = pytz.timezone("Africa/Nairobi")
STREAK_THRESHOLD   = 70  # % score needed for a day to "count" toward a streak

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
genai_client = genai.Client(api_key=GEMINI_API_KEY)


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


# ── Notion queries (for streaks, digest, and grounded Q&A) ───────────────────

async def fetch_recent_logs(days: int) -> list[dict]:
    """
    Pull the last `days` days of logs from Notion, oldest first.
    Returns a list of dicts: {date, score, completed, missed}
    """
    cutoff = (datetime.now(NAIROBI_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    logs = []
    try:
        response = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={"property": "Date", "date": {"on_or_after": cutoff}},
            sorts=[{"property": "Date", "direction": "ascending"}],
        )
        for page in response.get("results", []):
            props = page["properties"]
            date_val = props["Date"]["date"]
            if not date_val:
                continue
            score = props["Score"]["number"]
            completed_rt = props["Completed"]["rich_text"]
            missed_rt = props["Missed"]["rich_text"]
            logs.append({
                "date": date_val["start"],
                "score": score if score is not None else 0,
                "completed": completed_rt[0]["plain_text"] if completed_rt else "",
                "missed": missed_rt[0]["plain_text"] if missed_rt else "",
            })
    except Exception as e:
        logger.error(f"Notion query failed: {e}")
    return logs


def compute_streaks(logs: list[dict], threshold: int = STREAK_THRESHOLD) -> tuple[int, int]:
    """
    Returns (current_streak, longest_streak) based on days where score >= threshold.
    Assumes logs are sorted oldest -> newest.
    """
    if not logs:
        return 0, 0

    hits = [log["score"] >= threshold for log in logs]

    longest = 0
    running = 0
    for hit in hits:
        running = running + 1 if hit else 0
        longest = max(longest, running)

    current = 0
    for hit in reversed(hits):
        if hit:
            current += 1
        else:
            break

    return current, longest


def build_week_bar_chart(logs: list[dict]) -> str:
    """Renders a simple monospace bar chart for the last 7 days."""
    lines = []
    for log in logs[-7:]:
        day_name = datetime.strptime(log["date"], "%Y-%m-%d").strftime("%a")
        pct = int(log["score"])
        filled = round(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"{day_name} {bar} {pct}%")
    return "\n".join(lines)


def most_missed_activity(logs: list[dict]) -> str:
    counter = Counter()
    for log in logs:
        for label in log["missed"].split("\n"):
            label = label.strip()
            if label and label != "None":
                counter[label] += 1
    if not counter:
        return "None — great week!"
    label, count = counter.most_common(1)[0]
    return f"{label} (missed {count}x)"


# ── Claude-powered Q&A ─────────────────────────────────────────────────────────

DATA_KEYWORDS = (
    "score", "streak", "week", "month", "today", "yesterday",
    "average", "missed", "completed", "progress", "activity", "days"
)


def looks_data_related(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in DATA_KEYWORDS)


async def build_notion_context(days: int = 30) -> str:
    logs = await fetch_recent_logs(days)
    if not logs:
        return "No logged history yet."
    lines = [f"{log['date']}: {log['score']}% done. Missed: {log['missed'] or 'None'}" for log in logs]
    return "\n".join(lines)


async def ask_ai(question: str, context: str | None = None) -> str:
    system = (
        "You are a friendly, encouraging assistant embedded in Brian's personal "
        "daily habit tracker Telegram bot. Keep answers short (a few sentences), "
        "conversational, and suited for a Telegram message. If given tracker data, "
        "base your answer on it precisely rather than guessing."
    )
    user_content = question
    if context:
        user_content = f"Here is Brian's recent tracker data:\n{context}\n\nQuestion: {question}"

    try:
        response = await genai_client.aio.models.generate_content(
            model="gemini-flash-latest",
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=400,
            ),
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        return "⚠️ Sorry, I couldn't reach the AI just now. Try again in a moment."


# ── Motivational quotes ────────────────────────────────────────────────────────
# Pulled from free, keyless APIs: ZenQuotes (broad variety, thousands of authors)
# mixed with the Stoicism Quote API (guarantees Marcus Aurelius, Seneca, Epictetus
# show up regularly). Falls back to this local list if both APIs are unreachable.

QUOTES = [
    ("Code is poetry.", "Unknown"),
    ("We are all born ignorant, but one must work hard to remain stupid.", "Benjamin Franklin"),
    ("Most of us will do anything to avoid facing ourselves.", "Lolly Daskal"),
    ("Doing the best at this moment puts you in the best place for the next moment.", "Oprah Winfrey"),
    ("You have power over your mind - not outside events. Realize this, and you will find strength.", "Marcus Aurelius"),
    ("The impediment to action advances action. What stands in the way becomes the way.", "Marcus Aurelius"),
    ("Discipline is choosing between what you want now and what you want most.", "Abraham Lincoln"),
    ("It always seems impossible until it's done.", "Nelson Mandela"),
    ("Small daily improvements are the key to staggering long-term results.", "Unknown"),
    ("The expert in anything was once a beginner.", "Helen Hayes"),
    ("Don't watch the clock; do what it does. Keep going.", "Sam Levenson"),
    ("You don't have to be great to start, but you have to start to be great.", "Zig Ziglar"),
]


async def fetch_quote_online() -> tuple[str, str]:
    """
    Fetch a random quote from free, keyless APIs.
    ~35% chance of pulling from the Stoicism Quote API (Marcus Aurelius, Seneca,
    Epictetus); otherwise ZenQuotes for broader variety. Falls back to the local
    QUOTES list if both are unreachable.
    """
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            if random.random() < 0.35:
                resp = await client.get("https://stoicismquote.com/api/v1/quote/random")
                resp.raise_for_status()
                data = resp.json()
                item = data[0] if isinstance(data, list) else data
                quote = (item.get("quote") or item.get("text") or "").strip()
                author = (item.get("author") or "Stoic Philosopher").strip()
                if quote:
                    return quote, author
        except Exception as e:
            logger.warning(f"Stoicism quote API failed, trying ZenQuotes: {e}")

        try:
            resp = await client.get("https://zenquotes.io/api/random")
            resp.raise_for_status()
            data = resp.json()
            item = data[0]
            return item["q"].strip(), item["a"].strip()
        except Exception as e:
            logger.warning(f"ZenQuotes API failed, falling back to local list: {e}")

    return random.choice(QUOTES)


async def send_quote(app: Application):
    quote, author = await fetch_quote_online()
    msg = f"💡 \"{quote}\"\n— {author}"
    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)


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

    logs = await fetch_recent_logs(90)
    current, longest = compute_streaks(logs)
    streak_msg = f"🔥 Current streak: {current} day(s)"
    if current > 0 and current == longest:
        streak_msg += " — that's your best ever!"

    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"✅ Logged to Notion.\n{streak_msg}\n\nSee you tomorrow at 6 AM!",
    )

    # Reset for next day
    daily_state[date_str] = set()


async def send_weekly_digest(app: Application):
    logs = await fetch_recent_logs(7)
    if not logs:
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="📊 No data logged this week yet — nothing to recap!",
        )
        return

    chart = build_week_bar_chart(logs)
    avg = round(sum(log["score"] for log in logs) / len(logs))
    top_miss = most_missed_activity(logs)
    start = logs[0]["date"]
    end = logs[-1]["date"]

    msg = (
        f"📊 *Week in Review* ({start} → {end})\n\n"
        f"```\n{chart}\n```\n"
        f"*Average: {avg}%*\n"
        f"*Most-missed:* {top_miss}\n\n"
        f"New week, fresh momentum 🚀"
    )
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )


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
        "/streak — see your current and best streak\n"
        "/summary — trigger evening summary now\n"
        "/morning — trigger morning message now\n"
        "/digest — trigger weekly digest now\n"
        "/quote — get a random motivational quote\n"
        "/ask <question> — ask me anything, including about your own progress\n\n"
        "You can also just type a plain question and I'll answer it.",
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


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_weekly_digest(context.application)


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_quote(context.application)


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logs = await fetch_recent_logs(90)
    current, longest = compute_streaks(logs)
    msg = (
        f"🔥 *Current streak:* {current} day(s)\n"
        f"🏅 *Longest streak:* {longest} day(s)\n\n"
        f"_(Counting days at {STREAK_THRESHOLD}%+ over the last 90 days)_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Ask me something, e.g. `/ask how did I do this week?`", parse_mode="Markdown")
        return
    await answer_question(update, question)


async def answer_question(update: Update, question: str):
    await update.message.chat.send_action("typing")
    context_data = await build_notion_context(30) if looks_data_related(question) else None
    answer = await ask_ai(question, context_data)
    await update.message.reply_text(answer)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text: number replies like '1 3 5', or natural-language questions."""
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
        # Not a number reply — treat it as a question for Claude
        await answer_question(update, text)


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
    app.add_handler(CommandHandler("digest",  cmd_digest))
    app.add_handler(CommandHandler("quote",   cmd_quote))
    app.add_handler(CommandHandler("streak",  cmd_streak))
    app.add_handler(CommandHandler("ask",     cmd_ask))
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
    scheduler.add_job(
        send_weekly_digest,
        CronTrigger(day_of_week="sun", hour=21, minute=30, timezone=NAIROBI_TZ),
        args=[app],
        id="weekly_digest"
    )
    QUOTE_TIMES = [
        (6, 0), (7, 30), (9, 0), (10, 30), (12, 0), (13, 30),
        (15, 0), (16, 30), (18, 0), (19, 30), (21, 0), (22, 30),
    ]
    for hour, minute in QUOTE_TIMES:
        scheduler.add_job(
            send_quote,
            CronTrigger(hour=hour, minute=minute, timezone=NAIROBI_TZ),
            args=[app],
            id=f"quote_{hour:02d}{minute:02d}"
        )
    scheduler.start()

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()