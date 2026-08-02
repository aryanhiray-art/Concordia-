// /stocks used to be a search tab. The search moved into the ballot, so the
// index has nothing left to show — it redirects to /vote rather than 404 or
// linger as a dead page (old links, the browser's history and any bookmark all
// keep working). /stocks/[symbol], the full research page, is untouched.

import { redirect } from "next/navigation";

export default function StocksIndex() {
  redirect("/vote");
}
