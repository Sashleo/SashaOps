"""
database/psx_data.py — PSX company, financials, index and price persistence.

Pakistan-native. Backs the deterministic financial pipeline:
- psx_companies   : listed company registry (symbol, name, sector, website)
- psx_financials  : annual/quarterly financials parsed from PSX company pages
- psx_prices      : daily prices from PSX market summary
- psx_index       : KSE index snapshots

No AI. All writes are parameterised.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, List

import structlog

from database.connection import get_db

log = structlog.get_logger()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Company registry ──────────────────────────────────────────────────────────

def upsert_psx_company(symbol: str, company_name: str, sector_name: str = "",
                       is_debt: bool = False, website: Optional[str] = None,
                       market_cap: Optional[float] = None) -> None:
    """Insert/update a PSX-listed company."""
    db = get_db()
    db.execute(
        """INSERT INTO psx_companies (symbol, company_name, sector_name, is_debt, website, market_cap, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
             company_name=excluded.company_name,
             sector_name=excluded.sector_name,
             is_debt=excluded.is_debt,
             website=COALESCE(excluded.website, psx_companies.website),
             market_cap=COALESCE(excluded.market_cap, psx_companies.market_cap),
             last_updated=excluded.last_updated""",
        (symbol.upper(), company_name, sector_name or None,
         1 if is_debt else 0, website, market_cap, _now()),
    )
    db.commit()


def bulk_upsert_symbols(symbols: List[dict]) -> int:
    """Upsert a batch of PSX symbols (from get_all_psx_symbols). Returns count."""
    n = 0
    for s in symbols:
        sym = s.get("symbol")
        if not sym:
            continue
        upsert_psx_company(
            symbol=sym,
            company_name=s.get("name", sym),
            sector_name=s.get("sectorName", ""),
            is_debt=bool(s.get("isDebt")),
        )
        n += 1
    return n


def get_company(symbol: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM psx_companies WHERE symbol = ?", (symbol.upper(),)
    ).fetchone()
    return dict(row) if row else None


def set_company_website(symbol: str, website: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE psx_companies SET website = ?, last_updated = ? WHERE symbol = ?",
        (website, _now(), symbol.upper()),
    )
    db.commit()


# ── Financials ────────────────────────────────────────────────────────────────

_FIN_COLS = (
    "revenue", "gross_profit", "ebitda", "net_income", "total_assets",
    "total_equity", "total_debt", "cash", "current_assets",
    "current_liabilities", "eps",
    "ebit", "retained_earnings", "inventory", "receivables", "payables",
    "interest_expense", "depreciation", "ocf", "capex",
)

# The analysis dict uses slightly different key names than the DB columns.
_FIN_ALIASES = {"total_equity": "shareholders_equity"}


def _fin_value(metrics: dict, col: str):
    """Read a financial column value, honouring analysis-dict key aliases."""
    v = metrics.get(col)
    if v is None and col in _FIN_ALIASES:
        v = metrics.get(_FIN_ALIASES[col])
    return v


def upsert_psx_financials(symbol: str, period: str, period_type: str,
                          metrics: dict, raw: Optional[dict] = None) -> None:
    """Insert/update one financial period row for a company."""
    db = get_db()
    cols = ["symbol", "period", "period_type", *_FIN_COLS, "raw_json", "fetched_at"]
    vals = [
        symbol.upper(), period, period_type,
        *[_fin_value(metrics, c) for c in _FIN_COLS],
        json.dumps(raw) if raw is not None else None,
        _now(),
    ]
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in (*_FIN_COLS, "raw_json", "fetched_at"))
    db.execute(
        f"""INSERT INTO psx_financials ({",".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(symbol, period, period_type) DO UPDATE SET {updates}""",
        vals,
    )
    db.commit()


def get_psx_financials(symbol: str, period_type: str = "annual",
                       limit: int = 4) -> List[dict]:
    """Return the most recent financial periods for a company (newest first)."""
    db = get_db()
    rows = db.execute(
        """SELECT * FROM psx_financials
           WHERE symbol = ? AND period_type = ?
           ORDER BY period DESC LIMIT ?""",
        (symbol.upper(), period_type, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Prices & index ────────────────────────────────────────────────────────────

def upsert_psx_price(symbol: str, trade_date: str, current: Optional[float] = None,
                     **kw) -> bool:
    """Insert a daily PSX price row. Returns True if inserted."""
    db = get_db()
    db.execute(
        """INSERT OR IGNORE INTO psx_prices
           (symbol, trade_date, open, high, low, current, ldcp, change_pct, volume, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol.upper(), trade_date, kw.get("open"), kw.get("high"), kw.get("low"),
         current, kw.get("ldcp"), kw.get("change_pct"), kw.get("volume"), _now()),
    )
    inserted = db.execute("SELECT changes()").fetchone()[0] > 0
    db.commit()
    return inserted


def upsert_psx_index(index_code: str, trade_date: str, close_value: Optional[float],
                     **kw) -> bool:
    """Insert a KSE index snapshot. Returns True if inserted."""
    db = get_db()
    db.execute(
        """INSERT OR IGNORE INTO psx_index
           (index_code, trade_date, close_value, change_pts, change_pct, volume, market_cap, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (index_code, trade_date, close_value, kw.get("change_pts"),
         kw.get("change_pct"), kw.get("volume"), kw.get("market_cap"), _now()),
    )
    inserted = db.execute("SELECT changes()").fetchone()[0] > 0
    db.commit()
    return inserted


def upsert_psx_announcement(symbol: str, ann_type: str, ann_date: Optional[str],
                            title: Optional[str], document_url: Optional[str] = None) -> bool:
    """Insert a PSX announcement row. Returns True if inserted (dedup on the
    natural key symbol+ann_type+ann_date+title, since PSX has no announcement id)."""
    db = get_db()
    db.execute(
        """INSERT OR IGNORE INTO psx_announcements
           (symbol, ann_type, ann_date, title, document_url, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (symbol.upper(), ann_type, ann_date, title, document_url, _now()),
    )
    inserted = db.execute("SELECT changes()").fetchone()[0] > 0
    db.commit()
    return inserted


# ── Sector peers ──────────────────────────────────────────────────────────────

def get_psx_sector_peers(symbol: str, limit: int = 12) -> List[dict]:
    """
    Return other PSX companies in the same sector as `symbol`, with their latest
    price. Used by the Competitors module for real local comparables.
    """
    db = get_db()
    company = get_company(symbol)
    if not company or not company.get("sector_name"):
        return []
    rows = db.execute(
        """SELECT c.symbol, c.company_name, c.sector_name,
                  p.current, p.change_pct
           FROM psx_companies c
           LEFT JOIN (
               SELECT symbol, current, change_pct,
                      ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
               FROM psx_prices
           ) p ON c.symbol = p.symbol AND p.rn = 1
           WHERE c.sector_name = ? AND c.symbol != ? AND c.is_debt = 0
           ORDER BY c.company_name LIMIT ?""",
        (company["sector_name"], symbol.upper(), limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Prompt summary ────────────────────────────────────────────────────────────

def get_psx_summary_text(symbol: str) -> str:
    """Compact PSX company + latest financials block for AI prompt injection."""
    company = get_company(symbol)
    if not company:
        return ""
    lines = [f"PSX PROFILE — {company['company_name']} ({symbol.upper()})",
             f"  Sector: {company.get('sector_name') or 'n/a'}"]
    fins = get_psx_financials(symbol, "annual", limit=2)
    if fins:
        latest = fins[0]
        def _f(k):
            v = latest.get(k)
            return f"{v:,.0f}" if isinstance(v, (int, float)) else "n/a"
        lines.append(f"  Latest financials ({latest.get('period')}):")
        lines.append(f"    Revenue: {_f('revenue')} | Gross Profit: {_f('gross_profit')} | "
                     f"EBITDA: {_f('ebitda')} | Net Income: {_f('net_income')}")
        lines.append(f"    Total Assets: {_f('total_assets')} | Equity: {_f('total_equity')} | "
                     f"Debt: {_f('total_debt')} | EPS: {_f('eps')}")
    return "\n".join(lines)
