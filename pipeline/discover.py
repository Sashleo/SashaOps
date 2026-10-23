"""pipeline/discover.py — S1 Discover: find annual-report announcement URLs."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import structlog
from intelligence.exchange_crawlers.psx import fetch_psx_company

log = structlog.get_logger()

# Title patterns sorted by reliability: annual_report > annual_results > interim.
_ANNUAL_REPORT_TITLES = [
    r"transmission of annual report",
    r"annual report for the year ended",
    r"annual audited\s+(?:financial\s+)?statements",
    r"annual report\s+\d{4}",
]
_ANNUAL_RESULTS_TITLES = [
    r"announcement of financial results for the year ended",
    r"audited financial results for the year",
    r"financial results for the year ended",
    r"annual (?:financial\s+)?results",
]
_INTERIM_TITLES = [
    r"quarterly report",
    r"half.?year(?:ly)?\s+report",
    r"interim\s+(?:financial\s+)?results",
    r"period ended (?:march|june|september)\s*\d",
]


def _ann_type(title: str) -> Optional[str]:
    t = (title or "").lower()
    for p in _ANNUAL_REPORT_TITLES:
        if re.search(p, t):
            return "annual_report"
    for p in _ANNUAL_RESULTS_TITLES:
        if re.search(p, t):
            return "annual_results"
    for p in _INTERIM_TITLES:
        if re.search(p, t):
            return "interim"
    return None


def _date_key(raw: str) -> date:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return date.min


def discover_one(ticker: str) -> dict:
    """
    Find annual-report announcement URLs for one ticker.

    Returns {
        "ticker":     str,
        "outcome":    "found" | "no_report_announcements" | "fetch_failed:<reason>",
        "candidates": [{"ticker", "url", "title", "ann_date", "ann_type"}, ...],
    }
    Candidates are sorted newest-first.
    """
    ticker = (ticker or "").strip().upper()
    try:
        data = fetch_psx_company(ticker, fetch_types=["announcements"])
    except Exception as e:
        reason = str(e)[:80]
        log.warning("S1 Discover fetch_failed", ticker=ticker, error=reason)
        return {"ticker": ticker, "outcome": f"fetch_failed:{reason}", "candidates": []}

    anns = [a for a in ((data or {}).get("financial_announcements") or [])
            if a.get("pdf_url")]

    candidates = []
    for a in anns:
        atype = _ann_type(a.get("title", ""))
        if atype:
            candidates.append({
                "ticker":   ticker,
                "url":      a["pdf_url"],
                "title":    (a.get("title") or "")[:200],
                "ann_date": a.get("date", ""),
                "ann_type": atype,
            })

    candidates.sort(key=lambda c: _date_key(c["ann_date"]), reverse=True)

    if not candidates:
        log.info("S1 Discover no_report_announcements", ticker=ticker,
                 total_anns=len(anns))
        return {"ticker": ticker, "outcome": "no_report_announcements", "candidates": []}

    log.info("S1 Discover found", ticker=ticker, candidates=len(candidates),
             types=sorted({c["ann_type"] for c in candidates}))
    return {"ticker": ticker, "outcome": "found", "candidates": candidates}
