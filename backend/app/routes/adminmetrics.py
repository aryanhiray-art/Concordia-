"""GET /api/admin/metrics — the tracking layer's read surface.

Separate module from routes/admin.py on purpose (parallel workstreams own that
file); same auth (require_admin: owner or cofounder via an interactive session,
or the static service token) and the same freshness philosophy as /panel:
STRICTLY DB-ONLY, no live broker calls. The daily series is whatever the
nightly job (jobs/daily_metrics.py) has written; the snapshot is today's
numbers recomputed live from the same event tables (cheap indexed reads), so
the console never shows a stale "now" even if the timer hasn't fired yet.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ..services import metrics
from .deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/metrics")
def admin_metrics(days: int = 90, admin: dict = Depends(require_admin)):
    """Daily series + current-moment snapshot + weekly voting-retention cohorts.

    `days` bounds the series window (7..730). An empty series means the nightly
    job hasn't run yet — the panel says so instead of blanking; the snapshot is
    always present regardless.
    """
    days = max(7, min(int(days), 730))
    return {
        "series": metrics.series(days),
        "snapshot": metrics.snapshot(),
        "cohorts": metrics.retention_cohorts(),
        "generated_at": int(time.time()),
    }
