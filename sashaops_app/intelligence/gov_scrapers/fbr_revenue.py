"""
gov_scrapers/fbr_revenue.py — FBR tax revenue collection.

Tries main pages plus press releases. NLP extraction surfaces regulatory
context (duty changes, withholding tax notices, SROs) even without numeric data.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_fbr_revenue, upsert_macro

_PAGES = [
    # Newsroom confirmed accessible June 2026; contains announcements and collection updates
    "https://www.fbr.gov.pk/Newsroom/",
    "https://www.fbr.gov.pk/",
    # Budget/policy pages (may have revenue context)
    "https://www.fbr.gov.pk/categ/revenue-division/",
    "https://www.fbr.gov.pk/revenue-statistics",
]

_PRESS_PAGES = [
    # These paths returned 404 in June 2026; FBR does not publish live stats at these URLs
    "https://www.fbr.gov.pk/Newsroom/",
    "https://www.fbr.gov.pk/categ/news-updates/",
]

_KEYWORDS = [
    "revenue collection", "tax collection", "collected", "revenue target",
    "gross collection", "net collection", "billion", "trillion",
    "fbr collected", "fbr revenue",
]

_DOMAIN_KEYWORDS = [
    "revenue collection", "tax collection", "customs duty", "income tax",
    "sales tax", "federal excise", "withholding tax", "sro", "statutory",
    "regulatory order", "tariff", "duty", "exemption", "refund",
    "fiscal", "budget", "target", "shortfall",
]


class FBRRevenueScraper(BasePakGovScraper):
    scraper_id = "fbr_revenue"
    label = "FBR revenue"
    category = "regulator"
    default_freshness_hours = 720
    target_success_rate = 0.0   # set from the live suite (Task 2.4)

    def scrape(self) -> Optional[dict]:
        out: dict = {}
        all_text = ""

        for url in _PAGES:
            soup = self.get_soup(url)
            if not soup:
                continue
            text = soup.get_text(" ", strip=True)
            all_text += " " + text
            collected = self.number_near(
                text, "revenue collection", "tax collection", "collected",
                "fbr collected", window=100
            )
            if collected is not None and collected > 0:
                out["collected"] = collected
                break

        if "collected" not in out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=3)
                if press_text:
                    all_text += " " + press_text
                    collected = self.number_near(
                        press_text, "revenue collection", "collected", "billion",
                        "tax revenue", window=120
                    )
                    if collected is not None and collected > 0:
                        out["collected"] = collected
                        break

        if all_text.strip():
            nlp = self.nlp_extract(all_text, _DOMAIN_KEYWORDS)
            if nlp.get("regulatory_text"):
                out["regulatory_text"] = nlp["regulatory_text"]
            if nlp.get("nlp_entities"):
                out["nlp_entities"] = nlp["nlp_entities"]

        return out if out else None

    def persist(self, data: dict) -> int:
        if "collected" not in data:
            return 0
        period = date.today().strftime("%Y-%m")
        upsert_fbr_revenue("total", period, data["collected"], None)
        upsert_macro("fbr", "tax_collection", period, data["collected"], "PKR_bn")
        return 1
