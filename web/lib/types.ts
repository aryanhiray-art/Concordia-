export type UniverseItem = { symbol: string; name: string; sort_order?: number };

export type TallyRow = { symbol: string; name: string; votes: number };

export type SessionView = {
  id: number;
  trade_date: string;
  status: "open" | "closed" | "executed";
  closes_at: number;
  top_n: number;
  voters: number;
  tally: TallyRow[];
  my_picks: string[];
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
