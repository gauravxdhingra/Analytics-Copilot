#!/usr/bin/env bash
# Prove the README quickstart on a fresh checkout: `make clone-check`.
#
# Clones the committed code (not your working tree) into a temporary directory, starts the whole
# stack there as its own compose project with no .env, runs the browser suite against it, and
# always tears everything down. It uses the canned model, so it needs no key and makes no calls
# to any model provider.
set -euo pipefail

repo="$(git rev-parse --show-toplevel)"
work="$(mktemp -d)"
project="clone-check"
export COMPOSE_PROJECT_NAME="$project"

# The stack publishes fixed local ports, so another copy must not be running.
for port in 3000 8081 5433 16686; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port is in use: stop the running stack first (make down)" >&2
    exit 2
  fi
done

cleanup() {
  status=$?
  docker compose -p "$project" -f "$work/repo/deploy/compose/compose.yaml" \
    down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT

git clone --quiet "$repo" "$work/repo"
cd "$work/repo"

test ! -e deploy/compose/.env || { echo "a fresh clone must have no .env" >&2; exit 1; }

echo "== make up (fresh clone, no .env, canned model)"
COPILOT_MODEL=scripted make up

echo "== browser suite against the containers"
(cd apps/web && npm ci --no-audit --no-fund --silent)
set -a
# shellcheck disable=SC1091
. deploy/compose/.env
set +a
(cd apps/web && E2E_STACK=compose npx playwright test --timeout 40000)

echo "== clone-check passed: $(git rev-parse --short HEAD)"
