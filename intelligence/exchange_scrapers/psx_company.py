"""
intelligence/exchange_scrapers/psx_company.py - Full PSX company page scraper.

Scrapes EVERYTHING the PSX company page displays, per ticker:
  identity      name, sector
  trading       price, change, change_pct, open/high/low (REG panel), volume,
                close, LDCP, total trades, bid/ask, day range, 52-week range
  equity        market cap, shares outstanding, free float (+ %)   <- market cap here
  ratios        P/E (TTM), 1-year change, YTD change
  profile       business description, CEO, chairman, company secretary,
                address, website, registrar
  tables        announcements (date/title/PDF), financials (annual + quarterly),
                payouts (dividends)

Method: html_bs4. The page is server-rendered plain HTML, so BeautifulSoup + lxml
is sufficient (no Playwright needed).

Parsing gotchas (verified 2026-07, see docs/scraper_verification.md):
  - OHLC/volume sit inside tabbed panels (REG / DFC / CSF / ODL). Only the FIRST
    panel (REG = regular market) holds real values; the others are zeros. Always
    scope to `div.quote__stats div.tabs__panel` (first) or you silently read 0s.
  - "Market Cap (000's)" is in THOUSANDS. Multiply by 1000 to store PKR.
  - Delisted companies (e.g. ENGRO) still serve a page, but it is a frozen
    snapshot with a stale "As of" date. They must not be treated as live prices.

fetch() is the nightly BULK job over every ticker in psx_companies, so market cap
is populated for every listed company, not only the analysed ones.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

import structlog

from intelligence.exchange_scrapers.base import BaseExchangeScraper, ScrapeResult, ScrapeStatus

log = structlog.get_logger()

BASE_URL = "https://dps.psx.com.pk/company/{ticker}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _num(s: Optional[str]) -> Optional[float]:
    """First number in a string, commas stripped. None when absent."""
    if not s:
        return None
    m = re.search(r"-?[\d,]+\.?\d*", s)
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def _range(s: Optional[str]):
    """'297.01 - 307.44' (any dash) -> (297.01, 307.44)."""
    if not s:
        return (None, None)
    nums = re.findall(r"[\d,]+\.?\d*", s)
    if len(nums) >= 2:
        try:
            return (float(nums[0].replace(",", "")), float(nums[1].replace(",", "")))
        except ValueError:
            pass
    return (None, None)


class PSXCompanyProfileScraper(BaseExchangeScraper):
    scraper_id = "psx_company_profile"
    label = "PSX company profile"
    category = "exchange"
    default_freshness_hours = 24
    target_success_rate = 0.0   # set from the live suite (Task 2.4)

    # ── One company ───────────────────────────────────────────────────────────
    def parse(self, ticker: str) -> dict:
        """Fetch + parse one company page into a flat dict. Raises on network error."""
        import requests
        from bs4 import BeautifulSoup

        url = BASE_URL.format(ticker=ticker.upper())
        r = requests.get(url, headers=_HEADERS, timeout=25)
        r.raise_for_status()
        s = BeautifulSoup(r.text, "lxml")

        d: dict = {"symbol": ticker.upper(), "source_url": url}

        # Identity + price
        name = s.select_one("div.quote__name")
        sect = s.select_one("div.quote__sector")
        price_el = s.select_one("div.quote__price")
        d["name"] = name.get_text(" ", strip=True) if name else None
        d["sector"] = sect.get_text(strip=True) if sect else None
        if price_el:
            ptxt = price_el.get_text(" ", strip=True)
            d["price"] = _num(ptxt)
            pctm = re.search(r"\(([-\d.]+)%\)", ptxt)
            d["change_pct"] = float(pctm.group(1)) if pctm else None

        # Delisted / stale snapshot guard
        full = s.get_text(" ", strip=True)
        d["delisted"] = "DELISTED" in (d.get("name") or "").upper() or "DELISTED" in full[:400].upper()
        asof = re.search(r"As of\s+([^^]{5,40})", full)
        d["as_of"] = asof.group(1).strip() if asof else None

        # ── Label/value stats. REG panel only for OHLC (see gotchas). ─────────
        def _panel_stats() -> dict:
            out = {}
            panel = s.select_one("div.quote__stats div.tabs__panel")   # first = REG
            if not panel:
                return out
            for it in panel.select("div.stats_item"):
                lab = it.select_one("div.stats_label")
                val = it.select_one("div.stats_value")
                if lab and val:
                    out[lab.get_text(" ", strip=True)] = val.get_text(" ", strip=True)
            return out

        def _all_stats() -> dict:
            out = {}
            for it in s.select("div.stats_item"):
                lab = it.select_one("div.stats_label")
                val = it.select_one("div.stats_value")
                if lab and val:
                    k = lab.get_text(" ", strip=True)
                    out.setdefault(k, val.get_text(" ", strip=True))
            return out

        reg = _panel_stats()
        allst = _all_stats()

        d["open"]   = _num(reg.get("Open"))
        d["high"]   = _num(reg.get("High"))
        d["low"]    = _num(reg.get("Low"))
        d["volume"] = _num(reg.get("Volume"))

        d["ldcp"]         = _num(allst.get("LDCP"))
        d["close"]        = _num(allst.get("Close"))
        d["total_trades"] = _num(allst.get("Total Trades"))
        d["bid_price"]    = _num(allst.get("Bid Price"))
        d["ask_price"]    = _num(allst.get("Ask Price"))

        for k, v in allst.items():
            kl = k.lower()
            if kl.startswith("p/e"):
                d["pe_ttm"] = _num(v)
            elif "52-week range" in kl:
                d["week52_low"], d["week52_high"] = _range(v)
            elif kl.startswith("day range"):
                d["day_low"], d["day_high"] = _range(v)
            elif "market cap" in kl:
                mc = _num(v)
                # Page prints thousands: "Market Cap (000's)".
                d["market_cap"] = mc * 1000 if mc is not None else None
            elif kl == "shares":
                d["shares_outstanding"] = _num(v)

        # Gotcha: "Free Float" is printed TWICE (absolute count, then percentage),
        # so a first-wins dict silently drops the percentage. Scan every stats_item.
        for it in s.select("div.stats_item"):
            lab = it.select_one("div.stats_label")
            val = it.select_one("div.stats_value")
            if not (lab and val):
                continue
            if lab.get_text(" ", strip=True).lower().startswith("free float"):
                raw = val.get_text(" ", strip=True)
                if "%" in raw:
                    d["free_float_pct"] = _num(raw)
                elif d.get("free_float") is None:
                    d["free_float"] = _num(raw)

        # ── Company profile (business description, key people, address) ───────
        prof = s.select_one("div.company__profile")
        if prof:
            ptxt = prof.get_text(" | ", strip=True)

            def _between(a: str, b: Optional[str]) -> Optional[str]:
                pat = re.escape(a) + r"\s*\|\s*(.+?)(?:\s*\|\s*" + (re.escape(b) if b else "$") + ")"
                m = re.search(pat, ptxt, re.S | re.I)
                return m.group(1).strip(" |") if m else None

            d["business_description"] = _between("BUSINESS DESCRIPTION", "KEY PEOPLE")
            d["address"] = _between("ADDRESS", "WEBSITE")
            d["website"] = _between("WEBSITE", "REGISTRAR")
            d["registrar"] = _between("REGISTRAR", None)

            # KEY PEOPLE render as "Name | Role" pairs.
            people = {}
            kp = re.search(r"KEY PEOPLE\s*\|(.+?)\|\s*ADDRESS", ptxt, re.S | re.I)
            if kp:
                parts = [x.strip() for x in kp.group(1).split("|") if x.strip()]
                for i in range(0, len(parts) - 1, 2):
                    people[parts[i + 1].lower()] = parts[i]
            d["key_people"] = people
            d["ceo"] = next((v for k, v in people.items() if "ceo" in k or "chief exec" in k), None)
            d["chairman"] = next((v for k, v in people.items() if "chair" in k), None)
            d["company_secretary"] = next((v for k, v in people.items() if "secretar" in k), None)

        # ── Announcements (Date | Title | Document) ───────────────────────────
        anns = []
        for tb in s.find_all("table"):
            hdr = [th.get_text(" ", strip=True).lower() for th in tb.find_all("th")]
            if not ("date" in hdr and "title" in hdr):
                continue
            for tr in tb.select("tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 2:
                    continue
                a = tr.find("a", href=True)
                anns.append({
                    "date": tds[0].get_text(" ", strip=True),
                    "title": tds[1].get_text(" ", strip=True),
                    "url": a["href"] if a else None,
                })
        d["announcements"] = anns[:40]

        # ── Financial + payout tables (year/quarter columns) ──────────────────
        tables = []
        for tb in s.find_all("table"):
            hdr = [th.get_text(" ", strip=True) for th in tb.find_all("th")]
            if not hdr or ("Date" in hdr and "Title" in hdr):
                continue
            rows = []
            for tr in tb.select("tbody tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append({"header": hdr, "rows": rows})
        d["tables"] = tables

        return d

    # ── Persist ───────────────────────────────────────────────────────────────
    def persist(self, d: dict) -> int:
        from database.connection import get_db
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        sym = d["symbol"]
        wrote = 0

        # psx_companies: identity + equity profile (market cap for EVERY ticker)
        db.execute(
            """INSERT INTO psx_companies (symbol, company_name, sector_name, last_updated)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                 company_name = COALESCE(excluded.company_name, psx_companies.company_name),
                 sector_name  = COALESCE(excluded.sector_name,  psx_companies.sector_name),
                 last_updated = excluded.last_updated""",
            (sym, d.get("name"), d.get("sector"), now),
        )
        db.execute(
            """UPDATE psx_companies SET
                 market_cap = COALESCE(?, market_cap),
                 shares_outstanding = COALESCE(?, shares_outstanding),
                 free_float = COALESCE(?, free_float),
                 free_float_pct = COALESCE(?, free_float_pct),
                 pe_ttm = COALESCE(?, pe_ttm),
                 week52_high = COALESCE(?, week52_high),
                 week52_low = COALESCE(?, week52_low),
                 ceo = COALESCE(?, ceo),
                 chairman = COALESCE(?, chairman),
                 company_secretary = COALESCE(?, company_secretary),
                 address = COALESCE(?, address),
                 website = COALESCE(?, website),
                 registrar = COALESCE(?, registrar),
                 business_description = COALESCE(?, business_description),
                 profile_fetched_at = ?
               WHERE symbol = ?""",
            (d.get("market_cap"), d.get("shares_outstanding"), d.get("free_float"),
             d.get("free_float_pct"), d.get("pe_ttm"), d.get("week52_high"), d.get("week52_low"),
             d.get("ceo"), d.get("chairman"), d.get("company_secretary"),
             d.get("address"), d.get("website"), d.get("registrar"),
             d.get("business_description"), now, sym),
        )
        wrote += 1

        # psx_prices: only for live (non-delisted) tickers with a price
        if d.get("price") and not d.get("delisted"):
            db.execute(
                """INSERT INTO psx_prices
                     (symbol, trade_date, open, high, low, current, ldcp, change_pct,
                      volume, close, total_trades, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(symbol, trade_date) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low,
                     current=excluded.current, ldcp=excluded.ldcp,
                     change_pct=excluded.change_pct, volume=excluded.volume,
                     close=excluded.close, total_trades=excluded.total_trades,
                     fetched_at=excluded.fetched_at""",
                (sym, date.today().isoformat(), d.get("open"), d.get("high"), d.get("low"),
                 d.get("price"), d.get("ldcp"), d.get("change_pct"), d.get("volume"),
                 d.get("close"), d.get("total_trades"), now),
            )
            wrote += 1

        # psx_announcements
        for a in d.get("announcements") or []:
            if not a.get("title"):
                continue
            try:
                db.execute(
                    """INSERT OR IGNORE INTO psx_announcements
                         (symbol, ann_type, ann_date, title, document_url, fetched_at)
                       VALUES (?,?,?,?,?,?)""",
                    (sym, "company", a.get("date"), a.get("title"), a.get("url"), now),
                )
                wrote += 1
            except Exception:
                pass

        db.commit()
        return wrote

    # ── Contract ──────────────────────────────────────────────────────────────
    def per_target(self, target_id: str) -> ScrapeResult:
        r = ScrapeResult(scraper_id=self.scraper_id, status=ScrapeStatus.NO_DATA,
                         method_used="html_bs4",
                         source_url=BASE_URL.format(ticker=target_id.upper()))
        try:
            d = self.parse(target_id)
        except Exception as e:
            return self.log(r.finish(ScrapeStatus.UNREACHABLE, error_message=str(e)[:200]))

        if not d.get("name"):
            return self.log(r.finish(ScrapeStatus.PARSER_FAILED,
                                     error_message="no company name on page"))
        try:
            rows = self.persist(d)
        except Exception as e:
            return self.log(r.finish(ScrapeStatus.PARSER_FAILED, error_message=str(e)[:200]))

        r.rows_inserted = rows
        r.artifacts = {"data": d}
        got_core = bool(d.get("market_cap") and d.get("sector") and d.get("price"))
        return self.log(r.finish(ScrapeStatus.SUCCESS if got_core else ScrapeStatus.PARTIAL))

    def fetch(self) -> ScrapeResult:
        """Nightly BULK job: every ticker in psx_companies (so market cap is
        populated for every listed company, not just analysed ones)."""
        from database.connection import get_db

        r = ScrapeResult(scraper_id=self.scraper_id, status=ScrapeStatus.NO_DATA,
                         method_used="html_bs4", source_url=BASE_URL.format(ticker="*"))
        try:
            tickers = [row[0] for row in get_db().execute(
                "SELECT symbol FROM psx_companies WHERE COALESCE(is_debt,0)=0 ORDER BY symbol"
            ).fetchall()]
        except Exception as e:
            return self.log(r.finish(ScrapeStatus.PARSER_FAILED, error_message=str(e)[:200]))

        if not tickers:
            return self.log(r.finish(ScrapeStatus.NO_DATA,
                                     error_message="psx_companies is empty; run psx_market first"))

        import time
        ok = failed = 0
        for t in tickers:
            try:
                d = self.parse(t)
                if d.get("name"):
                    self.persist(d)
                    ok += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            time.sleep(0.25)   # be polite: ~750 pages, do not hammer PSX

        r.rows_inserted = ok
        r.rows_failed = failed
        r.artifacts = {"tickers": len(tickers), "ok": ok, "failed": failed}
        if ok == 0:
            status = ScrapeStatus.PARSER_FAILED
        elif failed == 0:
            status = ScrapeStatus.SUCCESS
        else:
            status = ScrapeStatus.PARTIAL
        log.info("psx_company_profile bulk complete", total=len(tickers), ok=ok, failed=failed)
        return self.log(r.finish(status))
