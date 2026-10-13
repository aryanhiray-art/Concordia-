#!/usr/bin/env bash
# Deploy the current working tree to the public DEMO on the live box
# (demo.voteconcordia.com — fake broker, fake money, its own database).
#
#   ./scripts/deploy-demo.sh            # deploy web + backend, restart both
#   ./scripts/deploy-demo.sh --web      # web only
#   ./scripts/deploy-demo.sh --backend  # backend only
#
# You rarely run this by hand: deploy.sh calls it after every production deploy,
# because THE DEMO FOLLOWS PRODUCTION. It is a separate instance that neither
# deploy script used to touch, so it sat on a 2026-09-10 build for a month. A
# fix for a dead-end in its onboarding shipped to production on 2026-10-05 and
# never reached it — and the demo is what every "See the live demo" link on the
# marketing site opens, so real visitors kept hitting the bug until 2026-10-13.
#
# Same shape and guards as deploy.sh: it never touches /opt/concord-demo/.env or
# the demo DB, stages to its own dir (/root/concordia-demo) so it can't race a
# live deploy, and restarts only the demo units — the live concord-api and
# concord-web are never in its blast radius. It also refuses a dirty tree or
# unpushed commits, so the demo is always a commit that exists on GitHub.
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
  echo "✗ working tree is dirty — commit first (the demo must match a real commit)." >&2
  git status --short >&2
  exit 1
fi
git fetch origin -q
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "✗ HEAD differs from origin/main — push first so this deploy is reproducible." >&2
  exit 1
fi
echo "→ deploying $(git rev-parse --short HEAD) to $HOST (DEMO)"

tar czf - --exclude .git --exclude backend/.venv --exclude backend/data --exclude backend/demo.db \
    --exclude web/node_modules --exclude web/.next . \
| ssh "$HOST" "set -e
  rm -rf /root/concordia-demo && mkdir -p /root/concordia-demo
  tar xzf - -C /root/concordia-demo
  if [ ${DO_WEB} -eq 1 ]; then
    rsync -a --delete --exclude node_modules --exclude .next --exclude .env.production \
      /root/concordia-demo/web/ /opt/concord-web-demo/
    chown -R concord-web-demo:concord-web-demo /opt/concord-web-demo
    cd /opt/concord-web-demo
    # build failures MUST abort the deploy — piping to tail would mask them
    sudo -u concord-web-demo npx next build > /tmp/build-demo.log 2>&1 || { tail -25 /tmp/build-demo.log; exit 1; }
    grep -E '✓ Compiled' /tmp/build-demo.log | tail -1
  fi
  if [ ${DO_BACKEND} -eq 1 ]; then
    # never sync .env or the database
    rsync -a --exclude '.venv' --exclude 'data' --exclude 'concord.db*' --exclude '.env' \
      /root/concordia-demo/backend/ /opt/concord-demo/backend/
    chown -R concord-demo:concord-demo /opt/concord-demo/backend
  fi
  [ ${DO_WEB} -eq 1 ]     && systemctl restart concord-web-demo
  [ ${DO_BACKEND} -eq 1 ] && systemctl restart concord-api-demo
  sleep 3
  systemctl is-active concord-web-demo concord-api-demo
  curl -s http://127.0.0.1:8781/health; echo"

echo "→ public check: $(curl -s -o /dev/null -w '%{http_code}' https://demo.voteconcordia.com/)"
echo "✓ deployed (demo)"
