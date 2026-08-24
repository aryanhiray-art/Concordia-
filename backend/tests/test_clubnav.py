"""The club's REAL-money track record (services/clubnav).

Why this file exists: the previous chart compounded a paper decisions index and
printed +6.0% for a period in which members' real accounts did +4.8%, -3.2%
and +0.3%. A club's public record is the money, so these tests pin the
properties that keep it honest.

Contract under test:
- daily chained TWR: the index compounds real value changes
- a LATE JOINER's arriving capital is never a gain
- deposits/withdrawals are excluded (flow table + >25% backstop)
- bots (seeded flat rows, no brokerage account) never enter the index
- the recorded CLUB SLICE is preferred; an account total is used only for a
  member who committed 100% (their account IS the slice), so personal
  positions and gifted shares cannot inflate the club's record
- the ledger round-trips: computed days are kept and served

Run from backend/:  python -m pytest tests/test_clubnav.py -q
"""
import os
import tempfile
import time

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())

from app.db import db, init_db  # noqa: E402
from app.services import clubnav  # noqa: E402

DAY = 86_400
# 16:00 ET on a weekday, so every point lands in the intended ET date bucket
BASE = 1_781_884_800


def setup_module(_):
    init_db()


def _mk(mid: int, name: str, *, is_bot=0, alloc_type="percent", alloc_value=100.0):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO members(id,email,display_name,role,is_bot,created_at) "
                     "VALUES(?,?,?,'member',?,?)",
                     (mid, f"{name}@navclub.test", name, is_bot, int(time.time())))
        conn.execute("INSERT OR IGNORE INTO member_settings(member_id,opted_in,buying_power_pct,"
                     "daily_cap_usd,max_orders,voting_power,alloc_type,alloc_value,updated_at) "
                     "VALUES(?,1,0,0,25,0,?,?,?)",
                     (mid, alloc_type, alloc_value, int(time.time())))
    return mid


def _pt(mid: int, day: int, total, club=None):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO equity_points(member_id,ts,total_value,club_value) "
                     "VALUES(?,?,?,?)", (mid, BASE + day * DAY, total, club))


def _clear():
    with db() as conn:
        conn.execute("DELETE FROM equity_points")
        conn.execute("DELETE FROM club_nav")
        conn.execute("DELETE FROM members WHERE email LIKE '%@navclub.test'")


def test_index_chains_real_value_changes():
    _clear()
    m = _mk(101, "solo")
    _pt(m, 0, 100.0); _pt(m, 1, 110.0); _pt(m, 2, 99.0)
    pts = clubnav.compute()
    assert [p["club"] for p in pts] == [100.0, 110.0, 99.0]


def test_late_joiner_capital_is_not_a_gain():
    _clear()
    a = _mk(102, "early"); b = _mk(103, "late")
    _pt(a, 0, 100.0); _pt(a, 1, 100.0); _pt(a, 2, 100.0)
    _pt(b, 1, 900.0)                      # arrives with 9x the capital
    _pt(b, 2, 900.0)
    pts = clubnav.compute()
    assert pts[-1]["club"] == 100.0, "a member joining must not move the index"


def test_deposit_is_excluded_from_return():
    _clear()
    m = _mk(104, "depositor")
    _pt(m, 0, 100.0); _pt(m, 1, 500.0)    # +400% in a day = a deposit, not a return
    pts = clubnav.compute()
    assert pts[-1]["club"] == 100.0
    # and the same via the explicit flow ledger (a small, in-guard deposit)
    _clear()
    m = _mk(105, "flowed")
    _pt(m, 0, 100.0); _pt(m, 1, 110.0)
    with db() as conn:
        conn.execute("INSERT INTO member_flows(member_id,ts,amount,kind,source) "
                     "VALUES(?,?,?,'suspected','auto')", (m, BASE + DAY, 10.0))
    assert clubnav.compute()[-1]["club"] == 100.0


def test_bots_never_enter_the_index():
    _clear()
    human = _mk(106, "human"); bot = _mk(107, "botvoter", is_bot=1)
    _pt(human, 0, 100.0); _pt(human, 1, 110.0)
    _pt(bot, 0, 100.0); _pt(bot, 1, 100.0)   # flat bot rows would halve the move
    assert clubnav.compute()[-1]["club"] == 110.0


def test_club_slice_preferred_and_partial_stakes_need_it():
    _clear()
    # dollar-stake member: account grew on a GIFTED share while the slice fell.
    # Without a recorded slice value this member cannot enter the record at all.
    partial = _mk(108, "partial", alloc_type="dollar", alloc_value=100.0)
    _pt(partial, 0, 100.0); _pt(partial, 1, 111.0)
    assert clubnav.compute() == [] or clubnav.compute()[-1]["club"] == 100.0
    # once the slice IS recorded, the SLICE drives the record, not the account
    _pt(partial, 0, 100.0, club=100.0)
    _pt(partial, 1, 111.0, club=98.0)
    assert clubnav.compute()[-1]["club"] == 98.0


def test_ledger_round_trips():
    _clear()
    m = _mk(109, "keeper")
    _pt(m, 0, 100.0); _pt(m, 1, 105.0)
    clubnav.record(clubnav.compute())
    served = clubnav.series()
    assert served["basis"] == "real_money"
    assert served["club_pct"] == 5.0
    assert len(served["points"]) == 2
