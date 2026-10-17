"""
database/exchange_data.py — PSX-native query functions.

Pakistan-only. All queries target the PSX/forex tables (psx_index, psx_prices,
psx_companies, pkr_forex). Legacy region_id / exchange_id parameters are accepted
for call-site compatibility but ignored — there is exactly one market (PSX).

Used by:
- routers/dashboard.py     : market snapshot
- routers/psx.py           : PSX analyser
"""

from typing import Optional, List
from datetime import date, timedelta

from database.connection import get_db


def _date_from_period(period: str):
    """Convert a period code (7d/30d/90d/ytd/1y) to (from_date_str, to_date_str)."""
    today = date.today()
    to_date = today.isoformat()
    if period == "7d":
        from_date = (today - timedelta(days=7)).isoformat()
    elif period == "90d":
        from_date = (today - timedelta(days=90)).isoformat()
    elif period == "ytd":
        from_date = today.replace(month=1, day=1).isoformat()
    elif period == "1y":
        from_date = (today - timedelta(days=365)).isoformat()
    else:  # default 30d
        from_date = (today - timedelta(days=30)).isoformat()
    return from_date, to_date


# ── PKR Forex ─────────────────────────────────────────────────────────────────

def get_latest_forex_rates(region_id: Optional[str] = None,
                           quote_currencies: Optional[List[str]] = None) -> List[dict]:
    """Return the most recent PKR forex rows for the given quote currencies."""
    db = get_db()
    if quote_currencies is None:
        quote_currencies = ["USD", "EUR", "GBP", "JPY", "CNY", "SAR", "AED"]

    placeholders = ",".join("?" * len(quote_currencies))
    rows = db.execute(
        f"""SELECT f.*
            FROM pkr_forex f
            INNER JOIN (
                SELECT quote_currency, MAX(rate_date) AS max_date
                FROM pkr_forex
                WHERE quote_currency IN ({placeholders})
                GROUP BY quote_currency
            ) latest
              ON f.quote_currency = latest.quote_currency
             AND f.rate_date = latest.max_date
            ORDER BY f.quote_currency""",
        tuple(quote_currencies),
    ).fetchall()
    # Normalise to the legacy shape callers expect
    out = []
    for r in rows:
        d = dict(r)
        d.setdefault("base_currency", "PKR")
        d.setdefault("central_bank", d.get("source", "SBP"))
        d.setdefault("rate_type", "mid")
        out.append(d)
    return out


