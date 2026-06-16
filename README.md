# Concord

A **collective daily-vote trading club**. Members each connect *their own*
Robinhood account. Every trading day they vote on a 20-stock universe to pick the
day's buys; the top-N consensus basket executes **on each member's own account**,
sized as a % of their buying power. Inspired by [DeepMind's Concordia](https://github.com/google-deepmind/concordia)
collective-decision idea, grounded on Robinhood's Agentic Trading API.

```
concord/
├── ARCHITECTURE.md   ← the canonical design (read this first)
├── RUNBOOK.md        ← local dev, deploy, member onboarding, going live
├── backend/          ← FastAPI + SQLite + Robinhood OAuth/MCP + executor (forge VPS)
├── web/              ← Next.js voting UI (Vercel)
└── infra/            ← systemd units, nginx vhost, host setup
```

## What works today (verified)

- **Backend** (`backend/`): member auth + roles, 20-symbol universe, voting,
  top-N consensus tally, per-member sizing, dry-run + live executor with a master
  **ARMED** switch, per-member caps, `review→place` with idempotent `ref_id`,
  kill switch, full audit log. Per-member Robinhood **OAuth** (public PKCE client)
  + encrypted token vault, reusing forge's proven `refresh.py` MCP transport.
- **Frontend** (`web/`): join/login, Connect Robinhood, settings, the live ballot
  with countdown + tally, consensus results + per-account order status, and an
  owner admin strip (mode / arm / kill / manual run). Builds clean, deploys to Vercel.
- **Infra** (`infra/`): isolated `concord` unix user, systemd timers on US/Eastern
  (open 18:00 / close 09:25 / execute 09:31), nginx vhost for the public OAuth
  callback + Vercel CORS.

## Quick start

```bash
# backend
cd backend && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # fill SESSION_SECRET, CONCORD_FERNET_KEY, INVITE_CODE, OWNER_EMAIL
./.venv/bin/python -m app.seed
./.venv/bin/uvicorn app.main:app --reload --port 8780

# frontend
cd ../web && npm install && cp .env.local.example .env.local && npm run dev
```

Tests: `cd backend && ./.venv/bin/python -m pytest tests/ -q`

## Safety

Real orders require **`EXECUTION_MODE=live` AND the ARMED flag**. Default is
`dry_run` + disarmed, which runs the entire pipeline and records exactly what
*would* trade without placing anything. See `RUNBOOK.md` §5.
