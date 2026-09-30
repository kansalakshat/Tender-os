#!/usr/bin/env bash
# One-time setup of the fetching laptop (macOS). Safe to run again.
#
# GeM refuses datacenter addresses (Vercel, GitHub's runners), so its listing
# and bid documents are fetched from this laptop's own connection. This gets
# Python, the packages and GeM's browser in place, and checks both the
# database and GeM answer from here. Daily use is mac/start.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n==> %s\n' "$*"; }

# uv installs Python and the packages without Homebrew or an admin password.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# A virtualenv copied over from Windows (.venv/Scripts) cannot run on a Mac.
if [ -d .venv/Scripts ] || [ ! -x .venv/bin/python ]; then
  say "Creating .venv with Python 3.13"
  rm -rf .venv
  uv venv --python 3.13 .venv
fi

say "Installing packages"
# apscheduler and playwright are deliberately not in requirements.txt (Vercel
# must not install them); the daily run needs both here.
uv pip install --python .venv/bin/python -r requirements.txt apscheduler "playwright>=1.47.0"

say "Installing the browser GeM is read with"
.venv/bin/python -m playwright install chromium

# Secrets are copied over by hand, never committed.
for f in .env .env.production; do
  if [ ! -f "$f" ]; then
    echo "Missing $f -- copy it from the other laptop into $(pwd)"
    exit 1
  fi
done
grep -q '^DATABASE_URL=' .env.production || { echo ".env.production has no DATABASE_URL"; exit 1; }

say "Checking the database answers"
.venv/bin/python - <<'PY'
from dotenv import dotenv_values
import psycopg
url = dotenv_values(".env.production")["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url, connect_timeout=30) as conn:
    n = conn.execute("select count(*) from tenders").fetchone()[0]
print(f"database answers: {n:,} tenders")
PY

say "Checking GeM answers this connection"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 https://bidplus.gem.gov.in/all-bids || true)
if [[ "$code" == 2* || "$code" == 3* ]]; then
  echo "GeM answered (HTTP $code)"
else
  echo "GeM did not answer (got '$code'). Fetching GeM needs a home or office connection, not a VPN."
fi

mkdir -p .cache
say "Setup done. Daily: tell Claude 'start fetching', or run: bash mac/start.sh"
