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

## 7. Backups & disaster recovery

`concord.db` is the **only** store of the Fernet-encrypted per-member Robinhood
OAuth vault, the `executions` ledger, and the club's owned-shares basis. Losing
the disk without a backup is unrecoverable — so `concord-backup.timer` runs
daily.

| Time (ET) | Timer | What happens |
|-----------|-------|--------------|
| 02:30 | `concord-backup` | WAL-safe hot copy → `/opt/concord/backups/`, keeps newest 14, ships off-box if configured |

The job uses SQLite's online backup API (`jobs/backup.py`) — a consistent hot
copy, **never** a `cp` of the live WAL DB. Each copy is `concord-YYYYMMDD-HHMMSS.db`,
mode `0600`, owned by `concord`.

### Two secrets you MUST back up SEPARATELY, out of band

The backup contains the **encrypted** vault but **not** the key that decrypts it.
These live only in `/opt/concord/.env` and are **never** written into the backup
dir (by design):

- **`CONCORD_FERNET_KEY`** — without it, every member's RH token inside any DB
  backup is permanently undecryptable (they'd all have to reconnect Robinhood).
- **`CONCORD_ADMIN_TOKEN`** — the static service token the forge-dashboard uses
  to reach owner endpoints; lose it and the dashboard can't authenticate until
  you mint + redeploy a new one. (`SESSION_SECRET` rotating just logs members
  out — less critical, but back it up too.)

Copy those three values into a password manager / out-of-band secret store. Do
**not** drop them in `/opt/concord/backups/`.

### Off-box shipping (recommended)

Set `CONCORD_BACKUP_DEST` in `/opt/concord/.env` and `systemctl restart` is not
needed (the timer reads env fresh each run):

```bash
# a) an scp/rsync remote (concord user needs an SSH key + known_hosts entry;
#    concord's home is /opt/concord, so the key lives at /opt/concord/.ssh/):
CONCORD_BACKUP_DEST=backupuser@backup-host:/srv/concord-backups/
# b) OR a second mounted volume on this box:
CONCORD_BACKUP_DEST=/mnt/backup-volume/concord/
```

For a **local** volume path, also add it to `ReadWritePaths=` in
`concord-backup.service` (ProtectSystem=strict blocks writes outside
`/opt/concord`). Off-box failures only **warn** — the local backup still
succeeds and the job exits 0.

### Restore

```bash
# 1. stop everything that writes the DB
systemctl stop concord-api.service
systemctl stop 'concord-*.timer'
# 2. move the (possibly corrupt) live DB aside and clear stale WAL/SHM
cd /opt/concord/backend
mv concord.db concord.db.old.$(date +%s) 2>/dev/null || true
rm -f concord.db-wal concord.db-shm
# 3. drop a chosen backup in place (a plain .db — no WAL to replay)
sudo -u concord cp /opt/concord/backups/concord-YYYYMMDD-HHMMSS.db concord.db
chown concord:concord concord.db && chmod 600 concord.db
# 4. CONFIRM /opt/concord/.env still holds the ORIGINAL CONCORD_FERNET_KEY that
#    encrypted this DB (from your out-of-band backup) — else the vault is useless.
# 5. bring it back up
systemctl start concord-api.service
systemctl start 'concord-*.timer'
```

### Manual ops

```bash
# run a backup by hand
cd /opt/concord/backend && sudo -u concord ./.venv/bin/python jobs/backup.py
# list backups
ls -la /opt/concord/backups/
```

## 8. Voting power — turning on v2, and when to tighten it

The score function is selected by `POWER_FORM` inside `POWER_MODEL=blend`:

| | formula |
|---|---|
| `legacy` | `raw = 0.5·capShare + 0.5·accShare·conf` (two channels, normalised separately, added) |
| `merit` | `score = capital^γ · M`, `M = MERIT_MAX_MULTIPLE ^ tanh(accuracy / MERIT_ACCURACY_SCALE)` |

`merit` is one product with one normalisation. That single change is what makes a
departure a pure renormalisation — `share'ᵢ = shareᵢ / (1 − share_leaver)`, one
common factor, so pairwise ratios and rank order survive an exit exactly. Under
`legacy` the two channels inflate by different factors and members who did
nothing get re-ranked (measured on the live roster: up to 24.7% ratio drift).

**`merit` requires `POWER_MODEL=blend`.** The app refuses to boot otherwise —
without that guard `POWER_FORM=merit` would silently keep running `legacy`.

### Deploying is NOT a no-op, even at POWER_FORM=legacy

`POWER_FORM` is branched **only inside `snapshot_power_all`**. The accuracy
pipeline — `capture_cycle_prices`, `score_cycle` — has no form branch, so its
changes run under *both* forms, and accuracy is 50% of the legacy score via
`accShare`. Three of them move live legacy weights on deploy day:

- exit prices now cover the **previous** cycle's symbols, so a member whose
  picks all rotated out is now MEASURED where the old code recorded a
  non-measurement (verified: accuracy 0 → +0.0032, cycles 0 → 1 on one cycle)
- a single cycle's excess is winsorised at `ACCURACY_EXCESS_CAP`
- departed members are no longer scored

All three move accuracy toward *more* correct, which is why they are not gated.
But do not deploy expecting nothing to move. **Rehearse it:** copy prod's
`concord.db`, run `resolve_and_snapshot()` against the copy on the new code, and
diff `member_settings.accuracy`, `cycles_participated` and `voting_power`
against prod before deploying for real.

### Turning it on

**Preview the flip first.** `GET /api/admin/power-preview` computes BOTH forms
against the same live balances and writes nothing, returning each member's
legacy power, merit power, percentage change, rank under each, and the
club-level `biggest_gain_pct` / `biggest_loss_pct` / `rank_changes`. On a
realistic 8-member roster the flip moved members by −32% to +70%, and the
direction is counterintuitive — a large member with a NEGATIVE record can gain,
because legacy's separately-normalised accuracy channel paid out half the pool
independently of capital. A connected member with a record but **zero committed
capital right now** goes to exactly 0 under merit (score = capital × multiplier).
Look at the preview before you flip, not after.

```bash
ssh root@159.195.19.53 "curl -s -H 'Authorization: Bearer $CONCORD_ADMIN_TOKEN' localhost:8780/api/admin/power-preview | head -c 2000"
```

Cold start is exact AT `CAPITAL_GAMMA_BPS=10000` (gamma 1.0, the default and
what the live box runs): with no accuracy anywhere `M == 1.0` and every member's
power equals their committed dollars to the cent. Below gamma 1 the capital term
is `c**gamma`, so cold start stays proportional to stake but is not equal to it. On a club that already has
accuracy it is NOT a no-op — check `/api/admin/panel` for the merit column
first, then:

```bash
ssh root@159.195.19.53 "sed -i 's/^POWER_MODEL=blend/POWER_MODEL=blend\nPOWER_FORM=merit/' /opt/concord/.env && systemctl restart concord-api && grep -E '^POWER_' /opt/concord/.env"
```

Rollback is the same edit back to `legacy`. The transform is read-side only —
nothing is recomputed, no history is rewritten, no accuracy is lost.

### The share ceiling — leave it OFF until ~50 members

`MAX_VOTING_SHARE` caps any one member's share of the vote, water-filling the
excess to everyone below. Shares sum to 1, so a ceiling of `C` is
**unsatisfiable unless `C · N > 1`**, and a `C` only slightly above `1/N` pins
most of the club and quietly collapses it to one-member-one-vote. Simulated
pinned fraction by club size:

| ceiling | N=25 | N=50 | N=100 | N=250 | N=1000 |
|---|---|---|---|---|---|
| 2%  | impossible | impossible | **22% pinned** | 2.9% | 0.1% |
| 5%  | 58% | 14% | 2.7% | 0.3% | 0.0% |
| 10% | 11.6% | **2.6%** | 0.6% | 0.0% | 0.0% |
| *natural top-1 share* | *22.4%* | *16.5%* | *11.3%* | *5.9%* | *2.4%* |

**Schedule.** Set the ceiling at roughly **2× the natural top share**, never
below `2/N`. At today's 24 members leave it off — a 10% cap would pin ~12% of
the club and flatten the largest real contributor rather than bound an outlier.

| club size | `MAX_VOTING_SHARE` |
|---|---|
| under 50 | `0.0` (off) |
| 50+   | `0.10` |
| 150+  | `0.05` |
| 500+  | `0.02` |

```bash
ssh root@159.195.19.53 "printf 'MAX_VOTING_SHARE=0.10\n' >> /opt/concord/.env && systemctl restart concord-api && grep -E '^MAX_VOTING' /opt/concord/.env"
```

`MAX_VOTING_SHARE_MIN_MEMBERS=0` derives the activation threshold as
`ceil(2/C)` (10% → 20, 5% → 40, 2% → 100). Startup refuses an impossible pair.

Only a member actually pinned at the ceiling moves relative to the rest — two
**uncapped** members keep their pairwise ratio through an exit exactly
(measured 6.7e-16). That is the cap doing its job, not a regression.

### Which knob to turn

`MERIT_ACCURACY_SCALE` is the dial. `MERIT_MAX_MULTIPLE` is the *bound*
(`M ∈ [1/MM, MM]`, best:worst ≤ MM²) and should be left at 2.0 — for small
accuracy `tanh(x) ≈ x`, so only the ratio `ln(MM)/scale` matters and the two
knobs are near-degenerate. Raise `scale` to make records count for less.

`CAPITAL_GAMMA_BPS` must stay at 10000 (γ=1.0) unless identity is verified:
below 1.0, splitting one stake across k accounts multiplies its weight by
`k^(1−γ)` — +26% at γ=0.9 with ten accounts, +216% at γ=0.5.

`ACCURACY_INERTIA_REF_N=24` anchors today's club as the reference speed;
accuracy then moves `max(1, sqrt(N/24))` times more slowly as the club grows
(2.0× at 96 members, 4.6× at 500). N is read at scoring time and frozen into
`cycle_scores` — evaluating it at snapshot time would make an exit change N and
break the exit property above.


## 9. Cadence: switching between weekly and daily

`CYCLE_CADENCE` decides what `calendar_us.cycle_phase()` returns:

| value | trading days | phase |
|---|---|---|
| `weekly` | Monday only (or the week's first trading day) | `both` |
| `daily` | every trading day | `both` |

The systemd timers already fire **Mon–Fri** (`concord-close-vote` 09:15,
`concord-execute` 10:15, `concord-open-vote` daily 18:00) — it is `cycle_phase`
returning `None` that makes the other days idle. So the cadence itself is one
env value. **Two things do NOT follow automatically:**

1. **Bot voting.** `concord-bot-vote.timer` was Wed/Sat/Mon, which belonged to
   the weekly cadence where one ballot stayed open all week. Under daily that
   would leave Tue/Thu/Fri deciding a real-money basket on human turnout alone.
   It is now Mon–Fri 05:30 / 07:00 / 08:20 ET (same three-pass retry design,
   all before the 09:15 close). Reinstall it when changing cadence.

2. **`MIN_HUMAN_VOTERS=1` now bites every day.** The club will not place orders
   in a cycle with no human ballot. Weekly, that meant voting once a week;
   daily, it means **someone has to vote every trading day** or that day simply
   does not trade (it logs "human quorum not met (0 < 1)" and stands down). That
   is the intended safety behaviour, not a bug — but it is the difference
   between a missed week and four missed days out of five.

```bash
ssh root@159.195.19.53 "sed -i 's/^CYCLE_CADENCE=.*/CYCLE_CADENCE=daily/' /opt/concord/.env && systemctl restart concord-api && grep '^CYCLE_CADENCE' /opt/concord/.env"
```

Daily also means up to `2 x TOP_N` orders per member per day (sell the exits,
buy the entries). That is why each leg has its own `MAX_ORDERS` budget — see
services/executor.py. Expect real churn: a 20-name basket rotating daily is a
lot of round-trips, and every one crosses a spread.


## 10. What happens when a morning fails

**Rule: if everyone fails to vote, the previous basket holds.** Nothing is sold,
nothing is bought, the existing positions simply stand.

This is enforced by the **thin-basket floor**, not by the quorum. With
`MIN_HUMAN_VOTERS=0` (owner, 2026-09-01) the human-quorum gate no longer stands
the club down, so the floor is now the ONLY thing between a failed morning and a
full liquidation: an empty ballot produces an empty basket, and the sell leg
reads every held name as "dropped from basket". Below `MIN_BASKET_SIZE` picks
the run downgrades to review-only and places no orders at all.

| ballot outcome | what trades |
|---|---|
| 0 picks (every job failed) | **nothing** — previous basket holds |
| 1–4 picks | **nothing** — below the floor |
| 5–20 picks | the basket trades, rotating out everything not in it |

**The gap to be aware of:** the floor is 5 against a `TOP_N` of 20. A *partial*
failure — say two bots vote and produce 6 names — clears the floor and rotates
the whole 20-name portfolio into 6 positions. That is a real concentration event
caused by a cron failure, not by a decision. Raising `MIN_BASKET_SIZE` toward
`TOP_N` narrows it (at the cost of standing down more often); it is a judgement
call about which failure you would rather have, and it is one env value:

```bash
ssh root@159.195.19.53 "sed -i 's/^MIN_BASKET_SIZE=.*/MIN_BASKET_SIZE=12/' /opt/concord/.env && systemctl restart concord-api"
```

(If the key is absent, append it — unset defaults to 5.)
