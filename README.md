# SethMentionz

Sister bot to SethBetz. Scans Polymarket "mention" markets (speeches, tweets, interviews, posts) resolving within the next 12 hours, scores them with a blended LLM + signal-momentum confidence engine, and fires a Telegram alert with a one-tap approval button when a market clears 5–10% edge at ≥94% confidence.

## Stack

- Python 3.11+, APScheduler
- Supabase / Postgres
- Polymarket Gamma API + CLOB API
- Claude (Anthropic) for LLM leg
- Telegram Bot API for alerts & approval
- PM2 on Railway VPS

## Setup

```bash
cp .env.example .env
# fill in all values in .env

pip install -r requirements.txt

python main.py          # run directly
# or
pm2 start ecosystem.config.js
```

## Pipeline

```
scanner  →  confidence  →  edge  →  telegram alert  →  approval  →  execute
  │               │            │
  │         LLM leg +     order book
  │         signal leg    depth check
  │
  └─ Gamma API → filter by resolution window → mention-pattern match → Supabase
```

| Phase | Module | What it does |
|-------|--------|--------------|
| 0 | `main.py` | Scaffold, Supabase connect, Telegram "online" ping, APScheduler loop |
| 1 | `scanner/` | Poll Gamma API, filter mention markets, upsert `mention_markets` |
| 2 | `confidence/` | LLM leg (Claude) + signal leg (NewsAPI), blend, write `mention_signals` |
| 3 | `edge/` | Order-book edge calc, qualify opportunities, write `mention_opportunities` |
| 4 | `telegram/` | Alert on qualifying rows, inline [Approve/Skip] buttons, dry-run log |
| 5 | `telegram/` | Wire real CLOB order placement into [Approve] |
| 6 | all | PM2 deploy, resolution tracker, calibration report, error alerting |

## Environment variables

See `.env.example` for the full list with comments.

## Logs

PM2 writes to `./logs/out.log` and `./logs/error.log`.
