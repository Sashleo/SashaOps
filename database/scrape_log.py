"""
database/scrape_log.py - Write and read the scrape_log table.

One row per BaseScraper.fetch(). This is the measurement surface for Phase 2:
the Data Health card and the live regression suite both read it.
"""

from __future__ import annotations

import json
from typing import Optional

import structlog

log = structlog.get_logger()


def log_scrape(result) -> None:
    """Persist a ScrapeResult. Never raises (a logging failure must not fail a scrape)."""
    try:
        from database.connection import get_db
        db = get_db()
        status = getattr(result.status, "value", result.status)
        db.execute(
            """INSERT INTO scrape_log
               (scraper_id, status, rows_inserted, rows_updated, rows_failed,
                started_at, finished_at, duration_ms, error_message,
                source_url, method_used, artifacts_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.scraper_id,
                str(status),
                int(result.rows_inserted or 0),
                int(result.rows_updated or 0),
                int(result.rows_failed or 0),
                result.started_at.isoformat() if result.started_at else None,
                result.finished_at.isoformat() if result.finished_at else None,
                result.duration_ms,
                (result.error_message or None),
                result.source_url,
                result.method_used,
                json.dumps(result.artifacts or {})[:4000],
            ),
        )
        db.commit()
    except Exception as e:
        log.debug("scrape_log write failed", scraper=getattr(result, "scraper_id", "?"),
                  error=str(e)[:120])


def latest_for(scraper_id: str) -> Optional[dict]:
    """Most recent scrape_log row for a scraper, or None."""
    try:
        from database.connection import get_db
        row = get_db().execute(
            "SELECT * FROM scrape_log WHERE scraper_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (scraper_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def latest_all() -> dict:
    """Most recent scrape_log row per scraper_id, keyed by scraper_id."""
    try:
        from database.connection import get_db
        rows = get_db().execute(
            """SELECT s.* FROM scrape_log s
               JOIN (SELECT scraper_id, MAX(started_at) AS mx
                     FROM scrape_log GROUP BY scraper_id) t
                 ON s.scraper_id = t.scraper_id AND s.started_at = t.mx"""
        ).fetchall()
        return {r["scraper_id"]: dict(r) for r in rows}
    except Exception:
        return {}


def success_rate(scraper_id: str, last_n: int = 20) -> float:
    """Share of the last N runs that were SUCCESS. 0.0 when there is no history."""
    try:
        from database.connection import get_db
        rows = get_db().execute(
            "SELECT status FROM scrape_log WHERE scraper_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (scraper_id, last_n),
        ).fetchall()
        if not rows:
            return 0.0
        ok = sum(1 for r in rows if r["status"] == "success")
        return round(ok / len(rows), 3)
    except Exception:
        return 0.0
