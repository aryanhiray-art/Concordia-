#!/usr/bin/env bash
# Run the whole app locally against a FAKE Robinhood — no real money, no real
# brokerage calls, its own throwaway database.
#
#   ./scripts/dev.sh          # first run installs deps, then starts both servers
#
#   web  → http://localhost:3000
#   api  → http://localhost:8780
#   invite code: demo
#
# DEMO_MODE swaps the Robinhood client for a canned one, so "connect your
# account" walks the real UI against a stage-prop broker. Verification emails
# aren't sent — the 6-digit codes print to this terminal as `DEMO-MAIL`.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }
command -v npm     >/dev/null || { echo "node/npm required" >&2; exit 1; }

# ── backend ────────────────────────────────────────────────────────────────
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "→ creating backend venv (first run)…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi
# a throwaway key per run: the demo DB holds no real tokens
FERNET="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

DEMO_MODE=1 EXECUTION_MODE=dry_run CONCORD_DB_PATH=./demo.db \
INVITE_CODE=demo SESSION_SECRET=demo-local-only CONCORD_FERNET_KEY="$FERNET" \
WEB_ORIGIN=http://localhost:3000 API_PUBLIC_URL=http://localhost:8780 \
  .venv/bin/python -m uvicorn app.main:app --port 8780 --reload &
API_PID=$!

# ── frontend ───────────────────────────────────────────────────────────────
cd "$ROOT/web"
[ -d node_modules ] || { echo "→ npm install (first run)…"; npm install --silent; }

NEXT_PUBLIC_CONCORD_API=http://localhost:8780 npm run dev &
WEB_PID=$!

trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT INT TERM
echo
echo "  web    http://localhost:3000     (invite code: demo)"
echo "  api    http://localhost:8780"
echo "  email codes print here as DEMO-MAIL — ctrl-c to stop"
echo
wait
