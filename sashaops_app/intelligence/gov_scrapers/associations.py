"""
gov_scrapers/associations.py — Industry association scrapers (best-effort, keyword-selected).

Trade associations publish the most timely sector production data in Pakistan — cement
dispatches (APCMA), textile exports (APTMA), vehicle sales (PAMA), sugar production (PSMA),
pharma (PPMA), and the OICCI business-confidence survey. Selected at runtime by the detected
industry. Qualitative headlines returned for prompt injection; numeric figures, when parsed,
persist to industry_stats.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, List, Tuple

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_industry_stat


class AssociationScraper(BasePakGovScraper):
    """Generic association scraper. `industries` controls when it is selected."""

    def __init__(self, scraper_id: str, name: str, industries: Tuple[str, ...],
                 urls: List[str], metric_keywords: Tuple[str, ...], metric_name: str,
                 unit: str = ""):
        self.scraper_id = scraper_id
        self.name = name
        self.industries = industries
        self.urls = urls
        self.metric_keywords = metric_keywords
        self.metric_name = metric_name
        self.unit = unit

    def scrape(self) -> Optional[dict]:
        for url in self.urls:
            soup = self.get_soup(url)
            if not soup:
                continue
            text = soup.get_text(" ", strip=True)
            value = self.number_near(text, *self.metric_keywords, window=90)
            headlines = []
            for a in soup.find_all("a"):
                t = a.get_text(" ", strip=True)
                if 20 <= len(t) <= 140:
                    headlines.append(t)
                if len(headlines) >= 4:
                    break
            if value is not None or headlines:
                return {
                    "association": self.name,
                    "metric": self.metric_name,
                    "value": value,
                    "headlines": headlines,
                }
        return None

    def persist(self, data: dict) -> int:
        if data.get("value") is None:
            return 0
        period = date.today().strftime("%Y-%m")
        industry = self.industries[0] if self.industries else "general"
        upsert_industry_stat(industry, self.metric_name, period,
                             data["value"], self.unit, source=self.scraper_id)
        return 1


ASSOCIATION_SCRAPERS: List[AssociationScraper] = [
    AssociationScraper(
        "assoc_apcma", "All Pakistan Cement Manufacturers Association", ("cement",),
        ["https://apcma.com/", "https://www.apcma.com/"],
        ("dispatches", "total dispatch", "cement dispatch"), "dispatches", "MT"),
    AssociationScraper(
        "assoc_aptma", "All Pakistan Textile Mills Association", ("textile",),
        ["https://www.aptma.org.pk/", "https://aptma.org.pk/"],
        ("exports", "export", "capacity utilization"), "exports", "USD_mn"),
    AssociationScraper(
        "assoc_pama", "Pakistan Automotive Manufacturers Association", ("auto",),
        ["https://www.pama.org.pk/", "https://pama.org.pk/"],
        ("production", "sales", "units"), "production", "units"),
    AssociationScraper(
        "assoc_psma", "Pakistan Sugar Mills Association", ("sugar",),
        ["https://psmacentre.com/", "https://www.psma.com.pk/"],
        ("production", "sugar production", "crushing"), "production", "MT"),
    AssociationScraper(
        "assoc_ppma", "Pakistan Pharmaceutical Manufacturers Association", ("pharma",),
        ["https://www.ppma.org.pk/", "https://ppma.org.pk/"],
        ("industry size", "growth", "market"), "industry_size", "PKR_bn"),
    AssociationScraper(
        "assoc_oicci", "Overseas Investors Chamber of Commerce & Industry",
        ("cement", "textile", "energy", "banking", "telecom", "pharma", "fmcg",
         "oil_gas", "tech", "steel", "sugar", "fertilizer", "auto", "chemicals", "general"),
        ["https://oicci.org/", "https://www.oicci.org/"],
        ("business confidence", "confidence index", "bci"), "business_confidence", "index"),
]
