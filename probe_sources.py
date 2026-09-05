"""One-off survey of candidate tender portals.

Uses the project's OWN compliance path -- BaseConnector.check_robots_allowed and
the real GePNIC parser -- so a PASS here means the connector will genuinely work,
not that a page merely loaded. robots.txt is fetched first and the listing page is
only touched when robots permits it.

Writes probe_results.json for the report. Not part of the app; delete when done.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser

from app.connectors.gepnic import LISTING_PATH, GePNICConnector

# CAPTCHA markers -- if the listing itself is gated we do not touch it (rule #3).
CAPTCHA_MARKERS = ("captcha", "enter the characters", "provide captcha")

CANDIDATES = {
    # Round 2: real addresses, replacing the guessed <state>tenders.gov.in names
    # that failed DNS in round 1. Sourced from search + the CPPP states list.
    "eproc.bihar.gov.in": "Bihar",
    "eproc.cgstate.gov.in": "Chhattisgarh (eproc)",
    "cgeprocurement.gov.in": "Chhattisgarh (alt)",
    "govtprocurement.delhi.gov.in": "Delhi",
    "eproc.karnataka.gov.in": "Karnataka",
    "etenders.chd.nic.in": "Chandigarh",
    "eproc.py.gov.in": "Puducherry",
    "tender.py.gov.in": "Puducherry (alt)",
    "sikkimtenders.gov.in": "Sikkim",
    "eproc.punjab.gov.in": "Punjab",
    "tenders.gujarat.gov.in": "Gujarat",
    "etender.up.nic.in": "Uttar Pradesh (nic)",
    "etender.uk.gov.in": "Uttarakhand (alt)",
    "tenders.apeprocurement.gov.in": "Andhra Pradesh",
    "eprocurement.gov.in": "AP/TS shared eProcurement",
    "etenders.kerala.gov.in": "Kerala (alt)",
    "eproc.jharkhand.gov.in": "Jharkhand (alt)",
    "eproctenders.gov.in": "generic probe",
    "tenders.telangana.gov.in": "Telangana (alt)",
    "eprocurement.telangana.gov.in": "Telangana (eproc)",
}


def probe(host: str, label: str) -> dict:
    out = {"domain": host, "state": label, "robots": None, "verdict": None,
           "detail": None, "rows": 0}
    base = f"https://{host}"

    connector_cls = type(
        f"Probe_{host.replace('.', '_').replace('-', '_')}",
        (GePNICConnector,),
        {"source_name": f"probe:{host}", "base_url": base, "rate_limit_seconds": 1.0},
    )
    client = httpx.Client(
        timeout=httpx.Timeout(12.0), follow_redirects=True,
        headers={"User-Agent": __import__("app.compliance", fromlist=["x"]).user_agent()},
    )
    connector = connector_cls(client=client)
    connector.max_retries = 1
    try:
        allowed = connector.check_robots_allowed(connector.paths)
        out["robots"] = "allowed" if allowed else (connector.robots_reason or "disallowed")
        if not allowed:
            reason = (connector.robots_reason or "").lower()
            out["verdict"] = "unreachable" if "could not reach" in reason else "robots-disallowed"
            out["detail"] = connector.robots_reason
            return out

        # robots permits it, so the listing page may now be read.
        resp = connector.get(base + LISTING_PATH)
        if resp.status_code != 200:
            out["verdict"] = "no-listing"
            out["detail"] = f"listing returned HTTP {resp.status_code}"
            return out

        # The word "captcha" on the page is NOT itself a gate: CPPP and MP both
        # show a CAPTCHA'd *search form* beside a listing that renders server-side.
        # What matters is whether the DATA TABLE is readable without solving one.
        doc = HTMLParser(resp.text)
        tables = doc.css("table.list_table")
        captcha_on_page = any(m in doc.text().lower() for m in CAPTCHA_MARKERS)
        captcha_over_data = any(
            m in t.text().lower() for t in tables for m in CAPTCHA_MARKERS
        )
        rows = connector.parse_rows(resp.text)
        out["rows"] = len(rows)
        out["captcha_on_page"] = captcha_on_page

        if captcha_over_data:
            out["verdict"] = "captcha-gated"
            out["detail"] = "CAPTCHA sits over the listing table; off-limits (rule #3)"
        elif rows:
            out["verdict"] = "PASS"
            out["detail"] = (
                f"parsed {len(rows)} rows server-side"
                + (" (CAPTCHA present but only on the search form)" if captcha_on_page else "")
            )
            out["sample"] = (rows[0].get("title") or "")[:90]
        elif captcha_on_page:
            out["verdict"] = "captcha-gated"
            out["detail"] = "listing renders empty; the data is behind the CAPTCHA'd search"
        else:
            out["verdict"] = "no-rows"
            out["detail"] = "page loaded, not gated, but the parser found no rows"
    except Exception as exc:
        out["verdict"] = "error"
        out["detail"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        connector.close()
    return out


if __name__ == "__main__":
    results = []
    for i, (host, label) in enumerate(CANDIDATES.items(), 1):
        r = probe(host, label)
        results.append(r)
        print(f"[{i:>2}/{len(CANDIDATES)}] {host:<28} {r['verdict']:<18} "
              f"rows={r['rows']:<4} {(r['detail'] or '')[:60]}", flush=True)
    Path("probe_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = [r for r in results if r["verdict"] == "PASS"]
    print(f"\n{len(passed)} of {len(results)} usable: "
          + ", ".join(r["domain"] for r in passed))
