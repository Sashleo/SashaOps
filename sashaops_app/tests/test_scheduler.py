"""
Regression tests for core/scheduler.py's cadence logic (Task 2.5).

The previous scheduler (core/exchange_scheduler.py) had drifted from the
ScrapeResult contract - it read result.rows_skipped / result.error_msg, neither of
which exist (rows_failed / error_message do) - so every logged cycle silently
raised an AttributeError, swallowed as a generic warning. These tests exercise the
pure cadence functions directly (no network, no real scrapers) so a similar
drift is caught immediately rather than only visible in production logs.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from core.scheduler import (
    _every_minutes, _daily_at, _weekly_at, _days_at, _monthly_at, _hourly,
    _is_market_hours, build_jobs,
)

PKT = ZoneInfo("Asia/Karachi")


def _dt(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=PKT)


def test_every_minutes_due_when_never_run():
    due = _every_minutes(15)
    assert due(_dt(2026, 7, 29, 10, 0), None) is True


def test_every_minutes_not_due_before_interval():
    due = _every_minutes(15)
    last = _dt(2026, 7, 29, 10, 0)
    assert due(_dt(2026, 7, 29, 10, 10), last) is False
    assert due(_dt(2026, 7, 29, 10, 16), last) is True


def test_daily_at_respects_time_of_day():
    due = _daily_at(9, 0)
    assert due(_dt(2026, 7, 29, 8, 59), None) is False
    assert due(_dt(2026, 7, 29, 9, 0), None) is True


def test_daily_at_only_once_per_day():
    due = _daily_at(9, 0)
    last = _dt(2026, 7, 29, 9, 5)   # already ran today
    assert due(_dt(2026, 7, 29, 14, 0), last) is False
    assert due(_dt(2026, 7, 30, 9, 0), last) is True   # next day


def test_weekly_at_only_fires_on_its_weekday():
    # Monday=0 ... Sunday=6
    due = _weekly_at(6, 3, 0)   # Sunday 03:00 (psx_financials)
    assert due(_dt(2026, 7, 29, 3, 0), None) is False    # Wed 2026-07-29
    assert due(_dt(2026, 8, 2, 3, 0), None) is True      # Sun 2026-08-02


def test_days_at_fires_on_either_specified_weekday():
    due = _days_at((0, 4), 8, 0)   # Mon + Fri, 08:00 (ogra_prices)
    assert due(_dt(2026, 7, 27, 8, 0), None) is True     # Mon
    assert due(_dt(2026, 7, 31, 8, 0), None) is True     # Fri
    assert due(_dt(2026, 7, 29, 8, 0), None) is False    # Wed


def test_monthly_at_fires_only_on_its_day():
    due = _monthly_at(1, 8, 0)
    assert due(_dt(2026, 7, 29, 8, 0), None) is False
    assert due(_dt(2026, 8, 1, 8, 0), None) is True


def test_monthly_at_only_once_per_month():
    due = _monthly_at(1, 8, 0)
    last = _dt(2026, 8, 1, 8, 5)
    assert due(_dt(2026, 8, 1, 20, 0), last) is False
    assert due(_dt(2026, 9, 1, 8, 0), last) is True


def test_hourly_is_every_60_minutes():
    due = _hourly()
    last = _dt(2026, 7, 29, 10, 0)
    assert due(_dt(2026, 7, 29, 10, 59), last) is False
    assert due(_dt(2026, 7, 29, 11, 0), last) is True


def test_market_hours_excludes_weekends():
    assert _is_market_hours(_dt(2026, 8, 1, 12, 0)) is False    # Saturday
    assert _is_market_hours(_dt(2026, 8, 2, 12, 0)) is False    # Sunday


def test_market_hours_window_on_a_weekday():
    assert _is_market_hours(_dt(2026, 7, 29, 9, 0)) is False    # before open
    assert _is_market_hours(_dt(2026, 7, 29, 10, 0)) is True    # during
    assert _is_market_hours(_dt(2026, 7, 29, 16, 0)) is False   # after close


def test_build_jobs_covers_every_plan_job():
    jobs = {j.id for j in build_jobs()}
    expected = {
        "psx_prices_intraday", "psx_prices_close", "psx_company_profile",
        "psx_financials", "psx_announcements", "sbp_monetary", "sbp_forex",
        "fbr_revenue", "nepra_power", "ogra_prices", "pbs_lsmi", "pta_stats",
        "worldbank_pak", "sector_medians", "news_feeds", "sanity_checks",
    }
    assert jobs == expected


def test_no_job_is_silently_disabled():
    for job in build_jobs():
        assert job.disabled_reason is None, f"{job.id} disabled: {job.disabled_reason}"
