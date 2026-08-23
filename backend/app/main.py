"""Concord FastAPI app — entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8780
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import cors_origins, insecure_prod_config, settings
from .db import audit, init_db, is_armed, kv_get, kv_set
from .routes import admin, adminmetrics, auth, club, members, program, rh_oauth, stocks, voting
from .seed import seed_universe

app = FastAPI(title="Concord", version="1.0")

_cors_kwargs: dict = dict(
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Any-localhost-port is a dev/preview convenience (servers hop ports) AND a
# credentialed-CORS hole — restrict it to dev/demo sandboxes (finding #27). On
# internet-facing instances (prod AND beta — beta vaults real tokens too) only
# the explicit web_origin is allowed. `admin.localhost` (any *.localhost label)
# is included so the admin-host flow is testable locally — deployed boxes never
# need this: the Next app proxies /api same-origin, so no preflight ever fires.
if not settings.is_internet_facing:
    _cors_kwargs["allow_origin_regex"] = r"https?://([a-z0-9-]+\.)?(localhost|127\.0\.0\.1)(:\d+)?"
app.add_middleware(CORSMiddleware, **_cors_kwargs)


# ─── config-change watch ─────────────────────────────────────────────────────
# .env flips (cadence, times, mode, caps) restart the API but historically left
# NO trace — the 08/20 weekly-cadence flip is only reconstructable from memory.
# On every boot we fingerprint the operationally-meaningful settings; a changed
# fingerprint audits a {key: [old, new]} diff, so every flip + restart lands in
# the audit trail.

_CONFIG_WATCH_KEYS = (
    "execution_mode", "cycle_cadence", "power_cadence", "power_anchor",
    "vote_open_at", "vote_close_at", "execute_at", "top_n",
    "max_notional_per_member", "min_human_voters", "cash_buffer_pct",
)


def check_config_changed() -> dict | None:
    """Compare current settings against the stored kv fingerprint.

    First boot (no stored fingerprint) stores silently. A change audits
    ("system", "config_changed", None, {"changed": {key: [old, new]}}) with
    ONLY the changed keys, then stores the new fingerprint. Returns the
    changed map (None when nothing changed). Requires init_db() to have run.
    """
    current = {k: getattr(settings, k) for k in _CONFIG_WATCH_KEYS}
    fingerprint = json.dumps(current, sort_keys=True)
    stored = kv_get("config_fingerprint")
    if stored == fingerprint:
        return None
    changed: dict | None = None
    if stored is not None:                     # first boot stores silently
        try:
            old = json.loads(stored)
        except ValueError:                     # unreadable blob — re-baseline
            old = {}
        changed = {k: [old.get(k), current[k]] for k in _CONFIG_WATCH_KEYS
                   if old.get(k) != current[k]} or None
        if changed:
            audit("system", "config_changed", None, {"changed": changed})
    kv_set("config_fingerprint", fingerprint)
    return changed


@app.on_event("startup")
def _startup() -> None:
    # Fail closed (finding #5): never boot a prod money system with a forgeable
    # default session secret / shared default invite code / missing vault key.
    problems = insecure_prod_config()
    if problems:
        raise RuntimeError(
            "Refusing to boot: insecure default(s) in prod — "
            + ", ".join(problems)
            + ". Set strong random values in the environment before starting."
        )
    init_db()
    seed_universe()
    check_config_changed()


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "concord",
        "app_env": settings.app_env,
        "execution_mode": settings.execution_mode,
        "armed": is_armed(),
    }


app.include_router(auth.router)
app.include_router(members.router)
app.include_router(rh_oauth.router)
app.include_router(voting.router)
app.include_router(stocks.router)
app.include_router(program.router)
app.include_router(club.router)
app.include_router(admin.router)
app.include_router(adminmetrics.router)
