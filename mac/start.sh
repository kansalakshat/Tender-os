#!/usr/bin/env bash
# Daily: fetch from this laptop's connection and watch it live.
#
# Starts the site locally with today's "Daily run" already going (purge, every
# source including GeM, bid documents, dedup -- the same cycle as
# run_prod_worker.py --once), writing to the production database. Then opens
# sign-in; an admin account lands on /admin, whose job panel follows the run.
#
# Bound to 127.0.0.1: the dashboard can start crawls and read the whole
# corpus, and has no business listening on the network.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
PORT=8000
URL="http://127.0.0.1:$PORT"
mkdir -p .cache

# Today's code, and any package it now needs. Neither failing stops the run.
git pull --ff-only --quiet || echo "git pull skipped -- running the code as it is"
uv pip install --quiet --python .venv/bin/python -r requirements.txt apscheduler "playwright>=1.47.0" \
  || echo "package update skipped"

# Yesterday's server goes: a fresh one starts today's run.
if [ -f .cache/server.pid ] && kill -0 "$(cat .cache/server.pid)" 2>/dev/null; then
  echo "Stopping the previous server"
  kill "$(cat .cache/server.pid)" 2>/dev/null || true
  sleep 3
fi
lsof -ti "tcp:$PORT" | xargs kill 2>/dev/null || true

DATABASE_URL="$(grep '^DATABASE_URL=' .env.production | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
if [ -z "$DATABASE_URL" ]; then
  echo "No DATABASE_URL in .env.production -- run mac/setup.sh first"
  exit 1
fi
export DATABASE_URL
export AUTOSTART_JOB="Daily run"

echo "==== $(date) server starting" >> server.log
nohup .venv/bin/python -u -m uvicorn app.api:app --host 127.0.0.1 --port "$PORT" >> server.log 2>&1 &
SERVER=$!
echo "$SERVER" > .cache/server.pid
# Stay awake while the server runs (it exits with it). A closed lid on battery
# still sleeps: keep the laptop plugged in with the lid open.
caffeinate -ims -w "$SERVER" &

for _ in $(seq 1 90); do
  curl -sf "$URL/healthz" >/dev/null && break
  sleep 1
done
if ! curl -sf "$URL/healthz" >/dev/null; then
  echo "The server did not start. Last lines of server.log:"
  tail -30 server.log
  exit 1
fi

open "$URL/login?next=/admin"
echo "Fetching has started. Sign in with the admin account; /admin shows it live."
echo "Server log: tail -f server.log    Stop: kill \$(cat .cache/server.pid)"
