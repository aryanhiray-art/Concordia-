# Concord — collective daily-vote trading club

> A Concordia-style trading club. Members each connect **their own** Robinhood
> account. Every trading day, members **search the S&P 500 and add up to 20
> picks**. The **top-20 consensus basket** (`TOP_N=20`) executes **on each
> member's own account independently**, deploying that member's **committed
> dollar stake** — which rides its own P&L cycle to cycle — water-filled across
> the basket. A single on/off switch governs each member's participation
> (off ⇒ no voting). The owner watches everything from a Forge dashboard tab.

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
  hours. Therefore **execution runs after the bell, not pre-open** (10:15 ET).
  Each member's per-name amount is a water-filled dollar target derived from
  their committed stake — see "Sizing" in §3, not a plain division of buying
  power.

---

## 3. Daily lifecycle (US/Eastern)

| Time (ET) | Job | Action |
|-----------|-----|--------|
| 18:00 (prev day) | `open_vote` | open the next trading day's session; ballot = the S&P 500 universe |
| 09:15 | `close_vote` | lock votes; tally; select **top-N** consensus picks (`TOP_N`, **20** live) |
| 10:15 | `execute`  | per opted-in member: derive the cycle budget, water-fill per-name dollar targets, `review` → `place` market sells + buys (regular hours) |

Each job is **market-calendar gated** (skips weekends/holidays), and on top of
that `calendar_us.cycle_phase()` decides whether the date is a cycle day at all
— on a non-cycle day it returns `None` and the job idles. `CYCLE_CADENCE`
(`daily` live) drives that. The unit times above are fixed; the cadence chooses
the days.

### Sizing — how a member's dollars reach the basket

**This is no longer "`bp% / N` per pick".** A member commits a **dollar stake**
(a percent setting is just a way of specifying that dollar figure; it converts to
dollars on the first deployed cycle). Each cycle, for a compounding member
(`services/executor.py`, `services/alloc.py`):

1. **`alloc.compound_budget`** derives the cycle's target. The slice **rides its
   own P&L in both directions** from the committed base — grown to $110 → deploy
   $110, fallen to $90 → deploy $90, never topped up from fresh cash — adjusted
   by any stake *change* (`budget = club_value + (alloc_value − alloc_applied)`)
   and by **`slice_carry`**, the club dollars the last cycle meant to deploy but
   could not. An empty slice (< $1 held) bootstraps from the full stake. The
   function itself clamps to total account value and `MAX_NOTIONAL_PER_MEMBER`.
   *(A pinned, non-compounding stake uses `alloc.rebalance_budget` instead: the
   committed figure outright, same hard caps. It never rides carry.)*
2. **Clamped to deployable capacity, then to the daily cap** — in that order, in
   the executor, not inside the budget function (which is called with
   `daily_cap_usd=None` precisely so these two stay distinguishable).
   `capacity = (club slice + slice_carry) + deployable(buying_power)`, where
   `deployable` subtracts `CASH_BUFFER_PCT`. A commitment above capacity can
   never fully deploy, so the budget is capped at it — and a *persistently*
   over-committed stake is written down by the over-commitment clamp (see the
   glossary below). The member's `daily_cap_usd` is applied last, to the budget
   only; it never feeds the clamp, because a daily cap limits deployment rather
   than restating what the member has committed.
3. **`n_eff` caps how many names the budget can hold.**
   `n_eff = max(1, min(len(picks), int(budget // 1.00)))` — the broker's $1
   order minimum means a budget of $7 can hold at most 7 names, so a small stake
   holds the **top** names only. Rank hysteresis (band `H=4`) lets a held name
   keep its slot while it stays within `n_eff+H`, so daily re-ranking does not
   force a full sell/re-buy round trip every cycle.
4. **`alloc.basket_targets` water-fills the per-name dollar targets** so they
   **sum to the budget**. A name that has grown past its fair share but by less
   than $1 (the minimum placeable trim) keeps its value and the rest absorb the
   difference — winners ride, entries shrink, the total adds up. A name more than
   $1 over fair share is targeted *at* fair share and the sell leg trims it.

So the per-name amount is a **water-filled dollar target against the cycle
budget**, not an equal `budget / N` slice, and `N` is `n_eff`, not a fixed 5.

### Operator vocabulary

Terms you will meet in run reports, the admin panel and `member_settings`.

