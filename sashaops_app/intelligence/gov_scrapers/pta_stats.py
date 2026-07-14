"""
gov_scrapers/pta_stats.py — PTA telecom sector indicators.

PTA's indicators page is often JavaScript-rendered; this scraper tries
the main page, a data/reports page, and press releases. NLP extraction
surfaces connectivity and broadband policy text.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_pta

_PAGES = [
    # Homepage confirmed accessible June 2026; shows "Telecom Indicators Overview" section
    # Actual indicator values are JS-rendered on /category/telecom-indicators/164 — static parse gets partial data
    "https://www.pta.gov.pk/home",
    "https://www.pta.gov.pk/",
    "https://www.pta.gov.pk/category/telecom-indicators/164",
    "https://www.pta.gov.pk/en/telecom-indicators",
    "https://www.pta.gov.pk/en/data-statistics/",
]

_PRESS_PAGES = [
    # Press release paths returned 404 in June 2026
    "https://www.pta.gov.pk/en/media-center/press-releases",
    "https://www.pta.gov.pk/en/media-center/news",
]

_KEYWORDS = [
    "cellular mobile", "mobile subscribers", "total subscribers",
    "broadband subscribers", "broadband", "teledensity",
    "internet subscribers", "3g", "4g", "million",
]

_DOMAIN_KEYWORDS = [
    "mobile subscribers", "broadband subscribers", "internet penetration",
    "telecom sector", "teledensity", "spectrum", "5g", "4g", "3g",
    "digital pakistan", "e-commerce", "fintech", "mobile banking",
    "license", "regulatory", "pta", "telecom policy",
]


class PTAStatsScraper(BasePakGovScraper):
    scraper_id = "pta_stats"
    label = "PTA telecom stats"
    category = "stats"
    default_freshness_hours = 1440
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

            mobile = self.number_near(
                text, "cellular mobile", "mobile subscribers",
                "total subscribers", window=100
            )
            bb = self.number_near(text, "broadband subscribers", "broadband", window=90)
            teledensity = self.number_near(text, "teledensity", window=70)
            internet = self.number_near(text, "internet subscribers", "internet users", window=90)

            if mobile and mobile > 1:
                out["mobile_subs"] = mobile
            if bb and bb > 1:
                out["broadband_subs"] = bb
            if teledensity and 0 < teledensity < 200:
                out["teledensity"] = teledensity
            if internet and internet > 1:
                out["internet_subs"] = internet
            if out:
                break

        if not out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=3)
                if press_text:
                    all_text += " " + press_text
                    mobile = self.number_near(
                        press_text, "mobile subscribers", "cellular", "subscribers", window=100
                    )
                    bb = self.number_near(
                        press_text, "broadband", "internet subscribers", window=100
                    )
                    if mobile and mobile > 1:
                        out["mobile_subs"] = mobile
                    if bb and bb > 1:
                        out["broadband_subs"] = bb
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
        units = {
            "mobile_subs": "million",
            "broadband_subs": "million",
            "teledensity": "%",
            "internet_subs": "million",
        }
        for metric in ("mobile_subs", "broadband_subs", "teledensity", "internet_subs"):
            if metric in data:
                upsert_pta(metric, period, data[metric], units[metric])
                n += 1
        return n
