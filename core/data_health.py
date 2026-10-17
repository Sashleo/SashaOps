"""
core/data_health.py — Real per-scraper status for the Data Health card (Phase 2
Task 2.6). Reads scrape_log (the single source of truth every job in
core/scheduler.py writes to) joined against the job registry itself, so the card
always lists exactly the jobs that actually run - never a hand-maintained list
that drifts from reality.

Colour rules (per the plan):
  green  SUCCESS and last run within its default freshness window
  amber  PARTIAL, or fresh between 1x-3x the freshness window
  red    UNREACHABLE / PARSER_FAILED, or stale beyond 3x the window, or never run
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

import structlog

log = structlog.get_logger()

# Jobs currently running via a manual "Refresh" click, so the card can show a
# spinner state and the partial knows whether to keep polling.
_running: set[str] = set()
_lock = threading.Lock()

# For these jobs, ScrapeStatus.NO_DATA ("ran fine, nothing to store") IS the
# healthy outcome - e.g. sanity_checks finding zero implausible values, or the
# news_feeds heartbeat when the cache already holds fresh headlines. Everywhere
# else, zero rows from an actual data source is ambiguous enough to flag amber.
_NO_DATA_IS_HEALTHY = {"sanity_checks", "news_feeds"}


def is_running(job_id: str) -> bool:
    with _lock:
        return job_id in _running


def any_running() -> bool:
    with _lock:
        return bool(_running)


def _mark_running(job_id: str) -> None:
    with _lock:
        _running.add(job_id)


def _mark_done(job_id: str) -> None:
    with _lock:
        _running.discard(job_id)


def trigger_refresh(job_id: str) -> bool:
    """Run one job's fn() in a background thread. Returns False if unknown or
    already running (never double-runs the same job)."""
    from core.scheduler import build_jobs
    jobs = {j.id: j for j in build_jobs()}
    job = jobs.get(job_id)
    if not job or is_running(job_id):
        return False

    def _worker():
        _mark_running(job_id)
        try:
            job.fn()
        except Exception as e:
            log.warning("Manual refresh failed", job=job_id, error=str(e)[:200])
        finally:
            _mark_done(job_id)

    threading.Thread(target=_worker, name=f"refresh-{job_id}", daemon=True).start()
    return True


def trigger_refresh_all() -> int:
    """Run every job sequentially in one background thread.
    SQLCipher uses a single connection — parallel threads cause deadlocks."""
    from core.scheduler import build_jobs
    jobs = build_jobs()
    if any_running():
        return 0

    def _run_all():
        for job in jobs:
            _mark_running(job.id)
            try:
                job.fn()
            except Exception as e:
                log.warning("Refresh-all job failed", job=job.id, error=str(e)[:200])
            finally:
                _mark_done(job.id)

    threading.Thread(target=_run_all, name="refresh-all", daemon=True).start()
    return len(jobs)


def _human_value(job_id: str, row: Optional[dict]) -> str:
    """A friendlier value than a bare row count, for the handful of jobs where one
    cheap extra query makes the card much more readable (per the plan's own
    illustrative examples: "policy rate: 11.0%", "USD: 278.5")."""
    if not row:
        return "-"
    try:
        from database.connection import get_db
        db = get_db()
        if job_id == "sbp_monetary":
            r = db.execute(
                "SELECT value FROM sbp_indicators WHERE indicator='policy_rate' "
                "ORDER BY period DESC LIMIT 1"
            ).fetchone()
            if r and r["value"] is not None:
                return f"policy rate: {r['value']:.2f}%"
        elif job_id == "sbp_forex":
            r = db.execute(
                "SELECT rate FROM pkr_forex WHERE quote_currency='USD' "
                "ORDER BY rate_date DESC LIMIT 1"
            ).fetchone()
            if r and r["rate"] is not None:
                return f"USD/PKR: {r['rate']:.2f}"
        elif job_id == "pbs_lsmi":
            r = db.execute(
                "SELECT value FROM sbp_indicators WHERE indicator='cpi' "
                "ORDER BY period DESC LIMIT 1"
            ).fetchone()
            if r and r["value"] is not None:
                return f"CPI YoY: {r['value']:.2f}%"
    except Exception:
        pass
    n = row.get("rows_inserted")
    return f"{n} row{'s' if n != 1 else ''}" if n is not None else "-"


def compute_data_health() -> list[dict]:
    from core.scheduler import build_jobs
    from database.scrape_log import latest_all

    jobs = build_jobs()
    latest = latest_all()
    now = datetime.now(timezone.utc)
    rows = []

    for job in jobs:
        row = latest.get(job.id)
        running = is_running(job.id)

        if row is None:
            status, age_display = "red", "never run"
        else:
            started = row.get("started_at")
            try:
                dt = datetime.fromisoformat(started)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_hours = (now - dt).total_seconds() / 3600.0
            except Exception:
                age_hours = None

            db_status = row.get("status")
            if db_status in ("unreachable", "parser_failed"):
                status = "red"
            elif age_hours is not None and age_hours > job.default_freshness_hours * 3:
                status = "red"
            elif db_status == "partial" or (
                age_hours is not None
                and job.default_freshness_hours < age_hours <= job.default_freshness_hours * 3
            ):
                status = "amber"
            elif db_status == "success":
                status = "green"
            elif db_status == "no_data" and job.id in _NO_DATA_IS_HEALTHY:
                status = "green"
            else:
                status = "amber"

            if age_hours is None:
                age_display = "unknown"
            elif age_hours < 1:
                age_display = f"{int(age_hours * 60)}m ago"
            elif age_hours < 48:
                age_display = f"{age_hours:.1f}h ago"
            else:
                age_display = f"{age_hours / 24:.1f}d ago"

        rows.append({
            "id": job.id,
            "label": job.label,
            "cadence": job.cadence,
            "status": "running" if running else status,
            "last_run": age_display,
            "value": _human_value(job.id, row),
            "error": (row or {}).get("error_message"),
        })

    return rows


def get_sanity_violations() -> list[dict]:
    """
    Every value currently flagged 'implausible' by the hourly sanity sweep
    (Task 2.7), across both the macro observation pipeline (pk_macro) and PSX
    prices - a distinct amber entry per violation, with the manual-check prompt
    the plan calls for, so a bad number is impossible to miss on the Data Health
    card even though its owning job's own status line stays green (the SCRAPE
    succeeded; it's the VALUE that looks wrong).
    """
    from database.provenance import get_flagged, get_flagged_prices, SANITY_BOUNDS

    out = []
    for o in get_flagged():
        lo_hi = SANITY_BOUNDS.get(o["indicator"])
        bounds = f"expected {lo_hi[0]}-{lo_hi[1]}" if lo_hi else "outside its plausible range"
        out.append({
            "label": f"{o['indicator']} = {o['value']}",
            "message": f"{bounds}. Source: {o.get('source') or o.get('scraper_id') or '?'}, "
                      f"period {o.get('period', '?')}.",
            "prompt": f"Check {o.get('source_url') or 'the source'} by hand before trusting this value.",
        })
    for p in get_flagged_prices():
        out.append({
            "label": f"{p['symbol']} price = {p['current']}",
            "message": "expected > 0 and < 10,000,000 PKR",
            "prompt": f"Check dps.psx.com.pk/company/{p['symbol']} by hand before trusting this value.",
        })
    return out
