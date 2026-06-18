"""Concord FastAPI app — entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8780
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import cors_origins, settings
from .db import init_db, is_armed
from .routes import admin, auth, members, program, rh_oauth, voting
from .seed import seed_universe

app = FastAPI(title="Concord", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    # also accept any localhost port (dev/preview servers move ports around)
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
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
