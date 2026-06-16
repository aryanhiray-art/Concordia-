#!/usr/bin/env bash
# One-time host setup for Concord on the forge VPS.
# Creates an ISOLATED unix user `concord` that cannot read the other forge
# projects' secrets (and vice-versa), deploys the code to /opt/concord, builds
# both halves, and installs the systemd units. Run as root.
set -euo pipefail

CONCORD_HOME=/opt/concord
SRC=${1:-/root/forge/concord}   # where the repo's concord/ dir lives on the box

echo "── 1. dedicated, sandboxed user ──"
id concord &>/dev/null || useradd --system --create-home --home-dir "$CONCORD_HOME" --shell /usr/sbin/nologin concord

echo "── 2. deploy code → $CONCORD_HOME ──"
mkdir -p "$CONCORD_HOME"
rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude '.next' \
      --exclude 'concord.db*' --exclude '.env' "$SRC"/ "$CONCORD_HOME"/
chown -R concord:concord "$CONCORD_HOME"
chmod 700 "$CONCORD_HOME"

echo "── 3. backend venv ──"
sudo -u concord python3 -m venv "$CONCORD_HOME/backend/.venv"
sudo -u concord "$CONCORD_HOME/backend/.venv/bin/pip" install -q -r "$CONCORD_HOME/backend/requirements.txt"

echo "── 4. .env (edit secrets after!) ──"
if [ ! -f "$CONCORD_HOME/.env" ]; then
  cp "$CONCORD_HOME/.env.example" "$CONCORD_HOME/.env"
  KEY=$(sudo -u concord "$CONCORD_HOME/backend/.venv/bin/python" -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
  SESS=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
  sed -i "s|^CONCORD_FERNET_KEY=.*|CONCORD_FERNET_KEY=$KEY|" "$CONCORD_HOME/.env"
  sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=$SESS|" "$CONCORD_HOME/.env"
  echo "  -> generated FERNET + SESSION secrets. NOW set INVITE_CODE, API_PUBLIC_URL, WEB_ORIGIN, EXECUTION_MODE."
fi
chown concord:concord "$CONCORD_HOME/.env"
chmod 600 "$CONCORD_HOME/.env"

echo "── 5. seed universe ──"
( cd "$CONCORD_HOME/backend" && sudo -u concord ./.venv/bin/python -m app.seed )

echo "── 6. systemd units ──"
cp "$CONCORD_HOME"/infra/concord-*.service "$CONCORD_HOME"/infra/concord-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now concord-api.service
systemctl enable --now concord-open-vote.timer concord-close-vote.timer concord-execute.timer

echo "── done. nginx: install infra/nginx-concord-api.conf and reload. ──"
systemctl --no-pager status concord-api.service | head -5
