"""
intelligence/stocks.py — PSX-native market data helpers.

All data comes from the local PSX database (psx_index, psx_companies, psx_prices).
No external API calls, no yfinance dependency.
"""

from typing import Optional, Tuple, List
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()


@dataclass
class IndexData:
    ticker: str
    name: str
    current_price: Optional[float] = None
    change_pct_1d: Optional[float] = None
    change_pct_1w: Optional[float] = None
    change_pct_1m: Optional[float] = None
    change_pct_ytd: Optional[float] = None
    volume: Optional[int] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    currency: str = "PKR"
    error: Optional[str] = None
    is_partial: bool = False


@dataclass
class StockData:
    ticker: str
    company_name: str
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    change_pct_1d: Optional[float] = None
    change_pct_1w: Optional[float] = None
    change_pct_1m: Optional[float] = None
    revenue_ttm: Optional[float] = None
    ebitda: Optional[float] = None
    debt_to_equity: Optional[float] = None
    price_to_book: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    currency: str = "PKR"
    exchange: str = "PSX"
    analyst_rating: str = ""
    error: Optional[str] = None
    is_partial: bool = False


@dataclass
class RegionMarketData:
    region_id: str
    indices: List[IndexData] = field(default_factory=list)
    fetch_duration_ms: int = 0
    has_partial_data: bool = False
    error: Optional[str] = None


def fetch_region_market_data(region_id: str) -> Tuple[RegionMarketData, Optional[str]]:
    """
    Return KSE100 index data from the local PSX database.
    Reads from psx_index (populated by the PSX exchange scraper).
    Never raises — returns (data, error_or_none).
    """
    from database.exchange_data import get_latest_index, get_index_history

    result = RegionMarketData(region_id=region_id)

    idx = get_latest_index()
    if not idx or idx.get("close_value") is None:
        result.error = "PSX index data not yet available - run the PSX scraper"
        result.has_partial_data = True
        return result, result.error

    # Build % changes from history
    history = get_index_history(days=260)
    closes  = [r["close_price"] for r in history if r.get("close_price")]

    def _pct(n_days: int) -> Optional[float]:
        if len(closes) < n_days + 1:
            return None
        last  = closes[-1]
        prior = closes[-(n_days + 1)]
        return round((last / prior - 1) * 100, 2) if prior else None

    ytd = None
    if len(closes) >= 2:
        ytd = round((closes[-1] / closes[0] - 1) * 100, 2) if closes[0] else None

    index_data = IndexData(
        ticker       = idx.get("index_code", "KSE100"),
        name         = idx.get("index_code", "KSE-100"),
        current_price = float(idx["close_value"]),
        change_pct_1d = idx.get("change_pct"),
        change_pct_1w = _pct(5),
        change_pct_1m = _pct(22),
        change_pct_ytd = ytd,
        currency     = "PKR",
    )
    result.indices.append(index_data)
    return result, None


def fetch_stock_data(ticker: str) -> Tuple[Optional[StockData], Optional[str]]:
    """
    Fetch PSX company data from the local database.
    Reads from psx_companies (profile/sector/P-E) and psx_prices (price history).
    Never raises — returns (data, error_or_none).
    """
    from database.exchange_data import (
        get_latest_company_price, get_company_price_history, search_company
    )

    ticker = ticker.upper().strip()

    price_row    = get_latest_company_price(ticker)
    company_row  = search_company(ticker) or {}
    history      = get_company_price_history(ticker, days=260)
    closes       = [r["close_price"] for r in history if r.get("close_price")]

    if not price_row and not company_row:
        return None, f"No PSX data for {ticker}"

    def _pct(n: int) -> Optional[float]:
        if len(closes) < n + 1:
            return None
        last, prior = closes[-1], closes[-(n + 1)]
        return round((last / prior - 1) * 100, 2) if prior else None

    high_52w = max(closes[-260:]) if len(closes) >= 5 else None
    low_52w  = min(closes[-260:]) if len(closes) >= 5 else None

    data = StockData(
        ticker        = ticker,
        company_name  = company_row.get("company_name", ticker),
        current_price = price_row.get("current") if price_row else None,
        market_cap    = company_row.get("market_cap"),
        pe_ratio      = company_row.get("pe_ratio"),
        change_pct_1d = price_row.get("change_pct") if price_row else None,
        change_pct_1w = _pct(5),
        change_pct_1m = _pct(22),
        high_52w      = high_52w,
        low_52w       = low_52w,
        currency      = "PKR",
        exchange      = "PSX",
    )
    return data, None


# ── PSX sector peer benchmarks ────────────────────────────────────────────────
# Maps keyword_engine industry tags → PSX sector_name LIKE patterns.
# Used by fetch_psx_sector_peers() to find local comparables.
_INDUSTRY_TO_PSX_SECTOR: dict[str, list[str]] = {
    "cement":     ["cement"],
    "textile":    ["textile"],
    "banking":    ["banking"],
    "oil_gas":    ["oil", "gas"],
    "energy":     ["power generation", "power distribution"],
    "telecom":    ["technology", "telecom", "communication"],
    "pharma":     ["pharmaceutical", "pharma"],
    "fmcg":       ["food", "personal care"],
    "fertilizer": ["fertilizer"],
    "auto":       ["automobile"],
    "chemicals":  ["chemical"],
    "steel":      ["steel"],
    "sugar":      ["sugar"],
    "insurance":  ["insurance"],
    "paper":      ["paper"],
    "glass":      ["glass", "ceramic"],
    "modaraba":   ["modaraba"],
}


