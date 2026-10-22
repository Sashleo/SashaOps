"""
routers/psx.py — PSX Analyser routes.

The analysis page renders its header immediately, then each section loads via
its own HTMX partial. All data comes from psx_analyser.build_analysis.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from database.connection import get_db
from security.auth import require_session

log = structlog.get_logger()
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ── build_analysis cache ───────────────────────────────────────────────────────
# Keyed by ticker; invalidated on save-overrides, clear-overrides, refresh-profile.
# TTL is a safety net so a stale entry never survives more than 5 minutes.
import time as _time
_ANALYSIS_CACHE: dict = {}
_CACHE_TTL = 300


def _cached_build(ticker: str):
    """Return a cached PSXAnalysis, or call build_analysis() and cache the result."""
    from intelligence.psx_analyser import build_analysis
    entry = _ANALYSIS_CACHE.get(ticker)
    if entry and _time.monotonic() - entry[0] < _CACHE_TTL:
        return entry[1]
    a = build_analysis(ticker)
    _ANALYSIS_CACHE[ticker] = (_time.monotonic(), a)
    return a


def _invalidate(ticker: str) -> None:
    _ANALYSIS_CACHE.pop(ticker, None)

# Section number -> (title, template, one-line data-source note). Only the sections
# that have a data path today are registered; the rest are declared as pending so
# the page states what is coming rather than rendering an empty card.
SECTIONS = {
    1:  ("Company Profile", "psx/section_01_profile.html",
         "PSX company profile scrape"),
    2:  ("Statement of Financial Position", "psx/section_02_balance.html",
         "Annual report statement of financial position, where extractable"),
    3:  ("Statement of Profit or Loss", "psx/section_03_income.html",
         "PSX financial statements, annual periods"),
    4:  ("Statement of Cash Flows", "psx/section_04_cashflow.html",
         "Annual report statement of cash flows, where extractable"),
    5:  ("Ratio Analysis", "psx/section_05_ratios.html",
         "Computed from parsed statements; benchmarked against sector medians"),
    7:  ("DuPont Decomposition", "psx/section_07_dupont.html",
         "ROE decomposed into margin, turnover and leverage"),
    8:  ("Distress Prediction", "psx/section_08_distress.html",
         "Altman Z-Score, formula selected by sector"),
    9:  ("Valuation", "psx/section_09_valuation.html",
         "Market price, book value and earnings against listed sector peers"),
    10: ("Sector Position", "psx/section_11_sector.html",
         "PSX sector peers and computed percentiles"),
    11: ("Investment Considerations", "psx/section_12_considerations.html",
         "Deterministic rules over computed values only"),
    12: ("Data Entry and DCF", "psx/section_13_entry.html",
         "Fill missing fields and run a discounted cash flow valuation"),
}

# All twelve sections render. Each states its own data requirement when the
# underlying filing does not carry the figures, so nothing is silently omitted.
PENDING_SECTIONS: list = [
    "Statement Quality (section 6): requires audit report text from an annual report PDF. "
    "Audit opinion, going-concern status and auditor identity are not computed.",
]

# Tickers currently being refreshed, so the banner can show progress and the
# poller knows when to stop.
_refreshing: set[str] = set()
_lock = threading.Lock()


def _sidebar(request: Request) -> dict:
    from database.clients import list_clients
    session = getattr(request.state, "session", {}) or {}
    clients = list_clients()
    cid = session.get("client_id")
    return {"clients": clients,
            "active_client": next((c for c in clients if c["id"] == cid), None)}


def _sectors() -> list:
    try:
        rows = get_db().execute(
            "SELECT DISTINCT sector_name FROM psx_companies "
            "WHERE sector_name IS NOT NULL AND COALESCE(is_debt,0)=0 "
            "ORDER BY sector_name"
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def _recent(limit: int = 25, sector: str = "") -> list:
    """Companies with stored financials, newest first - the analysable set."""
    sql = """SELECT c.symbol, c.company_name, c.sector_name, c.market_cap,
                    MAX(f.fetched_at) AS last_seen, COUNT(f.period) AS periods
             FROM psx_companies c
             JOIN psx_financials f ON f.symbol = c.symbol
                  AND f.period_type = 'annual' AND f.revenue IS NOT NULL
             WHERE COALESCE(c.is_debt,0)=0 {sector_clause}
             GROUP BY c.symbol
             ORDER BY last_seen DESC LIMIT ?"""
    params: list = []
    clause = ""
    if sector:
        clause = "AND c.sector_name = ?"
        params.append(sector)
    params.append(limit)
    try:
        rows = get_db().execute(sql.format(sector_clause=clause), params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.info("recent lookup failed", error=str(e)[:100])
        return []


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, sector: str = ""):
    require_session(request)
    return templates.TemplateResponse(request, "psx.html", {
        "active_page": "psx",
        "sectors": _sectors(),
        "selected_sector": sector,
        "recent": _recent(sector=sector),
        **_sidebar(request),
    })


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    """Typeahead over ticker and company name."""
    require_session(request)
    q = (q or "").strip()
    results: list = []
    if len(q) >= 1:
        try:
            like = f"%{q}%"
            rows = get_db().execute(
                """SELECT symbol, company_name, sector_name FROM psx_companies
                   WHERE COALESCE(is_debt,0)=0
                     AND (symbol LIKE ? OR company_name LIKE ?)
                   ORDER BY CASE WHEN symbol = ? THEN 0
                                 WHEN symbol LIKE ? THEN 1 ELSE 2 END,
                            market_cap DESC NULLS LAST
                   LIMIT 12""",
                (like, like, q.upper(), f"{q.upper()}%"),
            ).fetchall()
            results = [dict(r) for r in rows]
        except Exception as e:
            log.info("search failed", error=str(e)[:100])
    return templates.TemplateResponse(request, "psx/_search_results.html", {
        "results": results, "q": q,
    })


def _fill_dcf_defaults(prefill: dict, is_bank: bool = False) -> None:
    """Populate DCF assumption fields from the resolved rate store (override→live→fallback)."""
    try:
        from intelligence.dcf_rates import get_dcf_rates
        rates = get_dcf_rates(is_bank=is_bank)
    except Exception:
        rates = {"risk_free_rate": 11.5, "equity_risk_premium": 5.0,
                 "tax_rate": 29.0, "terminal_growth": 8.0}
    if "risk_free_rate" not in prefill:
        prefill["risk_free_rate"] = round(rates["risk_free_rate"], 2)
    if "equity_risk_premium" not in prefill:
        prefill["equity_risk_premium"] = round(rates["equity_risk_premium"], 2)
    if "tax_rate" not in prefill:
        prefill["tax_rate"] = round(rates["tax_rate"], 2)
    if "terminal_growth" not in prefill:
        prefill["terminal_growth"] = round(rates["terminal_growth"], 2)


def _revenue_cagr(a) -> float:
    """Historical revenue CAGR from all available annual periods (newest first in a)."""
    from intelligence.metrics import compute_cagr
    revs = [a.income_statement.get(p, {}).get("revenue")
            for p in a.annual_periods]
    revs = [r for r in revs if r is not None and r > 0]
    if len(revs) >= 2:
        cagr = compute_cagr(list(reversed(revs)), len(revs) - 1)
        if cagr is not None:
            return cagr
    return 0.12   # 12% nominal fallback — roughly Pakistan GDP growth rate


TABS = ["overview", "financials", "ratios", "risk", "sector", "notes", "entry"]
TAB_LABELS = {
    "overview": "Overview", "financials": "Financials", "ratios": "Ratios",
    "risk": "Risk and valuation", "sector": "Sector", "notes": "Notes and sources",
    "entry": "Data entry",
}


@router.get("/{ticker}", response_class=HTMLResponse)
async def analysis(request: Request, ticker: str, tab: str = "overview"):
    require_session(request)
    tk = (ticker or "").strip().upper()
    a = _cached_build(tk)

    active_tab = tab if tab in TABS else "overview"

    prefill = {}
    if active_tab == "entry":
        latest = a.latest_period
        if latest:
            inc = a.income_statement.get(latest, {})
            bs = a.balance_sheet.get(latest, {})
            cf = a.cash_flow.get(latest, {})
            for k, v in {**inc, **bs, **cf}.items():
                if v is not None:
                    prefill[k] = v / 1_000.0 if k != "eps" else v
        if a.shares_outstanding:
            prefill["shares_outstanding"] = a.shares_outstanding / 1_000_000
        if a.current_price:
            prefill["current_price"] = a.current_price
        _fill_dcf_defaults(prefill, a.is_bank)

    return templates.TemplateResponse(request, "psx_analysis.html", {
        "active_page": "psx",
        "a": a,
        "tabs": TABS,
        "tab_labels": TAB_LABELS,
        "active_tab": active_tab,
        "sections": SECTIONS,
        "pending_sections": PENDING_SECTIONS,
        "refreshing": is_refreshing(a.ticker),
        "mode": "psx",
        "prefill": prefill,
        "sectors": [],
        "ticker": tk,
        "entry_period": a.latest_period or "",
        **_sidebar(request),
    })


@router.get("/{ticker}/tab/{tab_name}", response_class=HTMLResponse)
async def tab_panel(request: Request, ticker: str, tab_name: str):
    require_session(request)
    if tab_name not in TABS:
        return HTMLResponse("")
    tk = (ticker or "").strip().upper()
    t0 = _time.monotonic()
    a = _cached_build(tk)
    build_ms = int((_time.monotonic() - t0) * 1000)
    log.debug("tab panel", ticker=tk, tab=tab_name, build_ms=build_ms)

    ctx = {
        "a": a, "active_tab": tab_name,
        "tabs": TABS, "tab_labels": TAB_LABELS,
        "sections": SECTIONS, "pending_sections": PENDING_SECTIONS,
    }
    if tab_name == "entry":
        prefill = {}
        latest = a.latest_period
        if latest:
            inc = a.income_statement.get(latest, {})
            bs = a.balance_sheet.get(latest, {})
            cf = a.cash_flow.get(latest, {})
            for k, v in {**inc, **bs, **cf}.items():
                if v is not None:
                    prefill[k] = v / 1_000.0 if k != "eps" else v
        if a.shares_outstanding:
            prefill["shares_outstanding"] = a.shares_outstanding / 1_000_000
        if a.current_price:
            prefill["current_price"] = a.current_price
        _fill_dcf_defaults(prefill, a.is_bank)
        ctx.update(mode="psx", prefill=prefill, sectors=[], ticker=tk,
                   entry_period=a.latest_period or "")

    return templates.TemplateResponse(request, "psx/_tab_panel_response.html", ctx)


def is_refreshing(ticker: str) -> bool:
    with _lock:
        return ticker.upper() in _refreshing


@router.post("/{ticker}/refresh", response_class=HTMLResponse)
async def refresh(request: Request, ticker: str):
    """
    Queue a targeted re-scrape of this ticker only (profile, financials,
    announcements, price). Never blocks the page; the banner polls until done.
    """
    require_session(request)
    tk = (ticker or "").strip().upper()

    def _worker():
        with _lock:
            _refreshing.add(tk)
        try:
            from intelligence.exchange_scrapers.psx_company import PSXCompanyProfileScraper
            PSXCompanyProfileScraper().per_target(tk)
        except Exception as e:
            log.info("refresh: profile failed", ticker=tk, error=str(e)[:100])
        try:
            from intelligence.psx_financials import fetch_psx_doc_financials
            fetch_psx_doc_financials(tk)
        except Exception as e:
            log.info("refresh: financials failed", ticker=tk, error=str(e)[:100])
        finally:
            with _lock:
                _refreshing.discard(tk)

    if not is_refreshing(tk):
        threading.Thread(target=_worker, name=f"psx-refresh-{tk}", daemon=True).start()

    # Return immediately — do NOT call build_analysis here; it takes 1–3 s and
    # makes the button appear frozen. The polling loop (GET /staleness) handles
    # the full analysis once the scrape finishes.
    return HTMLResponse(f"""
