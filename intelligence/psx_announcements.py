"""
intelligence/psx_announcements.py — Persist PSX announcements (financial results,
board meetings, dividend notices) into psx_announcements. Feeds the "psx_announcements
hourly during market hours" job (Phase 2 Task 2.5); previously fetch_psx_company()'s
announcement tables were only ever used transiently in-memory, never stored.
"""

from __future__ import annotations

import structlog

from intelligence.exchange_crawlers.psx import fetch_psx_company
from database.psx_data import upsert_psx_announcement

log = structlog.get_logger()

_TYPES = {
    "financial_announcements": "financial_results",
    "board_meetings": "board_meeting",
    "dividend_notices": "dividend",
}


def refresh_psx_announcements(ticker: str) -> int:
    """Fetch and persist a ticker's announcement tables. Returns rows inserted."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return 0
    try:
        data = fetch_psx_company(ticker, fetch_types=list(_TYPES.keys()))
    except Exception as e:
        log.info("Announcement fetch failed", ticker=ticker, error=str(e)[:100])
        return 0
    if not data or "error" in data:
        return 0

    inserted = 0
    for key, ann_type in _TYPES.items():
        for item in data.get(key) or []:
            ok = upsert_psx_announcement(
                ticker, ann_type,
                item.get("date"), item.get("title"), item.get("pdf_url"),
            )
            if ok:
                inserted += 1
    return inserted
