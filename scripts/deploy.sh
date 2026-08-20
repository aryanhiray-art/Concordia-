#!/usr/bin/env bash
# Deploy the current working tree to the live box.
#
#   ./scripts/deploy.sh            # deploy web + backend, restart both
#   ./scripts/deploy.sh --web      # web only
#   ./scripts/deploy.sh --backend  # backend only
#
# Safe by construction: it never touches /opt/concord/.env or concord.db, so a
# deploy can't clobber secrets or member data. It also refuses to run with a
# dirty tree or unpushed commits — what's live should always be a commit that
# exists on GitHub, or nobody can reproduce a rollback.
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
  echo "✗ working tree is dirty — commit first (live must match a real commit)." >&2
  git status --short >&2
  exit 1
fi
git fetch origin -q
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "✗ HEAD differs from origin/main — push first so this deploy is reproducible." >&2
  exit 1
fi
echo "→ deploying $(git rev-parse --short HEAD) to $HOST"

tar czf - --exclude .git --exclude backend/.venv --exclude backend/data --exclude backend/demo.db \
    --exclude web/node_modules --exclude web/.next . \
| ssh "$HOST" "set -e
  tar xzf - -C /root/concordia
  if [ ${DO_WEB} -eq 1 ]; then
    rsync -a --delete --exclude node_modules --exclude .next --exclude .env.production \
      /root/concordia/web/ /opt/concord-web/
    chown -R concord-web:concord-web /opt/concord-web
    cd /opt/concord-web
    # build failures MUST abort the deploy — piping to tail would mask them
    sudo -u concord-web npx next build > /tmp/build.log 2>&1 || { tail -25 /tmp/build.log; exit 1; }
    grep -E '✓ Compiled' /tmp/build.log | tail -1
  fi
  if [ ${DO_BACKEND} -eq 1 ]; then
    # never sync .env or the database
    rsync -a --exclude '.venv' --exclude 'data' --exclude 'concord.db*' --exclude '.env' \
      /root/concordia/backend/ /opt/concord/backend/
    chown -R concord:concord /opt/concord/backend
  fi
  [ ${DO_WEB} -eq 1 ]     && systemctl restart concord-web
  [ ${DO_BACKEND} -eq 1 ] && systemctl restart concord-api
  sleep 3
  systemctl is-active concord-web concord-api
  curl -s http://127.0.0.1:8780/health; echo"

echo "→ public check: $(curl -s -o /dev/null -w '%{http_code}' https://voteconcordia.com/)"
echo "✓ deployed"
