# Beta channel: beta.voteconcordia.com

The real club's staging channel on the SAME box as the live instance: identical
code and real broker flows, but its **own database, own secrets, own unix
users**, and execution **hard-locked to dry-run** — `APP_ENV=beta` forces
`settings.is_live` False in `backend/app/config.py`, so even
`EXECUTION_MODE=live` + an armed kv row cannot place a real order there.
Frontend branding (".beta" suffix, Google hidden) comes from `isBetaHost()` at
runtime — it is the same Next build as live.

Ports/paths (live = 8780/3002 at /opt/concord + /opt/concord-web):

| piece | unit               | port | path                 |
|-------|--------------------|------|----------------------|
| API   | concord-api-beta   | 8782 | /opt/concord-beta    |
| web   | concord-web-beta   | 3004 | /opt/concord-web-beta|

## One-time provisioning (run as root on the live box)

1. **Isolated users** — beta must not be able to read the live instance's
   secrets (and vice-versa), same philosophy as the live/demo split:
   ```
   useradd --system --create-home --home-dir /opt/concord-beta     --shell /usr/sbin/nologin concord-beta
   useradd --system --create-home --home-dir /opt/concord-web-beta --shell /usr/sbin/nologin concord-web-beta
   chmod 700 /opt/concord-beta
   ```
2. **First code drop** — from a clean checkout on the box (later refreshes go
   through `scripts/deploy-beta.sh`):
   ```
   rsync -a --exclude .git --exclude backend/.venv --exclude 'backend/concord.db*' \
         --exclude backend/demo.db --exclude .env --exclude web/ \
         /root/concordia/ /opt/concord-beta/
   rsync -a --exclude node_modules --exclude .next /root/concordia/web/ /opt/concord-web-beta/
   chown -R concord-beta:concord-beta /opt/concord-beta
   chown -R concord-web-beta:concord-web-beta /opt/concord-web-beta
   ```
3. **Backend venv + seed**:
   ```
   sudo -u concord-beta python3 -m venv /opt/concord-beta/backend/.venv
   sudo -u concord-beta /opt/concord-beta/backend/.venv/bin/pip install -q -r /opt/concord-beta/backend/requirements.txt
   ( cd /opt/concord-beta/backend && sudo -u concord-beta ./.venv/bin/python -m app.seed )
   ```
4. **.env** — write /opt/concord-beta/.env by hand (never copy the live one):
   fresh `CONCORD_FERNET_KEY` + `SESSION_SECRET` + `CONCORD_ADMIN_TOKEN`, its
   own `INVITE_CODE`, `API_PUBLIC_URL=https://beta.voteconcordia.com`,
   `WEB_ORIGIN=https://beta.voteconcordia.com`, and — non-negotiable —
   `APP_ENV=beta` and `EXECUTION_MODE=dry_run`. Then
   `chown concord-beta:concord-beta` + `chmod 600` it.
5. **Web build**:
   ```
   cd /opt/concord-web-beta && sudo -u concord-web-beta npm ci --no-audit --no-fund && sudo -u concord-web-beta npx next build
   ```
6. **Units**:
   ```
   cp /root/concordia/infra/concord-api-beta.service /root/concordia/infra/concord-web-beta.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now concord-api-beta concord-web-beta
   curl -s http://127.0.0.1:8782/health
   ```
   No timers: beta has no open-vote/close-vote/execute schedule until the owner
   decides it needs one — and any future beta timers must point at the beta
   units, never the live ones.
7. **nginx + TLS** (DNS A record for beta.voteconcordia.com must already point
   at this box):
   ```
   cp /root/concordia/infra/nginx-concord-web-beta.conf /etc/nginx/sites-available/concord-web-beta
   ln -sf /etc/nginx/sites-available/concord-web-beta /etc/nginx/sites-enabled/concord-web-beta
   nginx -t && systemctl reload nginx
   certbot --nginx -d beta.voteconcordia.com
   ```

## Verifying the safety lock

After boot, `curl -s http://127.0.0.1:8782/health` must show the instance in
dry-run; and because `APP_ENV=beta` pins `is_live` False in code, flipping
`EXECUTION_MODE` or arming via the admin API still cannot place real orders
(covered by `backend/tests/test_beta_safety_lock.py`).

`APP_ENV=beta` also inherits prod's hardening (`settings.is_internet_facing`):
the API **refuses to boot** on a default/blank `SESSION_SECRET`, `INVITE_CODE`
or missing `CONCORD_FERNET_KEY` (so a botched step 4 fails loudly, not open),
session cookies are stamped `Secure`, and the localhost CORS carve-out is off.

## Redeploys

`./scripts/deploy-beta.sh` from a clean, pushed checkout — same guards as the
live `deploy.sh`, but it stages to /root/concordia-beta, rsyncs to the beta
paths, and restarts only the beta units. It never touches the beta DB or .env.
