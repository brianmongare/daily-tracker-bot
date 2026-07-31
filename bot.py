import json
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
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
STREAK_THRESHOLD = 70  # % score needed for a day to "count" toward a streak
GOAL_STATE_FILE = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "goal_state.json")
DEFAULT_GOAL_DAYS = 7  # a goal's default deadline, in days, if none is given

# ── Activities ────────────────────────────────────────────────────────────────
ACTIVITIES = [
    {"id": 1, "label": "🐍 Python Backend Study",
        "category": "Learning",   "target": "30–60 min"},
    {"id": 2, "label": "🧩 DSA / Problem Solving",
        "category": "Learning",   "target": "20 min"},
    {"id": 3, "label": "🔨 Work on a Mini Project",
        "category": "Learning",   "target": "30 min"},
    {"id": 4, "label": "📖 Read Docs / Articles",
        "category": "Learning",   "target": "15 min"},
    {"id": 5, "label": "🎮 Something Fun (your pick)",
     "category": "Fun",        "target": "Free"},
    {"id": 6, "label": "📝 Daily Reflection / Notes",
        "category": "Wind-down",  "target": "5 min"},
]

# ── State ─────────────────────────────────────────────────────────────────────
# Stores today's checked activity IDs
daily_state: dict[str, set] = {}

# Stores today's logged resources (articles/tutorials/videos Brian used)
daily_resources: dict[str, list] = {}

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


def get_today_resources() -> list:
    return daily_resources.get(today_key(), [])


def add_today_resource(text: str):
    resources = daily_resources.setdefault(today_key(), [])
    resources.append(text)


# ── Goal tracking ──────────────────────────────────────────────────────────────
# A "goal" is something Brian wants to achieve by a deadline (default one week).
# The bot won't let a new goal be set until the current one is resolved
# (marked achieved or not achieved), so progress is checked before moving on.

def load_goal_data() -> dict:
    default = {"active": None, "achieved_count": 0, "failed_count": 0}
    if not os.path.exists(GOAL_STATE_FILE):
        return default
    try:
        with open(GOAL_STATE_FILE, "r") as f:
            data = json.load(f)
            default.update(data)
            return default
    except Exception as e:
        logger.error(f"Failed to load goal state: {e}")
        return default


