"""A non-finite quote must never let the liquidation check report a false PASS.

`verify.verify_member(expect_flat=True)` is what confirms a manual liquidation
actually emptied an account. It sorts club holdings into two buckets: `unpriced`
(no quote — cannot confirm, FAIL) and `leftover` (priced and worth more than
$1 — FAIL). A NaN price used to land in NEITHER: `px.get(s) is not None` reads
True for a NaN so it is not "unpriced", and `value > DUST_USD` reads False
because every comparison against NaN is False, so it is not "leftover" either.
The member's real, still-held shares fell straight through the gap and the
account was reported flat.

Storing None for an unusable quote puts it back in the bucket it belongs to.

Run from backend/:  python -m pytest tests/test_verify_nan.py -q
"""
import os
import tempfile

os.environ.setdefault("CONCORD_DB_PATH", tempfile.mkstemp(suffix=".db")[1])
os.environ.setdefault("SESSION_SECRET", "test-secret")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("CONCORD_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("TOP_N", "3")
os.environ.setdefault("EXECUTION_MODE", "dry_run")

from app.services import verify  # noqa: E402


class _Client:
    """Only the quote surface matters here."""

    def __init__(self, payload):
        self.payload = payload

    def get_quotes(self, symbols):
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": self.payload[s]}}
            for s in symbols if s in self.payload]}}


def test_a_nan_quote_reads_as_unpriced_not_as_zero():
    """The unit under the finding: _quotes must hand back None, not NaN."""
    px = verify._quotes(_Client({"ALL": "NaN", "KO": "88.61"}), ["ALL", "KO"])

    assert px["KO"] == 88.61, "a real price is untouched"
    assert px["ALL"] is None, "an unusable price is a MISSING price"
    # the exact expression verify_member uses to bucket the holding
    assert (px.get("ALL") is not None) is False, "so it counts as unpriced -> FAIL"


def test_infinity_is_screened_the_same_way():
    """json.loads accepts bare Infinity too, and it beats the > DUST_USD test
    in the opposite direction — also not a usable price."""
    px = verify._quotes(_Client({"ALL": "Infinity"}), ["ALL"])

    assert px["ALL"] is None
