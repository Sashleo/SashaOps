"""
SBP Pakistan forex scraper.

Source: open.er-api.com (free, no key)
The SBP website (sbp.org.pk) data endpoints return 404 â€” the API subdomains
(dnb.sbp.org.pk) fail DNS resolution from containerised environments.
ExchangeRate-API is used instead for live daily rates.
Data: PKR mid rates vs 30+ major and EM currencies, updated daily.
"""

from datetime import datetime

from intelligence.exchange_scrapers.base import BaseExchangeScraper, ScrapeResult, ScrapeStatus
from database.connection import get_db

REGION = "pakistan"
BANK   = "SBP"
BASE   = "PKR"


class SBPPakistanForexScraper(BaseExchangeScraper):
    scraper_id = "sbp_forex"
    label = "Forex rates"
    category = "macro"
    default_freshness_hours = 24
    # Verified 2026-07-28 (docs/scraper_verification.md Priority 5): 13/13 pairs
    # inserted, 5 spot-checked against the live API by hand, exact match (0.00%
    # divergence). ExchangeRate-API has no documented downtime history here, so the
    # target is set high; a real drop below this should be investigated, not ignored.
    target_success_rate = 0.95
    region_id  = REGION

    def fetch(self) -> ScrapeResult:
        result   = ScrapeResult(scraper_id=self.scraper_id, status=ScrapeStatus.NO_DATA)
        db       = get_db()
        inserted, skipped, error = self._fetch_erapi_forex(db, REGION, BANK, BASE)
        db.commit()
        result.rows_inserted  = inserted
        result.rows_failed    = skipped
        result.error_message  = error or None
        result.method_used    = "json_api"
        result.finished_at    = datetime.utcnow()
        result.status = (ScrapeStatus.SUCCESS if inserted > 0
                         else ScrapeStatus.PARTIAL if skipped > 0
                         else ScrapeStatus.UNREACHABLE if error
                         else ScrapeStatus.NO_DATA)
        return self.log(result)

