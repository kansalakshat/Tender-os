from datetime import datetime, timezone

from neon_usage import GB, check

NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)          # halfway through


def project(transfer, storage=0.1 * GB, compute=10 * 3600):
    return {"consumption_period_start": "2026-09-01T00:00:00Z",
            "consumption_period_end": "2026-10-01T00:00:00Z",
            "data_transfer_bytes": transfer, "synthetic_storage_size": storage,
            "compute_time_seconds": compute}


def test_a_quiet_month_passes():
    assert check(project(1 * GB), NOW) == []


def test_a_pace_that_would_run_out_warns_before_it_does():
    """2.4 GB by mid-month is under half used, and on course for 4.8 of 5."""
    assert check(project(2.4 * GB), NOW)


def test_mostly_spent_warns_and_so_does_nearly_full_storage():
    assert check(project(3.1 * GB), datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert check(project(0.1 * GB, storage=0.4 * GB), NOW)
