"""
intelligence/exchange_scrapers/base.py — Abstract base for all exchange/forex scrapers.

Every scraper:
- Is synchronous (called from asyncio run_in_executor)
- Never raises — returns ScrapeResult with status 'failed' on error
- Writes rows directly to DB via parameterised queries
- Logs its run to the scraper_runs table
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple
import structlog

log = structlog.get_logger()

# Free, no-key forex API — updated daily, covers all EM currencies
ERAPI_URL = "https://open.er-api.com/v6/latest/USD"

# PKR pairs relevant to Pakistan trade, remittances, and reserves.
ERAPI_STORE_QUOTES = [
    "USD", "EUR", "GBP", "JPY", "CHF", "CNY",
    "SAR", "AED", "KWD", "QAR", "OMR",   # Gulf — remittance corridors
    "INR", "CNH",                         # regional trade
    "PKR",
]


@dataclass
class ScrapeResult:
    scraper_id: str
    region_id: str = "pakistan"   # retained for compatibility; always Pakistan
    status: str = "success"       # 'success' | 'failed' | 'partial'
    rows_inserted: int = 0
    rows_skipped: int = 0
    error_msg: str = ""
    details: dict = field(default_factory=dict)


class BaseExchangeScraper(ABC):
    scraper_id: str = ""
    region_id: str = "pakistan"

    REQUEST_TIMEOUT = 20        # seconds for HTTP requests
    REQUEST_HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; SashaOps Intelligence Bot/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    @abstractmethod
    def fetch(self) -> ScrapeResult:
        """Fetch data and write rows to DB. Never raises."""

    def run(self) -> ScrapeResult:
        """Public entry point — wraps fetch() and logs the run."""
        from database.connection import get_db
        started = datetime.now(timezone.utc).isoformat()
        result = ScrapeResult(scraper_id=self.scraper_id, region_id=self.region_id, status="failed")
        try:
            result = self.fetch()
        except Exception as e:
            result.status = "failed"
            result.error_msg = str(e)[:500]
            log.warning("Scraper raised unexpectedly", scraper=self.scraper_id, error=str(e))
        finally:
            completed = datetime.now(timezone.utc).isoformat()
            try:
                db = get_db()
                db.execute(
                    """INSERT INTO scraper_runs
                       (scraper_id, status, rows_inserted, rows_skipped, error_msg, started_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (result.scraper_id, result.status,
                     result.rows_inserted, result.rows_skipped,
                     result.error_msg or None, started, completed),
                )
                db.commit()
            except Exception as db_err:
                log.warning("Could not log scraper run", error=str(db_err))
        log.info(
            "Scraper finished",
            scraper=self.scraper_id,
            status=result.status,
            inserted=result.rows_inserted,
            skipped=result.rows_skipped,
        )
        return result

    def _fetch_erapi_forex(self, db, region: str, bank: str, base_currency: str) -> Tuple[int, int, str]:
        """
        Fetch forex rates from open.er-api.com (free, no key needed).
        Stores how many base-currency units buy 1 of each quote currency.
        e.g. base=NGN, quote=USD, rate=1360 → 1360 NGN buys 1 USD.
        Returns (inserted, skipped, error_msg).
        """
        import requests
        import email.utils
        from datetime import date

        try:
            resp = requests.get(ERAPI_URL, headers=self.REQUEST_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return 0, 0, f"ExchangeRate-API fetch error: {e}"

        if data.get("result") != "success":
            return 0, 0, f"ExchangeRate-API error: {data.get('error-type', 'unknown')}"

        usd_rates = data["rates"]  # 1 USD = N units of each currency
        try:
            ts = email.utils.parsedate_to_datetime(data["time_last_update_utc"])
            rate_date = ts.strftime("%Y-%m-%d")
        except Exception:
            rate_date = date.today().isoformat()

        base_in_usd = usd_rates.get(base_currency)
        if not base_in_usd or base_in_usd <= 0:
            return 0, 0, f"Base currency {base_currency} not in ExchangeRate-API response"

        inserted = skipped = 0
        for quote in ERAPI_STORE_QUOTES:
            if quote == base_currency:
                continue
            quote_in_usd = usd_rates.get(quote)
            if not quote_in_usd or quote_in_usd <= 0:
                continue
            # base_per_quote = how many base units per 1 quote unit
            rate = round(base_in_usd / quote_in_usd, 6)
            ok = self._upsert_forex_rate(db, region, bank, rate_date, base_currency, quote, "mid", rate, ERAPI_URL)
            if ok:
                inserted += 1
            else:
                skipped += 1

        return inserted, skipped, ""

    def _upsert_forex_rate(self, db, region_id: str, central_bank: str, rate_date: str,
                           base: str, quote: str, rate_type: str, rate: float,
                           source_url: Optional[str] = None) -> bool:
        """
        Insert a PKR forex rate into pkr_forex. Pakistan-only: base is always PKR,
        so we store (rate_date, quote_currency, rate). Returns True if inserted.
        """
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            db.execute(
                """INSERT OR IGNORE INTO pkr_forex
                   (rate_date, quote_currency, rate, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (rate_date, quote, rate, source_url or central_bank, fetched_at),
            )
            return db.execute("SELECT changes()").fetchone()[0] > 0
        except Exception as e:
            log.warning("pkr_forex upsert failed", error=str(e))
            return False