def save_goal_data(data: dict):
    try:
        with open(GOAL_STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save goal state: {e}")


def goal_progress(active: dict) -> tuple[int, int, bool]:
    """Returns (day_number, duration_days, deadline_reached) for an active goal."""
    start = datetime.strptime(active["start_date"], "%Y-%m-%d").date()
    today = datetime.now(NAIROBI_TZ).date()
    day_number = (today - start).days + 1
    duration_days = active["duration_days"]
    return day_number, duration_days, day_number >= duration_days


def format_goal_line(active: dict) -> str:
    if not active:
        return ""
    day_number, duration_days, deadline_reached = goal_progress(active)
    day_number = min(
        day_number, duration_days) if not deadline_reached else day_number
    status = " (deadline reached — /goaldone, /goalfail, or /goalextend)" if deadline_reached else ""
    return f"🎯 *Goal:* {active['text']} — Day {day_number}/{duration_days}{status}"


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


async def log_to_notion(date_str: str, checked: set, score_pct: int, resources: list | None = None, goal_text: str | None = None):
    """Write a daily summary row to Notion."""
    done_labels = [a["label"] for a in ACTIVITIES if a["id"] in checked]
    missed_labels = [a["label"] for a in ACTIVITIES if a["id"] not in checked]
    resources = resources or []

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
                "Resources": {
                    "rich_text": [{"text": {"content": "\n".join(resources) or "None"}}]
                },
                "Goal": {
                    "rich_text": [{"text": {"content": goal_text or "None"}}]
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
    cutoff = (datetime.now(NAIROBI_TZ) -
              timedelta(days=days)).strftime("%Y-%m-%d")
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
    lines = [
        f"{log['date']}: {log['score']}% done. Missed: {log['missed'] or 'None'}" for log in logs]
    return "\n".join(lines)


async def ask_ai(question: str, context: str | None = None) -> str:
    system = (
        "You are Brian's accountability and learning partner, embedded in his "
        "personal daily habit tracker Telegram bot. He's a developer working "
        "toward Machine Learning engineering, currently building skills in "
        "Python and FastAPI. Act like a supportive study partner who is genuinely "
        "invested in his progress: encouraging but honest, and happy to explain "
        "concepts or nudge him forward. Keep answers to 3-5 short sentences, "
        "conversational, and suited for a Telegram message. Always finish your "
        "thought completely - never cut off mid-sentence. If given tracker data "
        "or goal context, base your answer on it precisely rather than guessing."
    )
    goal_data = load_goal_data()
    goal_line = ""
    if goal_data.get("active"):
        day_number, duration_days, _ = goal_progress(goal_data["active"])
        goal_line = f"Brian's current goal: {goal_data['active']['text']} (day {day_number} of {duration_days}).\n"

    user_content = f"{goal_line}{question}" if goal_line else question
    if context:
        user_content = f"{goal_line}Here is Brian's recent tracker data:\n{context}\n\nQuestion: {question}"

    try:
        response = await genai_client.aio.models.generate_content(
            model="gemini-flash-latest",
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=1024,
            ),
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        return "⚠️ Sorry, I couldn't reach the AI just now. Try again in a moment."


# ── Motivational quotes ────────────────────────────────────────────────────────
# Pulled from free, keyless APIs: ZenQuotes (broad variety, thousands of authors)
# mixed with the Stoicism Quote API (guarantees Marcus Aurelius, Seneca, Epictetus
# show up regularly). Falls back to this local list if both are unreachable.

QUOTES = [
    ("Code is poetry.", "Unknown"),
    ("We are all born ignorant, but one must work hard to remain stupid.",
     "Benjamin Franklin"),
    ("Most of us will do anything to avoid facing ourselves.", "Lolly Daskal"),
    ("Doing the best at this moment puts you in the best place for the next moment.", "Oprah Winfrey"),
    ("You have power over your mind - not outside events. Realize this, and you will find strength.", "Marcus Aurelius"),
    ("The impediment to action advances action. What stands in the way becomes the way.", "Marcus Aurelius"),
    ("Discipline is choosing between what you want now and what you want most.",
     "Abraham Lincoln"),
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
            logger.warning(
                f"ZenQuotes API failed, falling back to local list: {e}")

    return random.choice(QUOTES)


async def send_quote(app: Application):
    quote, author = await fetch_quote_online()
    msg = f"💡 \"{quote}\"\n— {author}"
    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)


async def ask_ai_learn(topic: str) -> str:
    """
    Study-buddy mode for /learn: explains a Python/FastAPI concept like a pair
    programmer would, with a short code example, tied to Brian's goal if he has one.
    """
    goal_data = load_goal_data()
    goal_line = ""
    if goal_data.get("active"):
        goal_line = f" His current goal is: {goal_data['active']['text']}."

    system = (
        "You are Brian's Python/FastAPI study partner inside his habit tracker "
        "Telegram bot. He's working toward Machine Learning engineering and "
        "learns by building a real production app (a FastAPI/SQLAlchemy backend "
        f"called Graville Operations).{goal_line} When he asks you to explain "
        "something, teach it like a patient pair-programmer: a short plain-English "
        "explanation, then a brief, correct code example if relevant. End with one "
        "small question or exercise to check his understanding. Keep the whole "
        "reply under 12 lines, formatted plainly for Telegram (no markdown tables)."
    )
    try:
        response = await genai_client.aio.models.generate_content(
            model="gemini-flash-latest",
            contents=topic,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=1024,
            ),
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        return "⚠️ Sorry, I couldn't reach the AI just now. Try again in a moment."


async def ask_ai_with_search(question: str) -> str:
    """
    Same as ask_ai, but grounds the answer in real, current web results via
    Gemini's built-in Google Search tool - so links and facts are real,
    not just recalled from training data.

    NOTE: Google Search grounding has a separate, much stricter quota than
    normal Gemini text generation. Only call this from an explicit request
    (the /article command) - do NOT auto-route casual messages here, or
    you'll burn through the daily grounding quota fast (this caused repeated
    429 RESOURCE_EXHAUSTED errors previously).
    """
    system = (
        "You are a friendly assistant embedded in Brian's daily habit tracker "
        "Telegram bot. He wants a real, current article or resource on a topic. "
        "Use Google Search to find something genuinely useful and recent. "
        "Reply with the article title, a one-sentence summary of what it covers, "
        "and the real URL. Keep it to 3-5 sentences total, suited for Telegram."
    )
    try:
        response = await genai_client.aio.models.generate_content(
            model="gemini-flash-latest",
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=1024,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        answer = response.text.strip()

        # Append real source links from grounding metadata, if available
        try:
            candidate = response.candidates[0]
            chunks = candidate.grounding_metadata.grounding_chunks
            if chunks:
                links = []
                for chunk in chunks[:3]:
                    if chunk.web and chunk.web.uri:
                        title = chunk.web.title or chunk.web.uri
                        links.append(f"🔗 {title}\n{chunk.web.uri}")
                if links:
                    answer += "\n\n" + "\n\n".join(links)
        except Exception:
            pass  # grounding metadata is best-effort; don't fail the whole reply over it

        return answer
    except Exception as e:
        logger.error(f"Gemini search-grounded call failed: {e}")
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            return "⚠️ I've hit today's search limit for finding articles. Try again tomorrow, or ask me directly without /article - I can still answer from what I know."
        return "⚠️ Sorry, I couldn't search for that just now. Try again in a moment."


# ── Scheduled messages ────────────────────────────────────────────────────────

async def send_morning_message(app: Application):
    checked = get_today_checked()
    date_str = datetime.now(NAIROBI_TZ).strftime("%A, %d %B %Y")
    goal_data = load_goal_data()
    goal_line = format_goal_line(goal_data.get("active"))
    goal_block = f"{goal_line}\n\n" if goal_line else ""
    msg = (
        f"☀️ *Good morning Brian!*\n"
        f"_{date_str}_\n\n"
        f"{goal_block}"
        f"Here are your activities for today:\n\n"
        f"{build_activity_list(checked)}\n\n"
        f"Reply with the numbers of activities you complete during the day.\n"
        f"e.g. `1 3 5` to mark those as done. Log a resource with `/resource <link or note>`.\n\n"
        f"You've got this 💪"
    )
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )


async def send_evening_summary(app: Application):
    checked = get_today_checked()
    resources = get_today_resources()
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

    resources_block = ""
    if resources:
        resources_list = "\n".join(f"📎 {r}" for r in resources)
        resources_block = f"*Resources used today:*\n{resources_list}\n\n"

    msg = (
        f"🌙 *Day-End Check-in*\n\n"
        f"{build_activity_list(checked)}\n\n"
        f"*Score: {done}/{total} ({pct}%)*\n\n"
        f"{resources_block}"
        f"{verdict}\n\n"
        f"_Logging to Notion..._"
    )
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )

    goal_data = load_goal_data()
    active_goal = goal_data.get("active")
    await log_to_notion(date_str, checked, pct, resources, active_goal["text"] if active_goal else None)

    logs = await fetch_recent_logs(90)
    current, longest = compute_streaks(logs)
    streak_msg = f"🔥 Current streak: {current} day(s)"
    if current > 0 and current == longest:
        streak_msg += " — that's your best ever!"

    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"✅ Logged to Notion.\n{streak_msg}\n\nSee you tomorrow at 6 AM!",
    )

    # Goal deadline check — nudge Brian to resolve it before a new one can start
    if active_goal:
        day_number, duration_days, deadline_reached = goal_progress(
            active_goal)
        if deadline_reached:
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    f"🎯 *Goal deadline reached* (Day {day_number}/{duration_days})\n"
                    f"\"{active_goal['text']}\"\n\n"
                    f"Did you achieve it?\n"
                    f"/goaldone — yes, mark it achieved\n"
                    f"/goalfail — not this time\n"
                    f"/goalextend <days> — keep going a bit longer\n\n"
                    f"A new goal can't start until this one is resolved."
                ),
                parse_mode="Markdown"
            )

    # Reset for next day
    daily_state[date_str] = set()
    daily_resources[date_str] = []


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
        "/resource <note> — log a resource you used today\n"
        "/streak — see your current and best streak\n"
        "/summary — trigger the day-end check now\n"
        "/morning — trigger morning message now\n"
        "/digest — trigger weekly digest now\n"
        "/quote — get a random motivational quote\n"
        "/article <topic> — find a real, current article on a topic\n"
        "/learn <topic> — study-buddy explanation of a Python/FastAPI concept\n"
        "/goal <text> | <days> — set a goal with a deadline (default 7 days)\n"
        "/goalstatus — see your current goal's progress\n"
        "/goaldone — mark the active goal achieved\n"
        "/goalfail — mark the active goal not achieved\n"
        "/goalextend <days> — keep the active goal going longer\n"
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
    daily_resources[today_key()] = []
    await update.message.reply_text("🔄 Today's progress and resources reset.")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_evening_summary(context.application)


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_morning_message(context.application)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_weekly_digest(context.application)


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_quote(context.application)


