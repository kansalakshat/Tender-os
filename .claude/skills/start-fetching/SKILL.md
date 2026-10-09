---
name: start-fetching
description: Start the day's tender fetching and enrichment on this laptop (Mac or Windows) and open the admin dashboard to watch it live. Use when the user says "start fetching", "start fetch", "begin fetching", "fetch tenders", or similar.
---

# Start today's fetching

1. From the project folder run the start script for this machine:
   - **macOS:** `bash mac/start.sh`
   - **Windows:** `powershell -ExecutionPolicy Bypass -File windows\start.ps1`

   It:
   - pulls the latest code and packages,
   - stops yesterday's server and deep crawl, and starts a new server on
     http://127.0.0.1:8000 with the **Daily run** already going: purge, CPPP,
     GeM's newest 500 pages (bid documents read with 6 workers), the state
     portals, older unread documents, dedup. data.gov.in is switched off in the
     database and is skipped,
   - at the same time starts `gem_deep.py`: three GeM crawlers over pages
     501-end (newest-first, ~175 bids a minute each), then a 6-worker pass over
     every unread bid document,
   - writes to the production database from this laptop's own connection,
   - opens the sign-in page in the browser.

   If `.venv` or `.env.production` is missing, setup was never done: follow the
   `do-setup` skill first, then run this again. On Windows, keep the PC plugged
   in and set to not sleep; on the Mac the script keeps it awake.

2. Tell the user in two lines: sign in with the admin account, and the admin
   page opens with the run's log updating live in the job panel. Google sign-in
   and email sign-in both work on the local address.

3. Watch, and report progress when asked:
   - `server.log`: each source's result, e.g. `[GeM] ok: fetched=4997 new=4129`,
     then `read N of M new bid document(s)`, `enriched`, `linked`.
   - `gem_deep_1.log` .. `gem_deep_3.log`: `committed 2000 records so far, 1166
     new` lines; `gem_deep.log`: when the crawlers finish and how many
     documents were read.
   - Expect: the Daily run in ~1.5-2 hours; the deep crawl in ~1.5 hours, then
     its document pass. GeM's document server is sometimes slow at night
     (timeouts in the log); unread documents are retried by the next pass.

4. If a script prints an error, or the panel shows `error`, read the last lines
   of `server.log` (or the `gem_deep*.log` files), fix the cause, and run the
   start script again. A run in progress is replaced by a fresh one, which is
   safe: every step picks up where the database says it left off.

To stop fetching early:
- macOS: `kill $(cat .cache/server.pid) $(cat .cache/gem_deep.pid)`
- Windows: `taskkill /T /F /PID <id>` for each id in `.cache\server.pid` and
  `.cache\gem_deep.pid`
