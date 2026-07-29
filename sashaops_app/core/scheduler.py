"""
core/scheduler.py — Central job scheduler for every data-refresh job in Phase 2
(Task 2.5). Replaces core/exchange_scheduler.py, which only covered the exchange
scrapers on a single fixed 6h cycle and had drifted from the current ScrapeResult
contract (it read result.rows_skipped / result.error_msg, neither of which exist on
ScrapeResult - rows_failed / error_message do - so every cycle's own log line raised
an AttributeError that was silently swallowed as a generic "scraper raised" warning).

Each job runs its own scraper/function on its own cadence and writes ONE scrape_log
row via log_scrape(), so the Data Health card (Task 2.6) and the live test suite
(Task 2.4) both read a single, consistent source of truth. A job failing never
blocks another: each is wrapped and logged independently.

Cadences:
  - PSX price/announcement jobs are gated to PSX market hours in Asia/Karachi
    (09:30-15:30, Mon-Fri). No exchange holiday calendar is consulted - a holiday
    run simply finds stale/unchanged data, which is a known, acceptable limitation.
  - Government/macro scrapers run at fixed times of day, matching how often each
    source actually publishes (verified per-scraper in docs/scraper_verification.md).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import structlog

log = structlog.get_logger()

PKT = ZoneInfo("Asia/Karachi")
_TICK_SECONDS = 60          # how often the scheduler checks for due jobs
_JOB_STAGGER_SECONDS = 5    # gap between jobs that become due in the same tick


def _now() -> datetime:
    return datetime.now(PKT)


def _is_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:          # Sat=5, Sun=6
        return False
    return dtime(9, 30) <= now.time() <= dtime(15, 30)


@dataclass
class Job:
    id: str
    label: str
    fn: Callable[[], object]        # zero-arg callable; return value is logged, not used for control flow
    cadence: str                    # human-readable, shown in the boot log
    due: Callable[[datetime, Optional[datetime]], bool]   # (now, last_run) -> is it due?
    default_freshness_hours: float = 24.0   # Data Health card colour thresholds (Task 2.6)
    market_hours_only: bool = False
    last_run: Optional[datetime] = field(default=None, repr=False)
    disabled_reason: Optional[str] = None


# ── Cadence helpers ──────────────────────────────────────────────────────────

def _every_minutes(n: int):
    def due(now: datetime, last: Optional[datetime]) -> bool:
        return last is None or (now - last) >= timedelta(minutes=n)
    return due


def _daily_at(hh: int, mm: int = 0):
    def due(now: datetime, last: Optional[datetime]) -> bool:
        if now.time() < dtime(hh, mm):
            return False
        if last is None:
            return True
        return last.date() < now.date()
    return due


def _weekly_at(weekday: int, hh: int, mm: int = 0):
    """weekday: Monday=0 ... Sunday=6."""
    def due(now: datetime, last: Optional[datetime]) -> bool:
        if now.weekday() != weekday or now.time() < dtime(hh, mm):
            return False
        if last is None:
            return True
        return (now.date() - last.date()).days >= 6
    return due


def _days_at(weekdays: tuple[int, ...], hh: int, mm: int = 0):
    def due(now: datetime, last: Optional[datetime]) -> bool:
        if now.weekday() not in weekdays or now.time() < dtime(hh, mm):
            return False
        if last is None:
            return True
        return last.date() < now.date()
    return due


def _monthly_at(day: int, hh: int, mm: int = 0):
    def due(now: datetime, last: Optional[datetime]) -> bool:
        if now.day != day or now.time() < dtime(hh, mm):
            return False
        if last is None:
            return True
        return last.month != now.month or last.year != now.year
    return due


def _hourly():
    return _every_minutes(60)


# ── Job wrapping: every job writes exactly one scrape_log row ───────────────

def _run_scraper(cls) -> None:
    """A BaseScraper subclass: fetch() already logs to scrape_log itself."""
    cls().fetch()


def _run_plain(job_id: str, fn: Callable[[], int]) -> None:
    """A plain function returning a row/record count. Logged manually so every
    job - scraper or not - appears uniformly in scrape_log for the Data Health
    card and the live test suite."""
    from intelligence.scraper_base import ScrapeResult, ScrapeStatus
    from database.scrape_log import log_scrape

    started = datetime.utcnow()
    try:
        rows = fn()
        result = ScrapeResult(
            scraper_id=job_id,
            status=ScrapeStatus.SUCCESS if rows else ScrapeStatus.NO_DATA,
            rows_inserted=int(rows or 0),
            started_at=started,
            finished_at=datetime.utcnow(),
            method_used="internal",
        )
    except Exception as e:
        result = ScrapeResult(
            scraper_id=job_id,
            status=ScrapeStatus.PARSER_FAILED,
            error_message=str(e)[:300],
            started_at=started,
            finished_at=datetime.utcnow(),
        )
        log.warning("Job raised", job=job_id, error=str(e)[:200])
    log_scrape(result)


def _sector_medians_job() -> int:
    from intelligence.sector_medians import compute_sector_medians
    return compute_sector_medians()


def _sanity_checks_job() -> int:
    from database.provenance import resweep_sanity
    return resweep_sanity()


def _psx_prices_job() -> int:
    from intelligence.exchange_scrapers.psx_market import PSXMarketScraper
    result = PSXMarketScraper().fetch()
    return result.rows_inserted


def _psx_company_profile_job() -> int:
    from intelligence.exchange_scrapers.psx_company import PSXCompanyProfileScraper
    result = PSXCompanyProfileScraper().fetch()
    return result.rows_inserted


def _psx_announcements_job() -> int:
    from database.connection import get_db
    from intelligence.psx_announcements import refresh_psx_announcements
    tickers = [row[0] for row in get_db().execute(
        "SELECT symbol FROM psx_companies WHERE COALESCE(is_debt,0)=0 ORDER BY symbol"
    ).fetchall()]
    total = 0
    for t in tickers:
        try:
            total += refresh_psx_announcements(t)
        except Exception:
            pass
    return total


def _psx_financials_job() -> int:
    from intelligence.psx_financials import bulk_refresh_psx_financials
    return bulk_refresh_psx_financials()


def _news_feeds_job() -> int:
    """news_feeds already runs on its own 15-min cycle via core/feed_cache.py's
    run_feed_scheduler(), started independently at boot. This entry only records a
    heartbeat in scrape_log (from the live cache) so it appears on the Data Health
    card alongside every other job, without running a second, competing fetch."""
    from core.feed_cache import cache_status
    return len(cache_status())


def _gov_scraper_job(module: str, cls_name: str) -> Callable[[], None]:
    def run() -> None:
        mod = __import__(module, fromlist=[cls_name])
        _run_scraper(getattr(mod, cls_name))
    return run


# ── Job registry ─────────────────────────────────────────────────────────────

def build_jobs() -> list[Job]:
    return [
        # PSX (market-hours-aware)
        Job("psx_prices_intraday", "PSX prices (intraday)",
           _psx_prices_job, "every 15 min, market hours",
           _every_minutes(15), default_freshness_hours=1, market_hours_only=True),
        Job("psx_prices_close", "PSX prices (close)",
           _psx_prices_job, "daily 17:00 PKT", _daily_at(17, 0),
           default_freshness_hours=27),
        Job("psx_company_profile", "PSX company profile (bulk)",
           _psx_company_profile_job, "daily 02:00 PKT", _daily_at(2, 0),
           default_freshness_hours=30),
        Job("psx_financials", "PSX financials (bulk)",
           lambda: _run_plain("psx_financials", _psx_financials_job),
           "weekly Sunday 03:00 PKT", _weekly_at(6, 3, 0),
           default_freshness_hours=192),
        Job("psx_announcements", "PSX announcements",
           lambda: _run_plain("psx_announcements", _psx_announcements_job),
           "hourly, market hours", _hourly(),
           default_freshness_hours=4, market_hours_only=True),

        # Gov / macro scrapers (verified in docs/scraper_verification.md)
        Job("sbp_monetary", "SBP monetary policy",
           _gov_scraper_job("intelligence.gov_scrapers.sbp_monetary", "SBPMonetaryScraper"),
           "daily 09:00 PKT", _daily_at(9, 0), default_freshness_hours=720),
        Job("sbp_forex", "Forex rates",
           _gov_scraper_job("intelligence.exchange_scrapers.central_banks.sbp_pakistan",
                            "SBPPakistanForexScraper"),
           "daily 08:00 PKT", _daily_at(8, 0), default_freshness_hours=24),
        Job("fbr_revenue", "FBR revenue",
           _gov_scraper_job("intelligence.gov_scrapers.fbr_revenue", "FBRRevenueScraper"),
           "weekly Monday 08:00 PKT", _weekly_at(0, 8, 0), default_freshness_hours=720),
        Job("nepra_power", "NEPRA tariff determinations",
           _gov_scraper_job("intelligence.gov_scrapers.nepra_power", "NEPRAPowerScraper"),
           "weekly Monday 08:00 PKT", _weekly_at(0, 8, 0), default_freshness_hours=168),
        Job("ogra_prices", "OGRA fuel prices",
           _gov_scraper_job("intelligence.gov_scrapers.ogra_prices", "OGRAPricesScraper"),
           "Mon+Fri 08:00 PKT", _days_at((0, 4), 8, 0), default_freshness_hours=336),
        Job("pbs_lsmi", "PBS LSM + CPI",
           _gov_scraper_job("intelligence.gov_scrapers.pbs_lsmi", "PBSLsmiScraper"),
           "monthly (day 1) 08:00 PKT", _monthly_at(1, 8, 0), default_freshness_hours=720),
        Job("pta_stats", "PTA mobile subscribers",
           _gov_scraper_job("intelligence.gov_scrapers.pta_stats", "PTAStatsScraper"),
           "monthly (day 1) 08:00 PKT", _monthly_at(1, 8, 0), default_freshness_hours=720),
        Job("worldbank_pak", "World Bank Pakistan",
           _gov_scraper_job("intelligence.gov_scrapers.worldbank_pak", "WorldBankPakScraper"),
           "monthly (day 1) 08:00 PKT", _monthly_at(1, 8, 0), default_freshness_hours=8760),

        # Derived / maintenance
        Job("sector_medians", "PSX sector medians",
           lambda: _run_plain("sector_medians", _sector_medians_job),
           "daily 05:00 PKT", _daily_at(5, 0), default_freshness_hours=30),
        Job("news_feeds", "News feed cache (heartbeat)",
           lambda: _run_plain("news_feeds", _news_feeds_job),
           "every 30 min (fetch runs separately)", _every_minutes(30),
           default_freshness_hours=1),
        Job("sanity_checks", "Macro observation sanity sweep",
           lambda: _run_plain("sanity_checks", _sanity_checks_job),
           "hourly", _hourly(), default_freshness_hours=2),
    ]


# ── Runner ───────────────────────────────────────────────────────────────────

async def run_scheduler() -> None:
    """
    Background task: tick every _TICK_SECONDS, run whichever jobs are due. Started
    once via asyncio.create_task() at app boot. Never blocks - each job runs in the
    default executor so a slow scraper (OCR, a 400-page annual report) cannot stall
    the tick loop or any other job.
    """
    jobs = build_jobs()
    _log_boot_plan(jobs)

    loop = asyncio.get_running_loop()
    while True:
        now = _now()
        for job in jobs:
            if job.disabled_reason:
                continue
            if job.market_hours_only and not _is_market_hours(now):
                continue
            if not job.due(now, job.last_run):
                continue
            job.last_run = now
            log.info("Job starting", job=job.id)
            try:
                await loop.run_in_executor(None, job.fn)
            except Exception as e:
                log.warning("Job raised outside its own handler", job=job.id, error=str(e)[:200])
            await asyncio.sleep(_JOB_STAGGER_SECONDS)
        await asyncio.sleep(_TICK_SECONDS)


def _log_boot_plan(jobs: list[Job]) -> None:
    """Per the plan: log which jobs are enabled, their cadence, and flag any
    DISABLED scraper with its reason, at boot."""
    log.info("Scheduler starting", total_jobs=len(jobs), timezone="Asia/Karachi")
    for job in jobs:
        if job.disabled_reason:
            log.warning("Job DISABLED", job=job.id, reason=job.disabled_reason)
        else:
            log.info("Job enabled", job=job.id, label=job.label, cadence=job.cadence,
                     market_hours_only=job.market_hours_only)
