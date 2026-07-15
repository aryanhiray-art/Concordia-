"""Concord FastAPI app — entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8780
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import cors_origins, insecure_prod_config, settings
from .db import init_db, is_armed
from .routes import admin, auth, members, program, rh_oauth, voting
from .seed import seed_universe

app = FastAPI(title="Concord", version="1.0")

_cors_kwargs: dict = dict(
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Any-localhost-port is a dev/preview convenience (servers hop ports) AND a
# credentialed-CORS hole — restrict it to non-prod (finding #27). In prod only
# the explicit web_origin is allowed.
if not settings.is_prod:
    _cors_kwargs["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
app.add_middleware(CORSMiddleware, **_cors_kwargs)


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
app.include_router(program.router)
app.include_router(admin.router)
