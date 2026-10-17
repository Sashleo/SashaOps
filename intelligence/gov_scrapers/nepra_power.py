"""
gov_scrapers/nepra_power.py — NEPRA tariff determinations.

Verified 2026-07-28 (docs/scraper_verification.md Priority 8): the previous version
targeted `www.nepra.org.pk`, which is 301-redirected and had been reported 403 in an
earlier check. The bare domain `nepra.org.pk` (no www) resolves cleanly (200) and its
homepage lists recent tariff determinations in a table, newest first, each row
carrying a date and a link to the determination PDF (e.g. "TRF-623 Decision Aerem
Energy ... 17-07-2026 ...pdf"). The plan's Priority 8 target is exactly this: the
latest tariff determination's date and reference — not a single numeric rate, which
NEPRA does not publish on the homepage (rates vary by DISCO/consumer class and are
set out inside each determination PDF, not summarised as one figure).
"""

from __future__ import annotations

import re
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_nepra

_HOME = "https://nepra.org.pk/"
_DATE_RE = re.compile(r"\b(\d{2}-\d{2}-\d{4})\b")
_REF_RE = re.compile(r"(TRF|LAD|LAT|NCM|GCA|PAR)-[\w\-]+", re.I)


class NEPRAPowerScraper(BasePakGovScraper):
    scraper_id = "nepra_power"
    label = "NEPRA tariff determinations"
    category = "regulator"
    default_freshness_hours = 168   # determinations post several times a week
    # Verified 2026-07-28: latest determination row matched exactly (TRF-623, Aerem
    # Energy, 17-07-2026). The homepage listing is the site's primary content, so a
    # high target is reasonable; a real drop likely means the site changed layout.
    target_success_rate = 0.9
    source_url = _HOME

    def scrape(self) -> Optional[dict]:
        soup = self.get_soup(_HOME)
        if not soup:
            return None

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "tariff/Tariff/" not in href or not href.lower().endswith(".pdf"):
                continue
            tr = a.find_parent("tr")
            if not tr:
                continue
            row_text = tr.get_text(" ", strip=True)
            date_m = _DATE_RE.search(row_text)
            if not date_m:
                continue
            ref_m = _REF_RE.search(row_text) or _REF_RE.search(href)
            # Rows are listed newest-first; the FIRST tariff-determination row with a
            # date wins.
            return {
                "determination_date": date_m.group(1),
                "reference": ref_m.group(0) if ref_m else None,
                "title": row_text[:200],
                "_source_url": _HOME,
                "_pdf_url": href,
            }
        return None

    def persist(self, data: dict) -> int:
        if not data.get("determination_date"):
            return 0
        period = data["determination_date"]
        # This is a date+reference, not a scalar rate, so it does not fit
        # record_observation() (which requires a numeric value and refuses to store
        # None — by design, so a text finding is never mistaken for a missing
        # number). nepra_power carries the reference in `unit` and the row's own
        # source_url (self.source_url, logged by BasePakGovScraper.fetch()) plus
        # ScrapeResult.artifacts (set below via the caller) give the same
        # verifiability: which page, which row, when.
        upsert_nepra("latest_tariff_determination", period, None,
                    data.get("reference") or "")
        return 1
