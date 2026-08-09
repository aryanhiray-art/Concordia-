"""trade_readiness maps Robinhood's $1-probe alerts to onboarding verdicts.

The probe exists to catch EQUITY_SUITABILITY (questionnaire not done). A
fully-invested account — under $1 of idle cash — used to fall into the generic
"blocked" branch, so every ACTIVE member (whom the executor deliberately keeps
spent down to the reserve) failed the wizard's step-4 check forever, with a raw
Robinhood dict shown in red: EQUITY_NOT_ENOUGH_BP_DOLLAR_BASED / depositAmount
$0.24 on a working account (2026-08). Pin the mapping: no-idle-cash counts as
READY (reason "fully_invested"); the real blockers still block.

Run from backend/:  python -m pytest tests/test_trade_readiness.py -q
(throwaway temp DB; Robinhood client + vault mocked — no network.)
"""
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

from app.db import init_db  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.services import alloc  # noqa: E402


def setup_module(_):
    init_db()


class _Client:
    def __init__(self, checks):
        self._checks = checks

    def review_market_buy(self, acct, symbol, amount):
        assert symbol == "AAPL" and amount == "1.00"
        return {"data": {"order_checks": self._checks}}


def _probe(monkeypatch, member_id, checks):
    monkeypatch.setattr(vault, "account_number_for", lambda m: f"ACC{m}")
    monkeypatch.setattr(vault, "ensure_client", lambda m: _Client(checks))
    return alloc.trade_readiness(member_id, force=True)


def test_fully_invested_counts_as_ready(monkeypatch):
    # The exact alert from the live incident: dollar-based buy, 24¢ short.
    checks = {
        "alertType": "EQUITY_NOT_ENOUGH_BP_DOLLAR_BASED",
        "equityNotEnoughBpAlertDetails": {
            "depositAmount": {"amount": "0.2400", "currency": "USD"},
        },
    }
    out = _probe(monkeypatch, 9101, checks)
    assert out == {"connected": True, "ready": True, "reason": "fully_invested"}


def test_share_based_bp_variant_also_ready(monkeypatch):
    out = _probe(monkeypatch, 9102, {"alertType": "EQUITY_NOT_ENOUGH_BP"})
    assert out["ready"] is True
    assert out["reason"] == "fully_invested"


def test_questionnaire_still_blocks(monkeypatch):
    out = _probe(monkeypatch, 9103, {"alertType": "EQUITY_SUITABILITY"})
    assert out == {"connected": True, "ready": False, "reason": "needs_questionnaire"}


def test_other_alerts_still_block(monkeypatch):
    out = _probe(monkeypatch, 9104, {"alertType": "EQUITY_PATTERN_DAY_TRADE"})
    assert out["ready"] is False
    assert out["reason"] == "blocked"


def test_clean_review_is_ok(monkeypatch):
    out = _probe(monkeypatch, 9105, None)
    assert out == {"connected": True, "ready": True, "reason": "ok"}
