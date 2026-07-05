"""
database/macro_data.py — Pakistan macro / regulatory / benchmark persistence.

Backs the government-data pipeline. Each upsert targets a purpose-built table:
- sbp_indicators     : SBP monetary indicators
- pk_macro           : generic macro series (PBS, MoF, EAD, IMF, World Bank, FBR, BOI)
- pbs_lsmi           : Large Scale Manufacturing Index per sector
- pbs_trade          : exports/imports by commodity
- fbr_revenue        : tax collection
- nepra_power        : electricity tariff/capacity
- ogra_prices        : fuel prices
- pta_telecom        : telecom subscribers
- industry_stats     : industry association production stats
- global_sector_peers: yfinance global benchmark multiples

No AI. All writes parameterised. Read helpers + get_macro_summary_text() format a
compact macro block for AI prompt injection.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional, List

from database.connection import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _period_key(period: str) -> tuple:
    """
    Turn a period label into a chronologically-sortable (year, month) key so the
    'latest' record is picked by real date, not lexical string order. Handles
    '2025-06', 'Jun-2025', 'June 2025', 'FY2025', '13-Jun-2025', bare '2025'.
    Unparseable → (0, 0) so it never beats a dated record.
    """
    s = (period or "").lower()
    ym = re.search(r"(19|20)\d{2}", s)
    year = int(ym.group(0)) if ym else 0
    month = 0
    for name, num in _MONTHS.items():
        if name in s:
            month = num
            break
    if not month:
        mm = re.search(r"\b(0?[1-9]|1[0-2])\b", re.sub(r"(19|20)\d{2}", "", s))
        if mm:
            month = int(mm.group(0))
    return (year, month)


# ── Upserts ───────────────────────────────────────────────────────────────────

def upsert_sbp_indicator(indicator: str, period: str, value: float,
                         unit: str = "", source: str = "SBP") -> None:
    db = get_db()
    db.execute(
        """INSERT INTO sbp_indicators (indicator, period, value, unit, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(indicator, period) DO UPDATE SET
             value=excluded.value, unit=excluded.unit,
             source=excluded.source, fetched_at=excluded.fetched_at""",
        (indicator, period, value, unit, source, _now()),
    )
    db.commit()


def upsert_macro(source: str, indicator: str, period: str,
                 value: Optional[float] = None, unit: str = "",
                 detail: Optional[dict] = None) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO pk_macro (source, indicator, period, value, unit, detail, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source, indicator, period) DO UPDATE SET
             value=excluded.value, unit=excluded.unit,
             detail=excluded.detail, fetched_at=excluded.fetched_at""",
        (source, indicator, period, value, unit,
         json.dumps(detail) if detail is not None else None, _now()),
    )
    db.commit()


