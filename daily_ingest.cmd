@echo off
REM Daily ingest into the production database. Registered with Task Scheduler as
REM "TenderDailyIngest"; see README. Appends to ingest.log so a run that fails
REM leaves a trace -- a scheduled job whose only record is an exit code is a
REM scheduled job nobody notices has stopped working.
cd /d "%~dp0"
echo ---- %DATE% %TIME% start >> ingest.log
REM -u: unbuffered, so a run that stalls still shows where it got to.
".venv\Scripts\python.exe" -u run_prod_worker.py --once >> ingest.log 2>&1
echo ---- %DATE% %TIME% exit %ERRORLEVEL% >> ingest.log
