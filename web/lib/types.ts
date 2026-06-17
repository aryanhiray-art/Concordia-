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
} | null;

export type Member = {
  id: number;
  email: string;
  display_name: string;
  role: "owner" | "member";
  settings: MemberSettings;
  robinhood: RobinhoodStatus;
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
