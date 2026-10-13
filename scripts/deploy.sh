#!/usr/bin/env bash
# Deploy the current working tree to the live box.
#
#   ./scripts/deploy.sh            # deploy web + backend, restart both
#   ./scripts/deploy.sh --web      # web only
#   ./scripts/deploy.sh --backend  # backend only
#   ./scripts/deploy.sh --no-demo  # production only — skip the demo follow-up
#
# The public demo follows production: once this deploy has finished and passed
# its checks, the same commit (and the same --web/--backend scope) goes to
# demo.voteconcordia.com via deploy-demo.sh. See that script for why.
#
# Safe by construction: it never touches /opt/concord/.env or concord.db, so a
# deploy can't clobber secrets or member data. It also refuses to run with a
# dirty tree or unpushed commits — what's live should always be a commit that
# exists on GitHub, or nobody can reproduce a rollback.
set -euo pipefail

HOST="${CONCORD_HOST:-root@159.195.19.53}"
DO_WEB=1; DO_BACKEND=1; DO_DEMO=1; SCOPE=""
for arg in "$@"; do
  case "$arg" in
    --web)     DO_BACKEND=0; SCOPE="--web" ;;
    --backend) DO_WEB=0;     SCOPE="--backend" ;;
    --no-demo) DO_DEMO=0 ;;
    *) echo "usage: $0 [--web|--backend] [--no-demo]" >&2; exit 2 ;;
  esac
done
if [ ${DO_WEB} -eq 0 ] && [ ${DO_BACKEND} -eq 0 ]; then
  echo "✗ --web and --backend together deploy nothing — omit both to deploy both." >&2
  exit 2
fi

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
  # FRESH staging every deploy: tar-over-existing left deleted files behind as
  # ghosts, and the --delete rsync below faithfully re-shipped them (a stale
  # StatusBar.tsx broke the 2026-08-23 build once an API signature moved).
  rm -rf /root/concordia && mkdir -p /root/concordia
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

# The demo follows production. Production is completely deployed and checked by
# this point, so nothing below can delay or undo it — and a demo failure is
# reported, never propagated: the live club must not be held hostage by the demo.
if [ ${DO_DEMO} -eq 1 ]; then
  ./scripts/deploy-demo.sh ${SCOPE:+"$SCOPE"} \
    || echo "⚠ demo deploy FAILED — production is deployed and unaffected. Retry: ./scripts/deploy-demo.sh ${SCOPE}" >&2
fi
