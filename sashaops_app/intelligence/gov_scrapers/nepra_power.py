"""
gov_scrapers/nepra_power.py — NEPRA electricity tariffs and power sector data.

Tries tariff determination pages, press releases, and notifications.
NLP extraction captures tariff changes and capacity announcements.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_nepra

_PAGES = [
    # NEPRA returns 403 Forbidden on all pages as of June 2026 (IP-level block).
    # Kept for when access is restored; scraper will fall through to regulatory_text fallback.
    "https://www.nepra.org.pk/tariff/Tariff.php",
    "https://www.nepra.org.pk/tariff/",
    "https://www.nepra.org.pk/",
    "https://www.nepra.org.pk/power-sector-statistics/",
]

_PRESS_PAGES = [
    "https://www.nepra.org.pk/news-events/",
    "https://www.nepra.org.pk/publications/",
]

_KEYWORDS = [
    "industrial tariff", "industrial", "tariff", "rs.", "pkr",
    "per unit", "per kwh", "installed capacity", "total capacity",
    "generation capacity", "mw", "gw",
]

_DOMAIN_KEYWORDS = [
    "electricity tariff", "tariff determination", "industrial tariff",
    "installed capacity", "generation capacity", "power sector",
    "distribution company", "disco", "national grid", "nepra",
    "load shedding", "circular debt", "capacity payment", "fuel adjustment",
    "variable charge", "fixed charge", "consumer tariff",
]


class NEPRAPowerScraper(BasePakGovScraper):
    scraper_id = "nepra_power"
    label = "NEPRA power tariffs"
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

            tariff   = self.number_near(text, "industrial tariff", "industrial", "tariff", window=80)
            capacity = self.number_near(text, "installed capacity", "total capacity",
                                        "generation capacity", window=90)
            if tariff and 5 < tariff < 100:
                out["tariff_industrial"] = tariff
            if capacity and capacity > 1000:
                out["capacity_total"] = capacity
            if out:
                break

        if not out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=3)
                if press_text:
                    all_text += " " + press_text
                    tariff = self.number_near(
                        press_text, "tariff", "per unit", "per kwh", "rs.", window=100
                    )
                    capacity = self.number_near(
                        press_text, "installed capacity", "total capacity", "mw", window=100
                    )
                    if tariff and 5 < tariff < 100:
                        out["tariff_industrial"] = tariff
                    if capacity and capacity > 1000:
                        out["capacity_total"] = capacity
                    if out:
                        break

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
        if "tariff_industrial" in data:
            upsert_nepra("tariff_industrial", period, data["tariff_industrial"], "PKR/kWh")
            n += 1
        if "capacity_total" in data:
            upsert_nepra("capacity_total", period, data["capacity_total"], "MW")
            n += 1
        return n
