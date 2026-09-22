@echo off
setlocal enabledelayedexpansion
REM One-off full walk of the GeM listing, from THIS machine.
REM
REM Not on GitHub Actions: GeM answers "connection refused" at the socket to
REM datacenter IPs, so all six shards there failed before reading a byte. A
REM residential connection reaches it, which is why this is a local script.
REM
REM EIGHT shards over narrow ranges, not two over wide ones. Memory is not the
REM constraint it looked like (a shard is ~80 MB of Python plus a ~50 MB
REM browser); stalling is. Both earlier attempts crawled a few hundred records
REM and then sat at 0 CPU with no warning logged, which fits a hang inside
REM page.evaluate -- it is the one call in the page loop that Playwright gives
REM no timeout, so _next_page's 30s guard never fires.
REM
REM Narrow ranges are the cheap mitigation: a wedged shard costs 600 pages
REM instead of 2,200, and the other seven keep going. Re-running the script is
REM safe and is how a stalled range gets finished -- rows already stored come
REM back as updates, not duplicates.
cd /d "%~dp0"

REM app/db.py calls load_dotenv() WITHOUT override, so .env's localhost URL only
REM applies when DATABASE_URL is unset. Setting it here is what aims these
REM shards at production instead of the local copy. Quoted because the Neon URL
REM contains & between its query parameters.
for /f "tokens=1,* delims==" %%a in ('findstr /b "DATABASE_URL=" .env.production') do set "DATABASE_URL=%%b"
if not defined DATABASE_URL (
  echo No DATABASE_URL in .env.production -- refusing to run. & exit /b 1
)

REM 8 x 600 = 4,800 pages; the listing was 45,720 records (4,572 pages) today.
set PAGES=600
echo ==== %DATE% %TIME% catchup start, 8 shards x %PAGES% pages >> gem_catchup.log
start "gem-0" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 1    --max-pages %PAGES% >> gem_catchup.0.log 2>&1"
start "gem-1" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 601  --max-pages %PAGES% >> gem_catchup.1.log 2>&1"
start "gem-2" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 1201 --max-pages %PAGES% >> gem_catchup.2.log 2>&1"
start "gem-3" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 1801 --max-pages %PAGES% >> gem_catchup.3.log 2>&1"
start "gem-4" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 2401 --max-pages %PAGES% >> gem_catchup.4.log 2>&1"
start "gem-5" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 3001 --max-pages %PAGES% >> gem_catchup.5.log 2>&1"
start "gem-6" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 3601 --max-pages %PAGES% >> gem_catchup.6.log 2>&1"
start "gem-7" /b cmd /c "".venv\Scripts\python.exe" -u -m app.cli run GeM --start-page 4201 --max-pages %PAGES% >> gem_catchup.7.log 2>&1"
