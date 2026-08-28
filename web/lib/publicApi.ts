// Typed client for the two unauthenticated endpoints behind the public
// marketing site (backend/app/routes/public.py). Deliberately separate from
// lib/api.ts: nothing here reads or sends the member session, and nothing here
// may import from the member app's surface.
//
// No "use client" directive on purpose — this module touches no hook and no
// browser-only global, so a marketing section may call it from either a client
// component or the server. (lib/api.ts needs the directive because it reads
// localStorage.)
//
// Two rules the whole marketing page depends on:
//   1. Neither function ever throws a raw fetch error. Network failure, a
//      timeout, an HTML error page from a misconfigured proxy — everything
//      arrives as a PublicApiError with a status, so a section can `catch` and
//      fall back to FALLBACK_STATS without type-guarding a `unknown`.
//   2. Every field of PublicStats is guaranteed present and the declared type,
//      because the response is normalised field-by-field rather than cast. A
//      section rendering `stats.club_pct` can never get `undefined` or NaN —
//      SPEC's "never fall back to a zero" is enforced here, once, instead of at
//      every call site.

// Empty base = same-origin (Vercel rewrites /api + /oauth to the backend).
// Local dev sets NEXT_PUBLIC_CONCORD_API=http://localhost:8780. Identical
// resolution to lib/api.ts — the two clients must agree on where the API is.
const BASE = (process.env.NEXT_PUBLIC_CONCORD_API || "").replace(/\/$/, "");

// A landing page must never sit on a spinner that never resolves, so every
// request carries its own deadline rather than trusting the browser's.
const TIMEOUT_MS = 8000;

/** One day of the decisions index vs the benchmark, both based at 100. */
export type PublicStatsPoint = {
  /** ET calendar date, YYYY-MM-DD. */
  d: string;
  /** Decisions index, 100 = the first decided session. */
  club: number;
  /** SPY buy-and-hold, same base. Null on a day the benchmark price fetch
   *  could not cover — a gap in the benchmark line, not a zero. */
  spy: number | null;
};

/**
 * GET /api/public/stats — the club's published record, and nothing else. No
 * member count, no names, no account values, no currently-held symbols.
 *
 * `stale: true` means the backend served the published constants because its
 * performance cache was cold; `points` is then EMPTY (an invented curve would
 * be a lie even with the right endpoints), so anything drawing the chart must
 * check `points.length` rather than assuming a series.
 */
export type PublicStats = {
  /** First decided session, YYYY-MM-DD. */
  live_since: string;
  /** Last close the figures are marked to. */
  as_of: string;
  /** Decided baskets behind the index. */
  n_sessions: number;
  /** Decisions index total return, in percent (6.85 = +6.85%). */
  club_pct: number;
  /** SPY buy-and-hold over the same window, in percent. */
  spy_pct: number;
  points: PublicStatsPoint[];
  /** Names in a basket per cycle. */
  universe_size: number;
  stale: boolean;
};

/**
 * The published figures from SPEC §2 — verified by an independent recomputation
 * over all 24 locked baskets, every price spot-checked against Robinhood.
 *
 * Exported so a section that catches a failed fetch renders the SAME numbers
 * the API would have returned, from one place. Do not hardcode these in a
 * component; if they ever go out of date they must go out of date exactly once.
 * This table and the backend's routes/public._FALLBACK are the only two copies
 * that exist, and they must move together.
 */
export const FALLBACK_STATS: PublicStats = {
  live_since: "2026-06-17",
  as_of: "2026-08-24",
  n_sessions: 24,
  club_pct: 6.85,
  spy_pct: 3.04,
  points: [],
  universe_size: 20,
  stale: true,
};

/** Carries the HTTP status so a caller can tell a validation 400 (show the
 *  message) from a 429 (ask them to wait) from a 0 (we never reached the API). */
export class PublicApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "PublicApiError";
    this.status = status;
  }
}

/** FastAPI's `detail` is a string for app errors but a list of {loc, msg} for
 *  422s — flatten either into one sentence, never "[object Object]". */
async function detailOf(res: Response): Promise<string> {
  try {
    const d: unknown = (await res.json())?.detail;
    if (typeof d === "string" && d) return d;
    if (Array.isArray(d)) {
      const parts = d.map((x) => {
        const msg = (x as { msg?: unknown })?.msg;
        return typeof msg === "string" ? msg.replace(/^Value error, /, "") : "invalid value";
      });
      if (parts.length) return parts.join(" · ");
    }
  } catch {
    // a proxy error page, an empty body — fall through to the status text
  }
  return res.statusText || `request failed (${res.status})`;
}