async def cmd_article(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text(
            "Tell me a topic, e.g. `/article python async programming`",
            parse_mode="Markdown"
        )
        return
    await update.message.chat.send_action("typing")
    answer = await ask_ai_with_search(f"Find me a good, current article about: {topic}")
    await update.message.reply_text(answer)


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logs = await fetch_recent_logs(90)
    current, longest = compute_streaks(logs)
    msg = (
        f"🔥 *Current streak:* {current} day(s)\n"
        f"🏅 *Longest streak:* {longest} day(s)\n\n"
        f"_(Counting days at {STREAK_THRESHOLD}%+ over the last 90 days)_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_resource(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Tell me what you used, e.g. `/resource FastAPI docs - dependency injection`",
            parse_mode="Markdown"
        )
        return
    add_today_resource(text)
    count = len(get_today_resources())
    await update.message.reply_text(f"📎 Logged. {count} resource(s) noted for today.")


async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Set a goal like:\n`/goal Build a FastAPI auth system | 7`\n"
            "(the number after `|` is the deadline in days, default 7)",
            parse_mode="Markdown"
        )
        return

    goal_data = load_goal_data()
    if goal_data.get("active"):
        active = goal_data["active"]
        day_number, duration_days, _ = goal_progress(active)
        await update.message.reply_text(
            f"You already have an active goal: \"{active['text']}\" (Day {day_number}/{duration_days}).\n"
            f"Resolve it first with /goaldone, /goalfail, or /goalextend <days>.",
        )
        return

    parts = text.split("|")
    goal_text = parts[0].strip()
    try:
        duration_days = int(parts[1].strip()) if len(
            parts) > 1 else DEFAULT_GOAL_DAYS
    except ValueError:
        duration_days = DEFAULT_GOAL_DAYS

    goal_data["active"] = {
        "text": goal_text,
        "start_date": today_key(),
        "duration_days": duration_days,
    }
    save_goal_data(goal_data)
    await update.message.reply_text(
        f"🎯 New goal set: \"{goal_text}\"\n"
        f"Deadline: {duration_days} day(s) from today. I'll check in with you along the way!"
    )


