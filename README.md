# Concord

A **collective daily-vote trading club**. Members each connect *their own*
Robinhood account. Every trading day they search the S&P 500 to pick the day's
buys; the top-20 consensus basket executes **on each member's own account**,
sized from the **dollar stake** that member has committed to the club — a slice
that rides its own P&L rather than a fixed slice of buying power (see
`ARCHITECTURE.md` §3 "Sizing"). Inspired by [DeepMind's Concordia](https://github.com/google-deepmind/concordia)
collective-decision idea, grounded on Robinhood's Agentic Trading API.

```
concordia/
├── ARCHITECTURE.md   ← the canonical design (read this first)
├── RUNBOOK.md        ← local dev, deploy, member onboarding, going live
├── backend/          ← FastAPI + SQLite + Robinhood OAuth/MCP + executor
├── web/              ← Next.js member app (landing, wizard, voting, account)
├── infra/            ← systemd units, nginx vhost, host setup, MIGRATION.md
└── scripts/          ← dev.sh (local sandbox), deploy.sh (ship to production + demo)
```

## Getting started (2 minutes, zero risk)

```bash
git clone https://github.com/rohanjoglekar/concordia.git && cd concordia
./scripts/dev.sh
```

Opens the whole app at **http://localhost:3000** against a **fake Robinhood** —
its own throwaway database, no real money, no brokerage calls. Sign up with
invite code `demo`; the app walks the real onboarding wizard, and the 6-digit
verification codes print in your terminal as `DEMO-MAIL` instead of being
emailed. First run installs the Python venv and npm deps automatically.

## Deploying to production

`voteconcordia.com` runs on its own box (see `infra/MIGRATION.md`). Once your
SSH key is on it:

```bash
./scripts/deploy.sh              # web + backend, restart both
./scripts/deploy.sh --web        # web only
./scripts/deploy.sh --no-demo    # production only
```

The public demo (`demo.voteconcordia.com`) follows production: once the live
deploy has finished and passed its checks, the same commit goes to the demo. A
demo failure is reported but never fails the production deploy. To ship the demo
on its own, run `./scripts/deploy-demo.sh`.

It refuses to run on a dirty tree or unpushed commits — what's live is always a
commit that exists on GitHub — and it never syncs `.env` or `concord.db`, so a
deploy can't clobber secrets or member data.

> **This box holds real money.** Members' encrypted Robinhood tokens live in
> `/opt/concord/concord.db`, and `/opt/concord/.env` holds the key that decrypts
> them. Trading is gated behind `EXECUTION_MODE=live` **plus** a human arming
> step — never automate arming.

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
  (open 18:00 / close 09:15 / execute 10:15 — which *days* those act on is set by
  `CYCLE_CADENCE`, see `RUNBOOK.md` §9), nginx vhost for the public OAuth
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
