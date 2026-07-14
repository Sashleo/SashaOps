"""
database/provenance.py - The verifiable macro-data pipeline.

Every scraped macro observation is written through record_observation(), which
REQUIRES provenance alongside the value:

    value           the parsed number
    source_url      the exact page/endpoint it came from
    source_snippet  the raw as-printed text the number was parsed from
    method_used     html_bs4 | json_api | playwright | pdf | ocr
    scraper_id      which scraper produced it
    scraped_at      when

Why this exists: source websites change shape without warning. Phase 2 verification
alone caught three such traps on PSX (OHLC hidden in tabbed panels where the
non-REG panels are all zeros; "Market Cap (000's)" printed in thousands; "Free
Float" printed twice). A number with no provenance cannot be audited when that
happens. With provenance, any discrepancy can be traced to the exact page and the
exact text it was read from, and corrected.

Read side: get_observation() / get_observations() return the value together with
its source, so a page can render "11.0% (SBP, as of 2026-06-16)" with a link back
to the source, and a human can confirm it against the live site.

Implausible values are FLAGGED (sanity_flag='implausible'), never deleted - the
audit trail is the point.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List

import structlog

log = structlog.get_logger()

# Plausibility bounds (Task 2.7). A value outside its band is stored but flagged.
SANITY_BOUNDS = {
    "sbp_policy_rate": (5.0, 30.0),
    "fx_usd_pkr":      (100.0, 500.0),
    "fx_eur_pkr":      (100.0, 550.0),
    "fx_gbp_pkr":      (100.0, 600.0),
    "fx_aed_pkr":      (20.0, 200.0),
    "fx_sar_pkr":      (20.0, 200.0),
    "cpi_yoy":         (-5.0, 40.0),
    "gdp_growth":      (-10.0, 15.0),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_sanity(indicator: str, value: Optional[float]) -> Optional[str]:
    """Return 'implausible' when the value falls outside its documented band."""
    if value is None:
        return None
    lo_hi = SANITY_BOUNDS.get(indicator)
    if not lo_hi:
        return None
    lo, hi = lo_hi
    return None if lo <= float(value) <= hi else "implausible"


def record_observation(
    *,
    indicator: str,
    value: Optional[float],
    period: str,
    source: str,
    source_url: str,
    source_snippet: str,
    method_used: str,
    scraper_id: str,
    unit: str = "",
    detail: str = "",
) -> bool:
    """
    Write one macro observation WITH its provenance. Returns True when stored.

    source_url and source_snippet are mandatory: without them the value cannot be
    verified later. Callers must pass the raw text the number was parsed from.
    """
    if value is None:
        return False
    if not source_url or not source_snippet:
        log.warning("Refusing to store an unverifiable observation "
                    "(missing source_url/source_snippet)",
                    indicator=indicator, scraper=scraper_id)
        return False

    flag = check_sanity(indicator, value)
    if flag:
        log.warning("Implausible value stored and flagged", indicator=indicator,
                    value=value, bounds=SANITY_BOUNDS.get(indicator), scraper=scraper_id)

    try:
        from database.connection import get_db
        db = get_db()
        db.execute(
            """INSERT INTO pk_macro
                 (source, indicator, period, value, unit, detail, fetched_at,
                  source_url, source_snippet, method_used, scraper_id, sanity_flag)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source, indicator, period) DO UPDATE SET
                 value          = excluded.value,
                 unit           = excluded.unit,
                 detail         = excluded.detail,
                 fetched_at     = excluded.fetched_at,
                 source_url     = excluded.source_url,
                 source_snippet = excluded.source_snippet,
                 method_used    = excluded.method_used,
                 scraper_id     = excluded.scraper_id,
                 sanity_flag    = excluded.sanity_flag""",
            (source, indicator, period, float(value), unit, detail, _now(),
             source_url, source_snippet[:500], method_used, scraper_id, flag),
        )
        db.commit()
        return True
    except Exception as e:
        log.warning("record_observation failed", indicator=indicator, error=str(e)[:150])
        return False


# ── Read side: what the tool consumes ─────────────────────────────────────────

def get_observation(indicator: str) -> Optional[dict]:
    """Latest value for an indicator, WITH provenance so it can be shown + verified."""
    try:
        from database.connection import get_db
        row = get_db().execute(
            """SELECT indicator, value, unit, period, detail, fetched_at,
                      source, source_url, source_snippet, method_used,
                      scraper_id, sanity_flag
               FROM pk_macro WHERE indicator = ?
               ORDER BY fetched_at DESC LIMIT 1""",
            (indicator,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_observations(indicators: Optional[List[str]] = None) -> List[dict]:
    """Latest value per indicator, with provenance. Powers the verifiable data view."""
    try:
        from database.connection import get_db
        sql = """SELECT m.indicator, m.value, m.unit, m.period, m.fetched_at,
                        m.source, m.source_url, m.source_snippet, m.method_used,
                        m.scraper_id, m.sanity_flag
                 FROM pk_macro m
                 JOIN (SELECT indicator, MAX(fetched_at) AS mx
                       FROM pk_macro GROUP BY indicator) t
                   ON m.indicator = t.indicator AND m.fetched_at = t.mx"""
        rows = get_db().execute(sql).fetchall()
        out = [dict(r) for r in rows]
        if indicators:
            out = [r for r in out if r["indicator"] in indicators]
        return sorted(out, key=lambda r: r["indicator"])
    except Exception:
        return []


def get_flagged() -> List[dict]:
    """Observations currently flagged implausible (surfaced amber on Data Health)."""
    return [o for o in get_observations() if o.get("sanity_flag")]


def resweep_sanity() -> int:
    """Re-apply the bounds to everything stored (the hourly sanity job). Returns
    the number of rows whose flag changed. Never deletes."""
    changed = 0
    try:
        from database.connection import get_db
        db = get_db()
        rows = db.execute("SELECT id, indicator, value, sanity_flag FROM pk_macro").fetchall()
        for r in rows:
            flag = check_sanity(r["indicator"], r["value"])
            if flag != r["sanity_flag"]:
                db.execute("UPDATE pk_macro SET sanity_flag = ? WHERE id = ?", (flag, r["id"]))
                changed += 1
                if flag:
                    log.warning("Sanity check flagged a stored value",
                                indicator=r["indicator"], value=r["value"])
        db.commit()
    except Exception as e:
        log.warning("resweep_sanity failed", error=str(e)[:150])
    return changed
