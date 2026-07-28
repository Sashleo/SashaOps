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
    (["finance cost", "finance costs", "interest expense", "markup expense",
      "mark-up expense"], "interest_expense"),
    (["depreciation and amortisation", "depreciation & amortization",
      "depreciation and amortization", "depreciation"], "depreciation"),
    (["retained earnings", "unappropriated profit", "accumulated profit",
      "reserves and retained earnings"], "retained_earnings"),
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


def _report_is_reliable(report: dict) -> bool:
    """
    Whether an annual-report parse is trustworthy enough to feed real analysis.

    A DIGITAL report (real text layer) is protected by the parser's anchoring, sign
    and plausibility guards, so it is trusted. An OCR/scanned report is NOT: OCR
    routinely misreads digits and returns internally impossible figures (a scanned
    LUCK booklet parsed net income above revenue). An OCR report is therefore trusted
    ONLY when its balance sheet independently RECONCILES (Assets == Liabilities +
    Equity within 1%) - an arithmetic cross-check that near-random OCR errors cannot
    pass by chance. Otherwise we prefer a missing field ("Required: X") over a wrong
    one, per the accuracy-first rule.
    """
    if not report.get("_ocr"):
        return True
    return report.get("_balance_ok") is True


def _build_doc_financials(table: dict) -> dict:
    """Map a PSX financial table {metric:{period:value}} → analyse_from_document dict."""
    out: dict = {}
    rev_series: List[float] = []
    max_year = -1                      # latest 4-digit year seen across ALL metrics
    for metric, value_map in (table or {}).items():
        n = _norm(metric)
        # Track the latest fiscal year across every metric — banks/pharma may not
        # match the "revenue" alias first, so keying FY off revenue alone gave "FY None".
        if isinstance(value_map, dict):
            for p, v in value_map.items():
                if isinstance(v, (int, float)):
                    y = _year_of(p)
                    if y > max_year:
                        max_year = y
        for aliases, key in _DOC_MAP:
            if any(n == a or n.startswith(a) for a in aliases):
                series = _series(value_map)
                if series:
                    out.setdefault(key, series[0])
                    if key == "revenue" and not rev_series:
                        rev_series = series
                break
    if len(rev_series) > 1:
        out["revenue_prior_year"] = rev_series[1]
    if len(rev_series) > 2:
        out["revenue_2y_ago"] = rev_series[2]
    if max_year > 0:
        out["_fiscal_year"] = str(max_year)
    return out


# DB columns → analyse_from_document keys (mostly identity; equity is aliased).
_ROW_TO_DOC = {
    "revenue": "revenue", "gross_profit": "gross_profit", "ebitda": "ebitda",
    "net_income": "net_income", "total_assets": "total_assets",
    "total_equity": "shareholders_equity", "total_debt": "total_debt", "cash": "cash",
    "current_assets": "current_assets", "current_liabilities": "current_liabilities",
    "eps": "eps", "ebit": "ebit", "retained_earnings": "retained_earnings",
    "inventory": "inventory", "receivables": "receivables", "payables": "payables",
    "interest_expense": "interest_expense", "depreciation": "depreciation",
    "ocf": "ocf", "capex": "capex",
}


def _row_to_doc(row: dict) -> dict:
    """Rebuild an analyse_from_document dict from a persisted psx_financials row."""
    doc: dict = {}
    for col, key in _ROW_TO_DOC.items():
        v = row.get(col)
        if v is not None:
            doc[key] = v
    if row.get("period"):
        doc["_fiscal_year"] = str(row["period"])
    return doc


