#!/usr/bin/env bash
# Daily: fetch from this laptop's connection and watch it live.
#
# Starts the site locally with today's "Daily run" already going (purge, every
# source including GeM, bid documents, dedup -- the same cycle as
# run_prod_worker.py --once), writing to the production database. Alongside it,
# gem_deep.py walks GeM past the Daily run's 500 pages with three crawlers and
# then reads the new bids' documents. Then opens sign-in; an admin account lands
# on /admin, whose job panel follows the run.
#
# Bound to 127.0.0.1: the dashboard can start crawls and read the whole
# corpus, and has no business listening on the network.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
PORT=8000
URL="http://127.0.0.1:$PORT"
mkdir -p .cache

# Today's code, then start over on it: bash reads a script as it runs, so
# carrying on after a pull that changed this file would run half of each.
if [ -z "${START_PULLED:-}" ]; then
  git pull --ff-only --quiet || echo "git pull skipped -- running the code as it is"
  START_PULLED=1 exec bash "$0" "$@"
fi
# Any package, and the browser GeM is read with, that the code now needs.
# Neither failing stops the run.
uv pip install --quiet --python .venv/bin/python -r requirements.txt apscheduler "playwright>=1.47.0" \
  || echo "package update skipped"
.venv/bin/python -m playwright install chromium >/dev/null || echo "browser update skipped"

# Yesterday's server and deep crawl go: fresh ones start today's run.
for f in server gem_deep; do
  if [ -f ".cache/$f.pid" ] && kill -0 "$(cat ".cache/$f.pid")" 2>/dev/null; then
    echo "Stopping the previous $f"
    kill "$(cat ".cache/$f.pid")" 2>/dev/null || true
  fi
done
sleep 3
lsof -ti "tcp:$PORT" | xargs kill 2>/dev/null || true

DATABASE_URL="$(grep '^DATABASE_URL=' .env.production | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d $'\r')"
if [ -z "$DATABASE_URL" ]; then
  echo "No DATABASE_URL in .env.production -- run mac/setup.sh first"
  exit 1
fi
export DATABASE_URL
export AUTOSTART_JOB="Daily run"

echo "==== $(date) server starting" >> server.log
# Logging configured first, so the run's own progress lines reach server.log
# and not just the job panel.
nohup .venv/bin/python -u -c "import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s'); import uvicorn; uvicorn.run('app.api:app', host='127.0.0.1', port=$PORT)" >> server.log 2>&1 &
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

nohup .venv/bin/python -u gem_deep.py >> gem_deep.log 2>&1 &
DEEP=$!
echo "$DEEP" > .cache/gem_deep.pid
caffeinate -ims -w "$DEEP" &

open "$URL/login?next=/admin"
echo "Fetching has started. Sign in with the admin account; /admin shows it live."
echo "Logs: server.log (Daily run), gem_deep.log and gem_deep_1..3.log (deep GeM)"
echo "Stop: kill \$(cat .cache/server.pid) \$(cat .cache/gem_deep.pid)"
