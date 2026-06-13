"""
intelligence/psx_financials.py — PSX financial data (primary Pakistan source).

Pulls annual/quarterly financials and ratios from dps.psx.com.pk/company/{ticker}
(parsed by exchange_crawlers/psx.py), maps the HTML table rows to the flat dict shape
that financial_model expects, and persists to psx_financials. No AI, no yfinance for
Pakistani company numbers.
"""

from __future__ import annotations

import re
from typing import Optional, List

import structlog

from intelligence.exchange_crawlers.psx import fetch_psx_company
from database.psx_data import upsert_psx_financials, get_psx_financials

log = structlog.get_logger()


def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower())


def get_cached_financials(ticker: str) -> List[dict]:
    """Return previously-persisted annual financials for a ticker (newest first)."""
    return get_psx_financials(ticker, "annual", limit=4)


# ── Full-analysis adapter: PSX financials → FinancialAnalyzer input ───────────────
# Maps PSX statement rows to the keys analyse_from_document() expects, including
# multi-year revenue for growth metrics. This lets the SAME financial engine
# (ratios, Altman Z, DuPont, DCF, health score) run on PSX data instead of yfinance.
_DOC_MAP: list[tuple[list[str], str]] = [
    # Revenue — industrial companies use "net sales/turnover"; banks use "total income"
    # or "mark-up earned"; insurance uses "net premium income"; NBFCs use "total income".
    (["net sales", "revenue", "sales", "turnover", "net revenue",
      "total income", "net income from operations", "total revenue",
      "mark-up / return / interest earned", "mark-up earned",
      "net interest income", "interest income", "net markup income",
      "net premium income", "gross premium", "net premiums earned",
      "total operating revenue", "operating revenue",
      "fee and commission income", "income from operations"], "revenue"),
    (["gross profit"], "gross_profit"),
    (["ebitda"], "ebitda"),
    (["operating profit", "ebit", "profit from operations",
      "profit before taxation", "profit before tax"], "ebit"),
    (["profit after tax", "net profit", "net income", "profit for the year",
      "profit after taxation", "profit/(loss) after tax"], "net_income"),
    (["total assets"], "total_assets"),
    (["total equity", "shareholders equity", "shareholders' equity",
      "share holders equity", "total equity attributable"], "shareholders_equity"),
    (["total debt", "long term borrowings", "long term debt", "borrowings",
      "deposits and other accounts", "total liabilities"], "total_debt"),
    (["cash and cash equivalents", "cash and bank", "cash & cash equivalents",
      "cash and balances with treasury banks", "cash"], "cash"),
    (["current assets", "total current assets"], "current_assets"),
    (["current liabilities", "total current liabilities"], "current_liabilities"),
    (["inventory", "stock in trade", "stores and spares"], "inventory"),
    (["trade receivables", "trade debts", "receivables",
      "advances, deposits and prepayments"], "receivables"),
    (["trade payables", "trade and other payables", "payables"], "payables"),
    (["cash from operations", "net cash from operating", "operating cash flow"], "ocf"),
    (["capital expenditure", "capex", "additions to property"], "capex"),
    (["eps", "earnings per share", "basic eps", "diluted eps",
      "earnings per share - basic"], "eps"),
]


def _series(value_map: dict, count: int = 3) -> List[float]:
    """Up to `count` most-recent numeric values for a metric, newest first."""
    if not isinstance(value_map, dict):
        return []
    nums = [(p, v) for p, v in value_map.items() if isinstance(v, (int, float))]
    nums.sort(key=lambda kv: _year_of(kv[0]), reverse=True)
    return [v for _, v in nums[:count]]


def _year_of(period: str) -> int:
    m = re.search(r"(19|20)\d{2}", str(period))
    return int(m.group(0)) if m else -1


def _report_is_sane(report: dict) -> bool:
    """
    Accept a parsed annual report only if it forms a complete, internally-consistent
    statement: revenue, net income and total assets present, and basic accounting
    identities hold (total assets dominate current assets and equity). Guards against
    a heuristic PDF parse grabbing the wrong column or a note-reference number.
    """
    rev = report.get("revenue")
    ni  = report.get("net_income")
    ta  = report.get("total_assets")
    if not (rev and ni and ta) or ta <= 0:
        return False
    ca = report.get("current_assets") or 0
    eq = report.get("shareholders_equity") or 0
    cl = report.get("current_liabilities") or 0
    if ca > ta * 1.05 or eq > ta * 1.05 or cl > ta * 1.05:
        return False
    # Net income shouldn't exceed revenue by a wide margin (parse sanity).
    if abs(ni) > abs(rev) * 2:
        return False
    return True


def _build_doc_financials(table: dict) -> dict:
    """Map a PSX financial table {metric:{period:value}} → analyse_from_document dict."""
    out: dict = {}
    rev_series: List[float] = []
    latest_year: Optional[str] = None
    for metric, value_map in (table or {}).items():
        n = _norm(metric)
        for aliases, key in _DOC_MAP:
            if any(n == a or n.startswith(a) for a in aliases):
                series = _series(value_map)
                if series:
                    out.setdefault(key, series[0])
                    if key == "revenue" and not rev_series:
                        rev_series = series
                        # Capture the most recent fiscal year label
                        if isinstance(value_map, dict):
                            yrs = sorted(
                                [p for p, v in value_map.items() if isinstance(v, (int, float))],
                                key=_year_of, reverse=True,
                            )
                            if yrs:
                                latest_year = str(yrs[0])
                break
    if len(rev_series) > 1:
        out["revenue_prior_year"] = rev_series[1]
    if len(rev_series) > 2:
        out["revenue_2y_ago"] = rev_series[2]
    if latest_year:
        out["_fiscal_year"] = latest_year
    return out