async def cmd_goalstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal_data = load_goal_data()
    active = goal_data.get("active")
    if not active:
        await update.message.reply_text(
            f"No active goal right now.\n"
            f"✅ Achieved: {goal_data.get('achieved_count', 0)} | ❌ Not achieved: {goal_data.get('failed_count', 0)}\n\n"
            f"Set one with `/goal <what you want to achieve> | <days>`",
            parse_mode="Markdown"
        )
        return
    await update.message.reply_text(format_goal_line(active), parse_mode="Markdown")


async def cmd_goaldone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal_data = load_goal_data()
    active = goal_data.get("active")
    if not active:
        await update.message.reply_text("No active goal to resolve. Set one with /goal.")
        return
    goal_data["achieved_count"] = goal_data.get("achieved_count", 0) + 1
    goal_data["active"] = None
    save_goal_data(goal_data)
    await update.message.reply_text(
        f"🏆 Nailed it: \"{active['text']}\"! That's {goal_data['achieved_count']} goal(s) achieved.\n"
        f"Set your next one with /goal whenever you're ready."
    )


async def cmd_goalfail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal_data = load_goal_data()
    active = goal_data.get("active")
    if not active:
        await update.message.reply_text("No active goal to resolve. Set one with /goal.")
        return
    goal_data["failed_count"] = goal_data.get("failed_count", 0) + 1
    goal_data["active"] = None
    save_goal_data(goal_data)
    await update.message.reply_text(
        f"Not this time on \"{active['text']}\" — that's okay, it happens.\n"
        f"Set a new goal (maybe smaller in scope) with /goal whenever you're ready."
    )


