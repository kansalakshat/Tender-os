# Tenderleo — Indian public tender aggregator

Tenderleo collects public tender notices from official Indian government
portals, normalises them into one schema, stores them in Postgres, and serves
them as a website and a REST API: search and browse, a page per tender with the
facts read out of its bid document, company-profile matching, a personal
dashboard of tenders being tracked or bid on, and an operator dashboard.

Live site: https://tender-0s.vercel.app

---

## Contents

1. [How it works, in one picture](#how-it-works-in-one-picture)
2. [Where each part runs](#where-each-part-runs)
3. [The data pipeline in depth](#the-data-pipeline-in-depth)
4. [Living on Neon's free plan](#living-on-neons-free-plan)
5. [The website](#the-website)
6. [Running it day to day](#running-it-day-to-day)
7. [Compliance rules](#compliance-rules)
8. [Sources](#sources)
9. [Local setup and commands](#local-setup-and-commands)
10. [Adding a new source](#adding-a-new-source)
11. [Deploying](#deploying)
12. [Layout](#layout)
13. [Things that broke, and why](#things-that-broke-and-why)

---

## How it works, in one picture

```
  Government portals                    Fetching laptop (Mac or Windows)
  ------------------                    --------------------------------
  CPPP  eprocure.gov.in    --HTTP-->    Daily run      (server + job thread)
  20 GePNIC portals        --HTTP-->      purge -> every source -> documents -> dedup
  GeM   bidplus.gem.gov.in --Chromium-> gem_deep.py    (3 crawlers, pages 501..end,
        bid PDFs           --HTTP-->                    then a document pass)
                                        Admin dashboard on 127.0.0.1:8000
                                                 |
                                                 | writes (batched, fingerprinted)
                                                 v
                                     Neon Postgres (free plan, us-east-1)
                                                 ^
                       reads (edge-cached)       |      CPPP + states daily,
  Visitors --> Vercel (iad1) --> FastAPI app ----+----  purge twice daily
                                                        (GitHub Actions)
```

Everything is one Python codebase (`app/`). The same FastAPI app serves the live
site on Vercel and the operator dashboard on the fetching laptop; what differs
is where it runs and therefore what it can reach.

## Where each part runs

| Part | Runs on | Why there |
|---|---|---|
| Website + JSON API | Vercel serverless (`api/index.py`, region `iad1`) | Free, no server to keep up. Pinned next to the database, see [Function region](#function-region). |
| Database | Neon Postgres, free plan, `us-east-1` | Hosted Postgres with scale-to-zero. Its 5 GB/month of network transfer is the tightest limit in the system, see [Living on Neon's free plan](#living-on-neons-free-plan). |
| GeM crawl and all bid-document reading | The fetching laptop (Mac, or this Windows PC) | `bidplus.gem.gov.in` refuses datacenter and GitHub-runner addresses at the socket, and Vercel has no browser. A home connection is answered. |
| CPPP + state portals, daily | GitHub Actions `daily-ingest.yml` (03:00 UTC), and the laptop's Daily run | Plain HTTP, works from anywhere. GeM is skipped there (`SKIP_CONNECTORS: GeM`). |
| Purge of closed tenders | GitHub Actions `purge.yml` (00:15 and 12:15 UTC), Vercel Cron `/cron/purge`, and the first step of every Daily run | Cheap, so it runs from several places. |
| Uptime and Neon alarms | GitHub Actions `keep-warm.yml` (every 5 min), `neon-usage.yml` (10:00 IST) | `neon-usage` emails the owner if the database refuses connections or a free-plan limit is mostly spent. |

---

## The data pipeline in depth

### 1. Connectors: one class per portal

Every source is a subclass of `BaseConnector` (`app/connectors/base.py`) that
implements two methods:

- `fetch_batch(since)` yields raw rows from the portal's listing, and
- `normalize(raw)` turns one into a `TenderRecord` (`app/schemas.py`), the one
  shape every source is converted to.

Everything else — permission checks, politeness, writing to the database — is in
`BaseConnector.run()`, which no connector overrides. A run goes:

1. **Allowlist.** The portal's host must be in `config/approved_sources.yaml`
   with its licence, permitted paths and who verified it, or the run refuses.
2. **Switched on?** `sources.enabled` must be true. `tenders sources disable
   <name>` turns a source off for every runner at once (data.gov.in is off).
3. **robots.txt.** Checked before the first request, cached, re-checked weekly.
   A "no" refuses the run and marks the source inactive.
4. **Fetch politely.** One request per `rate_limit_seconds` per connector (3 s
   for CPPP and GeM), exponential backoff on 429/5xx, an honest User-Agent with
   a contact address.
5. **Skip what the purge would delete.** With `RETENTION_DAYS` set, a row whose
   deadline is already past the purge cutoff is never written.
6. **Write in batches** (next section), commit every 200 rows so a failure keeps
   what it already had.
7. **Record the run** in `connector_runs` (fetched / new / updated / errors).
8. **Report activity**: a heartbeat file for the admin page, see
   [The admin dashboard](#the-admin-dashboard).

### 2. Writing rows: fingerprints and batches

Most of any crawl is listings the database already holds, unchanged. Two
measured costs shaped how they are written:

- **Transfer.** Loading a whole stored row to compare it was the bulk of Neon's
  monthly transfer.
- **Latency.** Neon is ~310 ms away from the fetching laptop in India. One query
  per row capped a crawl at 2–3 rows a second no matter how fast the portal was.

So `_upsert_many()` works on batches of 50 rows (`UPSERT_BATCH`):

1. **Fingerprint each listing.** A SHA-1 of the normalised values, stored in the
   row's `raw_payload["_listing_fp"]`. Fields that change on every fetch without
   the tender changing are left out (`FP_VOLATILE`): CPPP's detail link carries a
   fresh session token every time, and a row's `serial` shifts as new tenders are
   published above it. Before they were excluded, every known CPPP row looked
   changed on every crawl.
2. **One query** returns `(external_ref, id, fingerprint)` for the whole batch.
   Unchanged rows stop here, costing a few bytes each.
3. **New rows**: one multi-row `INSERT … RETURNING id` for the batch. The ids are
   kept in `created_ids` so their documents can be read next.
4. **Changed rows**: one `SELECT` loads all of them; each is then merged:
   - `raw_payload` is **merged, never replaced** — enrichment writes the EMD,
     links and bid type into it, and a re-crawl must not wipe them;
   - a listing that stops publishing a value never erases one already read;
   - on an enriched row, the document's longer title and full organisation
     beat the listing card's truncated ones;
   - `last_updated_at` moves only on a real change.

A crawl of 121 listings went from **241 statements to 6**.

### 3. GeM, specifically

GeM (`app/connectors/gem.py`) is the largest source (~47,000 live bids) and the
only one read with a browser. Its listing renders client-side from an in-page
POST that carries its own CSRF token, so the connector drives headless Chromium
(Playwright) and lets the page make its own requests — nothing is replayed or
forged.

- **Newest first.** The listing's default order is *Bid End Date: Oldest First*,
  which scatters new bids over all ~4,700 pages. The connector selects the page's
  own *Bid Start Date: Latest First* sort, so new bids are on the first pages.
- **Jump, don't click.** `loadBids(n)` — the function GeM's own pager calls —
  opens any page directly, so a crawler can start at page 2,701.
- **The browser is rebuilt every 75 pages**, because Chromium's memory climbs
  with every page turn on this listing.
- **Stalls are retried** (3 attempts), and 429/503 responses trigger back-off.
- **Two card formats.** The first page (server-rendered) says `BID NO:`, pages the
  script draws say `Bid No.:`; the parser accepts both.

The Daily run reads GeM's newest **500 pages** (~5,000 bids, ~35 minutes),
enough for GeM's ~3,100 new bids a day. Long-running bids sit deeper and are
covered by the deep crawl.

### 4. The Daily run

`run_prod_worker.run_once()` — started by the admin dashboard's **Daily run**
job (and by `start fetching`) or by `python run_prod_worker.py --once`:

| Step | What happens |
|---|---|
| Purge | Closed tenders deleted first, so nothing later spends work on rows about to go. |
| Every source, in turn | CPPP, data.gov.in (switched off), GeM, then the 20 GePNIC portals, each with a 48-hour `since` window. After each source, the documents of the rows it just created are read with `ENRICH_WORKERS` threads (default 6), sharded by id. |
| Older documents | Up to `ENRICH_LIMIT` (4,000) more unread documents, soonest deadline first, 6 workers. |
| Dedup | Cross-source duplicate linking (below). |

Measured on 10 Oct 2026: GeM 4,997 bids read, **4,129 new**, in 35 minutes;
their documents read at ~300 a minute while GeM's server was fast.

### 5. The deep GeM crawl

`gem_deep.py` runs alongside the Daily run. It splits GeM pages 501–end across
**three crawlers** at once (each at GeM's 3 s pace), then reads every unread bid
document with 6 workers. On its first run, pages past ~2,700 were almost
entirely bids never seen before: **~16,000 new** in about 1.5 hours.

### 6. Reading bid documents (enrichment)

A listing card is a stub: on GeM, 81% of titles arrive truncated
("Some Item, Other It...") and neither the value nor the EMD appears.
`app/enrich.py` downloads each bid PDF and `app/bidpdf.py` reads out the full
item list, the buying organisation, the estimated value, the EMD, the document
links and the bid type.

- **Which rows**: ids only are selected (has a document URL, not a duplicate,
  deadline today or later, no `_enriched` marker), ordered soonest deadline
  first.
- **Parallel**: threads wait on downloads, sharded by `id % workers` so no two
  read the same row; PDF parsing runs in a process pool (CPU count − 1).
- **Failures are cheap**: a timeout or a database hiccup skips that tender,
  which stays unread, so the next pass retries it. GeM's document server is
  sometimes very slow at night (35 minutes of timeouts on 10 Oct); the run
  simply carries on.

### 7. Deduplication

The same tender is often published on more than one portal (GeM and CPPP
especially). Both rows are kept — each is a faithful record of its source — and
`app/dedup.py` links the duplicate to its twin with `duplicate_of`. Every list,
search and match query filters on `duplicate_of IS NULL`, so a linked row
disappears from the product but stays auditable.

- **Match rule**: canonicalised titles ≥ 0.88 similar, organisations ≥ 0.80,
  values within 2% (an unknown value never blocks), deadlines within 3 days.
  Titles that are nothing but procurement boilerplate match nothing.
- **Who survives**: the row whose document can actually be downloaded (usually
  GeM's, since CPPP's detail page is CAPTCHA-gated).
- **Scope and cost**: open tenders in a 120-day window, compared within
  deadline buckets. A local cache (`DEDUP_CACHE`, `.cache/dedup.json`) keeps the
  compared columns between runs, so only rows changed since the last run are
  downloaded.
- **No long transactions**: the read's transaction ends before comparing,
  which can take half an hour on ~45,000 rows. Neon closes connections left
  idle inside a transaction.

Hard-deduping at ingest was rejected deliberately: a wrong merge is
unrecoverable, a wrong link is one `UPDATE`.

### 8. Retention

`RETENTION_DAYS` controls deletion (`app/retention.py`). Production runs with
`0`: a tender is deleted the day after its deadline, by `tenders purge --days 0`
from `purge.yml`, Vercel Cron and the Daily run. Unset means archive mode —
nothing is deleted and nothing is skipped at ingest. There is no backup;
deletion is final by request.

---

## Living on Neon's free plan

The free plan allows **5 GB of network transfer a month**. In September 2026 it
ran out — backlog jobs pulled whole rows from production — and every
database-backed page returned 500 until the month turned over. The project
stays on the free plan, so transfer is a design constraint:

| Measure | Where |
|---|---|
| Never select whole `tenders` rows where a few columns do (`raw_payload` is the bulk) | everywhere; `CLAUDE.md` |
| Listing fingerprints: unchanged rows cost a few bytes | `BaseConnector._upsert_many` |
| Enrichment selects ids only before downloading anything | `enrich.needs_enrichment` |
| Dedup downloads only rows changed since its last run | `DEDUP_CACHE` |
| Anonymous pages cached at Vercel's edge (`s-maxage=300, stale-while-revalidate=600`) | `app/api.py` |
| Shared page data computed once per few minutes for every visitor | `db.shared()` |
| A per-address read throttle, and `robots.txt`, keep crawlers of *our* site in check | `app/security.py` |
| Status heartbeats are local files, not database writes | `app/activity.py` |
| Serverless uses no connection pool; the laptop pools | `db._pool_options` |
| A daily usage check that emails on trouble | `neon_usage.py`, `neon-usage.yml` |

Check usage any time: GitHub → Actions → **neon-usage** → Run workflow. On
10 Oct 2026 it read 0.44 of 5 GB transfer, 12 of 100 CU-hours, 0.184 of 0.5 GB
storage. Never run the old backlog scripts (`gem_catchup.cmd`,
`gem_enrich.cmd`) against production.

---

## The website

Server-rendered Jinja2 pages (`templates/`, autoescaped) over FastAPI
(`app/web.py`), with a little JavaScript per page (`static/js/`). The design is
light and calm on purpose: most users are 50–60.

| Page | What it does |
|---|---|
| `/` | Home: corpus figures and a strip of buyer logos. |
| `/browse` | Search and filter the whole corpus (text, sector, state and city, buyer, source, open only; sortable); a search can be saved. Rows come from `GET /tenders`. |
| `/t/{id}` | One tender: every fact held (`app/facts.py`), typical eligibility checks (`app/eligibility.py`), and links to its documents. |
| `/t/{id}/cppp` | Opens a CPPP notice: the visitor types CPPP's own CAPTCHA, our server relays it (`app/cppp_relay.py`). No CAPTCHA is solved by software. |
| `/buyers`, `/buyer` | Buying organisations and their tenders. |
| `/profile`, `/matches` | A company's questionnaire and its ranked matches (below). |
| `/dashboard` | Tracked tenders (wishlist), tenders being bid on ("Ready to fill"), saved searches. |
| `/login`, `/signup` | Email + password, or Google sign-in. |
| `/admin` | Operator dashboard, admins only (404 for everyone else). |

**State and city** are not published by most listings, so `app/geo.py` places a
tender by the states and districts named in its buyer, department and title;
the display and the filter read the same texts.

**Matching.** `tenders.category` is never published and the value is missing on
most listings, so sector comes from the title (`app/matching.py`,
`SECTOR_PATTERNS`, tuned on real titles: "BUS" in a power tender is a busbar,
"irrigation" on MP's portal means a pump feeder). Score = sector overlap,
keyword hits, district, comfortable time to prepare. Hard filters drop closed
tenders, ones closing before the company could prepare, and — only when the
value is known — ones beyond its capacity. An unknown value never disqualifies.

**Accounts and security**:
- Passwords: `hashlib.scrypt` with per-password salts.
- Sessions: HMAC-signed, expiring cookies, invalidated on logout via a session
  epoch.
- Google OAuth sign-in; email verification links over SMTP (or `outbox.log` with
  no mail server).
- Login, signup and verification are rate-limited with counters in Postgres
  (`rate_limits`), so the limit holds across Vercel instances.
- Security headers and a CSP on every response; `Secure` cookies and HSTS when
  `PUBLIC_BASE_URL` is https.

---

## Running it day to day

### Start fetching

On the fetching laptop, say **"start fetching"** to Claude Code (the
`start-fetching` skill). It:

1. **Updates the code first.** Fetches from GitHub and merges with any local
   work (local edits are stashed and put back, conflicts resolved and tested,
   nothing pushed). The script then runs entirely on the new code.
2. **Checks the database matches the code.** Compares `alembic current` with
   `alembic heads` on production; a pending migration is named and only applied
   after the owner says yes.
3. **Runs the start script**: `bash mac/start.sh` on the Mac,
   `powershell -ExecutionPolicy Bypass -File windows\start.ps1` on Windows.
   It installs any new packages and the matching Playwright browser, stops
   yesterday's server and deep crawl, then starts:
   - the server on http://127.0.0.1:8000 with the **Daily run** already going,
   - `gem_deep.py` alongside it,
   - and opens the admin sign-in page.

   On the Mac it keeps the machine awake; on Windows keep it plugged in and set
   not to sleep.

Logs: `server.log` (Daily run, one line per source result),
`gem_deep.log` and `gem_deep_1..3.log` (deep crawl, `committed N records so far,
M new`). First-time Mac setup is the `do-setup` skill (`mac/setup.sh`).

### The admin dashboard

`/admin` on the laptop (sign in with an address in `ADMIN_EMAILS`):

- **Top line** — what the machine is doing right now: **Fetching** (one line
  per crawler, e.g. `GeM: 4,200 read, 3,900 new`), **Reading documents**,
  **Finding duplicates**, or **Idle**. Every crawler, document reader and dedup
  pass writes a heartbeat file under `.cache/activity/` every few seconds; the
  page lists those under 150 s old. This is how crawls in *other* processes
  (`gem_deep.py`, the CLI) appear, which the in-memory job log never could.
- **Live ticker** — rows held, open now, change since the page opened, rows per
  minute; polled every 10 s from `/admin/live`.
- **Fetch now / job panel** — start one connector or the Daily run in this
  process and follow its log (`app/adminjobs.py`). Jobs run only on the laptop:
  on Vercel a background thread is frozen the moment a request is answered, so
  the deployed dashboard shows numbers and no controls.
- **Collection and documents progress**, a 7-day intake chart, failing sources.

### Scheduled jobs

| Job | When | Does |
|---|---|---|
| `daily-ingest.yml` | 03:00 UTC | `run_prod_worker.py --once` without GeM (its hosts refuse GitHub runners) |
| `purge.yml` | 00:15, 12:15 UTC | `tenders purge --days 0 --yes` |
| `keep-warm.yml` | every 5 min | hits `/healthz` so the first visitor does not wait for a cold start |
| `neon-usage.yml` | 10:00 IST | emails if Neon refuses connections or a limit is mostly spent |
| Vercel Cron `/cron/ingest` | 01:00 UTC | incremental CPPP + state portals within a 240 s budget (browser sources skipped) |
| Vercel Cron `/cron/purge` | 02:00 UTC | the purge, behind `CRON_SECRET` (503 if unset) |

---

## Compliance rules

Enforced in code and covered by tests (`tests/test_robots.py` is the executable
version of this section).

| # | Rule | Where it lives |
|---|------|----------------|
| 1 | Only approved hosts | `config/approved_sources.yaml`: a connector whose host is not listed refuses to run. Each entry records licence, permitted paths, verification date and who verified it. `compliance.BLOCKED_HOSTS` can still ban a host outright (currently empty). |
| 2 | robots.txt before the first request, cached, re-checked weekly | `BaseConnector.robots_allowed_cached`; `run()` refuses on a "no". |
| 3 | No CAPTCHA solving, fingerprint spoofing, stealth browsers or proxy rotation in any connector | 429/5xx are handled by backing off, never by evading. CAPTCHA-gated pages are off-limits; the one CAPTCHA a visitor meets (CPPP's) is typed by that visitor. |
| 4 | Respect rate limits | `rate_limit_seconds` per connector (2 s default, 3 s CPPP and GeM), exponential backoff on errors. |
| 5 | Identify honestly | `compliance.user_agent()` sends a descriptive UA with a contact address and refuses to run while `CONTACT_EMAIL` is unset or the placeholder. |
| 6 | Track provenance and licence per source | `sources` table, public at `GET /sources`. |
| 7 | No personal data | `compliance.scrub_personal` strips emails, phone numbers and officer names from every `raw_payload`. |

**GeM** was added on 2026-09-16 under an operator authorisation recorded in the
allowlist: `robots.txt` permits `/all-bids` and `/showbidDocument/<id>` (checked
with `RobotFileParser`), neither page has a CAPTCHA, and the listing is read by
letting the page issue its own requests.

---

## Sources

| Source | Connector | How it is read | Notes |
|---|---|---|---|
| **GeM** — bidplus.gem.gov.in | `gem.py` | Headless Chromium on `/all-bids`, bid PDFs over plain HTTP | ~47k live bids, the bulk of the corpus. Only from a home connection. |
| **CPPP** — eprocure.gov.in | `cppp.py` | Server-rendered listing `/cppp/latestactivetendersnew/cpppdata` | Pagination uses CPPP's own base64 `?url=` parameter (`?page=N` is ignored). The search form and `FrontEndLatestActiveTenders` are CAPTCHA-gated and off-limits. Sorted by publication date, so `since` stops the crawl early; `CPPP_MAX_PAGES` (50) caps a run. |
| **20 GePNIC portals** (18 states, Defence, Delhi) — MP, HP, Rajasthan, WB, TN, Kerala, Assam, Haryana, Punjab, UP, Uttarakhand, Manipur, Tripura, Arunachal, Odisha, Jharkhand, DNH, Chandigarh, Defence, Delhi | `gepnic.py` (one generic class, `STATE_INSTANCES` table) | `FrontEndListTendersbyDate`, the only GePNIC page without a CAPTCHA | Pagination is Tapestry links with session tokens, so pages are followed, not built. That page defaults to *tenders closing today*, so these portals yield few rows (the 7/14-day tabs need a human CAPTCHA check before they could be added). Each domain was verified separately. |
| **data.gov.in** | `data_gov_in.py` | Official API, pinned OCDS datasets only (`config/data_gov_in_resources.yaml`) | **Switched off** (`sources.enabled = false`) by the owner on 2026-10-10. |

Detail links that expire with a session (CPPP, GePNIC) are kept in
`raw_payload` for auditing but never published as `document_url`.

---

## Local setup and commands

Requires Python 3.11+ and Postgres 14+ (or a Neon branch).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
python -m playwright install chromium      # only needed to crawl GeM

cp .env.example .env          # then edit it
alembic upgrade head
```

`.env` needs at minimum `DATABASE_URL` and `CONTACT_EMAIL` (a real, monitored
address — connectors refuse to run with the placeholder). The fetching laptop
also keeps `.env.production` holding the production `DATABASE_URL`; both files
are gitignored.

```bash
tenders check-robots                 # robots.txt for every source, writes nothing
tenders run CPPP --since-hours 12    # one connector
tenders run GeM --start-page 2701 --max-pages 1000   # a slice of GeM
tenders sources list                 # on/off and rows held per source
tenders sources disable data.gov.in
tenders dedup
tenders purge --days 0 --dry-run

python run_prod_worker.py --once     # one Daily run
python gem_deep.py                   # the deep GeM crawl
uvicorn app.api:app --reload         # the site on http://127.0.0.1:8000
pytest -q                            # 439 tests, no network needed
```

Useful settings: `ENRICH_WORKERS` (6), `ENRICH_LIMIT` (4000), `SKIP_CONNECTORS`,
`ENRICH_SKIP_SOURCES`, `DEDUP_CACHE`, `CPPP_MAX_PAGES`, `RETENTION_DAYS`,
`AUTOSTART_JOB` (start a dashboard job with the server), `ADMIN_EMAILS`.

---

## Adding a new source

Steps 1–4 are done by a human, before any code is written.

1. **Read `https://<domain>/robots.txt` yourself.** If it disallows the paths the
   connector needs, stop.
2. **Read the site's terms of use / disclaimer.**
3. **Confirm there is no CAPTCHA, login or anti-bot gate** in front of the data.
   If only part of the site is gated, scope the connector to the un-gated part
   and write that scope in its docstring (`cppp.py` is the worked example).
4. **Identify the licence.**
5. Add the domain to `config/approved_sources.yaml` with licence, paths,
   verification date and verifier.
6. Subclass `BaseConnector`: set `source_name`, `base_url`, `license`,
   `rate_limit_seconds`, `paths`; implement `fetch_batch` and `normalize`. Do not
   override `run()`.
7. Register it in `app/connectors/__init__.py` (a GePNIC state is one row in
   `STATE_INSTANCES`).
8. Add tests against a captured real page (`tests/fixtures/`).
9. `tenders check-robots <name>`, then `tenders run <name> --since-hours 1`, and
   read the summary before scheduling it.

---

## Deploying

The repo is Vercel-ready (`vercel.json`, `api/index.py`, `requirements.txt`).
Pushing to `main` deploys.

1. **Hosted Postgres** (Neon). The URL must start `postgresql+psycopg://`.
2. **Migrations from a shell, not from Vercel**:
   `DATABASE_URL=<hosted-url> alembic upgrade head`. Run them *before* pushing
   code whose models need them, or the live site fails on those tables.
3. **Environment variables** in Vercel: `DATABASE_URL`, `SESSION_SECRET`,
   `CONTACT_EMAIL`, `PUBLIC_BASE_URL`, `CRON_SECRET`, `ADMIN_EMAILS`,
   `RETENTION_DAYS`, and the `SMTP_*` / `GOOGLE_*` keys (see `.env.example`).
4. **Google OAuth callback**: `https://<domain>/auth/google/callback`, exactly.

### Function region

`vercel.json` pins functions to `iad1`, next to the database in `us-east-1`,
not next to users. A page runs several queries, so paying the ocean crossing
once per request beats paying it once per query. If the database moves, move
this with it.

### What cannot run on Vercel

Long jobs and the GeM browser. A serverless instance is frozen once it answers,
so crawls run on the fetching laptop. `Dockerfile` and `deploy/oracle-setup.sh`
package one Daily run as a container for any always-on host that GeM answers
(not GitHub runners), if the laptop is ever to be retired; keep the base image
tag and the `playwright` pin in step.

---

## Layout

```
app/
  connectors/
    base.py          BaseConnector: allowlist, robots, rate limit, batched fingerprinted upsert
    cppp.py          eprocure.gov.in
    gem.py           bidplus.gem.gov.in (Playwright)
    gepnic.py        generic GePNIC connector + 20 portal instances
    data_gov_in.py   api.data.gov.in (switched off)
  enrich.py          bid-document reading;  bidpdf.py  GeM PDF parsing
  dedup.py           cross-source duplicate linking
  retention.py       purge of closed tenders
  scheduler.py       run_connector (+ an APScheduler loop for always-on hosts)
  adminjobs.py       dashboard jobs, incl. the Daily run
  activity.py        "fetching / reading / dedup" heartbeats for the admin page
  admin.py           operator numbers and ADMIN_EMAILS
  api.py, web.py     FastAPI app, JSON API, page routes
  facts.py, eligibility.py, geo.py, matching.py   what pages say about a tender
  auth.py, oauth.py, security.py, mailer.py       accounts, sessions, rate limits, mail
  cppp_relay.py      visitor-typed CAPTCHA relay for CPPP notices
  compliance.py      allowlist checks, honest UA, personal-data scrubbing
  models.py, schemas.py, db.py, cli.py
run_prod_worker.py   one Daily run (purge, every source, documents, dedup)
gem_deep.py          GeM pages 501..end with three crawlers, then documents
mac/                 setup.sh, start.sh  (the Mac fetching laptop)
windows/start.ps1    the Windows twin of mac/start.sh
neon_usage.py        the daily Neon limits check
templates/, static/  pages, CSS, per-page JS, icons, buyer logos
config/              approved_sources.yaml, data_gov_in_resources.yaml
migrations/          alembic (production at 0014)
tests/               439 tests, no network
.claude/skills/      do-setup, start-fetching
```

---

## Things that broke, and why

Kept because each one explains a line of code that would otherwise look
arbitrary.

| When | Symptom | Cause | Fix |
|---|---|---|---|
| Sep 2026 | Whole site 500 for days | Backlog jobs pulled whole rows from production; Neon's 5 GB transfer ran out | Purge first, fingerprints, edge cache, read throttle, dedup cache, `neon-usage` alarm |
| 8 Oct 2026 | Site 500 on every path (`FUNCTION_INVOCATION_FAILED`) | `selectolax` 1.0 removed `selectolax.parser`; the requirement had no upper bound | `selectolax>=0.3.21,<1.0` |
| 9 Oct 2026 | A deploy failed on `httptools` | Vercel built while `httptools` 0.9.0 was mid-upload to PyPI | Redeploy once the release finished |
| 10 Oct 2026 | GeM added ~0 rows | GeM's default sort became end-date-oldest, and script-drawn cards say `Bid No.:` | Select the page's latest-first sort; accept both labels |
| 10 Oct 2026 | CPPP crawl stalled 35 s every 5 pages | CPPP's detail-link token changes each fetch, so no fingerprint ever matched | `FP_VOLATILE` |
| 10 Oct 2026 | Crawl capped at ~3 rows/s | One Neon round trip (~310 ms) per row | Batches of 50, one query each way |
| 10 Oct 2026 | Dedup failed after 30 min: `IdleInTransactionSessionTimeout` | The read's transaction stayed open while comparing | End the transaction before comparing |
