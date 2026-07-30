/** Number formatting for the stock surfaces (panel + full research page).
 *  Dates live in lib/format.ts — import them from there, never from here. */

/** 2641000000000 → "2.64T", 45_100_000_000 → "45.1B", 1_200_000 → "1.2M". */
export function fmtBig(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const f = (v: number) => {
    const a = Math.abs(v);
    return String(parseFloat(a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2)));
  };
  if (abs >= 1e12) return `${f(n / 1e12)}T`;
  if (abs >= 1e9) return `${f(n / 1e9)}B`;
  if (abs >= 1e6) return `${f(n / 1e6)}M`;
  if (abs >= 1e3) return `${f(n / 1e3)}K`;
  return String(parseFloat(n.toFixed(2)));
}

export function fmtPrice(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 31.47 → "31.5"; null → "—". */
export function fmtRatio(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return String(parseFloat(n.toFixed(1)));
}

/** Where `v` sits between `lo` and `hi`, as 0–100. Null when the range is
 *  unusable (missing bound, or lo >= hi — never divide by a zero span). */
export function pctInRange(
  v: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined,
): number | null {
  if (v == null || lo == null || hi == null) return null;
  if (![v, lo, hi].every(Number.isFinite) || !(hi > lo)) return null;
  return Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
}
