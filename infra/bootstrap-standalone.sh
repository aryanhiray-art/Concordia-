#!/usr/bin/env bash
# Bootstrap a FRESH standalone Concordia box (Ubuntu 24.04) from this repo.
# Run as root:  HOST=concord.<ip>.sslip.io SRC=/root/concordia ./bootstrap-standalone.sh
#
# MIGRATION-SAFE BY DESIGN: installs everything but enables ONLY nginx.
# concord-api starts after you restore .env + concord.db from the old box;
# the trading timers are NEVER enabled here — that is the explicit last step
# of the cutover (see MIGRATION.md), so two boxes can never both trade.
set -euo pipefail

HOST=${HOST:?set HOST=<public hostname> (sslip.io of the new IP, or the real domain)}
SRC=${SRC:-/root/concordia}          # a checkout of the concordia repo on this box
CONCORD_HOME=/opt/concord
WEB_HOME=/opt/concord-web

echo "── 0. base packages + swap + firewall ──"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -qy python3-venv python3-pip nginx certbot python3-certbot-nginx \
                    sqlite3 rsync ufw fail2ban curl ca-certificates gnupg
# node 22 (nodesource) if missing
if ! command -v node >/dev/null || [ "$(node -e 'console.log(process.versions.node.split(".")[0])')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -qy nodejs
fi
# 2G swap so `next build` never OOMs a small box
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban

echo "── 1. isolated users ──"
id concord     &>/dev/null || useradd --system --create-home --home-dir "$CONCORD_HOME" --shell /usr/sbin/nologin concord
id concord-web &>/dev/null || useradd --system --create-home --home-dir "$WEB_HOME"     --shell /usr/sbin/nologin concord-web

echo "── 2. backend → $CONCORD_HOME ──"
mkdir -p "$CONCORD_HOME"
rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude '.next' \
      --exclude 'concord.db*' --exclude '.env' --exclude 'backups/' --exclude '.ssh/' \
      --exclude 'web/' "$SRC"/ "$CONCORD_HOME"/
chown -R concord:concord "$CONCORD_HOME"
chmod 700 "$CONCORD_HOME"
sudo -u concord python3 -m venv "$CONCORD_HOME/backend/.venv"
sudo -u concord "$CONCORD_HOME/backend/.venv/bin/pip" install -q -r "$CONCORD_HOME/backend/requirements.txt"

echo "── 3. member app → $WEB_HOME ──"
mkdir -p "$WEB_HOME"
rsync -a --delete --exclude 'node_modules' --exclude '.next' "$SRC"/web/ "$WEB_HOME"/
chown -R concord-web:concord-web "$WEB_HOME"
( cd "$WEB_HOME" && sudo -u concord-web npm ci --no-audit --no-fund && sudo -u concord-web npx next build )

echo "── 4. systemd units (installed, NOT enabled — cutover enables them) ──"
cp "$SRC"/infra/concord-*.service "$SRC"/infra/concord-*.timer /etc/systemd/system/
systemctl daemon-reload

echo "── 5. nginx + TLS ──"
sed "s/__HOST__/$HOST/" "$SRC"/infra/nginx-concord-web.conf > /etc/nginx/sites-available/concord-web
ln -sf /etc/nginx/sites-available/concord-web /etc/nginx/sites-enabled/concord-web
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx --non-interactive --agree-tos --register-unsafely-without-email -d "$HOST" || \
  echo "!! certbot failed — fix + re-run: certbot --nginx -d $HOST"

cat <<EOF

── bootstrap done. NOTHING is trading. Next (from MIGRATION.md): ──
 1. restore /opt/concord/.env from the old box (same FERNET/SESSION secrets,
    API_PUBLIC_URL=https://$HOST  WEB_ORIGIN=https://$HOST), chmod 600, chown concord.
 2. restore concord.db (sqlite3 .backup copy) → $CONCORD_HOME/backend/concord.db, chown concord.
 3. systemctl enable --now concord-api concord-web  → verify /health + member login + decrypt smoke.
 4. run the DRY-RUN execute check.
 5. ONLY at cutover: disable timers on the OLD box, then enable timers here.
EOF
