"""
PSX exchange crawlers.

Endpoints used:
- dps.psx.com.pk/symbols              → all listed symbols + sector names (JSON)
- dps.psx.com.pk/company/{TICKER}     → board, announcements, financials, ratios (static HTML)
- psx.com.pk/market-summary           → live sector breakdown, 44 sector tables (static HTML)
- dps.psx.com.pk/download/document/{id}.pdf → PDF documents (proxied to browser)

All endpoints return static HTML / JSON — no browser required.
"""

from __future__ import annotations

import time
import random
import requests
from bs4 import BeautifulSoup

# Politeness delay between consecutive PSX page requests (seconds).
# Keeps the scraper well under PSX's rate-limit threshold.
# Set PSX_REQUEST_DELAY=0 in the environment to disable (unit tests only).
import os as _os
_REQUEST_DELAY = float(_os.environ.get("PSX_REQUEST_DELAY", "1.5"))

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
}
SYMBOLS_URL      = "https://dps.psx.com.pk/symbols"
COMPANY_URL      = "https://dps.psx.com.pk/company/{ticker}"
MARKET_SUM_URL   = "https://www.psx.com.pk/market-summary"
INDICES_URL      = "https://dps.psx.com.pk/indices"
PDF_BASE         = "https://dps.psx.com.pk"


# ── Symbol search ──────────────────────────────────────────────────────────────

def search_psx_company(query: str) -> list[dict]:
    """
    Search PSX symbols by name or ticker.
    Returns up to 20 matches as [{symbol, name, sectorName}].
    """
    try:
        r = requests.get(SYMBOLS_URL, headers=BASE_HEADERS, timeout=10)
        r.raise_for_status()
        symbols = r.json()
    except Exception:
        return []

    q = query.strip().lower()
    if not q:
        return []

    results = []
    for s in symbols:
        if not isinstance(s, dict) or s.get("isDebt"):
            continue
        name = (s.get("name") or "").lower()
        sym  = (s.get("symbol") or "").lower()
        if q in name or q in sym:
            results.append(s)

    def _rank(s: dict) -> int:
        sym  = s.get("symbol", "").lower()
        name = s.get("name", "").lower()
        if sym == q:       return 0
        if sym.startswith(q): return 1
        if name.startswith(q): return 2
        return 3

    return sorted(results, key=_rank)[:20]


def get_all_psx_symbols() -> list[dict]:
    """Return all non-debt PSX symbols. Falls back to market summary if /symbols is down."""
    try:
        r = requests.get(SYMBOLS_URL, headers=BASE_HEADERS, timeout=10)
        r.raise_for_status()
        return [s for s in r.json() if isinstance(s, dict) and not s.get("isDebt")]
    except Exception:
        pass
    return _symbols_from_market_summary()


