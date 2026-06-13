"""
SBP Pakistan forex scraper.

Source: open.er-api.com (free, no key)
The SBP website (sbp.org.pk) data endpoints return 404 â€” the API subdomains
(dnb.sbp.org.pk) fail DNS resolution from containerised environments.
ExchangeRate-API is used instead for live daily rates.
Data: PKR mid rates vs 30+ major and EM currencies, updated daily.
"""

from intelligence.exchange_scrapers.base import BaseExchangeScraper, ScrapeResult
from database.connection import get_db

REGION = "pakistan"
BANK   = "SBP"
BASE   = "PKR"


class SBPPakistanForexScraper(BaseExchangeScraper):
    scraper_id = "sbp_forex"
    region_id  = REGION

    def fetch(self) -> ScrapeResult:
        result   = ScrapeResult(scraper_id=self.scraper_id, region_id=self.region_id)
        db       = get_db()
        inserted, skipped, error = self._fetch_erapi_forex(db, REGION, BANK, BASE)
        db.commit()
        result.rows_inserted = inserted
        result.rows_skipped  = skipped
        result.error_msg     = error
        result.status        = "success" if (inserted > 0 or skipped > 0) else ("failed" if error else "partial")
        return result

