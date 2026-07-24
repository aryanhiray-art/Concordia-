export function money(n: number | null | undefined, dp = 0) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: dp });
}
