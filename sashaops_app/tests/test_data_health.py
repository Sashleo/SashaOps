"""
Regression tests for core/data_health.py's status classification (Task 2.6).

Locks in the plan's colour rules and the NO_DATA special case: a job finding
zero anomalies/nothing-new (sanity_checks, news_feeds) is the HEALTHY outcome and
must render green, not amber - a real nuance found while building this card
(sanity_checks initially rendered amber for correctly finding 0 implausible values).
"""

from datetime import datetime, timedelta, timezone

from core.data_health import _NO_DATA_IS_HEALTHY, trigger_refresh, compute_data_health
from core.scheduler import Job, _every_minutes


def test_no_data_is_healthy_set_matches_maintenance_jobs():
    assert _NO_DATA_IS_HEALTHY == {"sanity_checks", "news_feeds"}


def test_unknown_job_id_refresh_is_rejected():
    assert trigger_refresh("not_a_real_job_id") is False


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _fake_job(job_id: str, freshness: float = 24.0) -> Job:
    return Job(job_id, job_id, lambda: 0, "test", _every_minutes(1),
              default_freshness_hours=freshness)


def test_never_run_job_is_red(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("x")])
    monkeypatch.setattr("database.scrape_log.latest_all", lambda: {})
    row = compute_data_health()[0]
    assert row["status"] == "red"
    assert row["last_run"] == "never run"


def test_fresh_success_is_green(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("x", freshness=24)])
    monkeypatch.setattr("database.scrape_log.latest_all",
                        lambda: {"x": {"status": "success", "started_at": _iso(1),
                                      "rows_inserted": 5, "error_message": None}})
    row = compute_data_health()[0]
    assert row["status"] == "green"


def test_stale_beyond_3x_freshness_is_red(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("x", freshness=24)])
    monkeypatch.setattr("database.scrape_log.latest_all",
                        lambda: {"x": {"status": "success", "started_at": _iso(80),
                                      "rows_inserted": 5, "error_message": None}})
    row = compute_data_health()[0]
    assert row["status"] == "red"


def test_stale_between_1x_and_3x_is_amber(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("x", freshness=24)])
    monkeypatch.setattr("database.scrape_log.latest_all",
                        lambda: {"x": {"status": "success", "started_at": _iso(40),
                                      "rows_inserted": 5, "error_message": None}})
    row = compute_data_health()[0]
    assert row["status"] == "amber"


def test_unreachable_is_red_regardless_of_age(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("x", freshness=24)])
    monkeypatch.setattr("database.scrape_log.latest_all",
                        lambda: {"x": {"status": "unreachable", "started_at": _iso(0.1),
                                      "rows_inserted": 0, "error_message": "timeout"}})
    row = compute_data_health()[0]
    assert row["status"] == "red"


def test_no_data_is_green_for_maintenance_jobs(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("sanity_checks", freshness=2)])
    monkeypatch.setattr("database.scrape_log.latest_all",
                        lambda: {"sanity_checks": {"status": "no_data", "started_at": _iso(0.5),
                                                   "rows_inserted": 0, "error_message": None}})
    row = compute_data_health()[0]
    assert row["status"] == "green"


def test_no_data_is_amber_for_ordinary_jobs(monkeypatch):
    monkeypatch.setattr("core.scheduler.build_jobs", lambda: [_fake_job("x", freshness=24)])
    monkeypatch.setattr("database.scrape_log.latest_all",
                        lambda: {"x": {"status": "no_data", "started_at": _iso(0.5),
                                      "rows_inserted": 0, "error_message": None}})
    row = compute_data_health()[0]
    assert row["status"] == "amber"
