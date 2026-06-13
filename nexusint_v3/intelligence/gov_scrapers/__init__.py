"""
intelligence/gov_scrapers — Pakistan government & regulatory data scrapers.

Each scraper is pure Python (requests + BeautifulSoup), caches to disk for 24h,
never raises, and persists to its purpose-built DB table. The World Bank scraper is
the reliable macro backbone; official-site scrapers are best-effort and degrade
gracefully (return None) when a site changes or blocks scraping.
"""

from __future__ import annotations

from typing import List

from intelligence.gov_scrapers.base import BasePakGovScraper

# Core macro / fiscal / regulator scrapers (always run)
from intelligence.gov_scrapers.worldbank_pak import WorldBankPakScraper
from intelligence.gov_scrapers.sbp_monetary import SBPMonetaryScraper
from intelligence.gov_scrapers.ogra_prices import OGRAPricesScraper
from intelligence.gov_scrapers.pbs_lsmi import PBSLsmiScraper
from intelligence.gov_scrapers.fbr_revenue import FBRRevenueScraper
from intelligence.gov_scrapers.nepra_power import NEPRAPowerScraper
from intelligence.gov_scrapers.pta_stats import PTAStatsScraper

# Ministry / policy scrapers (always run, best-effort headlines)
from intelligence.gov_scrapers.ministries import MINISTRY_SCRAPERS

# Industry association scrapers (keyword-selected)
from intelligence.gov_scrapers.associations import ASSOCIATION_SCRAPERS


# Always-run core set
CORE_SCRAPERS: List[BasePakGovScraper] = [
    WorldBankPakScraper(),
    SBPMonetaryScraper(),
    OGRAPricesScraper(),
    PBSLsmiScraper(),
    FBRRevenueScraper(),
    NEPRAPowerScraper(),
    PTAStatsScraper(),
]


def get_core_scrapers() -> List[BasePakGovScraper]:
    """Macro/regulator scrapers that run for every analysis."""
    return list(CORE_SCRAPERS)


def get_ministry_scrapers() -> List[BasePakGovScraper]:
    return list(MINISTRY_SCRAPERS)


def select_industry_association_scrapers(industry: str) -> List[BasePakGovScraper]:
    """Return association scrapers relevant to the detected industry."""
    return [s for s in ASSOCIATION_SCRAPERS if industry in getattr(s, "industries", ())]


def get_scrapers_for_run(industry: str = "general",
                         include_ministries: bool = True) -> List[BasePakGovScraper]:
    """Full scraper set for a Stage-1 run: core + (ministries) + industry associations."""
    scrapers = get_core_scrapers()
    if include_ministries:
        scrapers += get_ministry_scrapers()
    scrapers += select_industry_association_scrapers(industry)
    return scrapers
