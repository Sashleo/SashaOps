"""pipeline/discover.py — S1 Discover: find annual-report announcement URLs.

Known limit: PSX's /announcements page is JavaScript-rendered and yields no
static HTML tables.  Historical announcements beyond the company page's ~15
most recent entries are inaccessible without a browser/JS runtime.  The
previous year's annual report is returned only when it falls inside that window.
"""
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

# Extracts "Month D, YYYY" or "D Month YYYY" from titles like
# "Transmission of Annual Report for the Year Ended June 30, 2025".
_PERIOD_END_RE = re.compile(
    r"(?:year|period|quarter)\s+end(?:ed)?\s+"
    r"(\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE,
)
_PERIOD_END_FMTS = (
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%d %B %Y",  "%d %b %Y",
)


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


def _period_end_from_title(title: str) -> Optional[str]:
    """Extract ISO period-end date from an announcement title, or None."""
    m = _PERIOD_END_RE.search(title or "")
    if not m:
        return None
    raw = re.sub(r"\s+", " ", m.group(1)).strip().rstrip(",")
    for fmt in _PERIOD_END_FMTS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _validate_ann_structure(anns: list) -> str | None:
    """
    Check that the list returned for financial_announcements actually looks like
    an announcements table and not some other table the scraper may have picked up
    if PSX rearranges its page.

    Returns None if the structure looks correct, or "source_changed: <reason>"
    if it doesn't.  An empty list is fine — the company may have no announcements.
    """
    if not anns:
        return None

    sample = [a for a in anns[:5] if isinstance(a, dict)]
    if not sample:
        return f"source_changed: expected list of dicts, got {type(anns[0]).__name__}"

    expected = {"date", "title", "pdf_url"}
    for a in sample[:3]:
        missing = expected - set(a.keys())
        if missing:
            return f"source_changed: missing keys {sorted(missing)}"

    # Majority of dates must parse.  If they don't, a different table is being read.
    bad = [a.get("date", "") for a in sample
           if a.get("date") and _date_key(a["date"]) == date.min]
    if len(bad) > len(sample) // 2:
        return f"source_changed: dates do not parse: {bad[:2]}"

    return None


def discover_one(ticker: str) -> dict:
    """
    Find annual-report announcement URLs for one ticker.

    Returns {
        "ticker":     str,
        "outcome":    "found" | "no_report_announcements" |
                      "fetch_failed:<reason>" | "source_changed:<reason>",
        "candidates": [{"ticker", "url", "title", "ann_date", "period_end",
                         "ann_type"}, ...],
    }
    Candidates are sorted newest-first.
    Picks up to 2 annual_report entries when both are visible on the company page;
    falls back to annual_results or interim only when no annual_report is found.
    """
    ticker = (ticker or "").strip().upper()
    try:
        data = fetch_psx_company(ticker, fetch_types=["announcements"])
    except Exception as e:
        reason = str(e)[:80]
        log.warning("S1 Discover fetch_failed", ticker=ticker, error=reason)
        return {"ticker": ticker, "outcome": f"fetch_failed:{reason}", "candidates": []}

    all_anns = (data or {}).get("financial_announcements") or []
    err = _validate_ann_structure(all_anns)
    if err:
        log.warning("S1 Discover source_changed", ticker=ticker, reason=err)
        return {"ticker": ticker, "outcome": err, "candidates": []}

    anns = [a for a in all_anns if a.get("pdf_url")]

    candidates = []
    for a in anns:
        atype = _ann_type(a.get("title", ""))
        if atype:
            candidates.append({
                "ticker":     ticker,
                "url":        a["pdf_url"],
                "title":      (a.get("title") or "")[:200],
                "ann_date":   a.get("date", ""),
                "period_end": _period_end_from_title(a.get("title", "")),
                "ann_type":   atype,
            })

    candidates.sort(key=lambda c: _date_key(c["ann_date"]), reverse=True)

    # Prefer annual_report.  When at least one is visible, don't fall back to
    # annual_results or interim — the annual report is the authoritative source.
    ar = [c for c in candidates if c["ann_type"] == "annual_report"]
    if ar:
        chosen = ar[:2]   # latest + one-prior when both visible
    else:
        non_ar = [c for c in candidates
                  if c["ann_type"] in ("annual_results", "interim")]
        chosen = non_ar[:1]

    if not chosen:
        log.info("S1 Discover no_report_announcements", ticker=ticker,
                 total_anns=len(anns))
        return {"ticker": ticker, "outcome": "no_report_announcements", "candidates": []}

    log.info("S1 Discover found", ticker=ticker, chosen=len(chosen),
             types=sorted({c["ann_type"] for c in chosen}))
    return {"ticker": ticker, "outcome": "found", "candidates": chosen}
