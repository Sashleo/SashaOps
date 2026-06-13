"""
intelligence/psx_company_profile.py — PSX company profile lookup.

Resolves a listed company's official website URL, PSX sector, and latest announcement
straight from PSX — so the browser agent can scrape the real site without a DuckDuckGo
search step, and the keyword engine gets an authoritative sector name.
"""

from __future__ import annotations

import re
from typing import Optional

import structlog

from intelligence.exchange_crawlers.psx import fetch_psx_company, search_psx_company
from database.psx_data import upsert_psx_company, set_company_website

log = structlog.get_logger()

_URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)


def get_psx_company_profile(ticker: str) -> dict:
    """
    Returns {ticker, company_name, website_url, sector, latest_announcement}.
    Never raises — returns best-effort dict (fields may be None).
    """
    ticker = (ticker or "").strip().upper()
    profile = {
        "ticker": ticker,
        "company_name": None,
        "website_url": None,
        "sector": None,
        "latest_announcement": None,
    }
    if not ticker:
        return profile

    # Sector + name from the symbol registry (cheap JSON endpoint)
    try:
        for s in search_psx_company(ticker):
            if (s.get("symbol") or "").upper() == ticker:
                profile["company_name"] = s.get("name")
                profile["sector"] = s.get("sectorName")
                break
    except Exception as e:
        log.info("PSX symbol lookup failed", ticker=ticker, error=str(e))

    # Website + latest announcement from the company page
    try:
        data = fetch_psx_company(ticker, fetch_types=["board", "announcements"])
        if data and "error" not in data:
            profile["website_url"] = _extract_website(data)
            anns = data.get("financial_announcements") or []
            if anns:
                a = anns[0]
                profile["latest_announcement"] = f"{a.get('date','')}: {a.get('title','')}".strip(": ")
    except Exception as e:
        log.info("PSX company page fetch failed", ticker=ticker, error=str(e))

    # Persist what we learned
    try:
        if profile["company_name"]:
            upsert_psx_company(ticker, profile["company_name"],
                               sector_name=profile["sector"] or "",
                               website=profile["website_url"])
        elif profile["website_url"]:
            set_company_website(ticker, profile["website_url"])
    except Exception:
        pass

    return profile


def _extract_website(data: dict) -> Optional[str]:
    """Find a website URL in the PSX board/profile table."""
    board = data.get("board") or []
    for row in board:
        for v in row.values():
            if isinstance(v, str):
                m = _URL_RE.search(v)
                if m and "psx.com.pk" not in m.group(0):
                    return m.group(0).rstrip(".,)")
    return None
