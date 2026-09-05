# Source survey — 6 September 2026

Search for additional legitimate tender sources, authorised by the operator
(nirmaanos35@gmail.com). Every candidate was tested with the project's **own**
compliance path — `BaseConnector.check_robots_allowed` and the real GePNIC
parser — so a PASS means the connector actually works, not that a page loaded.
`robots.txt` was fetched **first**; a listing page was only touched where robots
permitted it. Nothing was bypassed, and GeM was never contacted.

Raw machine output: `probe_results.json`. Probe script: `probe_sources.py`.

---

## Result

**40 domains tested. 3 usable, 2 of them new.**

| | Count |
|---|---|
| **PASS — data obtained** | **3** (one was our existing MP portal, used as a control) |
| CAPTCHA-gated — listing not readable | 18 |
| Domain does not resolve | 16 |
| robots.txt disallows us | 1 |
| No listing at that path | 2 |

Corpus went **4,287 → 4,332 tenders**. 45 new rows from two new states.

---

## Sources ADDED — data obtained ✅

### 1. Himachal Pradesh — `hptenders.gov.in`
- **Data obtained: YES.** 3 tenders ingested (`fetched=3 new=3 errors=0`).
- robots.txt: **HTTP 404** — no restrictions (RFC 9309 treats this as allow-all).
- Disclaimer: standard NIC wording, *"to facilitate faster dissemination and easy
  access to information related to Tenders"*. No restriction on automated access.
- CAPTCHA: present on the **search form only**, not over the listing table. The
  listing renders server-side — same arrangement as CPPP and MP.
- Sample: *"CO School building at GSSS Badhalag Tehsil Arki Distt. Solan H.P"*

### 2. Rajasthan — `eproc.rajasthan.gov.in`
- **Data obtained: YES.** 42 tenders ingested (`fetched=62 new=42 errors=0`).
- robots.txt: **HTTP 404** — no restrictions.
- Disclaimer: same NIC dissemination wording, Government of Rajasthan. No
  restriction on automated access.
- CAPTCHA: search form only; listing table parsed 10 rows server-side on probe.
- Sample: *"Construction of 01 ACR (Additional Class Room) in GPS"*

Both are recorded in `config/approved_sources.yaml` with the verification date
and evidence, and have connector classes in `app/connectors/gepnic.py`.

---

## Sources REJECTED — no data

### CAPTCHA-gated (18) — listing is empty until a CAPTCHA is solved

