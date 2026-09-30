"""Shape helpers for the broker's ORDER FEED — the one place its pagination
and envelope conventions are decoded.

Three services (positions, reconcile, executor) each carried a private copy of
this logic, all three assuming `next` was a URL carrying `?cursor=`. Robinhood
actually returns `next` as a bare opaque token (e.g. `cD0yMDI2LTA5LTEx…`, a
base64 `p=<timestamp>`), so every copy returned None on page one, every feed
read silently stopped at 200 orders, and the derived club position — and the
commitments that ride on it — collapsed to whatever the last 200 orders
happened to net. Nothing raised, because "no cursor" and "no next page" were
the same value.

The rules here are therefore deliberately liberal about WHAT a cursor looks
like and strict about telling "there is no next page" apart from "there is a
next page I could not decode":

- `cursor_from_next` accepts a bare token, a URL with `?cursor=`, or a dict
  carrying `cursor`/`next`, and returns None ONLY when the value is empty.
  A truthy `next` that decodes to nothing is the caller's signal to fail
  closed (positions) or to refuse to claim the feed was exhausted (executor).
- `normalize_orders` unwraps the `{"data": {...}}` envelope and returns the
  page plus the RAW `next` value, so callers can make that distinction.
"""
from __future__ import annotations

import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse


def cursor_from_next(nxt: Any) -> str | None:
    """The cursor to request the next page with, or None when `nxt` is empty
    or undecodable. Callers must test `nxt` itself to tell those two apart."""
    if nxt is None:
        return None
    if isinstance(nxt, dict):
        for key in ("cursor", "next"):
            if nxt.get(key):
                return cursor_from_next(nxt[key])
        return None
    s = str(nxt).strip()
    if not s:
        return None
    if "://" in s or s.startswith("/") or "?" in s:
        try:
            c = parse_qs(urlparse(s).query).get("cursor")
        except Exception:  # noqa: BLE001
            return None
        return c[0] if c and c[0] else None
    return s                                   # a bare opaque token — pass it back as-is


def normalize_orders(payload: Any) -> tuple[list[dict], Any]:
    """(orders, raw_next) from a feed page in any of the envelopes the MCP
    has used: {"data": {"orders": […], "next": …}}, {"data": {"results": …}},
    the bare dict, or a bare list (no pagination)."""
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        orders = data.get("orders") or data.get("results") or []
        return [o for o in orders if isinstance(o, dict)], data.get("next")
    if isinstance(data, list):
        return [o for o in data if isinstance(o, dict)], None
    return [], None


def order_ts(order: dict) -> float | None:
    """Best-effort epoch seconds for a broker order's creation time (ISO 8601
    with Z or offset, or a numeric epoch)."""
    for key in ("created_at", "created_time", "updated_at"):
        v = order.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def oldest_ts(orders: list[dict]) -> float | None:
    """Creation time of the oldest order in a feed read, or None when no order
    carries a readable timestamp."""
    return min((t for t in (order_ts(o) for o in orders) if t is not None), default=None)
