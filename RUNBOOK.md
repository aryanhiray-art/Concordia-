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
4. In **Your account**, set the committed stake (a dollar amount, or a % of
   buying power that converts to dollars on the first deployed cycle), the daily
   cap, and tick **Participate**. Both the daily cap and the global
   `MAX_NOTIONAL_PER_MEMBER` ceiling clamp what the executor deploys; see §5.

## 4. Daily flow (automatic, US/Eastern)

| Time | Timer | What happens |
|------|-------|--------------|
| 18:00 | `concord-open-vote` | opens the next trading day's ballot |
| 09:15 | `concord-close-vote` | locks votes, picks the top-N basket |
| 10:15 | `concord-execute` | reviews + (if armed & live) places per-account sells + buys |

All three are holiday/weekend gated in code, so a stray timer fire is a no-op.

**These times are fixed; WHICH DAYS they act on is not.** The units fire on the
clock above every weekday, but `calendar_us.cycle_phase()` decides whether that
day is a trading cycle at all — on a non-cycle day it returns `None` and the job
stands down. That is what `CYCLE_CADENCE` controls (live: `daily`). See §9
before you reason about "why did nothing run on Tuesday".

**The clock is stated in two places and both must agree.** The systemd
`OnCalendar=` lines are what actually fires; `VOTE_OPEN_AT` / `VOTE_CLOSE_AT` /
`EXECUTE_AT` in `.env` are what the app *believes* the schedule is — the ballot
countdown, the open-vote/close-vote windows, the watchdog's "the run should have
happened by now" cutoff and `/api/admin/schedule` all read the env values, not
the units. Changing a timer without changing the env (or the reverse) desyncs
them silently. Verified in sync on the live box (18:00 / 09:15 / 10:15):

