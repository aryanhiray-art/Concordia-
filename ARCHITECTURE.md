# Concord — collective daily-vote trading club

> A Concordia-style trading club. Members each connect **their own** Robinhood
> account. Every trading day, members **search the S&P 500 and add up to 20
> picks**. The **top-20 consensus basket** executes **on each member's own
> account independently**, deploying **all of that member's available buying
> power** split evenly across the basket. A single on/off switch governs each
> member's participation (off ⇒ no voting). The owner watches everything from a
> Forge dashboard tab.

This document is the **canonical contract**. Backend, frontend, and infra all
conform to what is written here.

---

## 1. Topology

```
┌────────────────────────────┐         ┌──────────────────────────────────────┐
│  concord/web  (Vercel)     │  HTTPS  │  concord/backend  (forge VPS)         │
│  Next.js voting UI         │ ──────► │  FastAPI · uvicorn · :8780            │
│  - join / login            │  CORS   │  runs as dedicated unix user `concord`│
│  - Connect Robinhood       │         │  - member auth (signed session)       │
│  - daily ballot + tally    │         │  - Robinhood OAuth (per member)       │
│  - results / status        │         │  - encrypted token vault (SQLite)     │
└────────────────────────────┘         │  - voting + consensus                 │
                                        │  - executor (review → place)          │
┌────────────────────────────┐         │  systemd timers:                      │
│ forge-dashboard (owner)    │ ──────► │   open-vote / close-vote / execute    │
│ /concord admin tab         │  API    └───────────────┬──────────────────────┘
└────────────────────────────┘                         │ bearer (per member)
                                                        ▼
                                         Robinhood Agentic Trading MCP
                                         https://agent.robinhood.com/mcp/trading
```

**Isolation (answers "no access to the rest of forge"):** the backend runs as a
dedicated unix user `concord`. Its project dir, `.env` (chmod 600), DB, and
token vault are owned by `concord:concord` mode 700. The other forge projects
(`stock-options-ai`, `kalshi`, …) cannot read the token vault, and `concord`
cannot read their secrets. Members never get shell/file access — they only ever
touch the Vercel web app and the authenticated API.

---

## 2. Robinhood integration (grounded in the existing forge code)

Reuses the mechanics proven in `forge-dashboard/jobs/robinhood_refresh/`.

**Auth — per member, native OAuth (no raw passwords ever stored):**
1. Dynamic client registration once (public client, PKCE S256, auth method
   `none`) at `https://agent.robinhood.com/oauth/trading/register`, registering
   our **public** callback `https://<api-host>/oauth/robinhood/callback`.
2. Each member opens the Robinhood authorize URL (`https://robinhood.com/oauth`)
   with a per-session PKCE challenge + `state`; they log into **their own**
   Robinhood and consent.
3. Robinhood redirects to our callback with `code`; we exchange it at
   `https://api.robinhood.com/oauth2/token/` for that member's
   `access_token` + `refresh_token`.
4. Tokens are **encrypted at rest** (Fernet) and stored per member. Access
   tokens auto-refresh via the `refresh_token` grant (same logic as
   `refresh.py:get_access_token`).

**Execution — the agentic MCP (streamable-HTTP JSON-RPC), reusing the
`McpClient` from `refresh.py`:**
- Read: `get_accounts`, `get_portfolio`, `get_equity_positions`, `get_equity_quotes`
- Trade: `review_equity_order` (simulate + pre-trade alerts), then
  `place_equity_order` (real order)

