# push-ups-bot

Chat logs number-only messages as push-ups. Moscow timezone.

- Midnight: yesterday totals vs weekday goal, DeepSeek quote
- 10:00: today's goal + quote
- `/count` — regen today
- `/quote [0|1|2]` — quote only

`.env`: `TELEGRAM_TOKEN`, `DEEPSEEK_API_KEY`, `CHAT_ID`. Tallies persist in `data/data.json`.
