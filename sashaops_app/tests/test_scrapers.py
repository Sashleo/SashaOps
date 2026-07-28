"""
Live regression suite (Phase 2 Task 2.4). Hits REAL external sites/APIs, so it is
excluded by default (pytest.ini: addopts = -m "not live") and run explicitly with
`pytest -m live`. A failure here can mean the scraper broke OR the source is
temporarily down/rate-limiting - that judgement call is why this stays a manual
run rather than part of the default CI gate.

Targets are the fixed set in tests/scraper_targets.yaml, spanning all 8 PSX sectors
plus every gov/macro scraper verified in Phase 2 (docs/scraper_verification.md).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.live

_TARGETS = yaml.safe_load((Path(__file__).parent / "scraper_targets.yaml").read_text())

# ── Real database for this module (conftest.py mocks get_db globally so the rest
# of the suite can run without pysqlcipher3; live tests need a REAL connection to
# check scrape_log rows and real scraped data). Scoped to this module only - a
# `pytest -m live` run collects ONLY live-marked tests, so no other test module's
# behaviour depends on get_db staying mocked within that run. ────────────────────
import database.connection as _db_conn


def _real_get_db():
    if _db_conn._connection is None:
        raise RuntimeError("Database not initialised. Call init_database() first.")
    return _db_conn._connection


@pytest.fixture(scope="module", autouse=True)
def _real_database():
    # conftest.py replaces pysqlcipher3 itself in sys.modules with a MagicMock (so
    # the default suite can run without a real encrypted DB) - init_database()'s
    # own `from pysqlcipher3 import dbapi2` would otherwise resolve to that mock
    # even after restoring get_db, silently returning a connection whose queries
    # always report success with empty results. Undo the substitution first so
    # live tests get the REAL SQLCipher connection.
    sys.modules.pop("pysqlcipher3", None)
    sys.modules.pop("pysqlcipher3.dbapi2", None)
    _db_conn.get_db = _real_get_db
    _db_conn.init_database()
    yield


def _all_tickers() -> list[str]:
    out: list[str] = []
    for group in _TARGETS["psx_tickers"].values():
        out.extend(group)
    return out


# ── PSX company profile ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", _all_tickers())
def test_psx_company_profile_success(ticker):
    from intelligence.exchange_scrapers.psx_company import PSXCompanyProfileScraper
    result = PSXCompanyProfileScraper().per_target(ticker)
    assert result.status.value in ("success", "partial"), (
        f"{ticker}: {result.status} - {result.error_message}"
    )
    data = (result.artifacts or {}).get("data") or {}
    assert data.get("name"), f"{ticker}: no company name"
    assert data.get("sector"), f"{ticker}: no sector"


# ── PSX financials: minimum revenue + net_income for the latest annual period ───

@pytest.mark.parametrize("ticker", _all_tickers())
def test_psx_financials_minimum_fields(ticker):
    from intelligence.psx_financials import fetch_psx_doc_financials
    fetched = fetch_psx_doc_financials(ticker)
    assert fetched is not None, f"{ticker}: no financials data at all"
    doc, _name = fetched
    assert doc.get("revenue"), f"{ticker}: missing revenue"
    assert doc.get("net_income") is not None, f"{ticker}: missing net_income"


# ── Gov / macro scrapers: SUCCESS + fresh scrape_log + min_rows ─────────────────

def _scraper_class(scraper_id: str):
    from intelligence.gov_scrapers.sbp_monetary import SBPMonetaryScraper
    from intelligence.gov_scrapers.fbr_revenue import FBRRevenueScraper
    from intelligence.gov_scrapers.nepra_power import NEPRAPowerScraper
    from intelligence.gov_scrapers.ogra_prices import OGRAPricesScraper
    from intelligence.gov_scrapers.pbs_lsmi import PBSLsmiScraper
    from intelligence.gov_scrapers.pta_stats import PTAStatsScraper
    from intelligence.gov_scrapers.worldbank_pak import WorldBankPakScraper
    from intelligence.exchange_scrapers.central_banks.sbp_pakistan import SBPPakistanForexScraper
    return {
        "sbp_monetary": SBPMonetaryScraper,
        "fbr_revenue": FBRRevenueScraper,
        "nepra_power": NEPRAPowerScraper,
        "ogra_prices": OGRAPricesScraper,
        "pbs_lsmi": PBSLsmiScraper,
        "pta_stats": PTAStatsScraper,
        "worldbank_pak": WorldBankPakScraper,
        "sbp_forex": SBPPakistanForexScraper,
    }[scraper_id]


def _clear_todays_dedup(scraper_id: str) -> None:
    """
    Several scrapers use INSERT OR IGNORE / a same-day file cache, so re-running
    them same-day reports rows_inserted=0 by design (dedup working, not failure) -
    the exact gotcha documented for Priorities 5 and 11 in
    docs/scraper_verification.md. Clear the relevant same-day state first so this
    test measures whether the scraper WORKS, not whether it already ran today.
    """
    from intelligence.gov_scrapers.base import CACHE_DIR
    p = CACHE_DIR / f"{scraper_id}_{date.today().isoformat()}.json"
    if p.exists():
        p.unlink()
    if scraper_id == "sbp_forex":
        _db_conn.get_db().execute("DELETE FROM pkr_forex WHERE rate_date = date('now')")
        _db_conn.get_db().commit()


@pytest.mark.parametrize("scraper_id", list(_TARGETS["gov_scrapers"].keys()))
def test_gov_scraper_success_and_fresh(scraper_id):
    cfg = _TARGETS["gov_scrapers"][scraper_id]
    _clear_todays_dedup(scraper_id)

    cls = _scraper_class(scraper_id)
    result = cls().fetch()
    assert result.status.value == "success", (
        f"{scraper_id}: {result.status} - {result.error_message}"
    )
    assert result.rows_inserted >= cfg["min_rows"], (
        f"{scraper_id}: only {result.rows_inserted} rows, expected >= {cfg['min_rows']}"
    )

    from database.scrape_log import latest_for
    row = latest_for(scraper_id)
    assert row is not None, f"{scraper_id}: no scrape_log row written"
    started = datetime.fromisoformat(row["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - started).total_seconds() / 3600
    assert age_hours <= cfg["freshness_hours"], (
        f"{scraper_id}: last run {age_hours:.1f}h ago, expected within {cfg['freshness_hours']}h"
    )