def _symbols_from_market_summary() -> list[dict]:
    """Extract symbol list from psx.com.pk/market-summary as a fallback."""
    try:
        r = requests.get(MARKET_SUM_URL, headers=BASE_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    seen: set[str] = set()
    symbols: list[dict] = []
    for table in soup.find_all("table")[2:]:
        rows = table.find_all("tr")
        if not rows:
            continue
        header = rows[0].find_all(["td", "th"])
        sector = header[0].get_text(strip=True) if header else ""
        for row in rows[2:]:
            tds = row.find_all(["td", "th"])
            if len(tds) < 2:
                continue
            ticker = (tds[0].get("data-srip") or "").strip().upper()
            name = tds[0].get_text(strip=True)
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            symbols.append({"symbol": ticker, "name": name, "sectorName": sector})
    return symbols


# ── Company deep-fetch ─────────────────────────────────────────────────────────

def fetch_psx_company(ticker: str, fetch_types: list[str]) -> dict:
    """
    Fetch company data from dps.psx.com.pk/company/{ticker}.

    fetch_types may include:
      'board'           → Table 0: board members / company profile
      'announcements'   → Table 1: financial results announcements
      'board_meetings'  → Table 2: board meeting notices
      'dividends'       → Table 3: dividend / book closure notices
      'financials'      → Tables 4 + 5: annual and quarterly financials
      'ratios'          → Table 6: financial ratios

    Returns a dict with the requested data sections.
    """
    ticker = ticker.strip().upper()
    url    = COMPANY_URL.format(ticker=ticker)

    if _REQUEST_DELAY > 0:
        time.sleep(_REQUEST_DELAY + random.uniform(0, 0.5))

    try:
        r = requests.get(url, headers=BASE_HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e), "ticker": ticker}

    soup   = BeautifulSoup(r.text, "lxml")
    tables = soup.find_all("table")
    result: dict = {"ticker": ticker, "url": url}

    # Table 0 — Board / Company Profile
    if tables and "board" in fetch_types:
        board = []
        for row in tables[0].find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) >= 2 and cells[0]:
                board.append({"name": cells[0], "role": cells[1]})
        result["board"] = board

    # Table 1 — Financial results announcements
    if len(tables) > 1 and "announcements" in fetch_types:
        result["financial_announcements"] = _parse_ann_table(tables[1])

    # Table 2 — Board meeting notices
    if len(tables) > 2 and "board_meetings" in fetch_types:
        result["board_meetings"] = _parse_ann_table(tables[2])

    # Table 3 — Dividend / book closure notices
    if len(tables) > 3 and "dividends" in fetch_types:
        result["dividend_notices"] = _parse_ann_table(tables[3])

    # Tables 4 + 5 — Annual and quarterly financials
    if "financials" in fetch_types:
        if len(tables) > 4:
            result["annual_financials"]    = _parse_financial_table(tables[4])
        if len(tables) > 5:
            result["quarterly_financials"] = _parse_financial_table(tables[5])

    # Table 6 — Ratios
    if len(tables) > 6 and "ratios" in fetch_types:
        result["ratios"] = _parse_financial_table(tables[6])

    return result


def _parse_ann_table(table) -> list[dict]:
    """Parse announcement table → [{date, title, pdf_url}]."""
    rows  = table.find_all("tr")
    items = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        date_str = cells[0].get_text(strip=True)
        title    = cells[1].get_text(strip=True)
        pdf_url  = None
        if len(cells) > 2:
            for a in cells[2].find_all("a"):
                href = a.get("href", "")
                if href and "javascript" not in href.lower():
                    pdf_url = (PDF_BASE + href) if href.startswith("/") else href
                    break
        if date_str or title:
            items.append({"date": date_str, "title": title, "pdf_url": pdf_url})
    return items


def _parse_financial_table(table) -> dict:
    """Parse financial/ratio table → {metric: {period: value}}."""
    rows = table.find_all("tr")
    if not rows:
        return {}
    header_cells = rows[0].find_all(["td", "th"])
    periods      = [c.get_text(strip=True) for c in header_cells[1:]]

    data: dict = {}
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        metric = cells[0].get_text(strip=True)
        if not metric:
            continue
        values: dict = {}
        for j, cell in enumerate(cells[1:]):
            if j >= len(periods):
                break
            raw = cell.get_text(strip=True).replace(",", "").replace("(", "-").replace(")", "")
            try:
                values[periods[j]] = float(raw)
            except (ValueError, TypeError):
                values[periods[j]] = cell.get_text(strip=True) or None
        data[metric] = values
    return data


# ── Index snapshot (dps.psx.com.pk/indices) ───────────────────────────────────

