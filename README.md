# Indian Public Tender Aggregator

Aggregates public tender/procurement notices from official Indian government
sources, normalizes them into one schema, stores them in Postgres, and serves
them over a REST API.

This is the compliant alternative to a "GeM scraper". It does not scrape GeM,
and it is built so that it cannot be quietly made to.

---

## Compliance rules

These are enforced in code and covered by tests, not just written down here.
`tests/test_robots.py` is the executable version of this section.

| # | Rule | Where it lives |
|---|------|----------------|
| 1 | Never touch gem.gov.in or any subdomain | `app/compliance.py` `BLOCKED_HOSTS`. Checked when a connector class is *defined*, when it is constructed, on every `get()`, and on every redirect via an httpx event hook. |
| 2 | Check robots.txt before the first request, cache it, re-check weekly | `BaseConnector.check_robots_allowed` / `robots_allowed_cached`. `run()` calls it first and refuses if the answer is no. |
| 3 | No CAPTCHA solving, no fingerprint spoofing, no stealth browsers, no proxy rotation | Nothing in the dependency list can do any of it. 429/5xx are handled by backing off, never by evading. A source that needs any of it is not a source. |
| 4 | Respect rate limits | `rate_limit_seconds` per connector (default 2s, CPPP 3s), enforced in `BaseConnector.get`, with exponential backoff on errors. |
| 5 | Identify honestly | `compliance.user_agent()` builds a descriptive UA with a contact address, and **refuses to run** while `CONTACT_EMAIL` is unset or still the placeholder. No browser UA strings. |
| 6 | Track provenance and licence per source | `sources` table: `license`, `robots_txt_checked_at`, `robots_txt_allowed`. Exposed publicly at `GET /sources`. |
| 7 | No personal data | `compliance.scrub_personal` runs on every `raw_payload` during validation, so bidder emails, phone numbers, and officer names never reach the database. |

### On GeM

GeM's `robots.txt` disallows automated access, so there is no connector for it and
adding one raises `BlockedSourceError` at import time. Getting GeM data legitimately
requires a **formal data-sharing / API agreement with GeM** — a business-development
task, not an engineering one. Note that CPPP already carries a GeM-integrated feed,
which is the compliant route to much of the same information.

---

## Sources

### Tier 1 (built)

**CPPP — eprocure.gov.in** (`app/connectors/cppp.py`) — *verified working end to end.*

NIC's Central Public Procurement Portal, the mandatory publication point for central
government tenders above the GFR 2017 threshold, aggregating 100+ organisations.

`robots.txt` returns **HTTP 404**, i.e. no restrictions (RFC 9309 treats an
unavailable robots.txt as allow-all). The connector still verifies this itself at
runtime on every run.

One thing worth knowing: CPPP publishes the same data on two pages, and one of them
is CAPTCHA-gated.

- `/eprocure/app?page=FrontEndLatestActiveTenders` — CAPTCHA-gated in full
  ("Provide Captcha and click on Search button to list all active tenders").
  **Off-limits under rule #3.**
- `/cppp/latestactivetendersnew/cpppdata` — the CAPTCHA here gates only the *search
  form*. The default listing renders server-side without it, and its pagination
  links are plain URLs. **This is the only thing the connector touches.**

Pagination uses CPPP's own base64 `?url=` parameter; a plain `?page=N` is silently
ignored and returns page 1 again. The listing has no tender value (that lives on the
detail page), so `estimated_value` is null for CPPP rows.

**data.gov.in** (`app/connectors/data_gov_in.py`) — *built, not yet verified against
the live API.*

The Open Government Data Platform. Official API, free registered key, datasets
published under NDSAP, which permits reuse including commercial reuse. Only
`api.data.gov.in` is contacted, which is also the host whose robots.txt is checked
(404 / allow-all).

> ⚠️ **Partially verified.** `api.data.gov.in` is only intermittently reachable
> from the build machine — very roughly one connection in three succeeds, the
> rest time out at the TCP level. In the windows that did work, the catalogue
> endpoint (`/lists`) responded and the real field names for the pinned datasets
> were read off it, which is what the aliases below are built against. What has
> still never completed from here is a `/resource/{id}` fetch, so the ingest loop
> and pagination have not run end to end. Run `tenders run data.gov.in` from a
> better network and check the `RunSummary`.

