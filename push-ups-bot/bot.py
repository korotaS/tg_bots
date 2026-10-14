import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, time
from pathlib import Path

import requests
from dotenv import load_dotenv
from pytz import timezone
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
load_dotenv(Path(__file__).resolve().parent / ".env")

TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
CHAT_ID = os.getenv("CHAT_ID")
DATA_FILE = Path(__file__).resolve().parent / "data" / "data.json"
TZ = timezone("Europe/Moscow")

DEFAULT_THRESHOLD = 125
DAY_TO_THRESHOLD = {0: 125, 1: 75, 2: 125, 3: 75, 4: 125, 5: 75, 6: 0}
# ==========================================

data = defaultdict(list)
config = {"threshold": DEFAULT_THRESHOLD}


# ---------- persistence ----------
def load_all():
    global data, config
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            raw = json.load(f)
            data.update(raw.get("data", {}))
            config.update(raw.get("config", {}))


def get_users():
    global data
    users = set(
        item["user"]
        for day_data in data.values()
        for item in day_data
    )
    return sorted(users)


def save_all():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump({"data": data, "config": config}, f)


# ---------- utils ----------
def get_day(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d")


def get_day_of_week():
    return datetime.now().weekday()


# ---------- telegram handler ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # print(update.effective_chat.id, update.message.text)

    msg = update.message
    if not msg:
        return

    text = (msg.text or msg.caption or "").strip()
    # only pure number messages count, e.g. "20" — not "i have done 20"
    if not re.fullmatch(r"\d+", text):
        return

    day = get_day(msg.date)
    user = msg.from_user.username or msg.from_user.first_name

    data[day].append({
        "user": user,
        "text": text,
    })

    save_all()


# ---------- LLM (quotes only) ----------
def call_deepseek(prompt):
    r = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def get_quote(quote_type: int):
    match quote_type:
        case 0:
            prompt = "Напиши максимально всратую пацанскую цитату про человека, который провинился и перестал быть нормальным пацаном. 2-3 предложения максимум. Цитата:"
        case 1:
            prompt = "Напиши одну максимально всратую пацанскую цитату про успешный успех. 2-3 предложения максимум. Цитата:"
        case 2:
            prompt = "Напиши всратую пацанскую мотивационную цитату, чтобы после нее захотелось заниматься спортом и идти к успеху. Цитата:"
    try:
        return call_deepseek(prompt).strip().replace('"', '')
    except Exception as e:
        print(e)
        return ""


# ---------- core aggregation ----------
def aggregate(messages):
    totals = defaultdict(int)
    for m in messages:
        totals[m["user"]] += int(m["text"])
    for user in get_users():
        totals.setdefault(user, 0)
    return [{"user": u, "total": t} for u, t in sorted(totals.items())]


async def process_day(app, day: str):
    messages = data.get(day, [])
    results = aggregate(messages)
    if not results:
        print("NO RESULTS")
        return

    threshold = DAY_TO_THRESHOLD[get_day_of_week()]
    lines = [f"🏁 Daily results ({day}), goal: {threshold}\n"]

    all_passed = True

    for r in results:
        user = r["user"]
        total = r["total"]

        status = "✅" if total >= threshold else "❌"
        if total < threshold:
            all_passed = False

        lines.append(f"{user} — {total} {status}")

    quote = get_quote(int(all_passed))
    if quote:
        lines.append("\n" + quote)

    await app.bot.send_message(chat_id=int(CHAT_ID), text="\n".join(lines))


# ---------- commands ----------
async def quote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quote = get_quote(int(context.args[0]) if context.args else 1)
    await context.application.bot.send_message(chat_id=int(CHAT_ID), text=quote)


async def count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    await process_day(context.application, day)


# ---------- daily job ----------
async def daily_job(app):
    day = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    await process_day(app, day)

    # clear after processing
    # data.pop(day, None)
    save_all()


async def daily_start_job(app):
    threshold = DAY_TO_THRESHOLD[get_day_of_week()]
    lines = [f"Отжиманий нужно сегодня сделать: {threshold}\n"]
    quote = get_quote(2)
    if quote:
        lines.append("\n" + quote)
    await app.bot.send_message(chat_id=int(CHAT_ID), text="\n".join(lines))


# ---------- scheduler ----------
def setup_scheduler(app):
    async def job(context):
        await daily_job(app)

    app.job_queue.run_daily(
        job,
        time=time(hour=0, minute=0, tzinfo=TZ)
    )


def setup_scheduler_start(app):
    async def job(context):
        await daily_start_job(app)

    app.job_queue.run_daily(
        job,
        time=time(hour=10, minute=0, tzinfo=TZ)
    )


# ---------- main ----------
def main():
    load_all()

    # proxy_url = 'https://oai-proxy-hzkr3iwwhq-ew.a.run.app/v1'

    app = ApplicationBuilder().token(TOKEN).build()

    # app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(CommandHandler("quote", quote_cmd))
    app.add_handler(CommandHandler("count", count_cmd))
    app.add_handler(MessageHandler(filters.ALL, handle_message))

    setup_scheduler(app)
    setup_scheduler_start(app)

    print("Bot started...")
    app.run_polling()


# kek 
if __name__ == "__main__":
    main()
