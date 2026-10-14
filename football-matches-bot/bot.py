import json
import os
import re
from datetime import datetime, time, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from pytz import timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ================= CONFIG =================
load_dotenv(Path(__file__).resolve().parent / ".env")

TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
CHAT_ID = os.getenv("CHAT_ID")
TZ = timezone("Europe/Berlin")

COMPETITIONS = frozenset({"UCL", "UEL", "UECL", "UNL", "EUROQ", "WCQ"})
PRIORITY_COUNTRIES = frozenset({"UA", "RS"})
PRIORITY_NAMES = ("ukraine", "serbia")
KICKOFF_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TG_LIMIT = 4096
# ==========================================


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def yesterday_str():
    return (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


def _content_blocks(payload):
    content = payload.get("content")
    if isinstance(content, list):
        return content
    return []


def _has_search_sources(payload):
    for block in _content_blocks(payload):
        if block.get("type") != "web_search_tool_result":
            continue
        items = block.get("content")
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("type") == "web_search_result" and item.get("url"):
                return True
    return False


def _flatten_search_text(payload):
    lines = []
    for block in _content_blocks(payload):
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            lines.append(block["text"])
            continue
        if btype != "web_search_tool_result":
            continue
        items = block.get("content")
        if not isinstance(items, list):
            continue
        for item in items:
            if item.get("type") != "web_search_result":
                continue
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("snippet") or item.get("page_age") or ""
            lines.append(f"{title}\n{url}\n{snippet}".strip())
    return "\n\n".join(lines).strip()


def call_deepseek_search(day: str, kind: str):
    if kind == "results":
        prompt = (
            f"Search the web for men's football FULL-TIME SCORES from matches that "
            f"kicked off on {day} (Europe/Berlin calendar date). "
            "Include only: UEFA Champions League (UCL), UEFA Europa League (UEL), "
            "UEFA Europa Conference League (UECL) including league phase, knockouts "
            "and qualifying; UEFA Nations League (UNL); UEFA European Championship "
            "qualifiers (EUROQ); UEFA World Cup qualifiers only, not other confederations (WCQ). "
            "For each match report: competition, stage, whether it is first or second "
            "leg of a two-legged tie (or not two-legged), kickoff CET, home team, away team, "
            "home and away countries, previous/first-leg score if this is a second leg, "
            "and the full-time score (home-away). "
            "Prefer official UEFA, FIFA, and major sports-wire sources. "
            "If there are no such matches, say so explicitly."
        )
    else:
        prompt = (
            f"Search the web for men's football fixtures kicking off on {day} "
            f"(Europe/Berlin calendar date, times in CET/CEST). "
            "Include only: UEFA Champions League (UCL), UEFA Europa League (UEL), "
            "UEFA Europa Conference League (UECL) including league phase, knockouts "
            "and qualifying; UEFA Nations League (UNL); UEFA European Championship "
            "qualifiers (EUROQ); UEFA World Cup qualifiers only, not other confederations (WCQ). "
            "For each match report: competition, stage, whether it is first or second "
            "leg of a two-legged tie (or not two-legged), kickoff CET, home team, away team, "
            "home and away countries, and previous/first-leg score if this is a second leg. "
            "Prefer official UEFA, FIFA, and major sports-wire sources. "
            "If there are no such matches, say so explicitly."
        )
    r = requests.post(
        "https://api.deepseek.com/anthropic/v1/messages",
        headers={
            "x-api-key": DEEPSEEK_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-flash",
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 8,
                }
            ],
        },
        timeout=120,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("type") == "error" or payload.get("error"):
        raise RuntimeError("DeepSeek search error")
    if not _has_search_sources(payload):
        raise RuntimeError("search returned nothing")
    text = _flatten_search_text(payload)
    if not text:
        raise RuntimeError("search returned nothing")
    return text


