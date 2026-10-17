"""
intelligence/scraper_base.py - The single scraper contract.

Every scraper (exchange, gov, macro) inherits BaseScraper, declares its class
attributes, and returns a ScrapeResult from fetch(). fetch() never raises: a
failure is a ScrapeResult with a non-SUCCESS status and an error_message.

Each fetch() logs its outcome to the scrape_log table (database/scrape_log.py),
which is what the Data Health card and the regression suite read.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ScrapeStatus(str, Enum):
    """Outcome of one scrape. str-valued so `result.status == "success"` still works."""
    SUCCESS       = "success"        # got the data, stored it
    PARTIAL       = "partial"        # got some of it
    NO_DATA       = "no_data"        # source reachable + parsed, but nothing to store
    UNREACHABLE   = "unreachable"    # network/DNS/timeout/HTTP error
    PARSER_FAILED = "parser_failed"  # source reachable but the parse produced nothing usable
    DISABLED      = "disabled"       # deliberately off (documented in scraper_verification.md)


@dataclass
class ScrapeResult:
    scraper_id: str
    status: ScrapeStatus
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_failed: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    source_url: Optional[str] = None
    method_used: Optional[str] = None      # html_bs4 | json_api | playwright | pdf | ocr
    artifacts: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[int]:
        if not self.finished_at:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    @property
    def ok(self) -> bool:
        return self.status == ScrapeStatus.SUCCESS

    def finish(self, status: ScrapeStatus, **kw) -> "ScrapeResult":
        """Stamp the terminal state and return self (so callers can `return r.finish(...)`)."""
        self.status = status
        self.finished_at = datetime.utcnow()
        for k, v in kw.items():
            setattr(self, k, v)
        return self


class BaseScraper(ABC):
    """
    Contract for every scraper.

    scraper_id              stable key used in scrape_log and the Data Health card
    label                   human name shown in the UI
    category                exchange | macro | regulator | stats
    default_freshness_hours how old the newest row may be before the card goes amber
    target_success_rate     measured baseline from the live suite (Task 2.4); a drop
                            below this is a regression
    """
    scraper_id: str = ""
    label: str = ""
    category: str = ""
    default_freshness_hours: int = 24
    target_success_rate: float = 0.0

    @abstractmethod
    def fetch(self) -> ScrapeResult:
        """Fetch, parse, persist. Never raises. Always returns a ScrapeResult."""

    def per_target(self, target_id: str) -> ScrapeResult:
        """Scrape one target (e.g. a single PSX ticker). Override where meaningful."""
        raise NotImplementedError(f"{self.scraper_id} does not support per-target scraping")

    # ── Shared plumbing ───────────────────────────────────────────────────────
    def new_result(self, **kw) -> ScrapeResult:
        return ScrapeResult(scraper_id=self.scraper_id, status=ScrapeStatus.NO_DATA, **kw)

    def log(self, result: ScrapeResult) -> ScrapeResult:
        """Record the outcome in scrape_log. Never raises."""
        if result.finished_at is None:
            result.finished_at = datetime.utcnow()
        try:
            from database.scrape_log import log_scrape
            log_scrape(result)
        except Exception:
            pass
        return result
