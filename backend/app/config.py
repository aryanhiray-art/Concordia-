"""Env-driven configuration for the Concord backend.

All knobs live here so behaviour (execution mode, caps, schedule) is set by the
`.env` file (chmod 600, owned by the `concord` unix user) — never hardcoded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load concord/.env (one level up from backend/) then backend/.env if present.
    _here = Path(__file__).resolve()
    load_dotenv(_here.parents[2] / ".env")
    load_dotenv(_here.parents[1] / ".env", override=True)
except Exception:  # dotenv optional in prod where env is provided by systemd
    pass


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    app_env: str = os.environ.get("APP_ENV", "dev")
    db_path: str = os.environ.get("CONCORD_DB_PATH", str(Path(__file__).resolve().parents[1] / "concord.db"))

    session_secret: str = os.environ.get("SESSION_SECRET", "dev-insecure-session-secret")
    fernet_key: str = os.environ.get("CONCORD_FERNET_KEY", "")

    invite_code: str = os.environ.get("INVITE_CODE", "concord")
    owner_email: str = os.environ.get("OWNER_EMAIL", "").strip().lower()
    # Static service token: lets the forge-dashboard backend read owner/admin
    # endpoints without a member session (backend-to-backend). Empty = disabled.
    admin_api_token: str = os.environ.get("CONCORD_ADMIN_TOKEN", "").strip()

    web_origin: str = os.environ.get("WEB_ORIGIN", "http://localhost:3000")
    api_public_url: str = os.environ.get("API_PUBLIC_URL", "http://localhost:8780").rstrip("/")

    # Robinhood Agentic Trading endpoints
    rh_resource: str = os.environ.get("RH_RESOURCE", "https://agent.robinhood.com/mcp/trading")
    rh_register_url: str = os.environ.get("RH_REGISTER_URL", "https://agent.robinhood.com/oauth/trading/register")
    rh_authorize_url: str = os.environ.get("RH_AUTHORIZE_URL", "https://robinhood.com/oauth")
    rh_token_url: str = os.environ.get("RH_TOKEN_URL", "https://api.robinhood.com/oauth2/token/")
    rh_scope: str = os.environ.get("RH_SCOPE", "internal")
    rh_client_id_env: str = os.environ.get("RH_CLIENT_ID", "").strip()

    # Trading behaviour
    execution_mode: str = os.environ.get("EXECUTION_MODE", "dry_run").strip().lower()
    top_n: int = _int("TOP_N", 5)
    vote_open_at: str = os.environ.get("VOTE_OPEN_AT", "18:00")
    vote_close_at: str = os.environ.get("VOTE_CLOSE_AT", "09:25")
    execute_at: str = os.environ.get("EXECUTE_AT", "09:31")

    # Caps
    max_notional_per_member: float = _float("MAX_NOTIONAL_PER_MEMBER", 2000.0)
    default_buying_power_pct: float = _float("DEFAULT_BUYING_POWER_PCT", 10.0)
    default_daily_cap_usd: float = _float("DEFAULT_DAILY_CAP_USD", 500.0)
    default_max_orders: int = _int("DEFAULT_MAX_ORDERS", 10)

    @property
    def rh_redirect_uri(self) -> str:
        return f"{self.api_public_url}/oauth/robinhood/callback"

    @property
    def is_live(self) -> bool:
        return self.execution_mode == "live"


settings = Settings()


def cors_origins() -> list[str]:
    """Origins allowed to call the API (the web app, local + deployed)."""
    origins = {settings.web_origin, "http://localhost:3000", "http://127.0.0.1:3000"}
    return [o for o in origins if o]
