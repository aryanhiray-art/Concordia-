"""Env-driven configuration for the Concord backend.

All knobs live here so behaviour (execution mode, caps, schedule) is set by the
`.env` file (chmod 600, owned by the `concord` unix user) — never hardcoded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load concord/.env (one level up from backend/) then backend/.env if present.
    _here = Path(__file__).resolve()
    load_dotenv(_here.parents[2] / ".env")
    load_dotenv(_here.parents[1] / ".env", override=True)
except Exception:  # dotenv optional in prod where env is provided by systemd
    pass


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    app_env: str = os.environ.get("APP_ENV", "dev")
    db_path: str = os.environ.get("CONCORD_DB_PATH", str(Path(__file__).resolve().parents[1] / "concord.db"))

    session_secret: str = os.environ.get("SESSION_SECRET", "dev-insecure-session-secret")
    fernet_key: str = os.environ.get("CONCORD_FERNET_KEY", "")
    # Optional comma-separated key RING for rotation (primary first). MultiFernet
    # decrypts with any key in the ring, and re-encrypts with the primary — so you
    # can rotate the vault key without stranding existing tokens (finding #21).
    fernet_keys_raw: str = os.environ.get("CONCORD_FERNET_KEYS", "")

    invite_code: str = os.environ.get("INVITE_CODE", "concord")
    owner_email: str = os.environ.get("OWNER_EMAIL", "").strip().lower()
    # Google sign-in (OAuth web client id). Empty = the /api/auth/google
    # endpoint answers 503 and the web app hides the Google button.
    google_client_id: str = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    # Static service token: lets the forge-dashboard backend read owner/admin
    # endpoints without a member session (backend-to-backend). Empty = disabled.
    admin_api_token: str = os.environ.get("CONCORD_ADMIN_TOKEN", "").strip()

    web_origin: str = os.environ.get("WEB_ORIGIN", "http://localhost:3000")
    api_public_url: str = os.environ.get("API_PUBLIC_URL", "http://localhost:8780").rstrip("/")

    # Robinhood Agentic Trading endpoints
    rh_resource: str = os.environ.get("RH_RESOURCE", "https://agent.robinhood.com/mcp/trading")
    rh_register_url: str = os.environ.get("RH_REGISTER_URL", "https://agent.robinhood.com/oauth/trading/register")
    rh_authorize_url: str = os.environ.get("RH_AUTHORIZE_URL", "https://robinhood.com/oauth")
    rh_token_url: str = os.environ.get("RH_TOKEN_URL", "https://api.robinhood.com/oauth2/token/")
    rh_scope: str = os.environ.get("RH_SCOPE", "internal")
    rh_client_id_env: str = os.environ.get("RH_CLIENT_ID", "").strip()
    # Robinhood's agentic OAuth only accepts a localhost redirect (the consent
    # screen rejects a hosted HTTPS callback with robinhood.com/oauth/error).
    # So we redirect to localhost — the member's browser lands on a "can't
    # connect" page whose URL carries ?code=&state=, which they paste back into
    # Concord; we exchange it server-side (PKCE verifier is keyed by state).
    rh_redirect_uri_env: str = os.environ.get("RH_REDIRECT_URI", "").strip()

    # AI voting bots (local Ollama). Bots vote but never trade.
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    ollama_timeout: int = _int("OLLAMA_TIMEOUT", 120)
    bots_universe_size: int = _int("BOTS_UNIVERSE_SIZE", 120)   # tickers shown to each bot
    bot_start_power: float = _float("BOT_START_POWER", 100.0)   # initial voting power per bot
    # Bot containment: a per-bot voting-power ceiling (the weekly reweight can
    # compound but never past this), and a hard limit on the bots' AGGREGATE
    # share of the tally weight — if bots exceed it, all bot vote contributions
    # are scaled down so humans always keep at least (1 - share) of the say.
    bot_power_cap: float = _float("BOT_POWER_CAP", 300.0)              # per-bot power ceiling ($)
    bot_tally_share_max: float = _float("BOT_TALLY_SHARE_MAX", 0.90)   # max aggregate bot tally share (0–1)

    # Cadence — how often the club re-votes + trades, and how often voting power
    # redistributes. A cash account settles T+1, so trading every day can't work
    # (sell proceeds aren't buying power until the next business day). Testing
    # default = ALTERNATE trading days (a settlement day always sits between
    # rebalances) + WEEKLY power. Production target = weekly + monthly (set
    # CYCLE_CADENCE=weekly, POWER_CADENCE=monthly in .env).
    cycle_cadence: str = os.environ.get("CYCLE_CADENCE", "alternate").strip().lower()  # daily|alternate|weekly|mwf
    # 'mwf': Mon/Wed/Fri vote days run sell+buy in one session (limited-margin
    # members complete same-day via unsettled-funds buying power; cash members'
    # remainders defer) and Tue/Thu are top-up days for settled cash.
    # 'weekly' (production): ONE cycle a week — Monday runs both legs (same
    # margin/cash split as mwf), Tuesday is the deferred-cash top-up, Wed–Fri idle.
    power_cadence: str = os.environ.get("POWER_CADENCE", "weekly").strip().lower()      # weekly|biweekly|monthly
    # biweekly = every other Sunday, anchored here (must be a date the power
    # timer actually fires on — a Sunday).
    power_anchor: str = os.environ.get("POWER_ANCHOR", "2026-08-23").strip()
    cycle_anchor: str = os.environ.get("CYCLE_ANCHOR", "2026-06-23").strip()            # parity anchor; Tue 6/23 = first sell day
    cycle_start_date: str = os.environ.get("CYCLE_START_DATE", "2026-06-23").strip()    # cadence dormant before this (Mon 6/22 = fresh-start liquidation only)

    # Trading behaviour
    execution_mode: str = os.environ.get("EXECUTION_MODE", "dry_run").strip().lower()
    top_n: int = _int("TOP_N", 20)            # consensus basket size
    max_picks: int = _int("MAX_PICKS", 20)    # most a member may add to their ballot
    vote_open_at: str = os.environ.get("VOTE_OPEN_AT", "18:00")
    # DEFAULTS MUST MATCH THE SHIPPED TIMERS. These are only reached when the key
    # is absent from .env, i.e. on a fresh deployment — exactly when nobody is
    # watching. They read as the ballot countdown, the watchdog's missed-window
    # cutoff and /api/admin/schedule, so a stale default tells an operator the
    # club trades at a time it does not. Keep in step with
    # infra/concord-close-vote.timer and infra/concord-execute.timer.
    vote_close_at: str = os.environ.get("VOTE_CLOSE_AT", "09:15")   # 15min before the open
    execute_at: str = os.environ.get("EXECUTE_AT", "10:15")         # after the open, not at it
    # Voting power re-allocates weekly (Sunday). Power = account equity ($),
    # snapshotted on connect + every Sunday; votes are weighted by it.
    weekly_power_at: str = os.environ.get("WEEKLY_POWER_AT", "08:00")  # Sunday ET
    voting_power_base: float = _float("VOTING_POWER_BASE", 0.0)        # power before first snapshot

    # Sizing: deploy ALL available buying power across the basket, holding back a
    # small cash reserve so dollar market orders never trip a buying-power reject.
    # 0 = no reserve (owner call 2026-08-24). The reserve existed so the last
    # dollar order could not trip a buying-power reject, but it silently
    # stranded that % of every FULLY-committed account: a member whose stake is
    # 100% of their cash had 1% less deployable than their budget, so the final
    # name deferred every cycle (Neil, 08-24). A rejected last order is recorded
    # and retried; a permanently undeployable slice was not.
    cash_buffer_pct: float = _float("CASH_BUFFER_PCT", 0.0)   # % of BP kept as reserve
    max_orders: int = _int("MAX_ORDERS", 25)                  # hard per-member order ceiling
    # Global safety ceiling on the $ any single member's stake can deploy per
    # cycle. Enforced in alloc.rebalance_budget, which sizes both the executor's
    # budget and the voting-power snapshot.
    max_notional_per_member: float = _float("MAX_NOTIONAL_PER_MEMBER", 2000.0)
    # Human-quorum floor: the minimum number of HUMAN voters a session needs
    # before the executor will place REAL orders. Below this it drops to
    # review-only, so a basket chosen by the bots alone (zero/low human turnout)
    # is never bought with real money (finding #10).
    min_human_voters: int = _int("MIN_HUMAN_VOTERS", 1)
    # Missed-window catch-up cutoff (ET wall clock). concord-execute.timer is
    # Persistent=true, so a reboot spanning the 10:15 fire re-runs the job on
    # boot — safe in the morning (self-healed close + fresh basket), but a fire
    # after this time is trading a stale morning consensus into the close.
    # Scheduled runs past the cutoff stand down (audit 'missed_execute');
    # manual triggers bypass — the human is the judgment.
    execute_catchup_until: str = os.environ.get("EXECUTE_CATCHUP_UNTIL", "15:45").strip()
    # Thin-basket floor: the fewest consensus picks a closed basket may carry
    # before the executor places REAL orders. A near-empty ballot (one human
    # voting one stock) must not liquidate every member's portfolio into a
    # 1-2 name concentration — below the floor the run downgrades to
    # review-only. Dry-runs still simulate.
    min_basket_size: int = _int("MIN_BASKET_SIZE", 5)
    # ...and as a FRACTION of TOP_N, because an absolute floor does not scale.
    # At TOP_N=20 a floor of 5 stopped an empty ballot but happily let a PARTIAL
    # failure through: two bots voting six names cleared it, and the club rotated
    # a 20-name portfolio into 6 positions — a concentration event caused by a
    # cron job not running rather than by anyone deciding anything. The effective
    # floor is max(MIN_BASKET_SIZE, ceil(TOP_N * MIN_BASKET_FRACTION)), so it
    # tracks TOP_N automatically instead of silently loosening when TOP_N grows.
    #
    # 0.6 is chosen so a healthy ballot never trips it (20 bots plus the owner
    # each picking up to TOP_N names produce a full basket) while any morning
    # that loses most of its voters stands down and the previous basket holds.
    # 0 disables the fractional half and restores the absolute floor alone.
    min_basket_fraction: float = _float("MIN_BASKET_FRACTION", 0.6)

    @property
    def basket_floor(self) -> int:
        """Fewest picks that may be traded with real money.

        The fractional half only ever TIGHTENS, and only when satisfiable, so
        this cannot change any configuration that worked before. (Note the
        absolute floor can still exceed a very small TOP_N — pre-existing, and
        left alone here rather than silently altering how tiny baskets behave.)
        """
        import math as _math
        base = int(self.min_basket_size)
        frac = 0.0 if self.min_basket_fraction <= 0 else self.min_basket_fraction
        scaled = _math.ceil(self.top_n * frac)
        # Purely ADDITIVE: the fractional floor applies only when it is both
        # tighter than the absolute one AND actually satisfiable. It can never
        # loosen the existing floor, and it can never raise it past TOP_N (which
        # would stop the club trading with no error anywhere).
        if base < scaled <= self.top_n:
            return scaled
        return base
    # Post-run member receipts (G5a): after a LIVE run, each traded member gets
    # one plain-English email of what happened in their account. Fully fenced —
    # can never fail or delay a run (services/notify.py).
    member_run_emails: bool = _bool("MEMBER_RUN_EMAILS", True)
    # Size each buy against what the member's account ACTUALLY holds right now
    # (live positions), not the club's reconstructed ledger — so ledger drift, a
    # partial fill, or a manual sale can never mis-size a buy, and a liquidated
    # account is correctly seen as empty and rebought next cycle. SELLs still
    # gate on the club ledger (never sell a member's personal shares).
    buy_sizing_account: bool = _bool("BUY_SIZING_ACCOUNT", True)

    # Source of the per-member club POSITION used for sell/liquidation sizing.
    # "ledger"  = the estimate-based executions sum (legacy; drifted 16-90x high).
    # "derived" = reconstructed from the broker's own agentic order feed, anchored to
    #             reality, can only under-count (never sells personal shares).
    # Default "ledger" so deploying the code is a no-op; flip to "derived" to cut over
    # (instant rollback = flip back, no code deploy).
    club_position_source: str = os.environ.get("CLUB_POSITION_SOURCE", "ledger").strip().lower()

    # ── Reference account (Alpaca) ────────────────────────────────────────────
    # ONE operator-owned Alpaca account that mirrors the club's decided basket,
    # so the track record is measured from first-party fills instead of being
    # reconstructed out of members' commingled Robinhood accounts. Members are
    # unaffected — they stay on Robinhood and nobody re-onboards.
    #
    # Deliberately THREE separate switches, because "the key is present" must
    # never imply "you may trade":
    #   alpaca_configured  — a key pair exists (reads work: probe, NAV, history)
    #   reference_enabled  — the mirror path may run at all (still plans only)
    #   reference_armed    — and only THEN may it place a real order
    # Both of the latter default OFF, so deploying this code cannot place an
    # order. Arming is a human action taken on the box, never a deploy's.
    alpaca_key_id: str = os.environ.get("ALPACA_KEY_ID", "").strip()
    alpaca_secret: str = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    alpaca_paper: bool = _bool("ALPACA_PAPER", True)      # paper until told otherwise
    reference_enabled: bool = _bool("REFERENCE_ENABLED", False)
    reference_armed: bool = _bool("REFERENCE_ARMED", False)
    # Dollars deployed per cycle across the basket. The reference account is a
    # CASH account by design: with margin it would never defer a buy on T+1 and
    # would systematically beat members who do, making the published number
    # unachievable rather than merely good.
    reference_budget_usd: float = _float("REFERENCE_BUDGET_USD", 0.0)

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_key_id and self.alpaca_secret)

    @property
    def reference_will_place(self) -> bool:
        """Every gate that must be true before a real reference order is sent."""
        return bool(self.alpaca_configured and self.reference_enabled
                    and self.reference_armed and self.reference_budget_usd > 0
                    and not self.demo_mode and not self.is_beta)

    # ── Voting-power model (the ETHGlobal Concordia 50/50 formula) ──
    # 'stake'  = voting power is committed stake only (legacy).
    # 'blend'  = VP = capital_share·CAPITAL + (accuracy_share·confidence)·ACCURACY,
    #            i.e. money AND merit. OFF by default so shipping the code doesn't
    #            change live weights until you set POWER_MODEL=blend. Cold start:
    #            with no accuracy history yet, 'blend' reduces to pure stake, so
    #            the switch is seamless and accuracy phases in over CONFIDENCE_CYCLES.
    power_model: str = os.environ.get("POWER_MODEL", "stake").strip().lower()
    capital_bps: int = _int("CAPITAL_BPS", 5000)          # 50% capital weight
    accuracy_bps: int = _int("ACCURACY_BPS", 5000)        # 50% accuracy weight
    # Capital concavity: CapitalShare is built on capital(i)**gamma, not capital(i)
    # directly. gamma=1.0 (10000 bps) is exactly linear — today's behaviour, so
    # shipping this is a no-op until an operator dials it down. gamma<1 (e.g. 0.5 =
    # sqrt, the quadratic-voting curve) gives whale capital diminishing returns —
    # 4x the stake buys 2x the power, not 4x — organically, with no hard cap/cliff.
    capital_gamma_bps: int = _int("CAPITAL_GAMMA_BPS", 10000)
    # Accuracy is a TWO-timescale EWMA blend, not one. A single EWMA can't be both
    # "recent form matters a lot" and "a good multi-year record still counts" —
    # its decay is one fixed exponential, so a short half-life (recency-sensitive)
    # forces old cycles toward zero, while a long half-life (long memory) flattens
    # recency too. Blending a fast + a slow component gets both at once: short
    # half-life ~ current form, long half-life ~ career record.
    accuracy_short_half_life_cycles: int = _int("ACCURACY_SHORT_HALF_LIFE_CYCLES", 13)   # ~1 quarter
    accuracy_long_half_life_cycles: int = _int("ACCURACY_LONG_HALF_LIFE_CYCLES", 260)    # ~5 years
    accuracy_short_weight_bps: int = _int("ACCURACY_SHORT_WEIGHT_BPS", 6000)  # 60% current form
    accuracy_long_weight_bps: int = _int("ACCURACY_LONG_WEIGHT_BPS", 4000)    # 40% career record
    confidence_cycles: int = _int("CONFIDENCE_CYCLES", 12)  # accuracy phase-in length
    # ── v2 'merit' score form (POWER_FORM) ────────────────────────────────────
    # 'legacy' = today's ADDITIVE form: raw = cap_w·capShare + acc_w·accShare·conf.
    # 'merit'  = MULTIPLICATIVE: score_i = capital_i**gamma · M_i, one normalisation.
    #
    # The additive form normalises capital and accuracy SEPARATELY and adds them.
    # That is what makes a departure re-rank the members who stayed: an exit
    # inflates the capital channel by 1/(1-C_k) and the accuracy channel by
    # 1/(1-A_k) — two different factors — so survivors are re-weighted by where
    # their score came from (measured on the live roster: pairwise ratios drift up
    # to 24.7%). The product form has ONE denominator, so removing k scales every
    # survivor's share by the single common factor 1/(1-share_k): pairwise ratios
    # and rank order are preserved EXACTLY. See services/reputation.py.
    power_form: str = os.environ.get("POWER_FORM", "legacy").strip().lower()
    # The merit multiplier: M_i = MERIT_MAX_MULTIPLE ** tanh(accuracy_i / SCALE).
    # tanh saturates, so M is bounded in [1/MM, MM] with NO hard cap or cliff, and
    # accuracy 0 gives M == 1.0 exactly (cold start = raw stake at gamma == 1).
    # MM is the ONLY spread dial: best:worst multiplier can never exceed MM**2.
    merit_max_multiple: float = _float("MERIT_MAX_MULTIPLE", 2.0)
    # One unit of track record, in absolute accuracy: 0.010 = 100bps of
    # vote-weighted excess return vs the benchmark reaches tanh(1)=0.762 of the cap.
    #
    # Chosen over the tighter 0.005 for two MEASURED reasons. (1) SATURATION: on
    # the live roster 0.005 already pushes the best member to 1.94x against a
    # 2.00x ceiling, so the multiplier stops discriminating exactly where the
    # competition is; at 0.010 nobody is pinned and the club still spreads
    # 0.65x..1.67x. (2) FIRST-CYCLE JUMP: one cycle moves a new member's accuracy
    # by ~3.2% of that cycle's excess, so at 0.005 a single capped cycle was
    # worth 1.48x; at 0.010 it is 1.24x, and a typical (+2%) cycle moves them
    # 1.05x rather than 1.09x. A record should take several cycles to build.
    merit_accuracy_scale: float = _float("MERIT_ACCURACY_SCALE", 0.010)
    # Winsorises ONE cycle's vote-weighted excess before it enters the EWMA, so a
    # single freak cycle can't dominate a member's record. Bounds the damage from
    # a stale/gapped price pair booking a multi-week move as one cycle.
    accuracy_excess_cap: float = _float("ACCURACY_EXCESS_CAP", 0.10)
    # GOVERNANCE CEILING ON SHARE — no single member may exceed this fraction of
    # the vote. 0 = off (the shipped default).
    #
    # Enforced by water-filling: anyone above the ceiling is pinned to it and the
    # excess is redistributed pro-rata among everyone below, repeated until no
    # one is over. Two properties make this safe rather than a re-run of the old
    # additive defect: (a) the redistribution is a COMMON factor, so any two
    # UNCAPPED members keep their pairwise ratio exactly through an exit
    # (measured 6.7e-16); (b) only a member actually pinned at the ceiling moves
    # relative to the rest, which is the definition of a cap, not a bug.
    #
    # FEASIBILITY IS NOT OPTIONAL: shares sum to 1, so a ceiling of C is
    # unsatisfiable unless C > 1/N. Worse, a C only slightly above 1/N pins most
    # of the club and silently collapses it to one-member-one-vote — a 2% cap at
    # 100 members pins 22% of members. Hence the companion threshold below, and
    # the hard guard in reputation.snapshot_power_all.
    max_voting_share: float = _float("MAX_VOTING_SHARE", 0.0)
    # Club size at which the ceiling switches on. 0 = DERIVE it as ceil(2/C),
    # the smallest club in which the cap bounds an outlier instead of flattening
    # everyone (5% -> 40, 2% -> 100 — which is where simulation puts the pinned
    # fraction under ~5%). Set explicitly only to override that.
    max_voting_share_min_members: int = _int("MAX_VOTING_SHARE_MIN_MEMBERS", 0)
    # R1 — inertia grows with the club. The EWMA half-lives are multiplied by
    # max(1, sqrt(N_measured / REF_N)), so accuracy moves more slowly as more
    # members are scored. One-sided by construction: a shrinking roster can never
    # sharpen the score. N is read ONLY at scoring time and STAMPED into
    # cycle_scores — a member-count term evaluated at SNAPSHOT time would make an
    # exit change N and destroy the single-factor rescale above. 0 disables.
    accuracy_inertia_ref_n: int = _int("ACCURACY_INERTIA_REF_N", 24)
    benchmark_symbol: str = os.environ.get("BENCHMARK_SYMBOL", "SPY").strip().upper()
    # While bots are test stand-ins for humans they carry a FAKE stake (capital),
    # so under blend they participate like funded members instead of collapsing to
    # $0 (they hold no real money). Lower toward 0 as real humans join and the bots
    # should fade. Set BOT_STAKE_USD=0 to make bots pure-accuracy (no fake capital).
    bot_stake_usd: float = _float("BOT_STAKE_USD", 100.0)

    @property
    def rh_redirect_uri(self) -> str:
        # localhost only (Robinhood agentic requirement). Override via env if
        # Robinhood ever permits a different/registered redirect.
        return self.rh_redirect_uri_env or "http://localhost:8917/callback"

    # Transactional email (Resend HTTP API). Empty key = email features are
    # INERT: signup/start answers code_required=false, signup skips the code
    # check, forgot still answers 200 but sends nothing — exact pre-email
    # behaviour, so deploying the code without a key changes nothing.
    resend_api_key: str = os.environ.get("RESEND_API_KEY", "").strip()
    mail_from: str = os.environ.get("MAIL_FROM", "Concordia <no-reply@voteconcordia.com>")

    @property
    def email_enabled(self) -> bool:
        # demo_mode counts as enabled: the mailer prints to stdout instead of
        # calling Resend, so the sandbox can walk the full code flow offline.
        return bool(self.resend_api_key) or self.demo_mode

    # DEMO_MODE: the whole app runs against a fake Robinhood (robinhood/demo.py)
    # — connect always succeeds, balances/quotes are canned, no external calls.
    # For localhost sandboxes only; is_live is forced False so a demo instance
    # can never place a real order no matter what else is misconfigured.
    demo_mode: bool = _bool("DEMO_MODE", False)

    # DEMO ONLY: a JSON file holding the REAL club's shared record (history +
    # performance), refreshed by jobs/mirror_club.py running against the live
    # database. The demo's own trades stay simulated; only the club-level story
    # is real, so people trying it see the actual fund rather than fixtures.
    #
    # The demo process NEVER reads the live database — it reads this file, which
    # contains only the two payloads every member is already allowed to see and
    # which carry no member identity by construction. Ignored outside demo_mode,
    # so setting it on the real instance does nothing.
    club_mirror_path: str = os.environ.get("CLUB_MIRROR_PATH", "")

    @property
    def is_beta(self) -> bool:
        # APP_ENV=beta = the beta.voteconcordia.com staging channel: the real
        # code and real broker flows against its own database, but never real
        # orders (see is_live below).
        return self.app_env.strip().lower() == "beta"

    @property
    def is_live(self) -> bool:
        # Two hard locks sit in front of EXECUTION_MODE, at the single choke
        # point every real-order path checks (will_place = is_live AND armed):
        # demo_mode (the fake-broker sandbox) and app_env=beta (the staging
        # channel holds real member tokens, so a mis-set EXECUTION_MODE=live in
        # its .env — or an operator arming it — must still be incapable of
        # spending real money). Neither lock can be overridden by any other knob.
        return self.execution_mode == "live" and not self.demo_mode and not self.is_beta

    @property
    def is_prod(self) -> bool:
        return self.app_env.strip().lower() == "prod"

    @property
    def is_internet_facing(self) -> bool:
        # Prod AND beta both sit on the public internet behind TLS with real
        # member brokerage tokens in their vaults, so every secrets/transport
        # hardening gate (insecure-defaults boot refusal, Secure cookies, the
        # localhost CORS carve-out) must treat them identically. Only the
        # trade lock (is_live) distinguishes them. Demo/dev stay relaxed:
        # demo's vault is fake by construction and dev runs on plain HTTP.
        return self.is_prod or self.is_beta

    @property
    def cookie_secure(self) -> bool:
        # Secure attribute on the session cookie: on any internet-facing
        # instance the cookie must never ride a cleartext request (beta's
        # session guards real tokens just like prod's). Dev/demo sandboxes on
        # http://localhost stay signin-able without TLS.
        return self.is_internet_facing

    @property
    def fernet_keys(self) -> list[str]:
        """Rotation key ring (primary first). Empty when only the single key is set."""
        return [k.strip() for k in self.fernet_keys_raw.split(",") if k.strip()]


# Placeholder/insecure defaults that MUST NOT be used on an internet-facing
# instance (prod OR beta — beta holds real member tokens too, and a default
# SESSION_SECRET means anyone can mint an owner session and walk straight past
# require_admin). Boot fails closed in main.startup.
_INSECURE_SESSION_SECRETS = {"", "dev-insecure-session-secret", "change-me", "changeme"}
_INSECURE_INVITE_CODES = {"", "concord", "change-me", "changeme"}


def insecure_prod_config() -> list[str]:
    """Names of critical secrets still at an insecure default on prod/beta.

    Empty list = safe to boot. The Fernet key already fails closed in security._fernet;
    this covers the session secret (forgeable owner sessions) + the invite code.
    """
    if not settings.is_internet_facing:
        return []
    problems: list[str] = []
    if settings.session_secret.strip() in _INSECURE_SESSION_SECRETS:
        problems.append("SESSION_SECRET")
    if settings.invite_code.strip().lower() in _INSECURE_INVITE_CODES:
        problems.append("INVITE_CODE")
    if not settings.fernet_key:
        problems.append("CONCORD_FERNET_KEY")
    return problems


def invalid_power_config() -> list[str]:
    """Voting-power knobs that are out of range. Empty list = safe to boot.

    None of these were validated before v2, and several fail SILENTLY in the
    direction opposite to their intent — most dangerously CAPITAL_GAMMA_BPS above
    10000, which AMPLIFIES whale capital instead of dampening it (a typo of one
    extra zero hands one member nearly the whole vote, with no error). Voting
    power governs real trades, so a nonsense knob must refuse to boot rather than
    quietly re-weight the club.
    """
    p: list[str] = []
    if settings.power_form not in ("legacy", "merit"):
        p.append(f"POWER_FORM={settings.power_form!r} (expected 'legacy' or 'merit')")
    # POWER_FORM only takes effect inside POWER_MODEL=blend — power.snapshot_all
    # dispatches on power_model, and reputation.snapshot_power_all (the sole
    # reader of power_form) is unreachable under 'stake'. Setting merit without
    # blend therefore boots clean and silently keeps running the legacy formula,
    # which is the most likely deployment mistake and fails in the direction of
    # "I believe v2 is live". Refuse it instead.
    if settings.power_form == "merit" and settings.power_model != "blend":
        p.append("POWER_FORM=merit requires POWER_MODEL=blend "
                 f"(got POWER_MODEL={settings.power_model!r}) — merit would be silently ignored")
    if settings.power_model not in ("stake", "blend"):
        p.append(f"POWER_MODEL={settings.power_model!r} (expected 'stake' or 'blend')")
    if not 1.0 < settings.merit_max_multiple <= 5.0:
        p.append(f"MERIT_MAX_MULTIPLE={settings.merit_max_multiple} (expected 1.0 < x <= 5.0)")
    if settings.merit_accuracy_scale <= 0:
        p.append(f"MERIT_ACCURACY_SCALE={settings.merit_accuracy_scale} (expected > 0)")
    if not 0 < settings.accuracy_excess_cap <= 1.0:
        p.append(f"ACCURACY_EXCESS_CAP={settings.accuracy_excess_cap} (expected 0 < x <= 1.0)")
    if not 0.0 <= settings.min_basket_fraction <= 1.0:
        p.append(f"MIN_BASKET_FRACTION={settings.min_basket_fraction} (expected 0..1)")
    if not 0.0 <= settings.max_voting_share < 1.0:
        p.append(f"MAX_VOTING_SHARE={settings.max_voting_share} (expected 0 <= x < 1; 0 = off)")
    if settings.max_voting_share_min_members < 0:
        p.append(f"MAX_VOTING_SHARE_MIN_MEMBERS={settings.max_voting_share_min_members} (expected >= 0)")
    # A ceiling that can never be satisfied by the club it is configured for
    # would pin every member and collapse the vote to one-member-one-vote.
    if (settings.max_voting_share > 0 and settings.max_voting_share_min_members > 0
            and settings.max_voting_share * settings.max_voting_share_min_members <= 1.0):  # noqa: E501
        p.append(
            f"MAX_VOTING_SHARE={settings.max_voting_share} is unsatisfiable at "
            f"MAX_VOTING_SHARE_MIN_MEMBERS={settings.max_voting_share_min_members} "
            f"(shares sum to 1, so the cap needs > {1/settings.max_voting_share:.0f} members)")
    if settings.accuracy_inertia_ref_n < 0:
        p.append(f"ACCURACY_INERTIA_REF_N={settings.accuracy_inertia_ref_n} (expected >= 0)")
    # gamma > 1 amplifies whales — the exact opposite of the knob's purpose.
    if not 0 < settings.capital_gamma_bps <= 10000:
        p.append(f"CAPITAL_GAMMA_BPS={settings.capital_gamma_bps} (expected 0 < x <= 10000; "
                 "above 10000 AMPLIFIES large stakes)")
    if settings.accuracy_short_weight_bps + settings.accuracy_long_weight_bps != 10000:
        p.append("ACCURACY_SHORT_WEIGHT_BPS + ACCURACY_LONG_WEIGHT_BPS must total 10000 "
                 f"(got {settings.accuracy_short_weight_bps + settings.accuracy_long_weight_bps})")
    # Under 'merit' there are no additive channels, so these two weight NOTHING.
    # Refuse rather than let an operator tune a knob that has no effect.
    if settings.power_form == "merit" and (settings.capital_bps != 5000
                                           or settings.accuracy_bps != 5000):
        p.append("CAPITAL_BPS/ACCURACY_BPS are inert under POWER_FORM=merit "
                 "(the merit form has no additive channels to weight) — unset them")
    return p


settings = Settings()


def cors_origins() -> list[str]:
    """Origins allowed to call the API (the web app, local + deployed)."""
    origins = {settings.web_origin, "http://localhost:3000", "http://127.0.0.1:3000"}
    return [o for o in origins if o]