**Discovery does not decide what gets ingested — and that is deliberate.**
Searching this catalogue for procurement keywords is wildly imprecise:

- `tender` matches *"Variety-wise Daily Market Prices of **Tender Coconut**"*.
- `procurement` returns 242 datasets, mostly paddy, wheat and coarsegrain bought
  from farmers under MSP.

Ingesting those would fill the tenders table with vegetable prices. So
`config/data_gov_in_resources.yaml` is an allowlist: **only pinned resource ids
are fetched**, and with none pinned the connector ingests nothing and says so.
`tenders discover-data-gov-in` lists candidates with their titles and columns for
a human to review and pin.

Currently pinned: the six **Assam Public Procurement** datasets (2016-17 through
2021-22), which are the genuine public procurement records in that result set.
They are published as **OCDS** (Open Contracting Data Standard), so their columns
are flattened OCDS paths — `tender/id`, `tender/title`, `tender/value/amount`,
`tender/datePublished`, `tender/mainProcurementCategory`, `buyer/name`.

Dataset column names vary per publisher, so normalization maps columns by alias
(`FIELD_ALIASES`) rather than assuming a fixed schema. Two things that alias table
has to get right:

- **Units.** A column called `Estimated Cost (Rs. in Lakhs)` is multiplied by
  100,000, because reading it as rupees turns a 45-lakh tender into 45 rupees.
- **camelCase.** OCDS paths flatten to runs with no separator inside the word —
  `tender/datePublished` becomes `tender_datepublished`, so an alias of
  `published_date` never matches it. Several aliases are run-together for exactly
  this reason.

### Tier 2 — state GePNIC portals

**Madhya Pradesh — mptenders.gov.in** (`app/connectors/gepnic.py`) — *verified
working end to end.*

`GePNICConnector` is the generic, per-domain-configurable connector for NIC's
GePNIC engine (~48 instances nationally); `MPTendersConnector` is a two-line
subclass of it. Adding another state is a subclass plus an allowlist entry — but
the allowlist entry is the part that requires a human, and without it the
connector refuses to run.

Verified for MP on 2026-08-25:

- `robots.txt` → HTTP 404, no restrictions.
- The Disclaimer page describes the portal as existing "to facilitate faster
  dissemination and easy access to information related to Tenders" and places no
  restriction on automated access.
- `FrontEndLatestActiveTenders` and `FrontEndTendersByOrganisation` are
  **CAPTCHA-gated** ("Provide Captcha and click on Search button to list…") and
  are therefore off-limits. `FrontEndListTendersbyDate` has no CAPTCHA anywhere on
  the page and is the only page the connector reads.

Two structural differences from CPPP worth knowing before adding more states:

- Pagination is Tapestry `$TablePages.linkPage` links carrying session-bound
  tokens, so pages must be **followed**, not constructed — there is no `?page=N`.
- The listing is ordered by *closing* date, not publication date. An old row
  therefore does not mean the rest are old, so `--since-hours` filters rows
  individually instead of stopping the crawl early (CPPP does the opposite).

Detail links expire with the session, so they are kept in `raw_payload` for
auditing but never published as `document_url`.

**This approval covers mptenders.gov.in only.** Every other state is a separate
domain with its own robots.txt and its own terms — one state's permissiveness says
nothing about another's. Run the checklist below per domain.

---

## Setup

Requires Python 3.11+ and Postgres 14+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows;  source .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"

cp .env.example .env          # then edit it -- see below
createdb tenders
alembic upgrade head
```

If you need Postgres on Windows:

```powershell
winget install --id PostgreSQL.PostgreSQL.17 --silent `
  --accept-package-agreements --accept-source-agreements
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" -U postgres -h 127.0.0.1 tenders
```

> The silent install leaves the `postgres` superuser on the default password
> `postgres`, listening on localhost. Fine for local-first development, **not**
> fine for anything reachable from outside the machine — change it before this
> leaves your laptop.

