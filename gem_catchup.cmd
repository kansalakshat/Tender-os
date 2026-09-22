@echo off
setlocal enabledelayedexpansion
REM One-off full walk of the GeM listing, from THIS machine.
REM
REM Not on GitHub Actions: GeM answers "connection refused" at the socket to
REM datacenter IPs, so all six shards there failed before reading a byte. A
REM residential connection reaches it, which is why this is a local script.
REM
REM Four shards, not six: each drives its own Chromium, and gem.py already
REM records an OOM kill on a long run. recycle_every caps one browser's growth;
REM four at once is the ceiling worth taking on 16 GB.
cd /d "%~dp0"

REM app/db.py calls load_dotenv() WITHOUT override, so .env's localhost URL only
REM applies when DATABASE_URL is unset. Setting it here is what aims these
REM shards at production instead of the local copy. Quoted because the Neon URL
REM contains & between its query parameters.
for /f "tokens=1,* delims==" %%a in ('findstr /b "DATABASE_URL=" .env.production') do set "DATABASE_URL=%%b"
if not defined DATABASE_URL (
  echo No DATABASE_URL in .env.production -- refusing to run. & exit /b 1
)

set PAGES=1100
echo ==== %DATE% %TIME% catchup start, 4 shards x %PAGES% pages >> gem_catchup.log
start "gem-0" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 1    --max-pages %PAGES% >> gem_catchup.0.log 2>&1"
start "gem-1" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 1101 --max-pages %PAGES% >> gem_catchup.1.log 2>&1"
start "gem-2" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 2201 --max-pages %PAGES% >> gem_catchup.2.log 2>&1"
start "gem-3" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 3301 --max-pages %PAGES% >> gem_catchup.3.log 2>&1"
