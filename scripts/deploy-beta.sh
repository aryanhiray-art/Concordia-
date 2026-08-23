#!/usr/bin/env bash
# Deploy the current working tree to the BETA channel on the live box.
#
#   ./scripts/deploy-beta.sh            # deploy web + backend, restart both
#   ./scripts/deploy-beta.sh --web      # web only
#   ./scripts/deploy-beta.sh --backend  # backend only
#
# Same shape and guards as deploy.sh, aimed at the beta instance: it never
# touches /opt/concord-beta/.env or the beta DB, stages to its own dir
# (/root/concordia-beta) so it can't race a live deploy, and restarts only the
# beta units — the live concord-api/concord-web are never in its blast radius.
# It also refuses a dirty tree or unpushed commits — what's on beta should
# always be a commit that exists on GitHub, or nobody can reproduce a rollback.
set -euo pipefail

HOST="${CONCORD_HOST:-root@159.195.19.53}"
DO_WEB=1; DO_BACKEND=1
case "${1:-}" in
  --web)     DO_BACKEND=0 ;;
  --backend) DO_WEB=0 ;;
  "")        ;;
  *) echo "usage: $0 [--web|--backend]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain)" ]; then
  echo "✗ working tree is dirty — commit first (beta must match a real commit)." >&2
  git status --short >&2
  exit 1
fi
git fetch origin -q
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "✗ HEAD differs from origin/main — push first so this deploy is reproducible." >&2
  exit 1
fi
echo "→ deploying $(git rev-parse --short HEAD) to $HOST (BETA)"

tar czf - --exclude .git --exclude backend/.venv --exclude backend/data --exclude backend/demo.db \
    --exclude web/node_modules --exclude web/.next . \
| ssh "$HOST" "set -e
  rm -rf /root/concordia-beta && mkdir -p /root/concordia-beta
  tar xzf - -C /root/concordia-beta
  if [ ${DO_WEB} -eq 1 ]; then
    rsync -a --delete --exclude node_modules --exclude .next --exclude .env.production \
      /root/concordia-beta/web/ /opt/concord-web-beta/
    chown -R concord-web-beta:concord-web-beta /opt/concord-web-beta
    cd /opt/concord-web-beta
    # build failures MUST abort the deploy — piping to tail would mask them
    sudo -u concord-web-beta npx next build > /tmp/build-beta.log 2>&1 || { tail -25 /tmp/build-beta.log; exit 1; }
    grep -E '✓ Compiled' /tmp/build-beta.log | tail -1
  fi
  if [ ${DO_BACKEND} -eq 1 ]; then
    # never sync .env or the database
    rsync -a --exclude '.venv' --exclude 'data' --exclude 'concord.db*' --exclude '.env' \
      /root/concordia-beta/backend/ /opt/concord-beta/backend/
    chown -R concord-beta:concord-beta /opt/concord-beta/backend
  fi
  [ ${DO_WEB} -eq 1 ]     && systemctl restart concord-web-beta
  [ ${DO_BACKEND} -eq 1 ] && systemctl restart concord-api-beta
  sleep 3
  systemctl is-active concord-web-beta concord-api-beta
  curl -s http://127.0.0.1:8782/health; echo"

echo "→ public check: $(curl -s -o /dev/null -w '%{http_code}' https://beta.voteconcordia.com/)"
echo "✓ deployed (beta)"
