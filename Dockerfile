# The GeM half of the ingest, packaged to run anywhere that is not GitHub
# Actions.
#
# Why a container at all: GeM needs a real browser, so it cannot run on Vercel,
# and bidplus.gem.gov.in refuses connections from GitHub's runner ranges -- a
# blocklist aimed at scrapers, not at datacenters in general. Verified: the same
# host answers ordinary cloud addresses and a home connection fine. So this runs
# on any small always-on host, and the operator's laptop stops being load-bearing.
#
# The base image ships Chromium and its system libraries already matched to the
# Playwright version, which is the part that is painful to assemble by hand.
#
# The tag MUST track the playwright pinned just below. The image bundles one
# browser revision, and a pip install that drags in a different playwright looks
# for a revision that is not there -- "Executable doesn't exist", at run time,
# on a host nobody is watching. Bump both together or neither.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

# Dependencies first, so edits to the app do not re-resolve them on every build.
COPY requirements.txt .
# apscheduler is deliberately absent from requirements.txt (Vercel must not
# install it) but app/scheduler.py imports it even for a one-shot run.
RUN pip install --no-cache-dir -r requirements.txt apscheduler "playwright==1.62.0"

COPY app/ ./app/
COPY config/ ./config/
COPY run_prod_worker.py enrich_shard.py ./

# One cycle, then exit: this is a scheduled job, not a daemon. A long-lived
# process is what made the laptop unreliable -- it dies with a reboot and says
# nothing. Exiting means the scheduler simply runs it again tomorrow.
#
# DATABASE_URL and CONTACT_EMAIL come from the host's secret store.
# RETENTION_DAYS=0 matches production; unset, app/retention.py deletes nothing.
ENV RETENTION_DAYS=0
CMD ["python", "-u", "run_prod_worker.py", "--once"]