def fetch_psx_doc_financials(ticker: str) -> Optional[tuple]:
    """
    Fetch PSX financials and return (doc_financials_dict, company_name) ready for
    analyse_from_document(). Returns None if no usable revenue figure is found.

    Data priority:
    1. Live PSX DPS scraper (annual financials → quarterly fallback)
    2. Cached psx_financials DB rows (from a prior scrape)
    3. psx_companies summary (market cap only — enables health score / DCF stub)
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None
    data = fetch_psx_company(ticker, fetch_types=["financials", "ratios", "announcements"])

    doc: dict = {}
    if data and "error" not in data:
        doc = _build_doc_financials(data.get("annual_financials") or {})
        if not doc.get("revenue"):
            doc = _build_doc_financials(data.get("quarterly_financials") or {})

    # ── Fallback 1: cached psx_financials rows from a prior run ───────────────
    if not doc.get("revenue"):
        try:
            cached = get_cached_financials(ticker)
            for row in cached:
                raw = row.get("raw_data") or {}
                if isinstance(raw, str):
                    import json as _json
                    try:
                        raw = _json.loads(raw)
                    except Exception:
                        raw = {}
                candidate = _build_doc_financials(raw)
                if candidate.get("revenue"):
                    doc = candidate
                    doc["_source"] = "psx_financials_cache"
                    break
        except Exception as _ce:
            log.debug("psx_financials cache lookup failed", error=str(_ce))

    # ── Fallback 2: psx_companies has market_cap — build a market-cap-only stub
    # so at least the health score / DCF / comparables can run for listed banks.
    if not doc.get("revenue"):
        try:
            from database.connection import get_db as _get_db
            _db = _get_db()
            co = _db.execute(
                "SELECT company_name, market_cap, sector_name "
                "FROM psx_companies WHERE symbol=? LIMIT 1",
                (ticker,),
            ).fetchone()
            if co and co["market_cap"]:
                mc  = float(co["market_cap"])
                # Approximate net income as 8% of market cap (broad-market average)
                # and revenue as ~10× net income (10% net margin assumption)
                approx_eps_total = mc * 0.08
                approx_revenue   = approx_eps_total / 0.10   # assume 10% net margin
                doc = {
                    "revenue":             approx_revenue,
                    "net_income":          approx_eps_total,
                    "shareholders_equity": mc * 0.5,          # rough book-value proxy
                    "_source":             "psx_companies_stub",
                    "_stub":               True,
                }
                log.info(
                    "Using psx_companies market_cap stub for financial analysis",
                    ticker=ticker,
                    note="Ratios derived from market cap — treat as indicative only",
                )
        except Exception as _fb:
            log.debug("psx_companies stub lookup failed", error=str(_fb))

    if not doc.get("revenue"):
        return None

    # PSX publishes a ratios table (Gross/Net Profit Margin %, EPS growth, PEG).
    # Use it to back out figures the summary statements omit — notably gross profit
    # from the margin — so the engine can compute the full profitability picture.
    ratios = data.get("ratios") or {}

    def _ratio_latest(*aliases) -> Optional[float]:
        for label, vm in ratios.items():
            n = _norm(label)
            if any(a in n for a in aliases):
                s = _series(vm)
                if s:
                    return s[0]
        return None

    # ── Deepen with the annual-report PDF when it parses a complete statement ──
    # The PSX summary lacks balance-sheet items (assets, equity, debt, current
    # assets/liabilities) needed for liquidity, leverage, Altman Z and ROE. The
    # annual report has them. To avoid mixing unit conventions (PKR '000 vs mn)
    # across two sources, we adopt the report WHOLESALE only when it parses a
    # complete, internally-consistent set (revenue + net income + total assets).
    # Otherwise the clean summary stands (margins / health / DCF still compute).
    source = "psx_summary"
    try:
        from intelligence.psx_report_parser import fetch_psx_report_financials
        report = fetch_psx_report_financials(ticker)
        if report and _report_is_sane(report):
            # EBITDA from operating profit + depreciation when not stated directly.
            if "ebitda" not in report and report.get("ebit") and report.get("depreciation"):
                report["ebitda"] = report["ebit"] + report["depreciation"]
            doc = report
            source = "psx_annual_report_ocr" if report.get("_ocr") else "psx_annual_report"
    except Exception as e:
        log.info("PSX report enrichment skipped", ticker=ticker, error=str(e)[:80])

    # Gross profit from the PSX ratio table when the chosen source omits it.
    if "gross_profit" not in doc:
        gpm = _ratio_latest("gross profit margin")
        if gpm is not None and doc.get("revenue"):
            doc["gross_profit"] = doc["revenue"] * gpm / 100.0

    doc["_source"] = source
    name = ticker
    try:
        from intelligence.exchange_crawlers.psx import search_psx_company
        for s in search_psx_company(ticker):
            if (s.get("symbol") or "").upper() == ticker:
                name = s.get("name") or ticker
                break
    except Exception:
        pass

    # Persist the parsed statement so psx_financials builds a history.
    try:
        period = "latest"
        rev_map = (data.get("annual_financials") or {})
        for metric, vm in rev_map.items():
            if _norm(metric).startswith(("net sales", "revenue", "sales")):
                yrs = [p for p, v in vm.items() if isinstance(v, (int, float))]
                if yrs:
                    period = str(sorted(yrs, key=_year_of, reverse=True)[0])
                break
        upsert_psx_financials(ticker, period, "annual", doc, raw=ratios)
    except Exception as e:
        log.info("PSX financials persist skipped", ticker=ticker, error=str(e)[:80])

    return doc, name