def _is_fresh(fetched_at: Optional[str], max_age_hours: int = 24) -> bool:
    """True if an ISO timestamp is within max_age_hours of now."""
    if not fetched_at:
        return False
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(fetched_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        return age <= max_age_hours
    except Exception:
        return False


def fetch_psx_doc_financials(ticker: str) -> Optional[tuple]:
    """
    Fetch PSX financials and return (doc_financials_dict, company_name) ready for
    analyse_from_document(). Returns None if no usable revenue figure is found.

    Data priority:
    0. FRESH cache fast-path — a psx_financials row < 24h old (skips scrape + OCR).
    1. Live PSX DPS scraper (annual financials → quarterly fallback)
    2. Cached psx_financials DB rows (from a prior scrape)
    3. psx_companies summary (market cap only — enables health score / DCF stub)
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    # ── Fast path: a recent, revenue-bearing annual row means we can skip the slow
    # live scrape + PDF/OCR entirely (accuracy preserved — same persisted figures).
    # Prefer real year periods with balance-sheet data; never serve a 'latest' stub.
    try:
        cached = [r for r in get_cached_financials(ticker)
                  if r.get("revenue") and _is_fresh(r.get("fetched_at"))
                  and str(r.get("period", "")).strip().lower() != "latest"]
        # Best row = has balance sheet first, then most recent year.
        cached.sort(key=lambda r: (r.get("total_assets") is not None, _year_of(r.get("period"))),
                    reverse=True)
        for row in cached[:1]:
                doc = _row_to_doc(row)
                doc["_source"] = "psx_financials_cache"
                name = ticker
                try:
                    from intelligence.exchange_crawlers.psx import search_psx_company
                    for s in search_psx_company(ticker):
                        if (s.get("symbol") or "").upper() == ticker:
                            name = s.get("name") or ticker
                            break
                except Exception:
                    pass
                log.info("PSX financials served from 24h cache", ticker=ticker)
                return doc, name
    except Exception as _ce:
        log.debug("PSX financials fresh-cache check failed", error=str(_ce)[:80])

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
                    note="Ratios derived from market cap - treat as indicative only",
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

    # ── Deepen with the annual-report PDF ────────────────────────────────────
    # The PSX summary (structured HTML tables, not OCR) is the AUTHORITATIVE source
    # for income-statement items and is already in `doc`. The summary lacks
    # balance-sheet items (assets, equity, debt, current assets/liabilities) needed
    # for liquidity, leverage, Altman Z and ROE - the annual report has them. The
    # report is used only to FILL those gaps, and only when it is RELIABLE
    # (_report_is_reliable): a digital report, or an OCR report whose balance sheet
    # reconciles. An unreliable OCR parse is discarded so its misread digits never
    # reach an analysis - the field stays missing and surfaces as "Required: X".
    #   • WHOLESALE: a reliable, complete statement replaces the summary outright.
    #   • MERGE: a reliable partial report fills only the missing balance-sheet fields.
    source = "psx_summary"
    summary_fy = doc.get("_fiscal_year")
    _BS_FIELDS = ("total_assets", "shareholders_equity", "total_debt", "cash",
                  "current_assets", "current_liabilities", "inventory", "receivables",
                  "payables", "retained_earnings", "ebit", "interest_expense",
                  "depreciation", "gross_profit")
    try:
        from intelligence.psx_report_parser import fetch_psx_report_financials
        report = fetch_psx_report_financials(ticker)
        if report:
            if "ebitda" not in report and report.get("ebit") and report.get("depreciation"):
                report["ebitda"] = report["ebit"] + report["depreciation"]

            if not _report_is_reliable(report):
                # Scanned/OCR report that did not reconcile: do NOT let its values into
                # the analysis. Keep the clean summary; balance-sheet gaps stay missing.
                source = "psx_summary(report_rejected_unreliable_ocr)"
                log.info("Annual report rejected as unreliable OCR - summary kept",
                         ticker=ticker, balance_ok=report.get("_balance_ok"))
            elif _report_is_sane(report):
                # Reliable, complete statement — adopt wholesale.
                if not report.get("_fiscal_year") and summary_fy:
                    report["_fiscal_year"] = summary_fy
                doc = report
                source = "psx_annual_report_ocr" if report.get("_ocr") else "psx_annual_report"
            else:
                # Reliable but partial report — merge balance-sheet fields if units are
                # consistent with the summary (report revenue within 0.5x-2x of summary
                # revenue). PSX summary is in PKR '000; annual reports may be in '000 or
                # actual PKR. Infer the scale from the revenue ratio and snap to the
                # nearest power of 1000.
                s_rev = doc.get("revenue")
                r_rev_chk = report.get("revenue")
                scale, units_ok = 1.0, True
                if s_rev and r_rev_chk:
                    ratio = r_rev_chk / s_rev
                    if   0.5   <= ratio <= 2.0:    scale = 1.0
                    elif 500   <= ratio <= 2000:   scale = 1.0 / 1000    # report in actual PKR
                    elif 0.0005 <= ratio <= 0.002: scale = 1000.0        # report in PKR millions
                    else:                          units_ok = False      # can't reconcile
                # Fire whenever the report contributed ANY balance-sheet field the
                # summary lacks (total_assets ideal but not required — filling
                # inventory/receivables/payables still improves efficiency/leverage).
                if units_ok and any(report.get(k) is not None for k in _BS_FIELDS):
                    filled = []
                    for k in _BS_FIELDS:
                        if doc.get(k) is None and report.get(k) is not None:
                            doc[k] = report[k] * scale
                            filled.append(k)
                    if filled:
                        source = "psx_summary+report_bs" + ("_ocr" if report.get("_ocr") else "")
                        log.info("Merged balance-sheet fields from annual report",
                                 ticker=ticker, fields=filled, scale=scale)
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

    # Persist the parsed statement so psx_financials builds a history. Skip the
    # market-cap STUB — it has no real statement figures and (period='latest') would
    # otherwise sort ahead of and shadow the real annual row.
    if not doc.get("_stub"):
        try:
            period = doc.get("_fiscal_year") or "latest"
            if period == "latest":
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