`.env` needs at minimum:

- `DATABASE_URL` — Postgres connection string.
- `CONTACT_EMAIL` — **required.** A real, monitored address so a site operator can
  reach a human. Connectors refuse to run while this is the placeholder.
- `DATA_GOV_IN_API_KEY` — free, from https://data.gov.in (register, then find the
  key under *My Account*). Only the data.gov.in connector needs it.

## Running

```bash
tenders check-robots            # check robots.txt for every source, write nothing
tenders run                     # run every connector once
tenders run CPPP                # run one
tenders run "MP eProcurement"   # names with spaces need quoting
tenders run CPPP --since-hours 12
tenders dedup                   # link cross-source duplicates
tenders discover-data-gov-in    # list candidate datasets to pin (--show-fields)

python -m app.scheduler         # all connectors on a repeating schedule
uvicorn app.api:app --reload    # API + matching form on http://127.0.0.1:8000
                                # (form at /, API docs at /docs)
pytest -q                       # 141 tests, no network needed
```

`check-robots` exits non-zero if any source disallows us, which makes it usable as a
monitoring check.

### Backfilling CPPP

CPPP carries ~32,000 active tenders across ~3,200 pages. At the 3s rate limit
that is a shade under three hours, and a single run that long is fragile — a
stalled connection can hang a run past any per-request timeout, and one failure
would otherwise discard everything fetched so far.

Two things address that. Rows commit in batches of 200 as the run proceeds, so a
failure keeps what it already had; and `--start-page` makes a backfill resumable,
so it can be seeded in bounded chunks:

```bash
for start in $(seq 1 300 3300); do
  timeout 1500 tenders run CPPP --max-pages 300 --start-page $start
done
```

Re-running a chunk is safe — rows upsert on `(source_id, external_ref)`. Page
numbers drift as new tenders publish, so resuming by page is approximate; the
unique key absorbs the overlap.

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /tenders` | Filter by `q`, `category`, `status`, `department`, `organization`, `source_id`, `published_from/to`, `deadline_from/to`, `min_value`, `max_value`. Paginated (`limit`, `offset`), sortable by `deadline`, `published_date`, `first_seen_at`. |
| `GET /tenders/{id}` | Full record including `raw_payload`. |
| `GET /sources` | Sources with their compliance metadata — licence, robots.txt status, rate limit. Public on purpose: our own users deserve the same transparency. |
| `GET /runs` | Recent connector runs. How you notice a source has started refusing us. |
| `GET /health` | Liveness. |
| `GET /questionnaire` | The answer options (sectors, districts), served from the taxonomy. |
| `POST /companies` | Create a bidder profile. Returns its id. |
| `GET`/`PUT /companies/{id}` | Read / replace a profile. |
| `GET /companies/{id}/matches` | Ranked tenders for that profile, each with a score and reasons. |
| `POST /match` | Score a profile without saving it. |
| `GET /` | The company questionnaire (plain HTML form). |
| `GET /c/{id}` | That company's matches, server-rendered. |

Rows already linked to an earlier duplicate are hidden by default; pass
`include_duplicates=true` to see them.


## Matching companies to tenders

A company answers a short questionnaire at `/` and gets a ranked list of tenders
it could actually bid on, each with the reasons it matched.

**Why the questions are the questions they are.** `tenders.category` and
`tenders.estimated_value` are null on **100%** of rows from both live connectors
-- neither is published on a listing page. So "pick your category" has nothing to
match against, and the sector has to be derived from the tender *title*
(`app/matching.py`, `SECTOR_PATTERNS`: 11 sectors read off the real MP listing,
which classify 29 of its 30 rows; the one miss is a title that is nothing but a
reference number).

Three things that taxonomy has to get right, all found in the live data:

- Sector comes from the **title**, never the department. A medical college's
  scrap auction is a scrap contract, not a pharma one.
- No `bus` in the vehicle sector: in MP power tenders "BUS" is a busbar, and
  "33 kv BUS STAND" is a locality being electrified.
- No "irrigation" in agriculture: on this portal it means an AG *pump feeder*,
  i.e. electrical work.

**Scoring.** Sector overlap is worth the most, then keyword hits in the title,
then a district match, then a small bonus for having comfortable time to prepare.
A tender must match on sector *or* keyword to appear at all -- without that rule
every company sees every tender. The weights at the top of `matching.py` are
calibration knobs; retune them as the corpus grows.

**Hard filters** (a tender you cannot bid on is not a weak match): already
closed, closing sooner than the company's stated preparation time, no deadline at
all, or -- only when the tender's value is actually known -- beyond the company's
stated execution capacity. A **null value never disqualifies**, which matters
because every live row has one, and is the same rule `dedup.values_match` follows.


## Scheduling

`python -m app.scheduler` runs every connector on `SCHEDULE_INTERVAL_HOURS`
(default 6), staggered so two connectors never wake together, plus the dedup job.
Each run re-fetches a 2-hour overlap so a slow publish or a failed run does not
leave a permanent hole. Every run writes a row to `connector_runs`.

## Deduplication

The same tender legitimately appears on more than one source. Both rows are kept —
each is a faithful record of what its source published — and a periodic job links
the newer to the older via `duplicate_of`, matching on canonicalized title
(≥0.88 similarity), organization (≥0.80), value within 2%, and deadlines within 3
days. Titles that are pure procurement boilerplate match nothing rather than
matching everything.

Hard-deduping at ingest was rejected deliberately: a wrong merge is unrecoverable,
a wrong link is one `UPDATE`.

---

## Adding a new source

Every step of this checklist is mandatory. Steps 1–4 are done by a human, before
any code is written.

1. **Read `https://<domain>/robots.txt` yourself.** Confirm every path the connector
   needs is permitted. If it disallows them, stop — there is no next step.
