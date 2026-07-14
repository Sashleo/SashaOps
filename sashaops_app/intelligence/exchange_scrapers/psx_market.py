"""
intelligence/exchange_scrapers/psx_market.py — PSX market data scraper.

Populates the Pakistan market tables from PSX's public endpoints:
  - psx_companies : every listed symbol (dps.psx.com.pk/symbols)
  - psx_prices    : latest per-company price (psx.com.pk/market-summary)
  - psx_index     : KSE index snapshot when present in the market summary

Runs on the exchange scheduler and on demand via the markets "Fetch Fresh Data"
button. Pure Python, never raises.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from intelligence.exchange_scrapers.base import BaseExchangeScraper, ScrapeResult, ScrapeStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(limited|ltd|company|co|corporation|corp|the|pakistan)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class PSXMarketScraper(BaseExchangeScraper):
    scraper_id = "psx_market"
    label = "PSX prices"
    category = "exchange"
    default_freshness_hours = 24
    target_success_rate = 0.0   # set from the live suite (Task 2.4)
    region_id  = "pakistan"

    def fetch(self) -> ScrapeResult:
        from intelligence.exchange_crawlers.psx import (
            get_all_psx_symbols, fetch_psx_market_summary, fetch_psx_indices,
        )
        from database.psx_data import (
            bulk_upsert_symbols, upsert_psx_price, upsert_psx_index,
        )
        import structlog
        log = structlog.get_logger()

        result = ScrapeResult(scraper_id=self.scraper_id, status=ScrapeStatus.NO_DATA)
        inserted = 0
        td = date.today().isoformat()

        # 1) Listed companies registry (reliable JSON endpoint)
        symbols = []
        try:
            symbols = get_all_psx_symbols()
            if symbols:
                inserted += bulk_upsert_symbols(symbols)
        except Exception as e:
            result.error_message = f"symbols: {e}"[:200]

        # name → symbol map for matching market-summary rows
        name2sym: dict[str, str] = {}
        for s in symbols:
            nm = _norm_name(s.get("name") or "")
            if nm:
                name2sym.setdefault(nm, s.get("symbol"))

        # 2) Market summary → per-company prices + index
        try:
            summ = fetch_psx_market_summary()
        except Exception as e:
            summ = {"error": str(e)}

        if not summ.get("error"):
            for sec in summ.get("sectors", []):
                for co in sec.get("companies", []):
                    # Prefer data-srip ticker (direct, no name matching needed)
                    sym = co.get("symbol") or self._resolve_symbol(co.get("name"), name2sym)
                    if sym and co.get("current") is not None:
                        ok = upsert_psx_price(
                            sym, td, current=co.get("current"),
                            open=co.get("open"), high=co.get("high"),
                            low=co.get("low"), ldcp=co.get("ldcp"),
                            change_pct=co.get("change"), volume=co.get("volume"),
                        )
                        if ok:
                            inserted += 1
        else:
            log.warning("psx_market: market summary failed", error=summ.get("error"))

        # KSE index — fetched from dedicated indices endpoint (dps.psx.com.pk/indices)
        # The main market-summary page moved KSE100 to a JS-rendered card, no longer in Table 0
        try:
            index_rows = fetch_psx_indices()
            for idx in index_rows:
                name = idx.get("name", "")
                if "KSE100" in name and idx.get("current"):
                    ok = upsert_psx_index(
                        "KSE100", td,
                        close_value=idx["current"],
                        change_pts=idx.get("change_pts"),
                        change_pct=idx.get("change_pct"),
                    )
                    if ok:
                        inserted += 1
                    log.info("psx_market: KSE100 index written",
                             value=idx["current"], change_pct=idx.get("change_pct"))
                    break
            else:
                log.warning("psx_market: KSE100 not found in indices response",
                            rows_received=len(index_rows))
        except Exception as ie:
            log.warning("psx_market: index fetch failed", error=str(ie)[:120])

        # Update market_cap for companies where we can compute it from
        # price × shares_outstanding (shares = net_income / EPS from psx_financials).
        mc_updated = 0
        try:
            from database.connection import get_db as _get_db
            _db = _get_db()
            # Compute market_cap = price × shares_outstanding
            # where shares = net_income_thousands × 1000 / EPS (PKR/share)
            # Join psx_prices (today's price) with psx_financials (latest annual EPS+NI)
            rows_need_mc = _db.execute(
                """SELECT p.symbol, p.current,
                          f.net_income, f.eps
                   FROM psx_prices p
                   JOIN psx_companies c ON c.symbol = p.symbol
                   JOIN (
                       SELECT symbol, net_income, eps,
                              ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY period DESC) rn
                       FROM psx_financials
                       WHERE period_type='annual' AND eps IS NOT NULL AND net_income IS NOT NULL
                   ) f ON f.symbol = p.symbol AND f.rn = 1
                   WHERE p.trade_date = ?
                     AND p.current > 0
                     AND (c.market_cap IS NULL OR c.market_cap = 0)""",
                (td,),
            ).fetchall()
            for row in rows_need_mc:
                eps        = row["eps"]
                net_income = row["net_income"]   # PKR thousands
                price      = row["current"]
                if eps and net_income and eps > 0 and net_income > 0:
                    shares     = (net_income * 1000.0) / eps   # total shares outstanding
                    market_cap = shares * price / 1_000_000    # PKR millions
                    _db.execute(
                        "UPDATE psx_companies SET market_cap=?, last_updated=? WHERE symbol=?",
                        (market_cap, _now(), row["symbol"]),
                    )
                    mc_updated += 1
            if mc_updated:
                _db.commit()
                log.info("psx_market: market_cap updated", count=mc_updated)
        except Exception as mce:
            log.warning("psx_market: market_cap update failed", error=str(mce)[:120])

        result.rows_inserted = inserted
        result.finished_at = datetime.utcnow()
        result.status = (ScrapeStatus.SUCCESS if inserted
                         else ScrapeStatus.PARSER_FAILED if result.error_message
                         else ScrapeStatus.NO_DATA)
        log.info("psx_market: scrape complete",
                 status=result.status.value, rows=inserted, error=result.error_message)
        return self.log(result)

    @staticmethod
    def _resolve_symbol(name: str, name2sym: dict) -> str | None:
        nm = _norm_name(name)
        if not nm:
            return None
        if nm in name2sym:
            return name2sym[nm]
        # prefix match either direction (handles abbreviated summary names)
        for k, sym in name2sym.items():
            if k.startswith(nm) or nm.startswith(k):
                return sym
        return None

    @staticmethod
    def _to_float(s) -> float | None:
        try:
            return float(re.sub(r"[^0-9.\-]", "", str(s)))
        except (ValueError, TypeError):
            return None