def call_deepseek_json(day: str, search_text: str, kind: str):
    system_prompt = """Extract football matches into json.
Use only facts from the provided search notes. Do not invent matches.
Ignore women's matches and any competition not in the list.
If a field is unknown, use an empty string.

EXAMPLE JSON OUTPUT:
{
  "matches": [
    {
      "date": "2026-10-14",
      "competition": "UCL",
      "stage": "Round of 16",
      "leg": "2",
      "kickoff_cet": "21:00",
      "home": "Shakhtar",
      "away": "Arsenal",
      "home_country": "UA",
      "away_country": "GB",
      "previous_score": "Shakhtar 1-0 Brugge",
      "score": "2-1"
    }
  ]
}

competition must be one of: UCL, UEL, UECL, UNL, EUROQ, WCQ.
leg is "1", "2", or "".
kickoff_cet is HH:MM 24-hour CET/CEST.
previous_score is empty unless this is a second leg; then include first-leg home and away names and the score.
score is the full-time result as home-away (e.g. "2-1"). Empty for fixtures that have not been played.
date is YYYY-MM-DD of kickoff in Europe/Berlin.
"""
    if kind == "results":
        extra = "This is a RESULTS extract. Fill score with the full-time score."
    else:
        extra = "This is a FIXTURES extract. Leave score empty."
    user_prompt = (
        f"Target date: {day}. {extra} Return json with key matches.\n\n"
        f"SEARCH NOTES:\n{search_text}"
    )
    r = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        },
        timeout=60,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    if not content:
        raise RuntimeError("empty JSON from DeepSeek")
    return json.loads(content)


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def parse_matches(raw, day: str):
    items = raw.get("matches") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        match_date = _clean(item.get("date"))
        if match_date and match_date != day:
            continue
        competition = _clean(item.get("competition")).upper()
        if competition not in COMPETITIONS:
            continue
        kickoff = _clean(item.get("kickoff_cet"))
        if not KICKOFF_RE.match(kickoff):
            continue
        home = _clean(item.get("home"))
        away = _clean(item.get("away"))
        if not home or not away:
            continue
        leg = _clean(item.get("leg"))
        if leg not in {"", "1", "2"}:
            continue
        parsed = {
            "competition": competition,
            "stage": _clean(item.get("stage")) or "—",
            "leg": leg,
            "kickoff_cet": kickoff,
            "home": home,
            "away": away,
            "home_country": _clean(item.get("home_country")).upper(),
            "away_country": _clean(item.get("away_country")).upper(),
            "previous_score": _clean(item.get("previous_score")),
            "score": _clean(item.get("score")),
        }
        key = (
            parsed["kickoff_cet"],
            parsed["competition"],
            parsed["home"].lower(),
            parsed["away"].lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
    out.sort(key=lambda m: (m["kickoff_cet"], m["competition"], m["home"]))
    return out


def is_priority(match):
    if match["home_country"] in PRIORITY_COUNTRIES:
        return True
    if match["away_country"] in PRIORITY_COUNTRIES:
        return True
    blob = f"{match['home']} {match['away']}".lower()
    return any(name in blob for name in PRIORITY_NAMES)


def format_match(match):
    leg = f"leg {match['leg']}" if match["leg"] else "—"
    prev = f"prev {match['previous_score']}" if match["previous_score"] else "prev —"
    meta = (
        f"{match['kickoff_cet']} CET | {match['competition']} | "
        f"{match['stage']} | {leg} | {prev}"
    )
    if match.get("score"):
        teams = f"{match['home']} {match['score']} {match['away']}"
    else:
        teams = f"{match['home']} — {match['away']}"
    return f"{meta}\n{teams}"


def format_digest(matches, day: str, kind: str):
    if kind == "results":
        empty = f"No matches yesterday ({day})."
        title = f"Results ({day})"
    else:
        empty = f"No matches today ({day})."
        title = f"Today ({day})"
    if not matches:
        return empty
    priority = [m for m in matches if is_priority(m)]
    other = [m for m in matches if not is_priority(m)]
    parts = []
    if priority:
        parts.append("Ukraine / Serbia\n\n" + "\n\n".join(format_match(m) for m in priority))
    if other:
        parts.append("Other\n\n" + "\n\n".join(format_match(m) for m in other))
    return title + "\n\n" + "\n\n".join(parts)


def split_telegram(text):
    if len(text) <= TG_LIMIT:
        return [text]
    chunks = []
    current = ""
    for block in text.split("\n\n"):
        piece = block if not current else current + "\n\n" + block
        if len(piece) <= TG_LIMIT:
            current = piece
            continue
        if current:
            chunks.append(current)
        if len(block) <= TG_LIMIT:
            current = block
        else:
            while len(block) > TG_LIMIT:
                chunks.append(block[:TG_LIMIT])
                block = block[TG_LIMIT:]
            current = block
    if current:
        chunks.append(current)
    return chunks


async def send_chat(app, text: str):
    for chunk in split_telegram(text):
        await app.bot.send_message(chat_id=int(CHAT_ID), text=chunk)


def build_digest(day: str, kind: str):
    search_text = call_deepseek_search(day, kind)
    raw = call_deepseek_json(day, search_text, kind)
    matches = parse_matches(raw, day)
    if kind == "fixtures":
        for m in matches:
            m["score"] = ""
    return format_digest(matches, day, kind)


async def send_digest(app, day: str, kind: str):
    try:
        text = build_digest(day, kind)
    except Exception as e:
        print(e)
        label = "yesterday results" if kind == "results" else "today fixtures"
        msg = str(e)
        if "search returned nothing" in msg:
            text = f"Search returned nothing for {label}. Use /matches to retry."
        else:
            text = f"Failed to build {label}. Use /matches to retry."
    await send_chat(app, text)


async def process_day(app):
    await send_digest(app, yesterday_str(), "results")
    await send_digest(app, today_str(), "fixtures")


async def matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_day(context.application)


async def daily_job(app):
    await process_day(app)


def setup_scheduler(app):
    async def job(context):
        await daily_job(app)

    app.job_queue.run_daily(
        job,
        time=time(hour=10, minute=0, tzinfo=TZ),
    )


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("matches", matches_cmd))
    setup_scheduler(app)
    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
