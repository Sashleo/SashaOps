"""
routers/psx.py - PSX Analyser (Phase 3 Task 3.1).

The analysis page paints its header immediately from psx_companies, then each
section requests its own HTMX partial, so a twelve-section page never blocks on
the slowest section. All data comes from intelligence/psx_analyser.build_analysis;
no template touches the database.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from database.connection import get_db
from security.auth import require_session

log = structlog.get_logger()
router = APIRouter()
templates = Jinja2Templates(directory="templates")

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
    6:  ("Statement Quality", "psx/section_06_quality.html",
         "Auditor's report from the annual report, where readable"),
    7:  ("DuPont Decomposition", "psx/section_07_dupont.html",
         "ROE decomposed into margin, turnover and leverage"),
    8:  ("Distress Prediction", "psx/section_08_distress.html",
         "Altman Z-Score, formula selected by sector"),
    9:  ("Valuation", "psx/section_09_valuation.html",
         "Market price, book value and earnings against listed sector peers"),
    10: ("Regulatory Context", "psx/section_10_regulatory.html",
         "Static compliance rules, Pakistan. No AI, no external call"),
    11: ("Sector Position", "psx/section_11_sector.html",
         "PSX sector peers and computed percentiles"),
    12: ("Investment Considerations", "psx/section_12_considerations.html",
         "Deterministic rules over computed values only"),
}

# All twelve sections render. Each states its own data requirement when the
# underlying filing does not carry the figures, so nothing is silently omitted.
PENDING_SECTIONS: dict = {}

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
    return templates.TemplateResponse("psx.html", {
        "request": request,
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
    return templates.TemplateResponse("psx/_search_results.html", {
        "request": request, "results": results, "q": q,
    })


@router.get("/{ticker}", response_class=HTMLResponse)
async def analysis(request: Request, ticker: str):
    """
    Header renders immediately; each section then loads via its own HTMX request.
    build_analysis runs once here for the header and staleness only.
    """
    require_session(request)
    from intelligence.psx_analyser import build_analysis
    a = build_analysis(ticker)
    return templates.TemplateResponse("psx_analysis.html", {
        "request": request,
        "active_page": "psx",
        "a": a,
        "sections": SECTIONS,
        "pending_sections": PENDING_SECTIONS,
        "refreshing": is_refreshing(a.ticker),
        **_sidebar(request),
    })


@router.get("/{ticker}/section/{n}", response_class=HTMLResponse)
async def section(request: Request, ticker: str, n: int):
    """One section, rendered on its own so the page paints progressively."""
    require_session(request)
    if n not in SECTIONS:
        return HTMLResponse("")
    title, template, note = SECTIONS[n]
    from intelligence.psx_analyser import build_analysis
    a = build_analysis(ticker)
    return templates.TemplateResponse(template, {
        "request": request, "a": a, "n": n, "title": title, "note": note,
    })


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

    from intelligence.psx_analyser import build_analysis
    return templates.TemplateResponse("psx/_staleness.html", {
        "request": request, "a": build_analysis(tk), "refreshing": True,
    })


@router.get("/{ticker}/staleness", response_class=HTMLResponse)
async def staleness(request: Request, ticker: str):
    """Polled while a refresh is in flight; stops polling once it completes."""
    require_session(request)
    from intelligence.psx_analyser import build_analysis
    tk = (ticker or "").strip().upper()
    return templates.TemplateResponse("psx/_staleness.html", {
        "request": request, "a": build_analysis(tk), "refreshing": is_refreshing(tk),
    })


@router.get("/{ticker}/export")
async def export(request: Request, ticker: str, fmt: str = "pdf"):
    """Task 3.9: the twelve-section report, charts embedded as static PNG."""
    require_session(request)
    from fastapi.responses import Response
    from reports.psx_report import build_psx_report_pdf

    tk = (ticker or "").strip().upper()
    pdf_bytes, err = build_psx_report_pdf(tk)
    if err or not pdf_bytes:
        # Say why, rather than returning a broken download.
        return HTMLResponse(
            f"<div class='card'>Report could not be generated for {tk}: "
            f"{err or 'no output'}.</div>", status_code=500)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition":
                f'attachment; filename="{tk}_analysis_{stamp}.pdf"'},
    )
