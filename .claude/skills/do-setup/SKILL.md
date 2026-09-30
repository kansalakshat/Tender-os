---
name: do-setup
description: First-time setup of the macOS fetching laptop for the tender project. Use when the user says "do setup", "setup", "set up this laptop", "first time setup", or similar.
---

# First-time setup of the fetching laptop

This Mac is the machine that fetches GeM and reads bid documents: GeM refuses
datacenter addresses, so the crawl has to use this laptop's own connection. The
user is a beginner; keep every message short and plain, and do the work
yourself rather than handing them commands, except where macOS itself needs them.

Do these in order. Stop and explain in one line if a step cannot be fixed.

1. **Right place.** `uname` must say `Darwin`, and the current folder must hold
   `run_prod_worker.py`. If the code is missing, clone it:
   `git clone https://github.com/kansalakshat/Tender-os.git` and work inside it.
   If `git` is missing, macOS offers to install it when `git` first runs; ask the
   user to accept that prompt (`xcode-select --install`), then continue.

   **The folder usually arrives as a copy from the Windows laptop (Google
   Drive).** Its files have Windows line endings, and `.git` may be missing.
   Bring it to exactly the GitHub code once, before anything else -- untracked
   files (`.env`, `.env.production`, logs) are left alone:

   ```bash
   [ -d .git ] || { git init -q && git remote add origin https://github.com/kansalakshat/Tender-os.git; }
   git config core.autocrlf false
   git fetch -q origin main && git reset -q --hard origin/main
   ```

   This is a fresh copy with nothing of its own to keep, so the reset is safe
   here; do not repeat it later on a Mac where someone may have edited files.

2. **Secrets.** `.env` and `.env.production` must be in the project folder. The
   user copies them from the other laptop (AirDrop is fine). If either is
   missing, ask for it and wait. Never print their contents.

3. **Run** `bash mac/setup.sh` from the project folder. A `.venv` copied from
   Windows is replaced automatically. It installs uv, Python
   3.13, the packages and GeM's browser, then checks the database and GeM both
   answer. On failure, read the error, fix the cause and run it again. It is
   safe to re-run.

4. **Keep the Mac awake for long runs.** Tell the user, once: keep it plugged in
   with the lid open, and in System Settings -> Battery -> Options turn on
   "Prevent automatic sleeping on power adapter when the display is off". The
   daily script also keeps it awake while fetching.

5. **Finish** with one line: setup is done, and each day they say
   "start fetching".
