"""
gov_scrapers/ministries.py — Pakistani ministry policy-signal scrapers (best-effort).

Ministries communicate policy changes that move whole sectors (tariffs, subsidies, SEZ
notifications, IT incentives, PSDP allocations). These scrapers grab recent headline links
from each ministry's site as qualitative policy signals. They are non-numeric: results are
returned for prompt injection (gov_data) and not persisted to a numeric table.
"""

from __future__ import annotations

from typing import Optional, List

from intelligence.gov_scrapers.base import BasePakGovScraper


class MinistryScraper(BasePakGovScraper):
    """Generic ministry scraper — collects recent headline links as policy signals."""

    def __init__(self, scraper_id: str, name: str, urls: List[str]):
        self.scraper_id = scraper_id
        self.name = name
        self.urls = urls

    def scrape(self) -> Optional[dict]:
        for url in self.urls:
            soup = self.get_soup(url)
            if not soup:
                continue
            headlines: List[str] = []
            seen = set()
            for a in soup.find_all("a"):
                txt = a.get_text(" ", strip=True)
                # Keep substantive link text only
                if 25 <= len(txt) <= 160 and txt not in seen:
                    low = txt.lower()
                    if any(k in low for k in (
                        "policy", "notification", "tariff", "subsidy", "incentive",
                        "budget", "scheme", "sez", "import", "export", "duty",
                        "investment", "regulation", "approval", "announce")):
                        seen.add(txt)
                        headlines.append(txt)
                if len(headlines) >= 6:
                    break
            if headlines:
                return {"ministry": self.name, "policy_signals": headlines}
        return None

    # Qualitative — nothing to persist to a numeric table
    def persist(self, data: dict) -> int:
        return 0


MINISTRY_SCRAPERS: List[MinistryScraper] = [
    MinistryScraper("ministry_commerce", "Ministry of Commerce",
                    ["https://www.commerce.gov.pk/", "https://www.commerce.gov.pk/press-releases/"]),
    MinistryScraper("ministry_energy", "Power Division / Ministry of Energy",
                    ["https://power.gov.pk/", "https://www.energymin.gov.pk/"]),
    MinistryScraper("ministry_industries", "Ministry of Industries & Production",
                    ["https://moip.gov.pk/", "https://www.industries.gov.pk/"]),
    MinistryScraper("ministry_it", "Ministry of IT & Telecom (PSEB)",
                    ["https://moitt.gov.pk/", "https://www.pseb.org.pk/"]),
    MinistryScraper("ministry_planning", "Ministry of Planning (PSDP)",
                    ["https://www.pc.gov.pk/"]),
    MinistryScraper("ministry_finance", "Ministry of Finance",
                    ["https://www.finance.gov.pk/", "https://www.finance.gov.pk/press_releases.html"]),
    MinistryScraper("ministry_privatisation", "Privatisation Commission",
                    ["https://privatisation.gov.pk/"]),
]
