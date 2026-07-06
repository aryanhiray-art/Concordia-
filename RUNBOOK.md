# Concord — Runbook

Operational guide for the daily-vote trading club. **Real money. Read §3–§5
before you ever set `EXECUTION_MODE=live`.**

## 1. Local development

```bash
# backend (terminal 1)
cd concord/backend
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env        # then fill secrets (see below)
./.venv/bin/python -m app.seed    # seed the 20-stock universe
./.venv/bin/uvicorn app.main:app --reload --port 8780

# frontend (terminal 2)
cd concord/web
npm install
cp .env.local.example .env.local  # NEXT_PUBLIC_CONCORD_API=http://localhost:8780
npm run dev                        # http://localhost:3000
```

Generate the two required secrets for `.env`:
```bash
python -c "from cryptography.fernet import Fernet;print('CONCORD_FERNET_KEY='+Fernet.generate_key().decode())"
python -c "import secrets;print('SESSION_SECRET='+secrets.token_urlsafe(48))"
```

## 2. Production deploy (forge VPS)

The whole thing runs as an **isolated `concord` unix user** under `/opt/concord`,
walled off from the other forge projects by file permissions.

```bash
# on the VPS, as root, with the repo's concord/ available (e.g. /root/forge/concord)
bash /root/forge/concord/infra/setup-host.sh /root/forge/concord
# then edit /opt/concord/.env  → INVITE_CODE, OWNER_EMAIL, API_PUBLIC_URL, WEB_ORIGIN
#   API_PUBLIC_URL = https://concord-api.<domain>   (must match nginx + Robinhood callback)
#   WEB_ORIGIN     = https://<your-vercel-app>.vercel.app
systemctl restart concord-api

# nginx + TLS for the public API (Robinhood callback + Vercel CORS need it)
cp /opt/concord/infra/nginx-concord-api.conf /etc/nginx/sites-available/concord-api
ln -s /etc/nginx/sites-available/concord-api /etc/nginx/sites-enabled/
certbot --nginx -d concord-api.<domain> && systemctl reload nginx
```

**Vercel (voting UI):** import `concord/web`, set `NEXT_PUBLIC_CONCORD_API=https://concord-api.<domain>`, deploy. Put that deployed origin in `/opt/concord/.env` `WEB_ORIGIN` and `systemctl restart concord-api`.

**Robinhood client registration:** the API auto-registers an OAuth client on first
use whose redirect URI is `${API_PUBLIC_URL}/oauth/robinhood/callback`. That URL
must be publicly reachable (it is, via nginx) **before** any member connects.

## 3. Member onboarding

1. Member opens the Vercel app → **Join** with the club invite code + their email.
2. Click **Connect Robinhood** → they log into *their own* Robinhood and approve
   agentic trading. We store only the encrypted OAuth tokens — never a password.
3. Their Robinhood account must be **Agentic-enabled** (`agentic_allowed=true`).
   If the badge says "connected · not agentic", they must enable Robinhood's
   Agentic account, then reconnect.
4. In **Your account**, set buying-power %, daily cap, and tick **Participate**.
   (The daily cap is stored but not yet enforced by the executor — the global
   `MAX_NOTIONAL_PER_MEMBER` ceiling is what actually caps deployment; see §5.)

## 4. Daily flow (automatic, US/Eastern)

| Time | Timer | What happens |
|------|-------|--------------|
| 18:00 | `concord-open-vote` | opens the next trading day's ballot |
| 09:25 | `concord-close-vote` | locks votes, picks the top-N basket |
| 09:31 | `concord-execute` | reviews + (if armed & live) places per-account buys |

All three are holiday/weekend gated in code, so a stray timer fire is a no-op.

## 5. Going live (the safety gate)

Orders are placed **only when both are true**: `EXECUTION_MODE=live` **and** the
master **ARMED** flag is on. Until then every run is review-only (`review_equity_order`
is called, `place_equity_order` is not) and you can inspect the exact plan in the
dashboard / `executions` table.

```
1. Leave EXECUTION_MODE=dry_run for a few days. Watch the owner dashboard:
   each member shows buying_power, budget, per_pick $, and "reviewed" orders.
2. When satisfied: set EXECUTION_MODE=live in /opt/concord/.env; restart api.
   (Still nothing trades — ARMED is off.)
3. Each morning you want to trade for real: owner → Arm.  Disarm anytime.
4. KILL: owner → Kill  (sets halt + disarms). The executor checks the halt flag
   between members, so an in-progress run stops; orders already sent stay sent.
```

Caps that always apply (even live + armed): per-member `max_orders`, and the
global `MAX_NOTIONAL_PER_MEMBER` ceiling (default $2,000 — enforced in
`alloc.rebalance_budget`, which sizes both the executor's per-member budget and
the voting-power stake). Bot influence is capped too: `BOT_POWER_CAP` (per-bot
voting power, default $300) and `BOT_TALLY_SHARE_MAX` (bots' aggregate share of
the tally, default 0.90). Per-member `daily_cap_usd` is **stored in
`member_settings` but not yet enforced** — do not rely on it as a live cap.
Every order has a deterministic `ref_id` (uuid5) so retries never double-fill.

## 6. Manual ops

```bash
# tail logs
journalctl -u concord-api -f
journalctl -u concord-execute -n 100

# run a phase by hand (dry-run execute is safe)
cd /opt/concord/backend
sudo -u concord ./.venv/bin/python jobs/close_vote.py
sudo -u concord ./.venv/bin/python jobs/execute.py     # honours live+armed

# inspect state
sudo -u concord sqlite3 /opt/concord/backend/concord.db \
  "SELECT trade_date,status,picks_json FROM vote_sessions ORDER BY id DESC LIMIT 3;"
```

Owner API also exposes `POST /api/admin/arm`, `/api/admin/kill`,
`/api/admin/run/{open|close|execute}` and `GET /api/admin/overview`.