def fetch_psx_indices() -> list[dict]:
    """
    Fetch all PSX index values from dps.psx.com.pk/indices.

    Returns a list of dicts:
      [{"name": "KSE100", "current": 172399.90, "change_pts": 2696.30,
        "change_pct": 1.59, "high": ..., "low": ...}, ...]

    The first row is always KSE-100 when available.
    Returns [] on any error.
    """
    def _fv(s: str) -> float | None:
        try:
            return float(s.replace(",", "").replace("+", "").strip())
        except (ValueError, TypeError):
            return None

    try:
        r = requests.get(INDICES_URL, headers=BASE_HEADERS, timeout=12)
        r.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    if not table:
        return []

    indices = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # skip header row
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 4 or not cells[0]:
            continue
        # Expected columns: Index, High, Low, Current, Change, %Change
        indices.append({
            "name":       cells[0].upper().replace("-", "").replace(" ", ""),
            "high":       _fv(cells[1]) if len(cells) > 1 else None,
            "low":        _fv(cells[2]) if len(cells) > 2 else None,
            "current":    _fv(cells[3]) if len(cells) > 3 else None,
            "change_pts": _fv(cells[4]) if len(cells) > 4 else None,
            "change_pct": _fv(cells[5]) if len(cells) > 5 else None,
        })
    return indices


# ── Market summary / sector breakdown ─────────────────────────────────────────

def fetch_psx_market_summary() -> dict:
    """
    Parse psx.com.pk/market-summary.

    Returns:
      {
        "market_status": {...},   # from Table 0
        "sectors": [              # from Tables 2+
          {
            "sector": "CEMENT",
            "count": 19,
            "companies": [{name, ldcp, open, high, low, current, change, volume}]
          }, ...
        ]
      }
    """
    try:
        r = requests.get(MARKET_SUM_URL, headers=BASE_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e)}

    soup   = BeautifulSoup(r.text, "lxml")
    tables = soup.find_all("table")

    # Table 0: Exchange status
    market_status: dict = {}
    if tables:
        cells = tables[0].find_all(["td", "th"])
        for c in cells:
            txt = c.get_text(strip=True)
            if ":" in txt:
                k, _, v = txt.partition(":")
                market_status[k.strip()] = v.strip()

    # Tables 2+: Sector breakdowns
    sectors = []
    for t in tables[2:]:
        rows = t.find_all("tr")
        if not rows:
            continue
        first_row_cells = rows[0].find_all(["td", "th"])
        if not first_row_cells:
            continue
        sector = first_row_cells[0].get_text(strip=True)
        if not sector:
            continue

        companies = []
        for row in rows[2:]:
            tds = row.find_all(["td", "th"])
            if len(tds) < 6:
                continue
            # Extract ticker from data-srip attribute on the name cell (most reliable)
            name_td  = tds[0]
            ticker   = (name_td.get("data-srip") or "").strip().upper() or None
            name_txt = name_td.get_text(strip=True)
            if not name_txt:
                continue
            cells = [td.get_text(strip=True) for td in tds]

            def _fv(s: str) -> float | None:
                try:
                    return float(s.replace(",", ""))
                except (ValueError, TypeError):
                    return None

            companies.append({
                "name":    name_txt,
                "symbol":  ticker,
                "ldcp":    _fv(cells[1]) if len(cells) > 1 else None,
                "open":    _fv(cells[2]) if len(cells) > 2 else None,
                "high":    _fv(cells[3]) if len(cells) > 3 else None,
                "low":     _fv(cells[4]) if len(cells) > 4 else None,
                "current": _fv(cells[5]) if len(cells) > 5 else None,
                "change":  _fv(cells[6]) if len(cells) > 6 else None,
                "volume":  _fv(cells[7]) if len(cells) > 7 else None,
            })

        if companies:
            # Compute sector-level aggregates
            changes = [c["change"] for c in companies if c.get("change") is not None]
            vols    = [c["volume"]  for c in companies if c.get("volume")  is not None]
            avg_change = round(sum(changes) / len(changes), 2) if changes else None
            total_vol  = int(sum(vols)) if vols else None
            sectors.append({
                "sector":     sector,
                "count":      len(companies),
                "companies":  companies,
                "avg_change": avg_change,
                "total_vol":  total_vol,
            })

    return {"market_status": market_status, "sectors": sectors}
