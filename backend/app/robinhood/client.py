"""Robinhood Agentic Trading MCP client (streamable-HTTP JSON-RPC).

`McpClient` is lifted verbatim from forge-dashboard/jobs/robinhood_refresh/
refresh.py (the proven transport). `RobinhoodClient` adds the typed read +
trade tool wrappers we need, including review_equity_order / place_equity_order.
"""
from __future__ import annotations

import json
import uuid

import requests

from ..config import settings

PROTOCOL_VERSION = "2025-06-18"


class McpClient:
    """Minimal MCP streamable-HTTP client (verbatim shape from refresh.py)."""

    def __init__(self, url: str, bearer: str):
        self.url = url
        self.headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.session_id: str | None = None
        self._id = 0

    def _post(self, payload: dict) -> dict | None:
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = requests.post(self.url, json=payload, headers=headers, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"MCP HTTP {r.status_code}: {r.text[:300]}")
        sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        ctype = r.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            result = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        result = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            return result
        if not r.text.strip():
            return None
        return r.json()

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        resp = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        if resp is None:
            raise RuntimeError(f"empty response for {method}")
        if resp.get("error"):
            raise RuntimeError(f"{method} -> {resp['error']}")
        return resp.get("result") or {}

    def notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> dict:
        result = self.rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "concord", "version": "1.0"},
        })
        self.notify("notifications/initialized")
        return result

    def call_tool(self, name: str, arguments: dict, retries: int = 4) -> dict:
        import re
        import time
        for attempt in range(retries):
            result = self.rpc("tools/call", {"name": name, "arguments": arguments})
            if result.get("isError"):
                text = json.dumps(result)
                throttled = "429" in text or "throttl" in text.lower()
                if throttled and attempt < retries - 1:
                    # Robinhood rate-limit: it tells us "available in N seconds".
                    # Sleep that long (+1) and retry; ref_id keeps places idempotent.
                    m = re.search(r"available in (\d+)", text)
                    time.sleep(min((int(m.group(1)) + 1) if m else 5, 35))
                    continue
                raise RuntimeError(f"tool {name} errored: {text[:300]}")
            if isinstance(result.get("structuredContent"), dict):
                return result["structuredContent"]
            for block in result.get("content") or []:
                if block.get("type") == "text":
                    try:
                        return json.loads(block["text"])
                    except json.JSONDecodeError:
                        continue
            raise RuntimeError(f"tool {name}: no JSON payload in response")
        raise RuntimeError(f"tool {name}: throttled after {retries} attempts")


class RobinhoodClient:
    """High-level per-member client. Construct with a *fresh* access token."""

    def __init__(self, access_token: str):
        self.mcp = McpClient(settings.rh_resource, access_token)
        self.mcp.initialize()

    # ── read ──────────────────────────────────────────────────────────────
    def get_accounts(self) -> dict:
        return self.mcp.call_tool("get_accounts", {})

    def get_portfolio(self, account_number: str) -> dict:
        return self.mcp.call_tool("get_portfolio", {"account_number": account_number})

    def get_positions(self, account_number: str) -> dict:
        return self.mcp.call_tool("get_equity_positions", {"account_number": account_number})

    def get_quotes(self, symbols: list[str]) -> dict:
        return self.mcp.call_tool("get_equity_quotes", {"symbols": symbols})

    # ── trade ─────────────────────────────────────────────────────────────
    def review_market_buy(self, account_number: str, symbol: str, dollar_amount: str) -> dict:
        """Simulate a dollar-denominated market buy (regular hours)."""
        return self.mcp.call_tool("review_equity_order", {
            "account_number": account_number,
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "dollar_amount": dollar_amount,
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
        })

    def place_market_buy(self, account_number: str, symbol: str, dollar_amount: str,
                         ref_id: str | None = None) -> dict:
        """Place a REAL dollar-denominated market buy. ref_id = idempotency key."""
        return self.mcp.call_tool("place_equity_order", {
            "account_number": account_number,
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "dollar_amount": dollar_amount,
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
            "ref_id": ref_id or str(uuid.uuid4()),
        })

    def review_market_sell(self, account_number: str, symbol: str, quantity: str) -> dict:
        """Simulate a share-quantity market sell (used to fully exit a position)."""
        return self.mcp.call_tool("review_equity_order", {
            "account_number": account_number,
            "symbol": symbol,
            "side": "sell",
            "type": "market",
            "quantity": quantity,
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
        })

    def place_market_sell(self, account_number: str, symbol: str, quantity: str,
                          ref_id: str | None = None) -> dict:
        """Place a REAL share-quantity market sell. ref_id = idempotency key."""
        return self.mcp.call_tool("place_equity_order", {
            "account_number": account_number,
            "symbol": symbol,
            "side": "sell",
            "type": "market",
            "quantity": quantity,
            "market_hours": "regular_hours",
            "time_in_force": "gfd",
            "ref_id": ref_id or str(uuid.uuid4()),
        })


def find_agentic_account(accounts_payload: dict) -> dict | None:
    """Pick the agentic_allowed=true account from a get_accounts payload.

    The payload shape can be {data:{accounts:[...]}} or {data:[...]} or a bare
    list — handle defensively since the upstream wraps tool output.
    """
    data = accounts_payload.get("data", accounts_payload)
    if isinstance(data, dict):
        accounts = data.get("accounts") or data.get("results") or []
    elif isinstance(data, list):
        accounts = data
    else:
        accounts = []
    for a in accounts:
        if a.get("agentic_allowed") in (True, "true", 1):
            return a
    return accounts[0] if accounts else None
