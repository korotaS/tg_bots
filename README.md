# tg_bots

Small Telegram bots. Each folder is its own app.

Copy `.env.example` to `.env`, fill `TELEGRAM_TOKEN`, `DEEPSEEK_API_KEY`, `CHAT_ID`. Then:

```bash
cd <bot-dir>
uv sync
uv run python bot.py
```

- [push-ups-bot](push-ups-bot) — daily push-up tally
- [football-matches-bot](football-matches-bot) — daily UEFA match digest

Push to `main` rebuilds changed bots on the VPS (`/opt/tg_bots`). `.env` files live only on the host, not in git.
