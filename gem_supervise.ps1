# Restart GeM catch-up shards that are no longer running.
#
# Why this exists: shards die silently. Two of eight vanished mid-crawl with no
# traceback and no summary line -- cmd_run always prints one, so they were
# killed rather than finished, most likely by memory pressure. Nothing noticed,
# and the two dead ones covered pages 1-1200, the newest end of the listing.
#
# Safe to run repeatedly, and safe to run on a timer: a shard already running is
# left alone, and re-crawling a range only produces updates, never duplicates.
#
# --enrich: each shard reads the documents of the rows it creates, in the same
# pass. Without it the rows land with no EMD, no value and no links and wait on
# a separate pass that is ordered by soonest deadline -- so a bid closing in a
# fortnight would sit behind every one closing tomorrow.
#
#   powershell -ExecutionPolicy Bypass -File gem_supervise.ps1
param(
  [int]$Shards = 8,
  [int]$Pages  = 600
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# app/db.py calls load_dotenv() without override, so this is what aims the
# shards at production rather than the local copy.
$line = (Select-String -Path "$root\.env.production" -Pattern '^DATABASE_URL=' | Select-Object -First 1).Line
if (-not $line) { Write-Error "No DATABASE_URL in .env.production"; exit 1 }
$env:DATABASE_URL = $line -replace '^DATABASE_URL=', ''

$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*app.cli run GeM*' } |
  ForEach-Object { if ($_.CommandLine -match '--start-page\s+(\d+)') { [int]$matches[1] } })

$started = 0
for ($i = 0; $i -lt $Shards; $i++) {
  $start = 1 + $i * $Pages
  if ($running -contains $start) { continue }
  $log = Join-Path $root "gem_catchup.$i.log"
  Add-Content $log "---- $(Get-Date -Format s) supervisor starting shard $i at page $start"
  Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "-u","-m","app.cli","run","GeM","--start-page",$start,"--max-pages",$Pages,"--enrich" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput "$log.out" -RedirectStandardError "$log.err"
  Write-Output "started shard $i (page $start)"
  $started++
}
if ($started -eq 0) { Write-Output "all $Shards shards already running" }