**Two hard API constraints that shape the flow:**
- The trading account must be **`agentic_allowed=true`** (member enables
  Robinhood's "Agentic" account). We resolve it via `get_accounts`.
- **Dollar-denominated sizing ⇒ `dollar_amount`, which requires `type=market` +
  `market_hours=regular_hours`.** Dollar/fractional orders only fill in regular
  hours. Therefore **execution runs at market open, not pre-open.** Each
  member's per-pick amount = (all buying power − small reserve) ÷ basket size.

---

## 3. Daily lifecycle (US/Eastern)

| Time (ET) | Job | Action |
|-----------|-----|--------|
| 18:00 (prev day) | `open_vote` | open the next trading day's session; ballot = 20-symbol universe |
| 09:25 | `close_vote` | lock votes; tally; select **top-N** consensus picks |
| 09:31 | `execute`  | per opted-in member: size = `bp% / N` per pick; `review` → `place` market buys (regular hours) |

Each job is **market-calendar gated** (skips weekends/holidays). Voting is
"due before market open"; the basket executes at the bell. `N` (default 5) and
the open/close times are config.

---

## 4. Data model (SQLite, `concord.db`, WAL)

```
members(id, email, display_name, role['owner'|'member'], invite_code_used,
        created_at, session_version)

member_settings(member_id PK→members, opted_in[bool], buying_power_pct[float],
                daily_cap_usd[float], max_orders[int], updated_at)

rh_accounts(member_id PK→members, account_number, agentic_allowed[bool],
            enc_tokens[blob],          -- Fernet(access+refresh+client_id+meta)
            connected_at, last_refresh_at, status['connected'|'error'|'revoked'])

universe(symbol PK, name, sort_order, active[bool])

vote_sessions(id, trade_date[YYYY-MM-DD] UNIQUE, status['open'|'closed'|'executed'],
              opened_at, closes_at, closed_at, top_n, picks_json[null until closed])

votes(session_id→vote_sessions, member_id→members, symbol→universe, created_at,
      PRIMARY KEY(session_id, member_id, symbol))   -- one row per picked symbol

executions(id, session_id, member_id, symbol, side, type, dollar_amount,
           ref_id[uuid], status['planned'|'reviewed'|'placed'|'skipped'|'error'],
           review_json, place_json, error, created_at)

audit_log(id, ts, actor, action, target, detail_json)   -- append-only
```

State writes use the atomic tmp+`os.replace` / WAL patterns already used in
`stock-options-ai/trading/thesis_store.py` and `algo-trading/live/ledger.py`.

---

## 5. API surface (FastAPI, prefix `/api`)

**Member auth**
- `POST /api/auth/join`     `{invite_code, email, display_name}` → session cookie
- `POST /api/auth/login`    `{email}` (magic-less for v1: invite-gated club) → session
- `POST /api/auth/logout`
- `GET  /api/me`            → member + settings + rh connection status

**Robinhood connect**
- `GET  /api/oauth/robinhood/start`     → `{authorize_url}` (sets PKCE+state, member-scoped)
- `GET  /oauth/robinhood/callback`      → exchanges code, stores encrypted tokens, redirects to web `/connected`
- `POST /api/oauth/robinhood/disconnect`

**Participation (the one switch)**
- `PUT  /api/me/participate` `{on}` → persists opted_in immediately; off ⇒ voting blocked

**Voting**
- `GET  /api/universe?q=&limit=`     → search the S&P 500 (symbol or company name)
- `GET  /api/vote/today`             → today's session (status, closes_at, my picks, tally)
- `POST /api/vote/today`             `{symbols:[...]}` → replace my picks (≤20; 403 if off; 409 if closed)
- `GET  /api/vote/today/results`     → picks_json + per-member execution status (after execute)

**Admin (owner only)**
- `GET  /api/admin/overview`         → members, connections, today's session, plan/exec
- `POST /api/admin/arm`              `{armed:bool}` → master ARMED switch
- `POST /api/admin/kill`             → cancel/halt today's execution
- `POST /api/admin/run/{open|close|execute}` → manual trigger (dry-run honored)

All non-public routes require a valid signed session. `/oauth/.../callback` is
public (Robinhood redirect). CORS allows the Vercel origin only.

---

## 6. Safety model (real orders — non-negotiable guardrails)

Member chose **live real orders**. Guardrails regardless:
- `EXECUTION_MODE = dry_run | live` (default **dry_run** on first boot). dry_run
  builds + persists the full per-member plan and calls **`review_equity_order`
  only** — never `place_equity_order`.
- Master **`ARMED`** switch (DB flag, default **false**). `live` + `ARMED`
  required for any real order. Surfaced loudly in the dashboard.
- Per-member **`max_orders`** cap, plus a global **`MAX_NOTIONAL_PER_MEMBER`**
  ceiling (config, default $2,000) enforced in `alloc.rebalance_budget` — the
  single sizing function behind both the executor's per-member budget and the
  voting-power snapshot. Per-member **`daily_cap_usd`** is stored in
  `member_settings` but **not yet enforced** (the executor call site does not
  pass it through yet).
- Bot containment: per-bot voting power is clamped to **`BOT_POWER_CAP`**
  (default $300) at the weekly reweight, and the tally scales all bot vote
  contributions down whenever their aggregate share of total weight would
  exceed **`BOT_TALLY_SHARE_MAX`** (default 0.90) — humans always keep a floor
  of the say.
- Every order gets a fresh **`ref_id` UUID** (idempotency); retries reuse it.
- **`review_equity_order` always precedes `place_equity_order`**; if review
  returns blocking alerts (buying power, PDT, halt), the order is `skipped`.
- Full **audit_log** + `executions` rows for every intended/real order.
- **Kill switch** halts the executor mid-run; market-calendar gate prevents
  weekend/holiday runs.

---

## 7. Repo layout

```
concord/
├── ARCHITECTURE.md          ← this file (the contract)
├── README.md  RUNBOOK.md  .env.example  .gitignore
├── backend/
│   ├── requirements.txt
│   ├── app/  (main, config, db, security, calendar_us,
│   │          robinhood/{oauth,client}, routes/*, services/{voting,executor}, seed)
│   ├── jobs/ (open_vote, close_vote, execute)
│   └── tests/
├── web/                     ← Next.js voting app (Vercel)
└── infra/                   ← systemd units, nginx vhost, deploy notes
```
