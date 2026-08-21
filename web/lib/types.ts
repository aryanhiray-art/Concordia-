export type UniverseItem = { symbol: string; name: string; sort_order?: number };

export type TallyRow = { symbol: string; name: string; votes: number; weight?: number };
export type Alloc = { symbol: string; weight: number };   // weight = % of voting power

export type Balance = { total_value: number | null; cash: number | null; buying_power: number | null } | null;
export type Allocation = { type: "percent" | "dollar"; value: number; resolved_usd: number; balance: Balance };
export type Pnl = { invested: number; value: number; profit: number; profit_pct: number | null; positions: number };
/** GET /api/me/account — served from a cached snapshot the backend refreshes on a
 *  ~10-minute timer. The freshness trio is optional on purpose: an older backend
 *  (or one mid-deploy) omits it, and a missing `as_of` must read as "unknown",
 *  never as 1970. */
export type Account = {
  allocation: Allocation;
  pnl: Pnl;
  power: PowerView;
  /** epoch SECONDS of the snapshot; null/undefined = the backend didn't say. */
  as_of?: number | null;
  /** snapshot older than 30 minutes. */
  stale?: boolean;
  /** this payload came straight off the broker (a forced refresh), not the cache. */
  live?: boolean;
};

/** club: the club's ledger holds this symbol for you; false = a personal
 *  position sharing the account (labeled, never implied to be basket). */
export type Holding = { symbol: string; quantity: number; value: number; pnl: number | null; club?: boolean | null };
/** GET /api/me/holdings — same snapshot contract as Account. */
export type HoldingsView = { holdings: Holding[]; as_of?: number | null; stale?: boolean };

// ── the member's own recorded account-value history ────────────────────────
// GET /api/me/equity?range=… — real observations (~10-minute cadence going
// forward, weekly seeds behind), PLUS daily gap-fill estimates shaped by the
// club's own performance between real anchors (est: true on those). Owner
// call: a member's money rides the club's basket, so a two-week gap drawn as
// a straight line is falser than the club-shaped path. Real observations are
// never overridden, estimates retire as real sessions accumulate, and the
// broker has no account-history endpoint so the past cannot be re-recorded.
export type EquityRangeKey = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";
export type EquityPoint = { t: number; v: number; est?: boolean };   // est = club-shaped gap-fill
export type EquityHistory = {
  range: EquityRangeKey;
  points: EquityPoint[];
  /** e.g. "10m" / "1d" — informational; the chart reads the timestamps. */
  resolution?: string | null;
  /** epoch seconds of the oldest observation on record, any range. */
  first_ts?: number | null;
  ok?: boolean;
};

export type PowerPoint = { week_start: string; power: number; equity: number | null; delta: number | null };
export type PowerView = {
  power: number;
  delta: number | null;
  as_of_week: string | null;
  history: PowerPoint[];
};

// Club-level decisions-vs-SPY performance — the same series for every member.
export type PerfPoint = { d: string; club: number; spy: number };  // both indexed, base 100
export type PerfView = {
  start_date: string | null;
  computed_at: number;
  n_sessions: number;
  points: PerfPoint[];
  club_pct: number | null;
  spy_pct: number | null;
};

export type SessionView = {
  id: number;
  trade_date: string;
  status: "open" | "closed" | "executed";
  closes_at: number;
  top_n: number;
  voters: number;
  tally: TallyRow[];
  my_picks: Alloc[];
  picks: TallyRow[];
};

export type RobinhoodStatus = {
  account_number: string | null;
  agentic_allowed: number;
  status: string;
  connected_at: number | null;
} | null;

export type MemberSettings = {
  opted_in: number;
  buying_power_pct: number;
  daily_cap_usd: number;
  max_orders: number;
  /** The committed stake: alloc_type is "percent" | "dollar", alloc_value the
   *  raw number. Resolving it to dollars needs a live balance read, so the
   *  Overview gate uses alloc_value>0 as the cheap "has a stake" signal. */
  alloc_type?: string;
  alloc_value?: number;
  voting_power?: number;
} | null;

export type Member = {
  id: number;
  email: string;
  display_name: string;
  /** "owner" | "admin" | "member" — kept open: the admin console gates on
   *  owner/admin and new roles must fail closed, not fail to compile. */
  role: string;
  has_password?: boolean;
  /** Ever confirmed a code emailed to this address (signup flow or the interstitial). */
  email_verified?: boolean;
  /** True only when email sending is configured AND this member never verified
      (false for bots) — AppShell blocks the whole app behind ConfirmEmail on it. */
  needs_email_verification?: boolean;
  settings: MemberSettings;
  robinhood: RobinhoodStatus;
};

export type ApiKey = {
  id: number;
  name: string;
  prefix: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
};

export type ResearchResult = {
  source: string;
  research_count: number;
  eligible: string[];
  excluded: string[];
  allocations: Alloc[];
  dry_run: boolean;
  error?: string;
  session?: { id?: number; trade_date: string; status: string };
};

// ── Admin console (admin.voteconcordia.com) ────────────────────────────────
// Mirrors GET /api/admin/panel exactly — read-only ops snapshot for owner/admin.
// Numeric flags may arrive as 0/1 from SQLite, so booleans are typed loosely
// where the backend is known to store ints (see Member.settings above).

export type AdminMemberRow = {
  id: number;
  email: string;
  display_name: string;
  role: string;
  connected: boolean;
  agentic: boolean;
  opted_in: boolean;
  stake_type: string | null;
  stake_value: number | null;
  email_verified: boolean;
  /** Anything wrong with this member — empty array = healthy. */
  exceptions: string[];
};

