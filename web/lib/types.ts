export type UniverseItem = { symbol: string; name: string; sort_order?: number };

export type TallyRow = { symbol: string; name: string; votes: number; weight?: number };
export type Alloc = { symbol: string; weight: number };   // weight = % of voting power

export type Balance = { total_value: number | null; cash: number | null; buying_power: number | null } | null;
export type Allocation = { type: "percent" | "dollar"; value: number; resolved_usd: number; balance: Balance };
export type Pnl = { invested: number; value: number; profit: number; profit_pct: number | null; positions: number };
export type Account = { allocation: Allocation; pnl: Pnl; power: PowerView };

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
  role: "owner" | "member";
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

export type ExecutionRow = {
  symbol: string;
  side: string;
  type: string;
  dollar_amount: number | null;
  status: "planned" | "reviewed" | "placed" | "skipped" | "error";
  error: string | null;
  display_name?: string;
};