def upsert_lsmi(sector: str, period: str, index_value: Optional[float],
                yoy_change_pct: Optional[float] = None) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO pbs_lsmi (sector, period, index_value, yoy_change_pct, fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(sector, period) DO UPDATE SET
             index_value=excluded.index_value,
             yoy_change_pct=excluded.yoy_change_pct, fetched_at=excluded.fetched_at""",
        (sector, period, index_value, yoy_change_pct, _now()),
    )
    db.commit()


def upsert_trade(direction: str, commodity: str, period: str,
                 value_usd_mn: Optional[float], qty: Optional[float] = None,
                 unit: str = "") -> None:
    db = get_db()
    db.execute(
        """INSERT INTO pbs_trade (direction, commodity, period, value_usd_mn, qty, unit, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(direction, commodity, period) DO UPDATE SET
             value_usd_mn=excluded.value_usd_mn, qty=excluded.qty,
             unit=excluded.unit, fetched_at=excluded.fetched_at""",
        (direction, commodity, period, value_usd_mn, qty, unit, _now()),
    )
    db.commit()


def upsert_fbr_revenue(tax_type: str, period: str, collected_pkr_bn: Optional[float],
                       target_pkr_bn: Optional[float] = None) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO fbr_revenue (tax_type, period, collected_pkr_bn, target_pkr_bn, fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(tax_type, period) DO UPDATE SET
             collected_pkr_bn=excluded.collected_pkr_bn,
             target_pkr_bn=excluded.target_pkr_bn, fetched_at=excluded.fetched_at""",
        (tax_type, period, collected_pkr_bn, target_pkr_bn, _now()),
    )
    db.commit()


def upsert_nepra(metric: str, period: str, value: Optional[float], unit: str = "") -> None:
    db = get_db()
    db.execute(
        """INSERT INTO nepra_power (metric, period, value, unit, fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(metric, period) DO UPDATE SET
             value=excluded.value, unit=excluded.unit, fetched_at=excluded.fetched_at""",
        (metric, period, value, unit, _now()),
    )
    db.commit()


def upsert_ogra_price(product: str, price_date: str, price_pkr: float, unit: str = "PKR/litre") -> None:
    db = get_db()
    db.execute(
        """INSERT INTO ogra_prices (product, price_date, price_pkr, unit, fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(product, price_date) DO UPDATE SET
             price_pkr=excluded.price_pkr, unit=excluded.unit, fetched_at=excluded.fetched_at""",
        (product, price_date, price_pkr, unit, _now()),
    )
    db.commit()


def upsert_pta(metric: str, period: str, value: Optional[float], unit: str = "") -> None:
    db = get_db()
    db.execute(
        """INSERT INTO pta_telecom (metric, period, value, unit, fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(metric, period) DO UPDATE SET
             value=excluded.value, unit=excluded.unit, fetched_at=excluded.fetched_at""",
        (metric, period, value, unit, _now()),
    )
    db.commit()


def upsert_industry_stat(industry: str, metric: str, period: str,
                         value: Optional[float], unit: str = "", source: str = "") -> None:
    db = get_db()
    db.execute(
        """INSERT INTO industry_stats (industry, metric, period, value, unit, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(industry, metric, period, source) DO UPDATE SET
             value=excluded.value, unit=excluded.unit, fetched_at=excluded.fetched_at""",
        (industry, metric, period, value, unit, source, _now()),
    )
    db.commit()


def upsert_global_peer(industry: str, peer_ticker: str, peer_name: str = "",
                       **multiples) -> None:
    db = get_db()
    db.execute(
        """INSERT OR IGNORE INTO global_sector_peers
           (industry, peer_ticker, peer_name, pe_ratio, ev_ebitda, net_margin, revenue_growth, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (industry, peer_ticker.upper(), peer_name, multiples.get("pe_ratio"),
         multiples.get("ev_ebitda"), multiples.get("net_margin"),
         multiples.get("revenue_growth"), _now()),
    )
    db.commit()


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_sbp_indicator(indicator: str) -> Optional[dict]:
    """Return the chronologically-latest reading for an SBP indicator.

    Picks by parsed period date (not lexical string order), with fetched_at as the
    tiebreak — so e.g. a current 11% policy rate isn't shadowed by an older 'Jun'
    record that happens to sort after 'Jan' alphabetically.
    """
    db = get_db()
    rows = db.execute(
        "SELECT * FROM sbp_indicators WHERE indicator = ? ORDER BY fetched_at DESC LIMIT 24",
        (indicator,),
    ).fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: (_period_key(r["period"]), r["fetched_at"] or ""))
    return dict(best)


def get_ogra_price(product: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM ogra_prices WHERE product = ? ORDER BY price_date DESC LIMIT 1",
        (product,),
    ).fetchone()
    return dict(row) if row else None


def get_nepra(metric: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM nepra_power WHERE metric = ? ORDER BY period DESC LIMIT 1",
        (metric,),
    ).fetchone()
    return dict(row) if row else None


def get_industry_stat(industry: str, metric: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        """SELECT * FROM industry_stats
           WHERE industry = ? AND metric = ?
           ORDER BY period DESC LIMIT 1""",
        (industry, metric),
    ).fetchone()
    return dict(row) if row else None


def get_global_peers(industry: str) -> List[dict]:
    db = get_db()
    rows = db.execute(
        """SELECT * FROM global_sector_peers
           WHERE industry = ?
             AND fetched_at = (SELECT MAX(fetched_at) FROM global_sector_peers WHERE industry = ?)
           ORDER BY peer_ticker""",
        (industry, industry),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_macro() -> dict:
    """
    Return the latest Pakistan macro indicators as a flat dict of percentage-point
    numbers (e.g. sbp_policy_rate=22.0 means 22%). Keys are absent if no data exists.
    Used by the financial model to drive a data-backed risk-free rate.
    """
    out: dict = {}

    pr = get_sbp_indicator("policy_rate")
    if pr and pr.get("value") is not None:
        out["sbp_policy_rate"] = float(pr["value"])

    cpi = get_sbp_indicator("cpi")
    if cpi and cpi.get("value") is not None:
        out["cpi"] = float(cpi["value"])

    try:
        db = get_db()
        gdp = db.execute(
            "SELECT value FROM pk_macro WHERE indicator='gdp_growth' ORDER BY period DESC LIMIT 1"
        ).fetchone()
        if gdp and gdp["value"] is not None:
            out["gdp_growth"] = float(gdp["value"])
    except Exception:
        pass

    return out


# ── Prompt summary ────────────────────────────────────────────────────────────

def get_macro_summary_text(industry: Optional[str] = None) -> str:
    """Compact Pakistan macro block for AI prompt injection."""
    lines = ["PAKISTAN MACRO CONTEXT (latest available)"]

    def _add(label, row, key="value", unit_key="unit"):
        if row and row.get(key) is not None:
            unit = row.get(unit_key) or ""
            lines.append(f"  {label}: {row[key]:g} {unit}".rstrip())

    # SBP policy rate with trend + next MPC meeting (MPC-calendar-aware scraper).
    try:
        from intelligence.gov_scrapers.sbp_monetary import get_rate_status
        st = get_rate_status()
        if st.get("rate") is not None:
            extra = []
            if st.get("trend") and st["trend"] != "hold":
                extra.append(st["trend"])
            if st.get("next_mpc"):
                extra.append(f"next MPC {st['next_mpc']}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"  SBP Policy Rate: {st['rate']:g} %{suffix}")
        else:
            _add("SBP Policy Rate", get_sbp_indicator("policy_rate"))
    except Exception:
        _add("SBP Policy Rate", get_sbp_indicator("policy_rate"))
    _add("CPI Inflation",   get_sbp_indicator("cpi"))
    _add("FX Reserves",     get_sbp_indicator("forex_reserves"))

    db = get_db()
    gdp = db.execute(
        "SELECT * FROM pk_macro WHERE indicator='gdp_growth' ORDER BY period DESC LIMIT 1"
    ).fetchone()
    if gdp and gdp["value"] is not None:
        lines.append(f"  GDP Growth: {gdp['value']:g} {gdp['unit'] or '%'}")

    petrol = get_ogra_price("petrol")
    hsd    = get_ogra_price("hsd")
    if petrol:
        lines.append(f"  Petrol: PKR {petrol['price_pkr']:g}/litre")
    if hsd:
        lines.append(f"  Diesel (HSD): PKR {hsd['price_pkr']:g}/litre")

    if industry and industry != "general":
        tariff = get_nepra("tariff_industrial")
        if tariff:
            lines.append(f"  Industrial Electricity Tariff: PKR {tariff['value']:g}/kWh")
        disp = get_industry_stat(industry, "dispatches") or get_industry_stat(industry, "production")
        if disp and disp.get("value") is not None:
            lines.append(f"  {industry.title()} sector {disp['metric']}: {disp['value']:g} {disp.get('unit') or ''}".rstrip())

    return "\n".join(lines) if len(lines) > 1 else ""


def get_global_peers_text(industry: str) -> str:
    """Compact global sector benchmark block for AI prompt injection."""
    peers = get_global_peers(industry)
    if not peers:
        return ""
    lines = [f"GLOBAL {industry.upper()} PEER BENCHMARK (for comparison)"]
    pes = [p["pe_ratio"] for p in peers if p.get("pe_ratio")]
    evs = [p["ev_ebitda"] for p in peers if p.get("ev_ebitda")]
    for p in peers[:6]:
        pe = f"P/E {p['pe_ratio']:.1f}" if p.get("pe_ratio") else "P/E n/a"
        ev = f"EV/EBITDA {p['ev_ebitda']:.1f}" if p.get("ev_ebitda") else ""
        lines.append(f"  {p.get('peer_name') or p['peer_ticker']}: {pe} {ev}".rstrip())
    if pes:
        lines.append(f"  Sector median P/E: {sorted(pes)[len(pes)//2]:.1f}")
    if evs:
        lines.append(f"  Sector median EV/EBITDA: {sorted(evs)[len(evs)//2]:.1f}")
    return "\n".join(lines)
