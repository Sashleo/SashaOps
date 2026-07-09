"""
intelligence/competitors.py — PSX peer selection + side-by-side ratio comparison.

Two responsibilities:

  select_competitor_tickers(...) — pick the default competitor set:
      * If the user has starred sector peers in the market-tab watchlist
        (psx_watchlist), those win.
      * Otherwise: the top-N by market share (market_cap proxy; revenue fallback
        when market_cap is unpopulated) in the same PSX sector, excluding the
        target. Because the target is excluded, if it would have been in the top-N
        the next-best company fills the slot automatically.

  build_competitor_comparison(...) — actually COMPUTE each peer's ratios from
      psx_financials + psx_prices and render a markdown table showing how the
      subject company performs against peers and the peer median. This is the piece
      that was missing: peers were listed but never compared.

Pure local DB reads. No AI, no network. Never raises — returns "" on failure.
"""

from __future__ import annotations

from typing import Optional

import structlog

log = structlog.get_logger()


# ── metric helpers ───────────────────────────────────────────────────────────────
def _div(a, b) -> Optional[float]:
    try:
        if a is None or not b:
            return None
        return float(a) / float(b)
    except Exception:
        return None


def _latest_price(db, symbol: str) -> Optional[float]:
    r = db.execute(
        "SELECT current FROM psx_prices WHERE symbol=? ORDER BY trade_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return r["current"] if r and r["current"] else None


def compute_metrics(db, symbol: str) -> dict:
    """Compute comparable ratios for a PSX symbol from its latest annual financials."""
    f = db.execute(
        "SELECT revenue, gross_profit, net_income, total_assets, total_equity, "
        "total_debt, current_assets, current_liabilities, eps "
        "FROM psx_financials WHERE symbol=? AND period_type='annual' "
        "ORDER BY period DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    co = db.execute(
        "SELECT company_name, market_cap FROM psx_companies WHERE symbol=? LIMIT 1",
        (symbol,),
    ).fetchone()
    name = (co["company_name"] if co else symbol) or symbol
    mcap = co["market_cap"] if co else None
    if not f:
        return {"symbol": symbol, "name": name, "market_cap": mcap}
    price = _latest_price(db, symbol)
    return {
        "symbol":       symbol,
        "name":         name,
        "market_cap":   mcap,
        "revenue":      f["revenue"],
        "net_margin":   _div(f["net_income"], f["revenue"]),
        "gross_margin": _div(f["gross_profit"], f["revenue"]),
        "roe":          _div(f["net_income"], f["total_equity"]),
        "roa":          _div(f["net_income"], f["total_assets"]),
        "de":           _div(f["total_debt"], f["total_equity"]),
        "current_ratio": _div(f["current_assets"], f["current_liabilities"]),
        "pe":           _div(price, f["eps"]) if (price and f["eps"] and f["eps"] > 0) else None,
    }


def select_competitor_tickers(db, target: str, sector: str, limit: int = 5) -> list[str]:
    """Default competitor set — watchlist override, else top-N by market share."""
    target = (target or "").strip().upper()
    sector = (sector or "").strip()
    if not sector:
        return []

    # 1) market-tab watchlist override (global psx_watchlist), restricted to the sector
    try:
        watch = db.execute(
            "SELECT w.symbol FROM psx_watchlist w "
            "JOIN psx_companies c ON c.symbol = w.symbol "
            "WHERE c.sector_name = ? AND UPPER(w.symbol) != ? "
            "ORDER BY w.added_at DESC",
            (sector, target),
        ).fetchall()
        picked = [r["symbol"] for r in watch]
        if picked:
            return picked[:limit]
    except Exception:
        pass

    # 2) top-N by market share (market_cap proxy; revenue fallback)
    rows = db.execute(
        "SELECT symbol, market_cap FROM psx_companies "
        "WHERE sector_name = ? AND UPPER(symbol) != ? AND is_debt = 0",
        (sector, target),
    ).fetchall()
    scored: list[tuple[str, float]] = []
    for r in rows:
        share = r["market_cap"]
        if share is None:
            fr = db.execute(
                "SELECT revenue FROM psx_financials WHERE symbol=? AND period_type='annual' "
                "ORDER BY period DESC LIMIT 1",
                (r["symbol"],),
            ).fetchone()
            share = (fr["revenue"] if fr and fr["revenue"] else 0)
        scored.append((r["symbol"], float(share or 0)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:limit]]


# ── rendering ────────────────────────────────────────────────────────────────────
def _pct(v) -> str:
    return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "N/A"


def _x(v) -> str:
    return f"{v:.2f}×" if isinstance(v, (int, float)) else "N/A"


def _mn(v) -> str:
    try:
        return f"{float(v)/1_000_000:,.0f}" if v else "N/A"
    except Exception:
        return "N/A"


def _median(vals: list) -> Optional[float]:
    xs = sorted(v for v in vals if isinstance(v, (int, float)))
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def build_competitor_comparison(
    target_ticker: str,
    sector: str,
    target_name: str = "",
    target_metrics: Optional[dict] = None,
    limit: int = 5,
) -> str:
    """
    Return a markdown comparison table (subject vs peers + peer median), or "" if no
    peers are found. `target_metrics` (raw float ratios) is used for the subject row
    when it has no PSX ticker (unlisted companies); otherwise the subject is computed
    from the DB like the peers so units line up.
    """
    try:
        from database.connection import get_db
        db = get_db()
    except Exception:
        return ""

    tk = (target_ticker or "").strip().upper()
    peers = select_competitor_tickers(db, tk, sector, limit=limit)
    if not peers:
        return ""

    peer_rows = [compute_metrics(db, p) for p in peers]

    # subject row
    if tk:
        subj = compute_metrics(db, tk)
        subj["name"] = target_name or subj.get("name") or tk
    else:
        subj = {"symbol": "", "name": target_name or "Subject", **(target_metrics or {})}

    metrics = ["net_margin", "gross_margin", "roe", "de", "current_ratio", "pe"]
    med = {m: _median([r.get(m) for r in peer_rows]) for m in metrics}

    def _row(r: dict, marker: str = "") -> str:
        nm = (marker + " " + (r.get("name") or r.get("symbol") or "-"))[:34]
        return (f"| {nm} | {_pct(r.get('net_margin'))} | {_pct(r.get('gross_margin'))} "
                f"| {_pct(r.get('roe'))} | {_x(r.get('de'))} | {_x(r.get('current_ratio'))} "
                f"| {_x(r.get('pe'))} | {_mn(r.get('revenue'))} |")

    lines = [
        f"### Peer Comparison - {sector} sector",
        "_Top competitors by market share (market cap). Ratios computed from latest PSX annual statements._",
        "",
        "| Company | Net Mgn | Gross Mgn | ROE | D/E | Current | P/E | Revenue (mn) |",
        "|---------|---------|-----------|-----|-----|---------|-----|--------------|",
        _row(subj, "►"),
    ]
    for r in peer_rows:
        lines.append(_row(r))
    lines.append(
        f"| _Peer median_ | {_pct(med['net_margin'])} | {_pct(med['gross_margin'])} "
        f"| {_pct(med['roe'])} | {_x(med['de'])} | {_x(med['current_ratio'])} "
        f"| {_x(med['pe'])} | - |"
    )
    lines.append("")

    # verdict: subject vs peer median on net margin + ROE
    verdicts = []
    for label, key in (("net margin", "net_margin"), ("ROE", "roe")):
        sv, mv = subj.get(key), med.get(key)
        if isinstance(sv, (int, float)) and isinstance(mv, (int, float)):
            gap = (sv - mv) * 100
            verdicts.append(
                f"{label} {_pct(sv)} is {abs(gap):.1f}pp "
                f"{'above' if gap >= 0 else 'below'} the peer median ({_pct(mv)})"
            )
    if verdicts:
        lines.append("**Relative positioning:** " + "; ".join(verdicts) + ".")
        lines.append("")

    return "\n".join(lines)