def fetch_psx_sector_peers(industry: str) -> Optional[dict]:
    """
    Compute PSX sector peer P/E medians from local psx_companies data.
    Returns {industry, peers, sector_median} or None if no data.
    Persists nothing — purely a read query.
    """
    from database.connection import get_db

    patterns = _INDUSTRY_TO_PSX_SECTOR.get(industry, [industry])
    if not patterns:
        return None

    db = get_db()
    conditions = " OR ".join(f"LOWER(c.sector_name) LIKE ?" for _ in patterns)
    params     = [f"%{p}%" for p in patterns]

    # psx_companies has no pe_ratio column. Derive P/E = price / EPS from the latest
    # annual EPS in psx_financials where available.
    rows = db.execute(
        f"""
        SELECT c.symbol, c.company_name, c.sector_name, c.market_cap,
               p.current AS price, p.change_pct,
               f.eps AS eps
        FROM psx_companies c
        LEFT JOIN (
            SELECT symbol, current, change_pct
            FROM psx_prices
            WHERE (symbol, trade_date) IN (
                SELECT symbol, MAX(trade_date) FROM psx_prices GROUP BY symbol
            )
        ) p ON c.symbol = p.symbol
        LEFT JOIN (
            SELECT symbol, eps
            FROM psx_financials
            WHERE period_type = 'annual' AND eps IS NOT NULL
              AND (symbol, period) IN (
                SELECT symbol, MAX(period) FROM psx_financials
                WHERE period_type = 'annual' AND eps IS NOT NULL GROUP BY symbol
              )
        ) f ON c.symbol = f.symbol
        WHERE c.is_debt = 0 AND ({conditions})
        ORDER BY c.market_cap DESC
        LIMIT 20
        """,
        params,
    ).fetchall()

    if not rows:
        return None

    peers = []
    pes   = []
    for r in rows:
        price = r["price"]
        eps   = r["eps"]
        pe    = (price / eps) if (price and eps and eps > 0) else None
        rec = {
            "ticker":    r["symbol"],
            "name":      r["company_name"],
            "pe_ratio":  round(pe, 2) if pe and pe > 0 else None,
            "ev_ebitda": None,
            "net_margin":     None,
            "revenue_growth": None,
        }
        peers.append(rec)
        if pe and pe > 0:
            pes.append(pe)

    def _median(xs):
        xs = sorted(xs)
        return round(xs[len(xs) // 2], 2) if xs else None

    return {
        "industry":      industry,
        "peers":         peers,
        "sector_median": {"pe": _median(pes), "ev_ebitda": None},
        "source":        "PSX",
    }


def format_market_data_text(region_data: RegionMarketData) -> str:
    """Format PSX market data as text for AI prompt injection."""
    if not region_data.indices:
        return "PSX market data not yet available."

    lines = ["MARKET DATA - KSE (Pakistan Stock Exchange)\n"]
    for idx in region_data.indices:
        if idx.error or idx.current_price is None:
            lines.append(f"{idx.name}: Data unavailable ({idx.error or 'no price'})")
        else:
            chg_str = f" | 1D: {idx.change_pct_1d:+.1f}%" if idx.change_pct_1d is not None else ""
            ytd_str = f" | YTD: {idx.change_pct_ytd:+.1f}%" if idx.change_pct_ytd is not None else ""
            lines.append(f"{idx.name}: {idx.current_price:,.2f} {idx.currency}{chg_str}{ytd_str}")

    return "\n".join(lines)


def format_stock_data_text(stock: StockData) -> str:
    """Format PSX stock data as text for AI prompt injection."""
    if stock.error:
        return f"Stock data for {stock.ticker} unavailable: {stock.error}"

    def _fmt(v, fmt=".2f", suffix=""):
        return f"{v:{fmt}}{suffix}" if v is not None else "N/A"

    def _humanize(v):
        if v is None:
            return "N/A"
        v = float(v)
        if abs(v) >= 1e12: return f"{v/1e12:.1f}T PKR"
        if abs(v) >= 1e9:  return f"{v/1e9:.1f}B PKR"
        if abs(v) >= 1e6:  return f"{v/1e6:.1f}M PKR"
        if abs(v) >= 1e3:  return f"{v/1e3:.1f}K PKR"
        return f"{v:.2f} PKR"

    lines = [
        f"PSX STOCK: {stock.company_name} ({stock.ticker})",
        f"Price: {_fmt(stock.current_price)} PKR",
        f"1D: {_fmt(stock.change_pct_1d, '+.1f', '%')} | 1W: {_fmt(stock.change_pct_1w, '+.1f', '%')} | 1M: {_fmt(stock.change_pct_1m, '+.1f', '%')}",
        f"52W High: {_fmt(stock.high_52w)} | Low: {_fmt(stock.low_52w)}",
        f"Market Cap: {_humanize(stock.market_cap)} | P/E: {_fmt(stock.pe_ratio)}",
    ]
    return "\n".join(lines)