Reading these would mean solving or evading a CAPTCHA, which the project forbids
(rule #3). **No data obtained from any of them.**

Odisha `tendersodisha.gov.in` · Jharkhand `jharkhandtenders.gov.in` · Assam
`assamtenders.gov.in` · Uttarakhand `uktenders.gov.in` · Jammu & Kashmir
`jktenders.gov.in` · West Bengal `wbtenders.gov.in` · Tamil Nadu
`tntenders.gov.in` · Goa `eprocure.goa.gov.in` · Manipur `manipurtenders.gov.in`
· Mizoram `mizoramtenders.gov.in` · Nagaland `nagalandtenders.gov.in` · Tripura
`tripuratenders.gov.in` · Arunachal Pradesh `arunachaltenders.gov.in` · Dadra &
Nagar Haveli `dnhtenders.gov.in` · Daman & Diu `ddtenders.gov.in` · Haryana
`etenders.hry.nic.in` · Uttarakhand UTL `tendersutl.gov.in` · Meghalaya
`meghalayatenders.gov.in`

> **Correction made during this survey.** My first pass flagged all of these
> *plus* Himachal and Rajasthan as CAPTCHA-gated, because the detector searched
> for the word "captcha" anywhere on the page. That was wrong: CPPP and MP both
> show a CAPTCHA'd *search form* beside a listing that renders fine. The rule was
> changed to "does the listing table parse, and is the CAPTCHA over the data?",
> which is what recovered Himachal and Rajasthan. Without that fix this survey
> would have returned zero sources.

### Domain does not resolve (16) — DNS failure

My guessed naming pattern (`<state>tenders.gov.in`) is wrong for most states.
These were never reached, so nothing is known about their policies.

Uttar Pradesh `etender.up.gov.in` · Kerala `tender.kerala.gov.in` · Sikkim ·
Ladakh · Chandigarh · Punjab · Bihar · Chhattisgarh · Haryana
`haryanatenders.gov.in` · Andhra Pradesh · Gujarat · Karnataka · Delhi ·
Puducherry · Andaman & Nicobar · Punjab PWD `eprocpbpwd.gov.in`

**Their real addresses exist and are worth a second pass** — e.g. Karnataka is
`eproc.karnataka.gov.in`, Odisha is `tendersodisha.gov.in`. This survey used a
naming convention rather than an authoritative list.

### robots.txt disallows (1)

- **Maharashtra `mahatenders.gov.in`** — `robots.txt` explicitly disallows
  `/nicgep/app`. This is the only portal that actively told us not to crawl it.
  Honoured; not added. Would need written permission from the portal operator.

### No listing at that path (2)

- **Telangana `tender.telangana.gov.in`** — HTTP 404 on `/nicgep/app`. Likely a
  different platform or path, not a refusal. Worth re-checking by hand.
- **`eprocure.gov.in`** — expected: CPPP is already ingested via its own
  connector at a different path. Included as a control.

---

## Not contacted

- **GeM (`gem.gov.in`)** — its robots.txt disallows automated access. Blocked at
  four layers in `app/compliance.py`; no request was made. Getting this data
  legitimately needs a data-sharing agreement with GeM, not code. CPPP already
  carries a GeM-integrated feed.
- **`api.data.gov.in`** — already configured, still unreachable (TCP timeouts,
  not a policy refusal). Unchanged by this survey.

---

---

## Round 2 — DNS re-run (20 more domains)

Round 1 lost 16 candidates to DNS failure because I guessed at
`<state>tenders.gov.in`. Round 2 used real addresses from search and the CPPP
states list. **Result: 0 usable.**

| Outcome | Domains |
|---|---|
| **robots.txt disallows** | Karnataka `eproc.karnataka.gov.in` — explicit `Disallow` on `/nicgep/app`. Honoured, not added. |
| **CAPTCHA-gated** | Delhi `govtprocurement.delhi.gov.in` · Chandigarh `etenders.chd.nic.in` · Punjab `eproc.punjab.gov.in` · Uttar Pradesh `etender.up.nic.in` |
| **Unreachable / no listing** | Bihar `eproc.bihar.gov.in` · Chhattisgarh (both addresses) · Puducherry (both) · Sikkim · Gujarat · Andhra Pradesh · Kerala · Jharkhand · Telangana (both) · Uttarakhand alt |

Karnataka is now the **second** portal to explicitly refuse crawling, after
Maharashtra. **Running total across both rounds: 60 domains tested, 3 usable.**

---

## CPPP detail pages — investigated, NOT viable ❌

`estimated_value` and `category` are null on 100% of rows because they live on
each tender's detail page rather than the listing. I proposed ingesting those
pages and **said they were not CAPTCHA-gated. That was wrong.** What the test
actually found:

1. **Stored `document_url` values are dead.** The base64 tokens expire — fetching
   one returns *"Invalid Url."* So the URLs already in the database cannot be
   re-fetched later, at all.
2. **A freshly harvested link also fails** with *"Invalid Url."* even inside the
   same HTTP session, with cookies carried.
3. **Adding a truthful `Referer`** (we genuinely did follow the link from that
   listing) gets past that — HTTP 200, no error.
4. **But the page that comes back contains no tender data.** Zero tables, none of
   the expected fields, and this instead:
   > *"What code is in the image? Enter the characters shown in the image."*

The detail pages are CAPTCHA-gated. Reading them means solving a CAPTCHA, which
rule #3 forbids. **No code was written for this and nothing was ingested.**

**Consequence:** `estimated_value` and `category` cannot be filled from CPPP by
any permitted route. The "max project value you can execute" question on the
questionnaire stays inert, and the honest note already shown under that field on
the form remains accurate. The only legitimate routes to that data are a
data-sharing agreement with the portal, or a source that publishes value on its
listing.

---

## What this survey shows

The assumption that ~48 GePNIC portals are all harvestable is wrong. **18 of the
21 reachable state portals gate their listing behind a CAPTCHA.** Madhya Pradesh,
Himachal Pradesh and Rajasthan are the exceptions, not the rule. State coverage
will not come from more scraping — it needs either portal-operator agreements or
the CPPP central feed, which already aggregates 100+ organisations and is 99% of
the corpus.

## Next steps worth taking

Both follow-ups I proposed have now been tried and both are closed:

- ~~Re-run against real domains~~ — done in round 2, 0 usable.
- ~~Ingest CPPP detail pages~~ — CAPTCHA-gated, not permitted.

What is actually left:

1. **Written access requests.** 22 portals (Maharashtra, Karnataka and the 20
   CAPTCHA-gated ones) have the data and simply will not serve it to a crawler.
   An email to each portal operator asking for API or bulk access is the only
   remaining route, and it is a business task, not an engineering one.
2. **Fix `api.data.gov.in` connectivity.** It is already built and approved, and
   is the one source that could add volume without anyone's permission. It fails
   on TCP timeouts from this machine, not on policy. Try another network.
3. **Accept CPPP as the corpus.** It aggregates 100+ central organisations and is
   already ~99% of the data. Effort is better spent on match quality than on
   chasing state portals that have collectively yielded 45 rows.

Sources consulted: [gepnic.gov.in](https://gepnic.gov.in/) ·
[CPPP states list (PDF)](https://eprocure.gov.in/mmp/sites/default/files/eproc/States_eProc_relatedlinks.pdf)
· [NIC GePNIC project page](https://www.nic.gov.in/project/government-eprocurement-system/)
