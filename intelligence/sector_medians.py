"""
intelligence/sector_medians.py — PSX sector-median benchmarks (M9).

Computes median gross_margin, net_margin, roe, current_ratio, debt_to_equity and P/E
for each PSX sector from ALL companies whose values pass plausibility validation
(intelligence/validation.py), and stores them in psx_sector_medians. verdicts.py and
the bank-valuation section (M8) read from this table. Sectors with < 4 valid companies
are reported as "insufficient peers" rather than compared.

Pure local DB work — no AI, no network. Runs daily via core/scheduler.py.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Optional

import structlog

log = structlog.get_logger()

_MIN_PEERS = 4
_METRICS = [
    "gross_margin", "net_margin", "roe", "roa",
    "current_ratio", "debt_to_equity",
    "asset_turnover", "equity_multiplier",
    "pe",
]


def _div(a, b):
    try:
        if a is None or not b:
            return None
        return float(a) / float(b)
    except Exception:
        return None


def _company_metrics(f, price) -> dict:
    """Compute comparable ratios for one company row from psx_financials + price."""
    eps = f["eps"]
    ta = f["total_assets"]
    return {
        "gross_margin":     _div(f["gross_profit"], f["revenue"]),
        "net_margin":       _div(f["net_income"], f["revenue"]),
        "roe":              _div(f["net_income"], f["total_equity"]),
        "roa":              _div(f["net_income"], ta),
        "current_ratio":    _div(f["current_assets"], f["current_liabilities"]),
        "debt_to_equity":   _div(f["total_debt"], f["total_equity"]),
        "asset_turnover":   _div(f["revenue"], ta),
        "equity_multiplier": _div(ta, f["total_equity"]),
        "pe":               (price / eps) if (price and eps and eps > 0) else None,
    }


def compute_sector_medians() -> int:
    """Recompute and persist sector medians. Returns rows written."""
    from database.connection import get_db
    from intelligence.validation import validate_metric
    from config.sectors import is_bank

    db = get_db()
    sectors = [r["sector_name"] for r in db.execute(
        "SELECT DISTINCT sector_name FROM psx_companies WHERE sector_name IS NOT NULL"
    ).fetchall()]

    today = date.today().isoformat()
    written = 0
    for sector in sectors:
        is_bank_sector = is_bank(sector)
        # collect each metric's validated values across the sector
        buckets: dict[str, list] = {m: [] for m in _METRICS}
        rows = db.execute(
            "SELECT symbol FROM psx_companies WHERE sector_name=? AND COALESCE(is_debt,0)=0", (sector,)
        ).fetchall()
        for r in rows:
            sym = r["symbol"]
            f = db.execute(
                "SELECT revenue, gross_profit, net_income, total_equity, total_assets, total_debt, "
                "current_assets, current_liabilities, eps FROM psx_financials "
                "WHERE symbol=? AND period_type='annual' ORDER BY period DESC LIMIT 1", (sym,)
            ).fetchone()
            if not f:
                continue
            pr = db.execute(
                "SELECT current FROM psx_prices WHERE symbol=? ORDER BY trade_date DESC LIMIT 1", (sym,)
            ).fetchone()
            price = pr["current"] if pr else None
            m = _company_metrics(f, price)
            for name, val in m.items():
                vr = validate_metric(name, val, is_bank_sector)
                if vr["status"] == "ok" and vr["value"] is not None:
                    buckets[name].append(vr["value"])

        for name, vals in buckets.items():
            if len(vals) >= 1:
                med = float(statistics.median(vals))
                db.execute(
                    """INSERT INTO psx_sector_medians (sector, metric, median, company_count, computed_date)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(sector, metric) DO UPDATE SET
                         median=excluded.median, company_count=excluded.company_count,
                         computed_date=excluded.computed_date""",
                    (sector, name, med, len(vals), today),
                )
                written += 1
    db.commit()
    log.info("Sector medians computed", rows=written, sectors=len(sectors))
    return written


def get_sector_median(sector: str, metric: str) -> Optional[dict]:
    """
    Return {"median", "company_count"} for a sector metric, or None if the sector
    has fewer than the minimum peer count (caller states 'insufficient peers').
    `sector` may be a keyword-engine key ('banking') or a raw PSX sector name.
    """
    from database.connection import get_db
    from config.sectors import resolve_sector, psx_sector_name
    db = get_db()

    candidates = [sector]
    key = resolve_sector(sector)
    psxn = psx_sector_name(key) if key else None
    if psxn:
        candidates.append(psxn)

    for cand in candidates:
        row = db.execute(
            "SELECT median, company_count FROM psx_sector_medians WHERE UPPER(sector)=? AND metric=?",
            ((cand or "").upper(), metric),
        ).fetchone()
        if row and row["company_count"] >= _MIN_PEERS:
            return {"median": row["median"], "company_count": row["company_count"]}
    return None