<div id="psx-staleness"
     hx-get="/psx/{tk}/staleness"
     hx-trigger="every 4s"
     hx-target="#psx-staleness"
     hx-swap="outerHTML">
  <div class="card mb-24" style="border-left:3px solid #6b7280">
    <div style="font-size:0.88em; color:var(--text-secondary)">
      Refreshing {tk}: profile, financials and announcements.
      Page reloads automatically when the scrape completes.
    </div>
  </div>
</div>
""")


@router.get("/{ticker}/staleness", response_class=HTMLResponse)
async def staleness(request: Request, ticker: str):
    """Polled while a refresh is in flight; stops polling once it completes."""
    require_session(request)
    from intelligence.psx_analyser import build_analysis
    tk = (ticker or "").strip().upper()
    still_refreshing = is_refreshing(tk)
    resp = templates.TemplateResponse(request, "psx/_staleness.html", {
        "a": build_analysis(tk), "refreshing": still_refreshing,
    })
    # When the scrape finishes, tell HTMX to reload the full page so all
    # section data (charts, ratios, verdicts) reflects the fresh scraped values.
    if request.headers.get("HX-Request") and not still_refreshing:
        resp.headers["HX-Refresh"] = "true"
    return resp


@router.get("/{ticker}/entry")
async def entry_form(request: Request, ticker: str):
    """Redirect legacy entry URL to the entry tab."""
    from fastapi.responses import RedirectResponse
    tk = (ticker or "").strip().upper()
    return RedirectResponse(f"/psx/{tk}?tab=entry", status_code=302)


@router.post("/{ticker}/dcf", response_class=HTMLResponse)
async def run_dcf(request: Request, ticker: str):
    """Run DCF with user-supplied assumptions over scanner data."""
    require_session(request)
    from intelligence.psx_analyser import build_analysis
    from intelligence.metrics import compute_dcf

    tk = (ticker or "").strip().upper()
    a = build_analysis(tk)
    form = dict(await request.form())

    def _f(key):
        v = form.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    latest = a.latest_period
    if not latest:
        return HTMLResponse("<div class='card'>No financial data available.</div>")

    inc = a.income_statement.get(latest, {})
    bs = a.balance_sheet.get(latest, {})
    cf = a.cash_flow.get(latest, {})

    rfr = _f("risk_free_rate")
    erp = _f("equity_risk_premium")
    beta = _f("beta")
    tax_rate = _f("tax_rate")
    tg = _f("terminal_growth")
    shares = _f("shares_outstanding")
    price = _f("current_price")

    required = {"risk_free_rate": rfr, "equity_risk_premium": erp,
                "beta": beta, "tax_rate": tax_rate, "terminal_growth": tg}
    missing = [k.replace("_", " ") for k, v in required.items() if v is None]
    if missing:
        return HTMLResponse(
            f"<div class='card'><div class='card-title'>DCF</div>"
            f"<p style='color:var(--text-muted)'>Missing: {', '.join(missing)}</p></div>")

    tax_expense = (inc.get("ebit") or 0) * (tax_rate / 100.0)
    pbt = (inc.get("ebit") or 0) - (inc.get("interest_expense") or 0)

    dcf = compute_dcf(
        revenue=inc.get("revenue") or 0,
        ebit=inc.get("ebit") or 0,
        depreciation=inc.get("depreciation") or 0,
        capex=abs(cf.get("capex") or 0),
        ocf=cf.get("ocf") or 0,
        current_assets=bs.get("current_assets") or 0,
        current_liabilities=bs.get("current_liabilities") or 0,
        cash=bs.get("cash") or 0,
        short_term_borrowings=0.0,
        total_debt=bs.get("total_debt") or 0,
        interest_expense=inc.get("interest_expense") or 0,
        tax_expense=tax_expense,
        pbt=pbt,
        # shares from form is in millions; a.shares_outstanding is already absolute
        shares_outstanding=(shares * 1_000_000 if shares else (a.shares_outstanding or 1_000_000)),
        risk_free_rate=rfr / 100.0,
        equity_risk_premium=erp / 100.0,
        beta=beta,
        tax_rate=tax_rate / 100.0,
        terminal_growth=tg / 100.0,
        current_price=price or a.current_price,
        scenario_assumptions={"base": {"revenue_growth": _revenue_cagr(a)}},
        is_bank=a.is_bank,
    )

    return templates.TemplateResponse(request, "psx/_dcf_result.html", {
        "a": a, "dcf": dcf,
    })


_OVERRIDE_FIELDS = (
    "revenue", "gross_profit", "ebitda", "ebit", "net_income", "interest_expense",
    "depreciation", "total_assets", "total_equity", "total_debt", "cash",
    "current_assets", "current_liabilities", "retained_earnings", "inventory",
    "receivables", "payables", "ocf", "capex",
)


@router.post("/{ticker}/save-overrides", response_class=HTMLResponse)
async def save_overrides(request: Request, ticker: str):
    """Save the entry form values to financial_overrides for this ticker."""
    require_session(request)
    from datetime import datetime as _dt

    tk = (ticker or "").strip().upper()
    form = dict(await request.form())
    period = (form.get("period") or "").strip()
    if not period:
        return HTMLResponse("<span style='color:var(--neg);font-size:0.85em'>No period — reload the page.</span>")

    db = get_db()
    now = _dt.utcnow().isoformat()
    saved = 0
    for field_name in _OVERRIDE_FIELDS:
        raw = form.get(field_name, "")
        if raw == "":
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        db.execute(
            """INSERT INTO financial_overrides (ticker, period, field, value_pkr_t, edited_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(ticker, period, field) DO UPDATE SET value_pkr_t=excluded.value_pkr_t,
                   edited_at=excluded.edited_at""",
            (tk, period, field_name, val, now),
        )
        saved += 1
    db.commit()
    _invalidate(tk)
    log.info("Financial overrides saved", ticker=tk, period=period, fields=saved)
    return HTMLResponse(
        f"<span style='color:var(--pos);font-size:0.85em'>Saved {saved} field(s) for {tk} {period}.</span>"
    )


@router.post("/{ticker}/clear-overrides", response_class=HTMLResponse)
async def clear_overrides(request: Request, ticker: str):
    """Delete all owner-entered overrides for this ticker."""
    require_session(request)
    tk = (ticker or "").strip().upper()
    db = get_db()
    db.execute("DELETE FROM financial_overrides WHERE ticker = ?", (tk,))
    db.commit()
    _invalidate(tk)
    log.info("Financial overrides cleared", ticker=tk)
    return HTMLResponse(
        f"<span style='color:var(--text-muted);font-size:0.85em'>Overrides cleared for {tk}.</span>"
    )


@router.post("/{ticker}/analyse-entry", response_class=HTMLResponse)
async def analyse_entry(request: Request, ticker: str):
    """Run the full metrics suite with user-supplied form overrides over scanner data.
    Used by the PSX entry tab 'Analyse' button to show ratios, DuPont, distress and
    DCF in one shot — not just DCF — whenever the user fills in extra fields."""
    require_session(request)
    from intelligence.psx_analyser import build_analysis
    from intelligence.metrics import compute_all, compute_dcf, compute_health_score

    tk = (ticker or "").strip().upper()
    a = build_analysis(tk)
    form = dict(await request.form())

    def _f(key):
        v = form.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    latest = a.latest_period
    if not latest:
        return HTMLResponse("<div class='empty-state' style='padding:var(--s-4)'>"
                            "No financial data available for this ticker.</div>")

    # Merge: form value wins over scanner value; fall back to scanner.
    db_inc = a.income_statement.get(latest, {})
    db_bs  = a.balance_sheet.get(latest, {})
    db_cf  = a.cash_flow.get(latest, {})

    def merge(db_dict, keys):
        return {k: (_f(k) if _f(k) is not None else db_dict.get(k)) for k in keys}

    inc = merge(db_inc, ["revenue", "gross_profit", "ebitda", "ebit", "net_income",
                          "interest_expense", "depreciation", "eps"])
    bs  = merge(db_bs,  ["total_assets", "total_equity", "total_debt", "cash",
                          "current_assets", "current_liabilities", "retained_earnings",
                          "inventory", "receivables", "payables"])
    cf  = merge(db_cf,  ["ocf", "capex"])

    result = compute_all(inc, bs, cf, sector=a.sector or "")
    result["health"] = compute_health_score(result.get("ratios", {}))

    # DCF — only if user supplied at least the five required assumptions
    rfr   = _f("risk_free_rate")
    erp   = _f("equity_risk_premium")
    beta  = _f("beta")
    taxr  = _f("tax_rate")
    tg    = _f("terminal_growth")
    shares = _f("shares_outstanding")
    price  = _f("current_price")

    dcf = None
    if all(v is not None for v in (rfr, erp, beta, taxr, tg)):
        tax_expense = (inc.get("ebit") or 0) * (taxr / 100.0)
        pbt = (inc.get("ebit") or 0) - (inc.get("interest_expense") or 0)
        dcf = compute_dcf(
            revenue=inc.get("revenue") or 0,
            ebit=inc.get("ebit") or 0,
            depreciation=inc.get("depreciation") or 0,
            capex=abs(cf.get("capex") or 0),
            ocf=cf.get("ocf") or 0,
            current_assets=bs.get("current_assets") or 0,
            current_liabilities=bs.get("current_liabilities") or 0,
            cash=bs.get("cash") or 0,
            short_term_borrowings=0.0,
            total_debt=bs.get("total_debt") or 0,
            interest_expense=inc.get("interest_expense") or 0,
            tax_expense=tax_expense,
            pbt=pbt,
            # shares from form is in millions; a.shares_outstanding is already absolute
            shares_outstanding=(shares * 1_000_000 if shares else (a.shares_outstanding or 1_000_000)),
            risk_free_rate=rfr / 100.0,
            equity_risk_premium=erp / 100.0,
            beta=beta,
            tax_rate=taxr / 100.0,
            terminal_growth=tg / 100.0,
            current_price=price or a.current_price,
            scenario_assumptions={"base": {"revenue_growth": _revenue_cagr(a)}},
            is_bank=a.is_bank,
        )

    return templates.TemplateResponse(request, "psx/_entry_result.html", {
        "ticker":  tk,
        "period":  latest,
        "result":  result,
        "dcf":     dcf,
    })


@router.post("/{ticker}/refresh-profile", response_class=HTMLResponse)
async def refresh_profile(request: Request, ticker: str):
    """Manually re-scrape the PSX company page for this one ticker and return
    a status fragment that HTMX swaps into the refresh button area."""
    require_session(request)
    tk = (ticker or "").strip().upper()
    try:
        from intelligence.exchange_scrapers.psx_company import PSXCompanyProfileScraper
        result = PSXCompanyProfileScraper().per_target(tk)
        ok = result.status.value in ("success", "partial")
        if ok:
            _invalidate(tk)
            return HTMLResponse(
                f"<span style='color:var(--pos);font-size:0.82em'>"
                f"Profile refreshed — {result.rows_inserted} rows updated. "
                f"<a href='/psx/{tk}?tab=overview' style='color:inherit'>Reload page to see changes</a></span>")
        return HTMLResponse(
            f"<span style='color:var(--warn);font-size:0.82em'>"
            f"Refresh completed with status: {result.status.value}. "
            f"{result.error_message or ''}</span>")
    except Exception as e:
        log.warning("Manual profile refresh failed", ticker=tk, error=str(e))
        return HTMLResponse(
            f"<span style='color:var(--neg);font-size:0.82em'>Refresh failed: {str(e)[:120]}</span>",
            status_code=200)


@router.get("/{ticker}/export")
async def export(request: Request, ticker: str, fmt: str = "pdf"):
    """Task 3.9: the twelve-section report, signed with ML-DSA."""
    require_session(request)
    import os
    import uuid
    from datetime import datetime as _dt
    from fastapi.responses import Response
    from reports.psx_report import build_psx_report_pdf
    from reports.signer import ensure_signing_key, sign_report
    from security.crypto import derive_master_key, encrypt_bytes
    from config.settings import settings

    tk = (ticker or "").strip().upper()
    pdf_bytes, err = build_psx_report_pdf(tk)
    if err or not pdf_bytes:
        return HTMLResponse(
            f"<div class='card'>Report could not be generated for {tk}: "
            f"{err or 'no output'}.</div>", status_code=500)

    sig_header = "unsigned"
    try:
        db = get_db()
        row = db.execute("SELECT master_salt FROM users LIMIT 1").fetchone()
        if row and row["master_salt"]:
            master_key = derive_master_key(settings.DB_MASTER_PASSWORD, bytes(row["master_salt"]))
            ensure_signing_key(master_key)
            sig_bytes, sig_err = sign_report(pdf_bytes, master_key)
            if sig_err:
                log.warning("PSX report signing failed", ticker=tk, error=sig_err)
            else:
                sig_header = "verified"
                report_id = str(uuid.uuid4())
                report_dir = "/app/data/reports"
                os.makedirs(report_dir, exist_ok=True)
                sig_path = os.path.join(report_dir, f"{tk}_{report_id[:8]}.sig")
                with open(sig_path, "wb") as f:
                    f.write(sig_bytes)

                # Store in DB under active client if one is selected
                session = getattr(request.state, "session", {}) or {}
                client_id = session.get("client_id")
                if client_id:
                    from database.clients import get_client_salt
                    from security.crypto import derive_master_key, derive_client_key
                    client_salt = get_client_salt(client_id)
                    if client_salt:
                        client_key = derive_client_key(master_key, client_salt)
                        enc_bytes = encrypt_bytes(pdf_bytes, client_key)
                        pdf_path = os.path.join(report_dir, f"{tk}_{report_id[:8]}.pdf.enc")
                        with open(pdf_path, "wb") as f:
                            f.write(enc_bytes)
                        algo_row = db.execute(
                            "SELECT algorithm FROM pqc_keys WHERE key_type='signing' AND is_active=1 LIMIT 1"
                        ).fetchone()
                        db.execute(
                            """INSERT OR IGNORE INTO reports
                               (id, client_id, ticker, business_name, report_type,
                                pdf_path, signature_path, signing_algo, created_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (report_id, client_id, tk, tk, "psx_export",
                             pdf_path, sig_path,
                             algo_row["algorithm"] if algo_row else None,
                             _dt.utcnow().isoformat()),
                        )
                        db.commit()
                        log.info("PSX report stored", ticker=tk, report_id=report_id)
    except Exception as e:
        log.warning("PSX report signing/storage failed", ticker=tk, error=str(e))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{tk}_analysis_{stamp}.pdf"',
            "X-Report-Signature": sig_header,
        },
    )
