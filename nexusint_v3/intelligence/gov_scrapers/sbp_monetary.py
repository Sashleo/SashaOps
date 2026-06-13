"""
gov_scrapers/sbp_monetary.py — State Bank of Pakistan monetary indicators.

Tries multiple URL patterns including press releases and the monetary policy
statement pages. NLP extraction ensures regulatory text is captured even when
numeric extraction fails (e.g., site redesigns).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_sbp_indicator

_PAGES = [
    # Primary — confirmed working June 2026; contains policy rate clearly labelled
    "https://www.sbp.org.pk/ecodata/index2.asp",
    "https://www.sbp.org.pk/ecodata/",
    # Fallbacks
    "https://www.sbp.org.pk/m_policy/index.asp",
    "https://www.sbp.org.pk/m_policy/",
    "https://www.sbp.org.pk/",
]

_PRESS_PAGES = [
    # /press/ and /m_policy/ returned 404 in June 2026; keeping as last resort
    "https://www.sbp.org.pk/press/index.asp",
    "https://www.sbp.org.pk/press/",
]

_KEYWORDS = [
    "policy rate", "policy (target) rate", "monetary policy", "repo rate",
    "interest rate", "basis points", "per cent", "percent", "rate to",
    "kept at", "revised to", "maintained at", "bps",
]

_DOMAIN_KEYWORDS = [
    "policy rate", "monetary policy", "inflation", "interest rate",
    "discount rate", "basis points", "exchange rate", "reserves",
    "current account", "fiscal deficit", "remittances",
]


class SBPMonetaryScraper(BasePakGovScraper):
    scraper_id = "sbp_monetary"

    def scrape(self) -> Optional[dict]:
        out: dict = {}
        all_text = ""

        # 1. Try main pages for numeric extraction
        for url in _PAGES:
            soup = self.get_soup(url)
            if not soup:
                continue
            text = soup.get_text(" ", strip=True)
            all_text += " " + text
            rate = self.number_near(text, *_KEYWORDS, window=100)
            if rate is not None and 1 < rate < 40:
                out["policy_rate"] = rate
                break

        # 2. Try press release pages if no rate found
        if "policy_rate" not in out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=2)
                if press_text:
                    all_text += " " + press_text
                    rate = self.number_near(press_text, *_KEYWORDS, window=120)
                    if rate is not None and 1 < rate < 40:
                        out["policy_rate"] = rate
                        break

        # 3. Always extract NLP regulatory content regardless of numeric outcome
        if all_text.strip():
            nlp = self.nlp_extract(all_text, _DOMAIN_KEYWORDS)
            if nlp.get("regulatory_text"):
                out["regulatory_text"] = nlp["regulatory_text"]
            if nlp.get("nlp_entities"):
                out["nlp_entities"] = nlp["nlp_entities"]

        return out if out else None

    def persist(self, data: dict) -> int:
        n = 0
        period = date.today().strftime("%Y-%m")
        if "policy_rate" in data:
            upsert_sbp_indicator("policy_rate", period, data["policy_rate"], "%", source="SBP")
            n += 1
        return n
