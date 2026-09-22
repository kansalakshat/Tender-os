@echo off
setlocal enabledelayedexpansion
REM Read the bid document for tenders that have one. Local, for the same reason
REM gem_catchup.cmd is: GeM's PDFs are on bidplus.gem.gov.in, which refuses a
REM datacenter address, so the hosted runner sets ENRICH_SKIP_SOURCES=GeM and
REM this covers what it leaves behind.
REM
REM This is the backlog pass. The daily task already enriches ENRICH_LIMIT rows
REM a cycle; ~21,000 waiting documents will not clear at that rate, and every
REM EMD, estimated value, document link and full title comes from here.
REM
REM Sharded on id %% count, which app/enrich.py's needs_enrichment() supports
REM directly -- the shards stay disjoint with no coordination.
REM
REM   gem_enrich.cmd          -- all shards, full limit
REM   gem_enrich.cmd 25       -- smoke test: 25 documents per shard
cd /d "%~dp0"

for /f "tokens=1,* delims==" %%a in ('findstr /b "DATABASE_URL=" .env.production') do set "DATABASE_URL=%%b"
if not defined DATABASE_URL (
  echo No DATABASE_URL in .env.production -- refusing to run. & exit /b 1
)

set LIMIT=%1
if "%LIMIT%"=="" set LIMIT=20000
REM Four, not eight: this runs while the listing crawl is still going, and both
REM hit the same host. Documents are plain HTTP with no browser, so four is
REM cheap on this machine -- the restraint is for GeM, not for us.
set SHARDS=4

echo ==== %DATE% %TIME% enrich start, %SHARDS% shards, limit %LIMIT% >> gem_enrich.log
for /l %%i in (0,1,3) do (
  start "enrich-%%i" /b cmd /c "".venv\Scripts\python.exe" -u enrich_shard.py %%i %SHARDS% %LIMIT% >> gem_enrich.%%i.log 2>&1"
)