async def cmd_goalextend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal_data = load_goal_data()
    active = goal_data.get("active")
    if not active:
        await update.message.reply_text("No active goal to extend. Set one with /goal.")
        return
    args_text = " ".join(context.args).strip()
    try:
        duration_days = int(args_text) if args_text else DEFAULT_GOAL_DAYS
    except ValueError:
        duration_days = DEFAULT_GOAL_DAYS

    active["start_date"] = today_key()
    active["duration_days"] = duration_days
    save_goal_data(goal_data)
    await update.message.reply_text(
        f"🔁 Extended: \"{active['text']}\" — new deadline in {duration_days} day(s). You've got this."
    )


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text(
            "Ask me to explain something, e.g. `/learn dependency injection in FastAPI`",
            parse_mode="Markdown"
        )
        return
    await update.message.chat.send_action("typing")
    answer = await ask_ai_learn(topic)
    await update.message.reply_text(answer)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Ask me something, e.g. `/ask how did I do this week?`", parse_mode="Markdown")
        return
    await answer_question(update, question)


async def answer_question(update: Update, question: str):
    """
    Plain-text questions and /ask always go through the regular (non-search)
    Gemini call. Real web search is reserved for the explicit /article command,
    to conserve the much stricter Google Search grounding quota.
    """
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
        await answer_question(update, text)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("done",    cmd_done))
    app.add_handler(CommandHandler("reset",   cmd_reset))
    app.add_handler(CommandHandler("resource", cmd_resource))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("digest",  cmd_digest))
    app.add_handler(CommandHandler("quote",   cmd_quote))
    app.add_handler(CommandHandler("article", cmd_article))
    app.add_handler(CommandHandler("learn",   cmd_learn))
    app.add_handler(CommandHandler("streak",  cmd_streak))
    app.add_handler(CommandHandler("goal",       cmd_goal))
    app.add_handler(CommandHandler("goalstatus", cmd_goalstatus))
    app.add_handler(CommandHandler("goaldone",   cmd_goaldone))
    app.add_handler(CommandHandler("goalfail",   cmd_goalfail))
    app.add_handler(CommandHandler("goalextend", cmd_goalextend))
    app.add_handler(CommandHandler("ask",     cmd_ask))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message))

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
        CronTrigger(hour=23, minute=59, timezone=NAIROBI_TZ),
        args=[app],
        id="day_end"
    )
    scheduler.add_job(
        send_weekly_digest,
        CronTrigger(day_of_week="sun", hour=21,
                    minute=30, timezone=NAIROBI_TZ),
        args=[app],
        id="weekly_digest"
    )
    QUOTE_TIMES = [
        (7, 0), (12, 0), (16, 30), (20, 0),
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
