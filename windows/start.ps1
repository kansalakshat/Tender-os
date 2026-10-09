# Daily: fetch from this PC's connection and watch it live. Windows twin of
# mac/start.sh:
#
#   powershell -ExecutionPolicy Bypass -File windows\start.ps1
#
# Starts the site locally with today's "Daily run" already going (purge, every
# source including GeM, bid documents, dedup), writing to the production
# database. Alongside it, gem_deep.py walks GeM past the Daily run's 500 pages
# with three crawlers, then reads the new bids' documents. Opens sign-in; an
# admin account lands on /admin, whose job panel follows the run.
#
# Bound to 127.0.0.1: the dashboard can start crawls and read the whole corpus,
# and has no business listening on the network.
Set-Location (Split-Path $PSScriptRoot)
$Port = 8000
$Url = "http://127.0.0.1:$Port"
New-Item -ItemType Directory -Force .cache | Out-Null

# Today's code, and any package it now needs. Neither failing stops the run.
git pull --ff-only --quiet
if (-not $?) { "git pull skipped -- running the code as it is" }
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt apscheduler "playwright>=1.47.0"
if (-not $?) { "package update skipped" }

# Yesterday's server and deep crawl go (with their crawlers and browsers).
foreach ($f in "server", "gem_deep") {
  if (Test-Path ".cache\$f.pid") {
    foreach ($id in (Get-Content ".cache\$f.pid") -split ' ') {
      taskkill /PID $id /T /F 2>$null | Out-Null
    }
  }
}
$held = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($held) { Stop-Process -Id $held.OwningProcess -Force }
Start-Sleep 3

$line = Select-String -Path .env.production -Pattern '^DATABASE_URL=' | Select-Object -First 1
if (-not $line) { "No DATABASE_URL in .env.production"; exit 1 }
$Db = $line.Line.Substring(13).Trim('"', "'")

function Start-Hidden($Command) {
  (Start-Process cmd.exe -ArgumentList '/c', "set `"DATABASE_URL=$Db`" && $Command" `
     -WindowStyle Hidden -PassThru).Id
}

# Logging configured first, so the run's own progress lines reach server.log
# and not just the job panel.
$py = "import logging; logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s'); import uvicorn; uvicorn.run('app.api:app', host='127.0.0.1', port=$Port)"
Start-Hidden "set `"AUTOSTART_JOB=Daily run`" && .venv\Scripts\python.exe -u -c `"$py`" >> server.log 2>&1" |
  Set-Content .cache\server.pid

$ok = $false
foreach ($i in 1..90) {
  try { Invoke-WebRequest "$Url/healthz" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok = $true; break }
  catch { Start-Sleep 1 }
}
if (-not $ok) { "The server did not start. Last lines of server.log:"; Get-Content server.log -Tail 30; exit 1 }

Start-Hidden ".venv\Scripts\python.exe -u gem_deep.py >> gem_deep.log 2>&1" |
  Set-Content .cache\gem_deep.pid

Start-Process "$Url/login?next=/admin"
"Fetching has started. Sign in with the admin account; /admin shows it live."
"Logs: server.log (Daily run), gem_deep.log and gem_deep_1..3.log (deep GeM)"
"Stop: taskkill /T /F /PID <each id in .cache\server.pid and .cache\gem_deep.pid>"