def get_forex_rate_history(region_id: Optional[str] = None,
                           quote_currency: str = "USD", days: int = 30) -> List[dict]:
    """Return daily PKR rates for a currency pair over the last N days."""
    db = get_db()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = db.execute(
        """SELECT rate_date, rate, source
           FROM pkr_forex
           WHERE quote_currency = ? AND rate_date >= ?
           ORDER BY rate_date ASC""",
        (quote_currency, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def get_forex_summary_text(region_id: Optional[str] = None) -> str:
    """Format recent PKR forex rates as text for AI prompt injection."""
    rates = get_latest_forex_rates(quote_currencies=["USD", "EUR", "GBP", "CNY", "SAR", "AED"])
    if not rates:
        return "PKR exchange rate data not yet available."

    lines = ["PKR FOREX RATES (PKR per 1 unit) — source: SBP / ExchangeRate-API\n"]
    for r in rates:
        lines.append(
            f"  PKR/{r['quote_currency']}: {r['rate']:.2f}  ({r.get('rate_date', '')})"
        )
    return "\n".join(lines)


def get_forex_range(region_id: Optional[str], quote_currency: str,
                    from_date: str, to_date: str) -> List[dict]:
    """Return daily PKR rates for a currency pair between two dates."""
    db = get_db()
    rows = db.execute(
        """SELECT rate_date, rate, source
           FROM pkr_forex
           WHERE quote_currency = ? AND rate_date >= ? AND rate_date <= ?
           ORDER BY rate_date ASC""",
        (quote_currency, from_date, to_date),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.setdefault("base_currency", "PKR")
        d.setdefault("rate_type", "mid")
        d.setdefault("central_bank", d.get("source", "SBP"))
        out.append(d)
    return out


# ── PSX Index ─────────────────────────────────────────────────────────────────

def get_latest_index(region_id: Optional[str] = None) -> Optional[dict]:
    """Return the most recent KSE100 index row."""
    db = get_db()
    row = db.execute(
        """SELECT * FROM psx_index
           ORDER BY trade_date DESC, id DESC
           LIMIT 1""",
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    # Map to legacy field names used by templates
    d["close_price"] = d.get("close_value")
    d["index_name"]  = d.get("index_code")
    d.setdefault("source", "PSX")
    return d


def get_index_history(region_id: Optional[str] = None, days: int = 30) -> List[dict]:
    """Return daily KSE index close values for the last N days."""
    db = get_db()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = db.execute(
        """SELECT trade_date, close_value AS close_price, change_pct,
                  index_code AS index_name
           FROM psx_index
           WHERE trade_date >= ? AND close_value IS NOT NULL
           ORDER BY trade_date ASC""",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_index_summary_text(region_id: Optional[str] = None) -> str:
    """Format KSE index data as text for AI prompt injection."""
    idx = get_latest_index()
    if not idx or idx.get("close_value") is None:
        return "PSX index data not yet available."
    change_str = f"  ({idx['change_pct']:+.2f}%)" if idx.get("change_pct") is not None else ""
    return (
        f"PSX MARKET INDEX\n"
        f"  {idx['index_code']}: {idx['close_value']:,.2f}{change_str}  [{idx['trade_date']}]\n"
        f"  Source: PSX"
    )


def get_index_range(region_id: Optional[str], from_date: str, to_date: str) -> List[dict]:
    """Return daily KSE index rows between two dates (ascending)."""
    db = get_db()
    rows = db.execute(
        """SELECT trade_date, close_value AS close_price, change_pct,
                  volume, index_code AS index_name, index_code
           FROM psx_index
           WHERE trade_date >= ? AND trade_date <= ? AND close_value IS NOT NULL
           ORDER BY trade_date ASC""",
        (from_date, to_date),
    ).fetchall()
    return [dict(r) for r in rows]


def get_period_summary(region_id: Optional[str], from_date: str, to_date: str) -> dict:
    """Compute summary stats over a KSE index date range."""
    rows = get_index_range(region_id, from_date, to_date)
    if not rows:
        return {}
    closes = [r["close_price"] for r in rows if r.get("close_price")]
    if not closes:
        return {}
    first, last = closes[0], closes[-1]
    pct = ((last - first) / first * 100) if first else 0.0
    return {
        "first_close":  first,
        "last_close":   last,
        "pct_change":   round(pct, 2),
        "high":         max(closes),
        "low":          min(closes),
        "trading_days": len(rows),
        "index_name":   rows[-1].get("index_name", "KSE100"),
    }


# ── PSX Companies ─────────────────────────────────────────────────────────────

def search_company(exchange_id: Optional[str], name: str) -> Optional[dict]:
    """Find a PSX-listed company by partial name match."""
    db = get_db()
    row = db.execute(
        """SELECT * FROM psx_companies
           WHERE LOWER(company_name) LIKE ? OR LOWER(symbol) LIKE ?
           LIMIT 1""",
        (f"%{name.lower()}%", f"%{name.lower()}%"),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["ticker"] = d.get("symbol")
    d["sector"] = d.get("sector_name")
    return d


def get_exchange_companies(exchange_id: Optional[str], sector: Optional[str] = None,
                           limit: int = 50) -> List[dict]:
    """Return PSX-listed companies, optionally filtered by sector."""
    db = get_db()
    if sector:
        rows = db.execute(
            """SELECT * FROM psx_companies
               WHERE is_debt = 0 AND LOWER(sector_name) LIKE ?
               ORDER BY company_name LIMIT ?""",
            (f"%{sector.lower()}%", limit),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT * FROM psx_companies
               WHERE is_debt = 0
               ORDER BY company_name LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ticker"] = d.get("symbol")
        d["sector"] = d.get("sector_name")
        out.append(d)
    return out


def get_company_price_history(exchange_id: Optional[str], ticker: str, days: int = 30) -> List[dict]:
    """Return daily price history for a PSX company."""
    db = get_db()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = db.execute(
        """SELECT trade_date, current AS close_price, change_pct, volume
           FROM psx_prices
           WHERE symbol = ? AND trade_date >= ?
           ORDER BY trade_date ASC""",
        (ticker, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_company_price(exchange_id: Optional[str], ticker: str) -> Optional[dict]:
    """Return most recent price for a PSX symbol."""
    db = get_db()
    row = db.execute(
        """SELECT * FROM psx_prices
           WHERE symbol = ?
           ORDER BY trade_date DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["close_price"] = d.get("current")
    d["currency"]    = "PKR"
    return d


def get_top_movers(exchange_id: Optional[str], trade_date: str = "", limit: int = 10) -> dict:
    """Return top gainers and losers from psx_prices for the most recent date."""
    db = get_db()
    if not trade_date:
        row = db.execute("SELECT MAX(trade_date) AS d FROM psx_prices").fetchone()
        trade_date = row["d"] if row and row["d"] else ""
    if not trade_date:
        return {"gainers": [], "losers": [], "trade_date": ""}
    rows = db.execute(
        """SELECT symbol AS ticker, current AS close_price, change_pct, volume
           FROM psx_prices
           WHERE trade_date = ? AND change_pct IS NOT NULL
           ORDER BY change_pct DESC""",
        (trade_date,),
    ).fetchall()
    all_rows = []
    for r in rows:
        d = dict(r)
        d["company_name"] = d["ticker"]
        d["currency"] = "PKR"
        all_rows.append(d)
    gainers = [r for r in all_rows if r["change_pct"] >= 0][:limit]
    losers  = sorted([r for r in all_rows if r["change_pct"] < 0],
                     key=lambda r: r["change_pct"])[:limit]
    return {"gainers": gainers, "losers": losers, "trade_date": trade_date}


def get_sector_breakdown(exchange_id: Optional[str] = None) -> List[dict]:
    """Per-sector summary: avg change_pct and company count from latest PSX prices."""
    db = get_db()
    rows = db.execute(
        """SELECT c.sector_name AS sector,
                  COUNT(*) AS company_count,
                  AVG(p.change_pct) AS avg_change_pct
           FROM psx_companies c
           LEFT JOIN (
               SELECT symbol, change_pct,
                      ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
               FROM psx_prices
           ) p ON c.symbol = p.symbol AND p.rn = 1
           WHERE c.sector_name IS NOT NULL AND c.is_debt = 0
           GROUP BY c.sector_name
           ORDER BY avg_change_pct DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


# ── PSX Sector Peers ─────────────────────────────────────────────────────────

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
}


def get_psx_sector_peers_text(industry_tag: str) -> str:
    """
    Format PSX sector peer P/E and price data as text for AI prompt injection.
    Reads from psx_companies + psx_prices — no external API, no yfinance.
    Returns empty string if no PSX companies found for this sector.
    """
    from intelligence.stocks import fetch_psx_sector_peers
    result = fetch_psx_sector_peers(industry_tag)
    if not result or not result["peers"]:
        return ""

    peers   = result["peers"]
    med_pe  = (result.get("sector_median") or {}).get("pe")
    lines   = [f"PSX SECTOR PEERS — {industry_tag.upper()} (Pakistan Listed Companies, source: PSX)\n"]

    for p in peers[:10]:
        pe_str = f"  P/E {p['pe_ratio']:.1f}x" if p.get("pe_ratio") else "  P/E N/A"
        lines.append(f"  {p['ticker']:8} {p['name'][:32]}{pe_str}")

    if med_pe:
        lines.append(f"\nPSX sector median P/E: {med_pe:.1f}x  ({len(peers)} listed peers)")

    return "\n".join(lines)


# ── Ops ───────────────────────────────────────────────────────────────────────

def get_scraper_status() -> List[dict]:
    """Return the last run result for each scraper — used by markets page."""
    db = get_db()
    rows = db.execute(
        """SELECT s.*
           FROM scraper_runs s
           INNER JOIN (
               SELECT scraper_id, MAX(started_at) AS last_run
               FROM scraper_runs
               GROUP BY scraper_id
           ) latest ON s.scraper_id = latest.scraper_id
                    AND s.started_at = latest.last_run
           ORDER BY s.scraper_id""",
    ).fetchall()
    return [dict(r) for r in rows]


def has_fresh_data(region_id: Optional[str] = None, max_age_hours: int = 8) -> bool:
    """Return True if PKR forex data is newer than max_age_hours."""
    db = get_db()
    from datetime import datetime, timezone
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None)
              - timedelta(hours=max_age_hours)).isoformat()
    row = db.execute(
        "SELECT 1 FROM pkr_forex WHERE fetched_at > ? LIMIT 1",
        (cutoff,),
    ).fetchone()
    return row is not None