| term | what it is |
|---|---|
| **`slice_carry`** | Club dollars **this** cycle's buy leg intended to deploy but could not (a buy deferred for settled cash, an order-cap skip). Column on `member_settings`. **Written, never added to** — the buy-leg write-back recomputes it from scratch each cycle, so a re-run of the same session cannot accumulate it. It is added back into the next `compound_budget`, so undeployed club money cannot silently leak out of the slice. Can go **negative**: a crumb-repair's $1 top-ups are club money fronted from account cash, and netting that spend back out keeps the committed stake flat. The sum still floors at zero. |
| **`carry_pending`** | Club dollars an **earlier** cycle is now *proven* (at the broker) never to have deployed — an errored order that `reconcile` has since verified absent or dead. These arrive **asynchronously**, so they cannot live in `slice_carry`, which the next write-back overwrites wholesale; they accumulate here instead. **`reconcile` is the only writer.** |
| **the drain** | The buy-leg write-back reads `carry_pending`, adds it to the cycle's own undeployed figure, writes the sum to `slice_carry`, and zeroes `carry_pending` — **all in one transaction**, so a credit landing mid-write cannot be zeroed uncounted. The drain is remembered per session (`carry_drained:{sid}:{mid}`), so re-running a cycle rebuilds the same figure rather than destroying it. Net effect: a dollar is credited once and deployed once. A **pinned** (non-compounding) member's buy leg clears both columns instead — a pinned budget is the committed figure outright and has no cycle to fund. |
| **`n_eff`** | The effective basket size for this member: `max(1, min(len(picks), int(budget // 1.00)))`. The broker's $1 minimum order means a small stake simply cannot hold 20 names, so it holds the top `n_eff` (with `H=4` rank hysteresis on names already held). |
| **`commit_clamped`** | The **over-commitment auto-clamp**. When a member's committed figure sits above their deployable capacity, the excess would defer and carry every cycle forever. The executor writes the commitment *down* to the capacity figure — but **only after the 2nd consecutive materially-clamped CYCLE** (tracked by stored session id, so same-day re-runs and a split cadence each count once). One low reading — a transient buying-power dip or a bad read — must never shrink someone's commitment. Appears in the run summary as `{"from": …, "to": …}` and triggers a member notification. Only a **compounding** member's commitment is ever rewritten. The daily cap is deliberately *not* what gets honored here: a daily cap limits deployment, it never rewrites the commitment. |
| **session status `'lapsed'`** | A terminal state, not an error. A `'closed'` session older than `STALE_BASKET_DAYS` (**5**) can never trade — the buy leg refuses it — so `_retire_stale_sessions` flips it to `'lapsed'` and its leftover `'error'` execution rows to `'abandoned'` (error text preserved). This unfreezes its voters and stops it feeding error rows to every reconcile pass. **It deliberately does NOT return an abandoned buy's dollars to the slice:** only `reconcile` reads the broker, so only `reconcile` can prove an order never landed; crediting one that *did* land would put the same money in the slice twice. Those dollars stay out — the conservative direction. The session currently executing is never lapsed. |

---

## 4. Data model (SQLite, `concord.db`, WAL)

```
members(id, email, display_name, role['owner'|'member'], invite_code_used,
        created_at, session_version)

member_settings(member_id PK→members, opted_in[bool], buying_power_pct[float],
                daily_cap_usd[float], max_orders[int], updated_at,
                -- the stake (see §3 "Sizing")
                alloc_type['percent'|'dollar'], alloc_value[float],
                compound[bool], alloc_applied[float|null],
                slice_carry[float], carry_pending[float],
                alloc_intent_type, alloc_intent_value,
                -- voting power / accuracy (see RUNBOOK §8)
                voting_power, voting_power_share, last_capital_usd,
                accuracy, accuracy_short, accuracy_long, cycles_participated,
                email_reminders[bool])

rh_accounts(member_id PK→members, account_number, agentic_allowed[bool],
            enc_tokens[blob],          -- Fernet(access+refresh+client_id+meta)
            connected_at, last_refresh_at, status['connected'|'error'|'revoked'])

universe(symbol PK, name, sort_order, active[bool])

vote_sessions(id, trade_date[YYYY-MM-DD] UNIQUE,
              status['open'|'closed'|'executed'|'lapsed'],   -- 'lapsed': see §3 glossary
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
- Per-member **`max_orders`** cap (each leg has its own budget), plus a global
  **`MAX_NOTIONAL_PER_MEMBER`** ceiling (config, default $2,000) enforced inside
  **both** budget functions — `alloc.rebalance_budget` (pinned stakes, and the
  voting-power snapshot) and `alloc.compound_budget` (compounding stakes).
  Per-member **`daily_cap_usd`** **is enforced**: the executor selects it in
  `_opted_members()` and clamps the budget with it (0/NULL = no cap). It limits
  *deployment* only — it never rewrites the member's committed stake.
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
