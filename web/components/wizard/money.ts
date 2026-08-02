// dp defaults to 0 for compact secondary figures (cash, buying power). The
// HEADLINE account value must pass dp=2: rounding it to whole dollars while
// the commit line shows exact cents made "Agentic account $400" sit above
// "Committing $399.99" — a one-cent gap that reads as a hidden fee. Nothing
// is withheld; the account was genuinely worth $399.99.
export function money(n: number | null | undefined, dp = 0) {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: dp });
}
