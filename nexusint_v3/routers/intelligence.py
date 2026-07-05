"""
routers/intelligence.py — Analysis module routes and stage gate endpoints.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.clients import list_clients
from database.analyses import get_all_results_for_run
from database.documents import list_documents
from config.modules import MODULES, get_modules_by_group
from config.settings import settings
from core.run_manager import (
    create_run, load_run, load_latest_active_run,
    approve_stage1, approve_stage2, advance_to_stage1,
    cancel_run, register_task, unregister_task, clear_cancellation,
    RunCancelled, RunMode, RunStage,
)
from intelligence.extended_analysis import EXTENDED_MODULES
# Stage workers now live in core/stage{1,2,3}.py (logic unchanged, split for clarity).
from core.stage1 import run_stage1, _guess_ticker
from core.stage2 import run_stage2
from core.stage3 import run_stage3
# Gov-scraper dashboard helpers (status card + background refresh).
from core.scraper_dashboard import _scraper_statuses, _run_all_scrapers_background

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")

ALL_MODULES = {**MODULES, **EXTENDED_MODULES}


def _sidebar(request: Request) -> dict:
    clients      = list_clients()
    client_id    = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _client_documents(sidebar: dict) -> list:
    ac = sidebar.get("active_client")
    return list_documents(ac["id"]) if ac else []


def _derive_client_key(client_id: str) -> Optional[bytes]:
    """Derive the client encryption key using master_salt from users table."""
    from security.crypto import derive_client_key, derive_master_key, derive_client_key_v2
    from database.connection import get_db
    from database.clients import get_client_key_row
    db = get_db()
    master_row = db.execute("SELECT master_salt FROM users LIMIT 1").fetchone()
    if not master_row or not master_row["master_salt"]:
        return None
    master_key = derive_master_key(settings.DB_MASTER_PASSWORD, bytes(master_row["master_salt"]))
    client_row = get_client_key_row(client_id)
    if not client_row:
        return None
    key_version = client_row.get("key_version") or 1
    if key_version >= 2:
        return derive_client_key_v2(master_key, client_row)
    return derive_client_key(master_key, client_row["salt"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def intelligence_page(
    request: Request,
    run_id: Optional[str] = None,
    business: Optional[str] = None,
    ticker: Optional[str] = None,
    region: Optional[str] = None,
):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    run_state = None
    analysis_results = {}
    # Prefill the business field when arriving from the Markets "Get Full BI Analysis"
    # link (/intelligence?business=...&ticker=...&region=...).
    last_business = (business or "").strip()
    run_mode = "gated"

    # Only auto-load an existing run when we're NOT arriving with a prefill request,
    # so the deep-link lands on a ready-to-run form for that company.
    if active_client and run_id:
        run_state = load_run(run_id)
    elif active_client and not last_business:
        run_state = load_latest_active_run(active_client["id"])

    modules_by_group = get_modules_by_group()
    # Also add extended modules by group
    for mod in EXTENDED_MODULES.values():
        if mod.group not in modules_by_group:
            modules_by_group[mod.group] = []
        modules_by_group[mod.group].append(mod)

    return templates.TemplateResponse("intelligence.html", {
        "request":          request,
        "active_page":      "intelligence",
        "modules_by_group": modules_by_group,
        "all_modules":      ALL_MODULES,
        "run_state":        run_state,
        "analysis_results": analysis_results,
        "last_business":    last_business,
        "run_mode":         run_mode,
        "client_documents": _client_documents(sidebar),
        **sidebar,
    })


@router.post("/start", response_class=HTMLResponse)
async def start_analysis(
    request: Request,
    background_tasks: BackgroundTasks,
    business_name: str = Form(...),
    modules: str = Form(...),
    mode: str = Form("gated"),
    document_ids: str = Form(""),
):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    if not active_client:
        return HTMLResponse("<div class='alert alert-error'>No client selected.</div>")

    module_list  = [m.strip() for m in modules.split(",") if m.strip()]
    doc_ids      = [d.strip() for d in document_ids.split(",") if d.strip()] or None
    run_mode     = RunMode.EXPRESS if mode == "express" else RunMode.GATED
    client_id    = active_client["id"]
    client_key   = _derive_client_key(client_id)
    region_id    = "pakistan"   # single operating market

    # Create run
    run_state = create_run(
        client_id=client_id,
        business_name=business_name,
        modules_requested=module_list,
        mode=run_mode,
    )
    run_state = advance_to_stage1(run_state)

    # Run Stage 1 in background (registered so it can be cancelled)
    _launch_stage(
        run_state.run_id,
        run_stage1(
            run_state.run_id, client_id, business_name, region_id, module_list,
            client_key, doc_ids,
        ),
    )

    # Return progress view
    modules_by_group = get_modules_by_group()
    for mod in EXTENDED_MODULES.values():
        if mod.group not in modules_by_group:
            modules_by_group[mod.group] = []
        modules_by_group[mod.group].append(mod)

    return templates.TemplateResponse("intelligence.html", {
        "request":          request,
        "active_page":      "intelligence",
        "modules_by_group": modules_by_group,
        "all_modules":      ALL_MODULES,
        "run_state":        run_state,
        "last_business":    business_name,
        "run_mode":         mode,
        "analysis_results": {},
        "client_documents": _client_documents(sidebar),
        **sidebar,
    })


@router.get("/run/{run_id}/status", response_class=HTMLResponse)
async def run_status(request: Request, run_id: str):
    require_session(request)
    sidebar    = _sidebar(request)
    run_state  = load_run(run_id)
    client_key = _derive_client_key(sidebar["active_client"]["id"]) if sidebar["active_client"] else None

    analysis_results = {}
    if run_state and run_state.stage in (RunStage.STAGE2_COMPLETE, RunStage.COMPLETE) and client_key:
        analysis_results = get_all_results_for_run(
            client_id=run_state.client_id,
            business_name=run_state.business_name,
            client_key=client_key,
        )

    modules_by_group = get_modules_by_group()
    for mod in EXTENDED_MODULES.values():
        if mod.group not in modules_by_group:
            modules_by_group[mod.group] = []
        modules_by_group[mod.group].append(mod)

    return templates.TemplateResponse("intelligence.html", {
        "request":          request,
        "active_page":      "intelligence",
        "modules_by_group": modules_by_group,
        "all_modules":      ALL_MODULES,
        "run_state":        run_state,
        "analysis_results": analysis_results,
        "last_business":    run_state.business_name if run_state else "",
        "run_mode":         run_state.mode.value if run_state else "gated",
        "client_documents": _client_documents(sidebar),
        **sidebar,
    })


@router.post("/run/{run_id}/cancel", response_class=HTMLResponse)
async def cancel_run_endpoint(request: Request, run_id: str):
    """
    Cancel a running or stuck analysis. This (1) sets the cooperative cancel flag,
    (2) force-cancels the running asyncio task, and (3) marks the run FAILED.
    Returns an HX-Redirect so the browser reloads /intelligence cleanly — this
    stops the 4-second auto-poll and shows a fresh page ready for a new run.
    """
    require_session(request)
    cancel_run(run_id)
    # HX-Redirect makes htmx do a full client-side navigation to a clean page.
    return HTMLResponse(
        "<div class='alert alert-warning'>Run cancelled.</div>",
        headers={"HX-Redirect": "/intelligence"},
    )


@router.post("/gate/{run_id}/approve", response_class=HTMLResponse)
async def approve_gate(
    request: Request,
    background_tasks: BackgroundTasks,
    run_id: str,
    selected_modules: Optional[str] = Form(None),
):
    require_session(request)
    sidebar = _sidebar(request)

    run_state = load_run(run_id)
    if not run_state:
        return HTMLResponse("<div class='alert alert-error'>Run not found.</div>")

    client_key = _derive_client_key(run_state.client_id)

    if run_state.stage == RunStage.STAGE1_COMPLETE:
        run_state = approve_stage1(run_id)
        _launch_stage(
            run_id,
            run_stage2(
                run_id, run_state.client_id, run_state.business_name,
                "pakistan", run_state.modules_pending, client_key,
            ),
        )
    elif run_state.stage == RunStage.STAGE2_COMPLETE:
        mods = selected_modules.split(",") if selected_modules else None
        run_state = approve_stage2(run_id, mods)
        _launch_stage(
            run_id,
            run_stage3(
                run_id, run_state.client_id, run_state.business_name,
                run_state.modules_complete, client_key,
            ),
        )

    modules_by_group = get_modules_by_group()
    for mod in EXTENDED_MODULES.values():
        if mod.group not in modules_by_group:
            modules_by_group[mod.group] = []
        modules_by_group[mod.group].append(mod)

    return templates.TemplateResponse("intelligence.html", {
        "request":          request,
        "active_page":      "intelligence",
        "modules_by_group": modules_by_group,
        "all_modules":      ALL_MODULES,
        "run_state":        run_state,
        "analysis_results": {},
        "last_business":    run_state.business_name,
        "run_mode":         run_state.mode.value,
        "client_documents": _client_documents(sidebar),
        **sidebar,
    })


@router.post("/gate/{run_id}/regenerate-report", response_class=HTMLResponse)
async def regenerate_report(
    request: Request,
    background_tasks: BackgroundTasks,
    run_id: str,
):
    """Re-run Stage 3 (report generation) using the existing analysis results."""
    require_session(request)
    sidebar    = _sidebar(request)
    run_state  = load_run(run_id)
    if not run_state:
        return HTMLResponse("<div class='alert alert-error'>Run not found.</div>")

    client_key = _derive_client_key(run_state.client_id)
    _launch_stage(
        run_id,
        run_stage3(
            run_id, run_state.client_id, run_state.business_name,
            run_state.modules_complete or run_state.modules_requested, client_key,
        ),
    )

    modules_by_group = get_modules_by_group()
    for mod in EXTENDED_MODULES.values():
        if mod.group not in modules_by_group:
            modules_by_group[mod.group] = []
        modules_by_group[mod.group].append(mod)

    analysis_results = {}
    if client_key:
        analysis_results = get_all_results_for_run(
            client_id=run_state.client_id,
            business_name=run_state.business_name,
            client_key=client_key,
        )

    return templates.TemplateResponse("intelligence.html", {
        "request":          request,
        "active_page":      "intelligence",
        "modules_by_group": modules_by_group,
        "all_modules":      ALL_MODULES,
        "run_state":        run_state,
        "analysis_results": analysis_results,
        "last_business":    run_state.business_name,
        "run_mode":         run_state.mode.value,
        "client_documents": _client_documents(sidebar),
        "regen_triggered":  True,
        **sidebar,
    })


# ── Background stage launcher ─────────────────────────────────────────────────

def _launch_stage(run_id: str, coro):
    """
    Launch a stage worker coroutine as a registered, self-cleaning asyncio task.

    Registering the task lets cancel_run() force-cancel it. The done-callback
    unregisters it and clears any cancellation flag so a later run with a reused
    id starts clean. Using create_task (instead of BackgroundTasks) gives us the
    task handle needed for cancellation.
    """
    async def _wrapped():
        try:
            await coro
        except (RunCancelled, asyncio.CancelledError):
            log.info("Stage task cancelled", run_id=run_id)
        finally:
            unregister_task(run_id)
            clear_cancellation(run_id)

    task = asyncio.create_task(_wrapped())
    register_task(run_id, task)
    return task


@router.post("/financial-analysis", response_class=HTMLResponse)
async def quick_financial_analysis(
    request: Request,
    business: str = Form(...),
    region: str = Form("pakistan"),
):
    """
    Standalone local financial analysis — no Gemini, no run queue, instant result.
    Accepts a ticker or company name, resolves to PSX data, and returns a full
    structured financial report (Altman Z breakdown, DuPont, DCF, FinBERT sentiment).
    """
    require_session(request)
    business = business.strip()
    if not business:
        return HTMLResponse('<div class="alert alert-error">Enter a company name or ticker.</div>')

    loop = asyncio.get_running_loop()

    def _run():
        from intelligence.financial_model import (
            run_local_analysis_psx,
            generate_local_financial_report,
            score_announcements_sentiment,
        )
        ticker = _guess_ticker(business)
        if not ticker:
            raise ValueError(f"Could not resolve '{business}' to a PSX ticker.")
        result     = run_local_analysis_psx(ticker)
        sentiment  = score_announcements_sentiment(ticker)
        # Sector peers — direct match on sector_name
        peers = ""
        try:
            from database.connection import get_db as _gdb
            _db = _gdb()
            _co = _db.execute(
                "SELECT sector_name FROM psx_companies WHERE symbol=? LIMIT 1", (ticker,)
            ).fetchone()
            if _co and _co["sector_name"]:
                _ps = _db.execute(
                    "SELECT symbol, company_name FROM psx_companies "
                    "WHERE sector_name=? AND symbol!=? AND is_debt=0 "
                    "ORDER BY company_name LIMIT 10",
                    (_co["sector_name"], ticker),
                ).fetchall()
                if _ps:
                    _lines = [f"PSX SECTOR PEERS — {_co['sector_name']}\n"]
                    for p in _ps:
                        _lines.append(f"  {p['symbol']:8} {p['company_name'][:35]}")
                    peers = "\n".join(_lines)
        except Exception:
            pass
        full_md = generate_local_financial_report(result, sentiment_scores=sentiment, peers_text=peers or None)
        return ticker, result, full_md

    try:
        ticker, result, full_md = await loop.run_in_executor(None, _run)
    except ValueError as e:
        return HTMLResponse(f'<div class="alert alert-error">{e}</div>')
    except Exception as e:
        log.warning("Quick financial analysis failed", business=business, error=str(e))
        return HTMLResponse(
            f'<div class="alert alert-error">Financial analysis failed for "{business}": {str(e)[:160]}</div>'
        )

    from core.report_utils import render_md_to_html as _render_md
    import html as _html_mod

    health     = (result.get("_raw") or {}).get("health_score")
    health_col = "#4ade80" if (health or 0) >= 7 else ("#facc15" if (health or 0) >= 5 else "#f87171")
    stub_note  = ""
    if result.get("_raw", {}).get("_stub") or result.get("source") == "psx_companies_stub":
        stub_note = (
            '<div class="alert alert-warning" style="margin-top:8px;font-size:0.8em">'
            '⚠ Figures estimated from PSX market data (no detailed financials scraped). '
            'Ratios are indicative — verify against official filings.</div>'
        )

    html = f"""
    <div id="financial-analysis-result" style="margin-top:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div>
          <span style="font-weight:700">{_html_mod.escape(result.get('company', ticker))}</span>
          <span style="font-size:0.8em;color:var(--text-muted);margin-left:8px">
            {ticker} · {result.get('currency','PKR')} · FY {result.get('fiscal_year','?')}
          </span>
        </div>
        <div style="font-size:1.1em;font-weight:700;color:{health_col}">
          {f"{health:.1f}" if health is not None else "—"}/10
        </div>
      </div>
      {stub_note}
      <div style="padding:14px;background:var(--bg-2);border-radius:6px;
                  border:1px solid var(--border)">
        {_render_md(full_md)}
      </div>
      <div style="margin-top:10px">
        <a href="/intelligence?business={_html_mod.escape(result.get('company',ticker).replace(' ','+'))}&region={region}"
           class="btn btn-primary" style="font-size:0.85em">
          Run Full Business Intelligence →
        </a>
      </div>
    </div>
    """
    return HTMLResponse(html)


@router.post("/refresh-scrapers", response_class=HTMLResponse)
async def refresh_scrapers(request: Request, background_tasks: BackgroundTasks):
    """Trigger a full scraper refresh in the background. Returns updated status card."""
    require_session(request)
    background_tasks.add_task(_run_all_scrapers_background, "pakistan")
    # Return the status card immediately — it will show "running" state
    statuses = _scraper_statuses()
    return templates.TemplateResponse("partials/scraper_status.html", {
        "request": request,
        "scraper_statuses": statuses,
        "refresh_running": True,
    })


@router.get("/scraper-status", response_class=HTMLResponse)
async def scraper_status(request: Request):
    """Poll endpoint for the scraper status card (HTMX auto-poll while refresh runs)."""
    require_session(request)
    statuses = _scraper_statuses()
    return templates.TemplateResponse("partials/scraper_status.html", {
        "request": request,
        "scraper_statuses": statuses,
        "refresh_running": False,
    })
