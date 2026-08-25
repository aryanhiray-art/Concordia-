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
    vote_close_at: str = os.environ.get("VOTE_CLOSE_AT", "08:30")   # 1h before the 09:30 open
    execute_at: str = os.environ.get("EXECUTE_AT", "09:31")
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


settings = Settings()


def cors_origins() -> list[str]:
    """Origins allowed to call the API (the web app, local + deployed)."""
    origins = {settings.web_origin, "http://localhost:3000", "http://127.0.0.1:3000"}
    return [o for o in origins if o]
