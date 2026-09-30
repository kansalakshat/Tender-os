---
name: start-fetching
description: Start the day's tender fetching and enrichment on this laptop and open the admin dashboard to watch it live. Use when the user says "start fetching", "start fetch", "begin fetching", "fetch tenders", or similar.
---

# Start today's fetching

1. From the project folder run `bash mac/start.sh`. It:
   - pulls the latest code and packages,
   - stops yesterday's local server and starts a new one on
     http://127.0.0.1:8000 with the **Daily run** already going (purge, every
     source including GeM, bid documents, dedup), using this laptop's
     connection and writing to the production database,
   - keeps the Mac awake while it runs,
   - opens the sign-in page in the browser.

   If `.venv` or `.env.production` is missing, setup was never done: follow the
   `do-setup` skill first, then run this again.

2. Tell the user in two lines: sign in with the admin account, and the admin
   page opens with the run's log updating live in the job panel. Google sign-in
   and email sign-in both work on the local address.

3. If the script prints an error, or the panel shows `error`, read the last
   lines of `server.log`, fix the cause, and run `bash mac/start.sh` again.
   A run in progress is replaced by a fresh one, which is safe: every step
   picks up where the database says it left off.

To stop fetching early: `kill $(cat .cache/server.pid)`.