```bash
ssh root@159.195.19.53 'grep -E "^VOTE_OPEN_AT|^VOTE_CLOSE_AT|^EXECUTE_AT" /opt/concord/.env; systemctl list-timers "concord-close-vote.timer" "concord-execute.timer" --all --no-pager'
```

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
global `MAX_NOTIONAL_PER_MEMBER` ceiling, default $2,000. That ceiling is
enforced inside **both** budget functions: `alloc.rebalance_budget` (pinned
stakes, and the voting-power snapshot) and `alloc.compound_budget` (compounding
stakes — what the executor uses for the live roster). Bot influence is capped too:
`BOT_POWER_CAP` (per-bot voting power, default $300) and `BOT_TALLY_SHARE_MAX`
(bots' aggregate share of the tally, default 0.90). Per-member `daily_cap_usd`
**is now enforced**: the executor reads it in `_opted_members()` and clamps the
budget with it (a cap of 0/NULL means no cap). Note it limits *deployment* only
— it never rewrites the member's committed stake. Every order has a
deterministic `ref_id` (uuid5) so retries never double-fill.

### `CASH_BUFFER_PCT` — live box disagrees with the intended value

| | value |
|---|---|
| code default / owner's stated intent (`config.py`) | **0** |
| what `/opt/concord/.env` actually holds | **1** |

`deployable(bp) = bp * (1 - CASH_BUFFER_PCT/100)`. The reserve exists so the
last dollar market order cannot trip a buying-power reject — but at 1% it
silently strands that fraction of every **fully committed** account: a member
whose stake is 100% of their cash has 1% less deployable than their budget, so
the final name defers every cycle (observed live, 08-24). A rejected last order
is recorded and retried; a permanently undeployable slice is not. That is why
the owner set the code default to 0.

**The live box has not been changed.** If you are debugging "one name never
gets bought" or a slice that always carries a few dollars, this is the first
thing to check:

```bash
ssh root@159.195.19.53 'grep "^CASH_BUFFER_PCT" /opt/concord/.env'
```

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

2. **`MIN_HUMAN_VOTERS` bites every day, not once a week.** The knob is the
   human-quorum floor: below it the executor drops to review-only and places no
   real orders, logging "human quorum not met". Weekly, clearing it meant voting
   once a week; daily, it would mean **someone has to vote every trading day** or
   that day simply does not trade — the difference between a missed week and four
   missed days out of five.

   **The live box runs `MIN_HUMAN_VOTERS=0` (owner, 2026-09-01), so this gate is
   currently OFF** and a zero-human-turnout day is no longer stood down by it.
   The thin-basket floor is what protects that day instead — see §10, and read it
   before you consider raising this back above 0.

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

**The partial-failure gap is closed.** An absolute floor does not scale: 5
against a `TOP_N` of 20 stopped an empty ballot but let two bots voting six
names through, rotating a 20-name portfolio into 6 positions — a concentration
event caused by a cron failure rather than a decision. The floor is now

```
basket_floor = max(MIN_BASKET_SIZE, ceil(TOP_N * MIN_BASKET_FRACTION))
```

with `MIN_BASKET_FRACTION=0.6`, so at `TOP_N=20` it is **12** and tracks `TOP_N`
automatically instead of loosening as the basket grows. It is purely additive:
it only applies when it is both tighter than the absolute floor and actually
satisfiable, so no configuration that worked before changes behaviour. Set
`MIN_BASKET_FRACTION=0` to restore the absolute floor alone.

| picks | at TOP_N=20 |
|---|---|
| 0 | nothing trades — previous basket holds |
| 1–11 | nothing trades — below the floor |
| 12–20 | the basket trades |

## 11. Feed-integrity gates: `derived_feed_short` and `slice_mark_suspect`

Both exist because of 2026-09-24. Robinhood pages its order feed with `next`
as a bare opaque token; three private cursor parsers assumed a URL, returned
None on page one, and every feed read silently ended at 200 orders. The
derived club position under-counted, the sizing "ride" anchored each
commitment to that collapsed mark, and the write-back persisted it — $990
became $82 in four cycles with no error and a green test suite. The parser
now lives in `app/robinhood/feed.py`; these two gates make the same class of
failure loud instead of silent.

**`derived_feed_short`** (audit action; the member is skipped as
`transient_rh_error`): `positions.club_shares_derived` refuses a feed whose
oldest order is newer than the member's oldest placed broker order in the
ledger — a feed that stops short of the club's own history is partial,
whatever the broker said about pages. It fires when:

- pagination breaks again (a new `next` shape → also `undecodable feed cursor`);
- the broker's feed retention no longer reaches the club's oldest order. The
  live members' history starts 2026-06-16; if Robinhood ever caps the feed
  (say 12 months) every member trips this at once and the club stops trading
  rather than mis-size. That is the fail-closed choice. The way out is a
  derived-position anchor (snapshot the club position at a date and read the
  feed from there) — not loosening the check. Interim: `CLUB_POSITION_SOURCE=ledger`.

**`slice_mark_suspect`** (audit action + `member_skipped`; the member gets a
"paused trading" email): before the ride is computed, the executor compares
what the club can ACCOUNT for — club positions at live quotes, `slice_carry`,
`carry_pending`, and buys still in `error` — against `alloc_applied`, the
figure it deployed last cycle. If more than 25% (min $5) is unaccounted for
while the account is still worth at least 75% of it, the dollars did not
leave — the club lost sight of them (feed truncated, positions read short,
proceeds parked as cash the mark cannot see). A real market loss shows in the
account total as well and is honored as before.

It also fires, correctly, when a member sells club shares by hand: the slice
really is smaller. After confirming that is what happened, honor it once:

```bash
ssh root@159.195.19.53 'cd /opt/concord/backend && sudo -u concord ./.venv/bin/python -c "from app.db import kv_set; kv_set(\"mark_accept:<member_id>\", \"1\")"'
```

The next live pass consumes the token (audit `slice_mark_accepted`); a dry run
leaves it in place. Never set it to clear a `derived_feed_short` — that one is a
broken read, not a smaller slice.