2. **Read the site's terms of use / disclaimer page.**
3. **Confirm there is no CAPTCHA, login, or anti-bot gate** between you and the
   data. If there is, the source is out. We do not solve, bypass, or evade it
   (rule #3). If only *part* of the site is gated, the connector must be scoped to
   the un-gated part, and that scope must be written down in the connector's
   docstring — see `cppp.py` for the worked example.
4. **Identify the licence** the data is published under and record it.
5. Add the domain to `config/approved_sources.yaml` with its licence, the paths, the
   verification date, and who verified it. A connector whose host is not in this
   file refuses to run — this is what stops a domain being added by accident.
6. Subclass `BaseConnector`, set `source_name`, `base_url`, `license`,
   `rate_limit_seconds`, and `paths`, then implement `fetch_batch` and `normalize`.
   Do not override `run()` — the robots-check-first behaviour lives there.
7. Register it in `app/connectors/__init__.py`.
8. Add tests: at minimum a parsing test against a captured real page (see
   `tests/fixtures/`) and a normalization test.
9. Run `tenders check-robots <name>`, then `tenders run <name> --since-hours 1`, and
   read the `RunSummary` before scheduling it.

## Layout

```
app/
  compliance.py    hard rules: blocked hosts, honest UA, personal-data scrubbing
  models.py        sources, tenders, connector_runs
  schemas.py       TenderRecord (the one normalized shape), API models
  connectors/
    base.py        BaseConnector: robots-check-first, rate limit, backoff, upsert
    cppp.py        eprocure.gov.in
    data_gov_in.py api.data.gov.in
    gepnic.py      generic state GePNIC connector + Madhya Pradesh
  dedup.py         cross-source duplicate linking
  matching.py      sector taxonomy + company-profile scoring
  scheduler.py     apscheduler jobs
  api.py           FastAPI
  web.py           the questionnaire form + results page (unstyled)
  cli.py           tenders run / check-robots / dedup
config/
  approved_sources.yaml        allowlist -- unlisted domains cannot run
                               (currently: CPPP, data.gov.in, MP)
  data_gov_in_resources.yaml   pinned dataset ids (empty = discover by search)
migrations/        alembic
tests/             141 tests, no network
```

## Out of scope

Anything targeting gem.gov.in; anti-detection, CAPTCHA-bypass, or proxy-rotation
tooling; bidder/seller personal or contact information; deployment infrastructure
(local-first until the pipeline is proven).