export type AdminPanel = {
  state: {
    execution_mode: string;
    is_live: boolean;
    armed: boolean;
    halt: boolean;
    will_place_next: boolean;
    reasons: string[];
  };
  members: AdminMemberRow[];
  cycle: {
    open: {
      id: number;
      trade_date: string;
      closes_at: number;           // epoch seconds (same convention as SessionView)
      voters: number;
      human_voters: number;
      quorum_met: boolean;
      picks_so_far: number;
    } | null;
    bots: { count: number; voted_today: number };
    last_executed: {
      id: number;
      trade_date: string;
      by_status: Record<string, number>;
      latest_verify_verdict: string | null;
    } | null;
  };
  money: {
    per_member: {
      id: number;
      display_name: string;
      stake_type: string | null;
      stake_value: number | null;
      voting_power: number | null;
    }[];
    /** Shape intentionally loose — cached club performance, rendered defensively. */
    club_perf_cached?: unknown;
  };
  audit: { ts: number; actor: string; action: string; target?: string | null }[];
  /** Latest decided basket (optional — older backends omit it). */
  basket?: { trade_date: string; picks: { symbol: string; name?: string | null; votes?: number | null; weight?: number | null }[] } | null;
  /** Probable ticker renames: a ledger phantom paired with an unledgered live
   *  position on the same account(s). Rendered as a migrate control. */
  drift?: { old: string; new: string; members: string[]; live_value: number }[];
};

// ── History split ──────────────────────────────────────────────────────────
// GET /api/club/history — one row per cycle, club-shared, NO member identities.
// `orders` maps execution status → count (e.g. {placed: 12, error: 1}).
export type ClubHistorySession = {
  id: number;
  trade_date: string;
  /** "open" | "closed" | "executed" — kept open so new states render neutral. */
  status: string;
  /** Presentation status: stale closed sessions whose cycle is over arrive as
   *  "executed"; only the LATEST closed row keeps "closed". Optional so the UI
   *  compiles (and falls back to `status`) before the backend ships it. */
  status_display?: string;
  basket: string[];
  voters: number;
  human_voters: number;
  orders: Record<string, number>;
};
export type ClubHistoryView = { sessions: ClubHistorySession[] };

// GET /api/me/votes — caller-only ballot log. `hits` = my picks that made the
// basket. An open session arrives with basket:[] and hits:[] (results hidden
// until close).
export type MyVoteSession = {
  id: number;
  trade_date: string;
  status: string;
  /** Same contract as ClubHistorySession.status_display. */
  status_display?: string;
  my_allocations: Alloc[];
  basket: string[];
  hits: string[];
};
export type MyVotesView = { sessions: MyVoteSession[] };

// ── Stocks (search page + the detail slide-over) ───────────────────────────
// GET /api/stocks/search — universe matcher, each hit {symbol, name}.
export type StockSearchResult = { symbol: string; name: string };

export type StockRangeKey = "1D" | "1W" | "1M" | "3M" | "1Y";

/** GET /api/stocks/{SYM}/history?range=… — one range's real bars, fetched on
 *  demand. 1D/1W are genuine intraday series (5-minute / 30-minute) whose
 *  points carry `t`; 1M/3M/1Y are daily and carry `d`. 1D also reports WHICH
 *  session it drew, so the UI never implies a stale bar is today's. */
export type StockHistory = {
  symbol: string;
  range: StockRangeKey;
  points: StockPricePoint[];
  intraday: boolean;
  as_of: number;
  day?: StockDayBar & { session_date?: string | null; is_today?: boolean };
};
/** One plotted close. Daily ranges carry `d` ("YYYY-MM-DD"); intraday ranges
 *  carry `t` (epoch seconds) because a date alone can't place a 5-minute bar.
 *  The chart positions x by whichever is present — never by array index, which
 *  would erase weekends and overnight gaps. */
export type StockPricePoint = { d?: string; t?: number; c: number };

/** GET /api/stocks/{SYM} fundamentals — every field nullable: the backend
 *  serves nulls when the broker is unreachable or doesn't know. */
export type StockFundamentals = {
  market_cap: number | null;
  pe_ratio: number | null;
  dividend_yield: number | null;   // already a percent (0.44 = 0.44%)
  high_52w: number | null;
  low_52w: number | null;
  volume: number | null;
  avg_volume: number | null;
  sector: string | null;
  description: string | null;
};

/** The latest daily bar — the broker's quote carries no intraday high/low, so
 *  the day strip reads OHLCV off the last bar of the history call. `d` is that
 *  bar's date (the current session once it opens, otherwise the previous one);
 *  every field is null when the broker doesn't carry it. */
export type StockDayBar = {
  d: string | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
};

export type StockDetail = {
  symbol: string;
  name: string;
  quote: { price: number | null; prev_close: number | null; change_pct: number | null };
  /** Optional so the UI compiles (and hides the strip) against an older backend. */
  day?: StockDayBar;
  range: Record<StockRangeKey, StockPricePoint[]>;
  fundamentals: StockFundamentals;
  in_universe: boolean;
  /** epoch seconds of the quote snapshot. */
  as_of: number;
};

export type ExecutionRow = {
  symbol: string;
  side: string;
  type: string;
  dollar_amount: number | null;
  status: "planned" | "reviewed" | "placed" | "skipped" | "error";
  error: string | null;
  display_name?: string;
};
