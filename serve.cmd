@echo off
setlocal
REM Serve the site and the operator dashboard on this machine.
REM
REM Registered with Task Scheduler as "TenderServe" to start at logon, so the
REM dashboard is simply there rather than something to remember to launch.
REM Jobs started from it run in this process, which is the point: a serverless
REM instance is frozen the moment it answers, so a job started there never
REM finishes. This process stays up.
REM
REM Bound to 127.0.0.1 deliberately. The dashboard can start crawls and read
REM the whole corpus; it has no business listening on the network.
cd /d "%~dp0"

for /f "tokens=1,* delims==" %%a in ('findstr /b "DATABASE_URL=" .env.production') do set "DATABASE_URL=%%b"
if not defined DATABASE_URL (
  echo No DATABASE_URL in .env.production -- refusing to start. & exit /b 1
)

echo ==== %DATE% %TIME% server starting >> server.log
".venv\Scripts\python.exe" -u -m uvicorn app.api:app --host 127.0.0.1 --port 8000 >> server.log 2>> server.err
