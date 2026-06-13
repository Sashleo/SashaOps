"""
gov_scrapers/pbs_lsmi.py — PBS Large Scale Manufacturing Index.

Tries multiple URL patterns. NLP extraction captures growth/decline context
in press releases even when the headline figure isn't found in structured form.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_lsmi, upsert_macro

_PAGES = [
    # Primary — confirmed working June 2026; static HTML with QIM 124.89 and 11.09% growth
    "https://www.pbs.gov.pk/industry-2/",
    # Fallbacks
    "https://www.pbs.gov.pk/content/large-scale-manufacturing",
    "https://www.pbs.gov.pk/qim-tables",
    "https://www.pbs.gov.pk/content/quantum-index-manufacturing",
    "https://www.pbs.gov.pk/content/monthly-lsm",
    "https://www.pbs.gov.pk/",
]

_PRESS_PAGES = [
    # These paths returned 404 in June 2026; kept as fallback only
    "https://www.pbs.gov.pk/content/press-releases",
    "https://www.pbs.gov.pk/content/news",
]

_KEYWORDS = [
    "large scale manufacturing", "lsm", "lsm growth", "qim",
    "quantum index", "manufacturing growth", "industrial growth",
    "percent", "per cent", "%",
]

_DOMAIN_KEYWORDS = [
    "large scale manufacturing", "lsm", "manufacturing index", "industrial output",
    "growth", "decline", "production", "quantum index", "textile", "cement",
    "food", "beverages", "chemicals", "fertilizer",
]


class PBSLsmiScraper(BasePakGovScraper):
    scraper_id = "pbs_lsmi"

    def scrape(self) -> Optional[dict]:
        out: dict = {}
        all_text = ""

        for url in _PAGES:
            soup = self.get_soup(url)
            if not soup:
                continue
            text = soup.get_text(" ", strip=True)
            all_text += " " + text
            growth = self.number_near(
                text, "large scale manufacturing", "lsm growth", "qim",
                "quantum index", window=100
            )
            if growth is not None and -50 < growth < 50:
                out["lsm_growth"] = growth
                break

        if "lsm_growth" not in out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=3)
                if press_text:
                    all_text += " " + press_text
                    growth = self.number_near(
                        press_text, "large scale manufacturing", "lsm", "quantum index",
                        "manufacturing", window=120
                    )
                    if growth is not None and -50 < growth < 50:
                        out["lsm_growth"] = growth
                        break

        if all_text.strip():
            nlp = self.nlp_extract(all_text, _DOMAIN_KEYWORDS)
            if nlp.get("regulatory_text"):
                out["regulatory_text"] = nlp["regulatory_text"]
            if nlp.get("nlp_entities"):
                out["nlp_entities"] = nlp["nlp_entities"]

        return out if out else None

    def persist(self, data: dict) -> int:
        if "lsm_growth" not in data:
            return 0
        period = date.today().strftime("%Y-%m")
        upsert_lsmi("overall", period, None, data["lsm_growth"])
        upsert_macro("pbs", "lsm_growth", period, data["lsm_growth"], "%")
        return 1
