/** Shared formatters. Dates are MM/DD/YYYY everywhere in the app (owner call) —
 *  import from here rather than hand-rolling toLocaleDateString, so one change
 *  moves the whole app. */

/** "2026-07-28" (a trade_date) → "07/28/2026". Parsed as a PLAIN date, never
 *  through `new Date("2026-07-28")` — that reads as UTC midnight and renders as
 *  the previous day for anyone west of Greenwich, which would silently misdate
 *  every trade in the club's history for US members. */
export function fmtTradeDate(d?: string | null): string {
  if (!d) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d.trim());
  if (!m) return d;
  const [, y, mo, day] = m;
  return `${mo}/${day}/${y}`;
}

/** Epoch seconds → "07/28/2026" in the viewer's timezone. */
export function fmtDate(ts?: number | null): string {
  if (!ts) return "—";
  const dt = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(dt.getMonth() + 1)}/${p(dt.getDate())}/${dt.getFullYear()}`;
}

/** Epoch seconds → "07/28/2026, 9:31 AM". */
export function fmtDateTime(ts?: number | null): string {
  if (!ts) return "—";
  const dt = new Date(ts * 1000);
  const time = dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${fmtDate(ts)}, ${time}`;
}

/** Epoch seconds → "9:31 AM" (freshness lines). */
export function fmtTime(ts?: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
