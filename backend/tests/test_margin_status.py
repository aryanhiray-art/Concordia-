"""Limited-margin status + acknowledgment (the settlement-delay killer).

A cash agentic account waits T+1 for sale proceeds to settle, so a cash
member's redeploy runs a day behind. /me/margin-status tells the wizard the
member's account type and hands cash members the broker's self-serve
limited-margin upgrade links; /me/margin-ack records the explicit
"I understand my trades are delayed" choice for members who stay on cash.

Run from backend/:  python -m pytest tests/test_margin_status.py -q
(throwaway temp DB; Robinhood client + vault mocked — no network.)
"""
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.db import db, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.robinhood import vault  # noqa: E402
from app.security import hash_password, make_session  # noqa: E402

client = TestClient(app)


def setup_module(_):
    init_db()


_seq = [0]


def _member() -> tuple[int, str]:
    _seq[0] += 1
    email = f"margin{_seq[0]}@test.local"
    with db() as con:
        cur = con.execute(
            "INSERT INTO members(email,display_name,role,password_hash,"
            "email_verified_at,created_at) VALUES(?,?,?,?,?,?)",
            (email, f"M{_seq[0]}", "member", hash_password("pw12345678"),
             int(time.time()), int(time.time())),
        )
        mid = cur.lastrowid
    return mid, make_session(mid, 1)


class _Client:
    def __init__(self, acct: str, atype: str, unsettled: str = "0.00",
                 upgrade_ok: bool = True):
        self.acct, self.atype, self.unsettled = acct, atype, unsettled
        self.upgrade_ok = upgrade_ok

    def get_accounts(self):
        return {"data": {"accounts": [
            {"account_number": "999999", "type": "margin"},
            {"account_number": self.acct, "type": self.atype,
             "unsettled_funds": self.unsettled},
        ]}}

    def get_limited_margin_upgrade_info(self, account_number):
        if not self.upgrade_ok:
            raise RuntimeError("tool unavailable")
        assert account_number == self.acct
        return {"data": {
            "eligible": True,
            "web_upgrade_url": f"https://robinhood.com/contact?account_number={account_number}&x=1",
            "mobile_upgrade_url": f"https://applink.robinhood.com/self_serve?account_number={account_number}&x=1",
            "upgrade_fallback": "Settings → Investing → Account type",
        }}


def _wire(monkeypatch, mid: int, rh: _Client, acct: str):
    monkeypatch.setattr(vault, "account_number_for",
                        lambda m: acct if m == mid else None)
    monkeypatch.setattr(vault, "ensure_client", lambda m: rh)


def _get(tok: str, force: bool = True):
    return client.get(f"/api/me/margin-status?force={'true' if force else 'false'}",
                      cookies={"concord_session": tok})


def test_cash_account_is_delayed_with_upgrade_links(monkeypatch):
    mid, tok = _member()
    _wire(monkeypatch, mid, _Client("111222", "cash", unsettled="36.26"), "111222")
    r = _get(tok)
    assert r.status_code == 200
    j = r.json()
    assert j["connected"] is True
    assert j["account_type"] == "cash"
    assert j["delayed"] is True
    assert j["unsettled_funds"] == "36.26"
    assert "111222" in j["upgrade"]["web_upgrade_url"]
    assert "applink" in j["upgrade"]["mobile_upgrade_url"]
    assert j["acknowledged"] is False


def test_limited_margin_is_not_delayed_and_no_upgrade(monkeypatch):
    mid, tok = _member()
    _wire(monkeypatch, mid, _Client("333444", "limited_margin"), "333444")
    j = _get(tok).json()
    assert j["account_type"] == "limited_margin"
    assert j["delayed"] is False
    assert j["upgrade"] is None


def test_upgrade_tool_failure_falls_back_to_constructed_links(monkeypatch):
    mid, tok = _member()
    _wire(monkeypatch, mid, _Client("555666", "cash", upgrade_ok=False), "555666")
    j = _get(tok).json()
    assert j["delayed"] is True
    up = j["upgrade"]
    assert "switch-to-margin-account" in up["web_upgrade_url"]
    assert "555666" in up["web_upgrade_url"]
    assert "target_margin_type=limited" in up["mobile_upgrade_url"]


def test_ack_roundtrip_and_cache(monkeypatch):
    mid, tok = _member()
    _wire(monkeypatch, mid, _Client("777888", "cash"), "777888")
    assert _get(tok).json()["acknowledged"] is False
    r = client.put("/api/me/margin-ack", json={"acknowledged": True},
                   cookies={"concord_session": tok})
    assert r.status_code == 200 and r.json()["acknowledged"] is True
    # force=true re-probes and re-reads the ack
    assert _get(tok).json()["acknowledged"] is True
    # cached read (force=false) also carries it — the ack was written into
    # the cached payload on the forced read above
    assert _get(tok, force=False).json()["acknowledged"] is True


def test_not_connected(monkeypatch):
    mid, tok = _member()
    monkeypatch.setattr(vault, "account_number_for", lambda m: None)
    j = _get(tok).json()
    assert j == {"connected": False}
