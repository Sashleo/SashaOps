"""
gov_scrapers/ogra_prices.py — OGRA petroleum prices.

Tries multiple URL patterns including price notification and press release pages.
NLP extraction captures regulatory text on price changes even when table parsing fails.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_ogra_price

_PAGES = [
    # Hub page confirmed accessible June 2026; detail price pages are JS-rendered (404 on direct URLs)
    "https://www.ogra.org.pk/prices",
    "https://www.ogra.org.pk/",
    # Detail pages — likely JS-rendered, kept as fallback
    "https://www.ogra.org.pk/petroleum-prices",
    "https://www.ogra.org.pk/web/ogra/pricing1",
]

_PRESS_PAGES = [
    "https://www.ogra.org.pk/press-releases",
    "https://www.ogra.org.pk/notifications",
]

_KEYWORDS = [
    "petrol", "motor spirit", "ms (petrol)", "high speed diesel",
    "hsd", "diesel", "lpg", "liquefied petroleum", "kerosene",
    "rs.", "pkr", "per litre", "/litre",
]

_DOMAIN_KEYWORDS = [
    "petroleum prices", "petrol", "diesel", "hsd", "lpg", "fuel price",
    "price notification", "pricing", "per litre", "downstream",
    "ogra", "upstream", "natural gas", "tariff",
]


class OGRAPricesScraper(BasePakGovScraper):
    scraper_id = "ogra_prices"
    label = "OGRA fuel prices"
    category = "regulator"
    default_freshness_hours = 336
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

            petrol = self.number_near(text, "petrol", "motor spirit", "ms (petrol)", window=80)
            hsd    = self.number_near(text, "high speed diesel", "hsd", "diesel", window=80)
            lpg    = self.number_near(text, "lpg", "liquefied petroleum", window=80)

            if petrol and 80 < petrol < 600:
                out["petrol"] = petrol
            if hsd and 80 < hsd < 600:
                out["hsd"] = hsd
            if lpg and 50 < lpg < 1000:
                out["lpg"] = lpg
            if out:
                break

        # Fallback: press releases contain price notification letters
        if not out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=2)
                if press_text:
                    all_text += " " + press_text
                    petrol = self.number_near(press_text, "petrol", "motor spirit", window=100)
                    hsd    = self.number_near(press_text, "high speed diesel", "hsd", window=100)
                    if petrol and 80 < petrol < 600:
                        out["petrol"] = petrol
                    if hsd and 80 < hsd < 600:
                        out["hsd"] = hsd
                    if out:
                        break

        # NLP regulatory text extraction (always run)
        if all_text.strip():
            nlp = self.nlp_extract(all_text, _DOMAIN_KEYWORDS)
            if nlp.get("regulatory_text"):
                out["regulatory_text"] = nlp["regulatory_text"]
            if nlp.get("nlp_entities"):
                out["nlp_entities"] = nlp["nlp_entities"]

        return out if out else None

    def persist(self, data: dict) -> int:
        n = 0
        today = date.today().isoformat()
        for product, unit in (("petrol", "PKR/litre"), ("hsd", "PKR/litre"), ("lpg", "PKR/kg")):
            if product in data:
                upsert_ogra_price(product, today, data[product], unit)
                n += 1
        return n