async function request<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const hasBody = opts.body !== undefined;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  // The deadline covers the BODY too, not just the headers: a proxy that
  // answers 200 and then stalls the stream would otherwise hang forever on
  // `res.json()` with the timer already cleared — exactly the never-resolving
  // spinner this module exists to prevent.
  try {
    let res: Response;
    try {
      res = await fetch(`${BASE}${path}`, {
        method: opts.method ?? "GET",
        // Only on a request that HAS a body: a Content-Type on a plain GET makes
        // it a non-simple cross-origin request, i.e. a preflight round-trip in
        // front of the one call every page load makes.
        headers: hasBody ? { "Content-Type": "application/json" } : undefined,
        body: hasBody ? JSON.stringify(opts.body) : undefined,
        signal: ctrl.signal,
        // These endpoints are anonymous by design — never let the member session
        // cookie ride along to a route that does not authenticate.
        credentials: "omit",
        // The GET is deliberately left on the default policy so the route's own
        // `Cache-Control: public, max-age=60` can actually do its job — the
        // backend sets that header precisely so a repeat visit costs nothing,
        // and a blanket `no-store` here would have thrown that layer away. The
        // waitlist POST is never cached by anything, so it keeps no-store.
        cache: hasBody ? "no-store" : "default",
      });
    } catch {
      // fetch rejects for a network failure AND for our own abort; neither should
      // reach a marketing section as a bare TypeError/DOMException.
      throw new PublicApiError("could not reach the club", 0);
    }
    if (!res.ok) throw new PublicApiError(await detailOf(res), res.status);
    try {
      return (await res.json()) as T;
    } catch {
      // A body that died mid-stream is a reachability failure, not a malformed
      // payload — report it as the 0 a caller already knows how to fall back on.
      if (ctrl.signal.aborted) throw new PublicApiError("could not reach the club", 0);
      // 200 with a body that is not JSON = something between us and the API is
      // answering instead of it.
      throw new PublicApiError("unreadable response", res.status);
    }
  } finally {
    clearTimeout(timer);
  }
}

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Build a guaranteed-complete PublicStats out of an untrusted payload. Each
 *  field falls back independently, so one missing key never discards a series
 *  that arrived intact. */
function normalise(raw: unknown): PublicStats {
  const r = (raw ?? {}) as Partial<Record<keyof PublicStats, unknown>>;
  const points = Array.isArray(r.points)
    ? (r.points as unknown[]).flatMap((p) => {
        const o = p as Partial<Record<keyof PublicStatsPoint, unknown>>;
        // A point with no index value is dropped, not zeroed: a 0 in a
        // growth-of-100 series draws a crash that never happened.
        if (!o || typeof o.d !== "string") return [];
        const club = numOrNull(o.club);
        if (club === null) return [];
        return [{ d: o.d, club, spy: numOrNull(o.spy) }];
      })
    : [];
  const club_pct = num(r.club_pct, FALLBACK_STATS.club_pct);
  const spy_pct = num(r.spy_pct, FALLBACK_STATS.spy_pct);
  // Did either headline figure come from the §2 table rather than the wire?
  // If so this payload is NOT live, whatever `stale` claims — presenting the
  // frozen 6.85 / 3.04 as a current reading would be the page stating a return
  // it did not just verify. Falling back is allowed; mislabelling it is not.
  const substituted =
    typeof r.club_pct !== "number" || !Number.isFinite(r.club_pct) ||
    typeof r.spy_pct !== "number" || !Number.isFinite(r.spy_pct);
  return {
    live_since: typeof r.live_since === "string" && r.live_since
      ? r.live_since : FALLBACK_STATS.live_since,
    as_of: typeof r.as_of === "string" && r.as_of ? r.as_of : FALLBACK_STATS.as_of,
    n_sessions: num(r.n_sessions, FALLBACK_STATS.n_sessions),
    club_pct,
    spy_pct,
    points,
    universe_size: num(r.universe_size, FALLBACK_STATS.universe_size),
    // A backend that predates the flag but sent a real series is not stale;
    // one that sent no series — or no usable percentages — is, whatever it says.
    stale: substituted || points.length === 0
      ? true
      : typeof r.stale === "boolean" ? r.stale : false,
  };
}

/**
 * The club's published record. Rejects with a PublicApiError when the API is
 * unreachable, rate-limited or broken — catch it and render FALLBACK_STATS:
 *
 *   const stats = await getPublicStats().catch(() => FALLBACK_STATS);
 */
export async function getPublicStats(): Promise<PublicStats> {
  return normalise(await request<unknown>("/api/public/stats"));
}

/**
 * Leave an address on the waitlist. `source` labels which block of the page it
 * came from (a plain slug like "hero" or "footer"; anything else is dropped
 * server-side).
 *
 * Resolves for a duplicate exactly as for a first-time submit — the backend
 * refuses to say whether an address is already known, so the UI must not try to
 * distinguish either. Rejects with a PublicApiError whose `.message` is safe to
 * show under the input (400 = the address is malformed, 429 = too many tries).
 */
export async function joinWaitlist(
  email: string,
  source?: string,
): Promise<{ ok: true }> {
  try {
    await request<unknown>("/api/public/waitlist", {
      method: "POST",
      body: { email, source },
    });
  } catch (e) {
    // Defensive: an earlier draft of the API contract answered a known address
    // with 409. Treat it as the success it is for the person typing — being
    // already on the list is not an error to put in front of them.
    if (e instanceof PublicApiError && e.status === 409) return { ok: true };
    throw e;
  }
  return { ok: true };
}
