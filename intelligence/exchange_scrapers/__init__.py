"""
intelligence/exchange_scrapers — Pakistan-only deterministic data scrapers.

Pakistan operates a single exchange (PSX) and a single central bank (SBP). PSX market
and company data is fetched by intelligence/exchange_crawlers/psx.py; SBP provides PKR
forex rates here.
"""

from typing import List
from intelligence.exchange_scrapers.base import BaseExchangeScraper
from intelligence.exchange_scrapers.central_banks import ALL_CENTRAL_BANK_SCRAPERS
from intelligence.exchange_scrapers.psx_market import PSXMarketScraper
from intelligence.exchange_scrapers.psx_company import PSXCompanyProfileScraper


def get_all_scrapers() -> List[BaseExchangeScraper]:
    """Active Pakistan scrapers: PSX market (companies/prices/index) + SBP PKR forex."""
    return [PSXMarketScraper(), *ALL_CENTRAL_BANK_SCRAPERS]


def get_psx_company_profile_scraper() -> PSXCompanyProfileScraper:
    """Nightly bulk job (all tickers). Scheduled separately from the 6h cycle."""
    return PSXCompanyProfileScraper()
