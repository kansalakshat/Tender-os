"""A restarted fetch carries on instead of starting over.

Restarting the start script used to send the Daily run back to CPPP and page 1
of GeM, and every deep crawler back to the start of its slice: 45 minutes of
re-reading pages already saved, "0 new" all the way.
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import run_prod_worker
from app.connectors.gem import GeMConnector


def _gem(resume_file, start_page=501):
    c = object.__new__(GeMConnector)
    c.resume_file = str(resume_file) if resume_file else None
    c.start_page = start_page
    return c


def test_gem_resumes_at_the_page_it_had_committed_today(tmp_path):
    f = tmp_path / "gem_deep_1.json"
    crawler = _gem(f)
    crawler._page = 730
    crawler.committed()

    assert _gem(f)._resume_page() == 730
    # Another slice's bookmark is not this one's.
    assert _gem(f, start_page=2001)._resume_page() == 2001


def test_gem_starts_over_on_a_new_day_or_without_a_file(tmp_path):
    f = tmp_path / "gem.json"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    f.write_text(json.dumps({"day": yesterday, "start": 501, "page": 900}))
    assert _gem(f)._resume_page() == 501, "yesterday's page is stale: the listing moved"
    assert _gem(tmp_path / "missing.json")._resume_page() == 501
    assert _gem(None)._resume_page() == 501


def test_daily_run_skips_what_finished_today_and_retries_the_rest(tmp_path, monkeypatch):
    import app.connectors, app.dedup, app.enrich, app.retention, app.scheduler

    ran = []

    def fake_run_connector(name, since, enrich_new, resume_file=None, slices=1):
        ran.append(name)
        return SimpleNamespace(status="ok" if name == "CPPP" else "error")

    monkeypatch.setattr(app.connectors, "REGISTRY", {"CPPP": None, "GeM": None})
    monkeypatch.setattr(app.scheduler, "run_connector", fake_run_connector)
    monkeypatch.setattr(app.retention, "purge_expired", lambda: 0)
    monkeypatch.setattr(app.enrich, "enrich_pending", lambda **kw: 0)
    monkeypatch.setattr(app.dedup, "link_duplicates", lambda: 0)
    monkeypatch.setattr(run_prod_worker, "RETRY_PAUSE", 0)

    # GeM fails, so each run tries it twice (see test_fetch_resilience).
    run_prod_worker.run_once(48.0, progress_dir=str(tmp_path))
    assert ran == ["CPPP", "GeM", "GeM"]
    run_prod_worker.run_once(48.0, progress_dir=str(tmp_path))
    assert ran[3:] == ["GeM", "GeM"], "CPPP finished today; GeM failed and is tried again"
    # Without a progress folder (CI), everything runs every time.
    run_prod_worker.run_once(48.0)
    assert ran[5:] == ["CPPP", "GeM", "GeM"]
