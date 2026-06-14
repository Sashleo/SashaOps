"""
routers/intelligence.py — Analysis module routes and stage gate endpoints.
"""

import asyncio
from collections import namedtuple
from typing import Optional

from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.clients import list_clients, get_client, get_client_salt
from database.analyses import save_analysis, get_all_results_for_run
from database.documents import list_documents
from config.modules import MODULES, get_modules_by_group
from config.regions import get_region
from config.settings import settings
from core.run_manager import (
    create_run, load_run, load_latest_active_run,
    approve_stage1, approve_stage2,
    advance_to_stage1, complete_stage1, complete_stage2,
    mark_module_running, mark_module_complete, mark_module_failed,
    fail_run, cancel_run,
    register_task, unregister_task, clear_cancellation,
    check_cancelled, is_cancelled, RunCancelled,
    RunMode, RunStage, DataCollectionSummary, AnalysisSummary,
)
from core.ai_client import call_gemini_raw
from core.privacy_wrapper import build_secure_prompt
from security.sanitiser import build_sanitiser_for_client
from core.memory_engine import build_memory_context, extract_and_store
from intelligence.feeds import fetch_feeds_for_region, classify_headlines, get_feed_summary_text
from intelligence.stocks import fetch_region_market_data
from intelligence.search import multi_search, format_search_results
from intelligence.browser import extract_company_website_content
from intelligence.news_analyser import analyse_news_for_business, format_brief_for_prompt
from intelligence.extended_analysis import EXTENDED_MODULES
from core.feed_cache import get_cached_feeds, set_cached_feeds
from database.exchange_data import get_forex_summary_text, get_index_summary_text
from intelligence.financial_model import format_ratios_for_prompt
from core.sanitiser import TokenSheet, tokenise_financials
from intelligence.verdicts import compute_verdicts
from core.output_validator import validate as _validate_response, build_retry_prompt
from core.narrative_fallbacks import render_fallback

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
async def intelligence_page(request: Request, run_id: Optional[str] = None):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    run_state = None
    analysis_results = {}
    last_business = ""
    run_mode = "gated"

    if active_client and run_id:
        run_state = load_run(run_id)
    elif active_client:
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
        _run_stage1(
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
            _run_stage2(
                run_id, run_state.client_id, run_state.business_name,
                "pakistan", run_state.modules_pending, client_key,
            ),
        )
    elif run_state.stage == RunStage.STAGE2_COMPLETE:
        mods = selected_modules.split(",") if selected_modules else None
        run_state = approve_stage2(run_id, mods)
        _launch_stage(
            run_id,
            _run_stage3(
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
        _run_stage3(
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


# ── Data Sources: on-demand scraper control ───────────────────────────────────

def _scraper_data_preview(scraper_id: str, data: dict) -> list[tuple[str, str]]:
    """
    Return up to 6 (label, value) pairs for display in the Data Sources card.
    Each scraper's cache has a different shape — we pick the most readable fields.
    """
    items: list[tuple[str, str]] = []
    if not data or not isinstance(data, dict):
        return items

    def _fmt(v) -> str:
        if v is None:
            return "—"
        if isinstance(v, dict):
            val = v.get("value")
            unit = v.get("unit", "")
            period = v.get("period", "")
            if val is not None:
                return f"{val:,.2f}{' ' + unit if unit else ''}{' (' + period + ')' if period else ''}"
            return str(v)
        if isinstance(v, (int, float)):
            return f"{v:,.2f}"
        return str(v)[:60]

    if scraper_id == "worldbank_pak":
        label_map = {
            "gdp_growth": "GDP Growth",
            "inflation": "CPI Inflation",
            "unemployment": "Unemployment",
            "current_account_pct_gdp": "Current Account / GDP",
            "exports_pct_gdp": "Exports / GDP",
            "gdp_per_capita_usd": "GDP per Capita",
        }
        for key, label in label_map.items():
            if key in data:
                items.append((label, _fmt(data[key])))
    elif scraper_id.startswith("ministry") or scraper_id == "ministries":
        # ministry data: list of announcement dicts
        entries = data if isinstance(data, list) else data.get("announcements", [])
        for entry in entries[:5]:
            title = entry.get("title") or entry.get("heading") or ""
            date_ = entry.get("date", "")
            if title:
                items.append((date_[:10] if date_ else "—", title[:80]))
    elif scraper_id == "associations":
        entries = data if isinstance(data, list) else data.get("news", [])
        for entry in entries[:5]:
            title = entry.get("title") or entry.get("heading") or ""
            date_ = entry.get("date", "")
            if title:
                items.append((date_[:10] if date_ else "—", title[:80]))
    else:
        # Generic: show scalar values first, then regulatory_text snippets as fallback
        for k, v in data.items():
            if k in ("regulatory_text", "nlp_entities"):
                continue  # handle below
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                items.append((k.replace("_", " ").title(), _fmt(v)))
            elif isinstance(v, dict):
                items.append((k.replace("_", " ").title(), _fmt(v)))
            if len(items) >= 6:
                break

        # If few numeric values found, fill remaining slots with regulatory text snippets
        reg_text = data.get("regulatory_text", [])
        if isinstance(reg_text, list):
            for snippet in reg_text:
                if len(items) >= 6:
                    break
                if snippet and isinstance(snippet, str):
                    items.append(("Regulatory", snippet[:120]))

    return items[:6]


def _scraper_statuses() -> list[dict]:
    """
    Build status list for all registered gov scrapers and feeds.
    Cache files use date-suffix naming: {scraper_id}_YYYY-MM-DD.json
    Falls back to exact-match {scraper_id}.json for any scraper that writes that way.
    """
    from pathlib import Path
    from datetime import datetime, timezone
    import json

    statuses = []
    cache_dir = Path(".tmp/gov_cache")

    scraper_defs = [
        ("worldbank_pak",  "World Bank Pakistan"),
        ("sbp_monetary",   "SBP Monetary Policy"),
        ("ogra_prices",    "OGRA Petroleum Prices"),
        ("pbs_lsmi",       "PBS Manufacturing Index"),
        ("fbr_revenue",    "FBR Revenue Statistics"),
        ("nepra_power",    "NEPRA Power Sector"),
        ("pta_stats",      "PTA Telecom Statistics"),
        ("ministries",     "Ministry Policy Feeds"),
        ("associations",   "Industry Associations"),
    ]

    for scraper_id, label in scraper_defs:
        status: dict = {
            "id": scraper_id,
            "label": label,
            "last_run": None,
            "age_minutes": None,
            "record_count": None,
            "status": "never_run",
        }

        # Find the most recent matching file: exact name or date-suffixed variants
        candidates: list[Path] = []
        if cache_dir.exists():
            exact = cache_dir / f"{scraper_id}.json"
            if exact.exists():
                candidates.append(exact)
            candidates.extend(sorted(cache_dir.glob(f"{scraper_id}_*.json")))
            if scraper_id == "ministries":
                candidates.extend(sorted(cache_dir.glob("ministry_*.json")))
            elif scraper_id == "associations":
                candidates.extend(sorted(cache_dir.glob("assoc_*.json")))

        if candidates:
            cache_file = max(candidates, key=lambda p: p.stat().st_mtime)
            try:
                mtime = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc)
                age = (datetime.now(timezone.utc) - mtime).total_seconds() / 60
                status["last_run"] = mtime.strftime("%Y-%m-%d %H:%M")
                status["age_minutes"] = int(age)
                status["status"] = "ok" if age < 1440 else "stale"

                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    status["record_count"] = len(data)
                elif isinstance(data, dict):
                    status["record_count"] = len(data)
                # Build a short human-readable preview of extracted values
                status["preview"] = _scraper_data_preview(scraper_id, data)
            except Exception:
                status["status"] = "error"
        else:
            # No cache file — check scraper_runs DB for "partial" (ran, got nothing)
            try:
                from database.connection import get_db
                db = get_db()
                run_row = db.execute(
                    "SELECT status, completed_at, error_msg FROM scraper_runs "
                    "WHERE scraper_id = ? ORDER BY completed_at DESC LIMIT 1",
                    (scraper_id,),
                ).fetchone()
                if run_row:
                    status["status"] = "partial"  # ran but no extractable data
                    status["last_run"] = (run_row["completed_at"] or "")[:16].replace("T", " ")
                    status["error_msg"] = run_row["error_msg"] or ""
            except Exception:
                pass

        statuses.append(status)

    return statuses


async def _run_all_scrapers_background(region_id: str = "pakistan") -> None:
    """Background task: run all gov scrapers and feed fetchers."""
    import asyncio
    loop = asyncio.get_running_loop()

    try:
        from intelligence.gov_scrapers import get_scrapers_for_run
        scrapers = get_scrapers_for_run("general", include_ministries=True)

        async def _run_one(scraper):
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, scraper.fetch),
                    timeout=60,
                )
                log.info("Scraper complete", scraper=scraper.__class__.__name__)
            except Exception as e:
                log.warning("Scraper failed", scraper=scraper.__class__.__name__,
                            error=str(e)[:80])

        await asyncio.gather(*[_run_one(s) for s in scrapers])

        # Also refresh feeds
        try:
            from intelligence.feeds import fetch_feeds_for_region
            from core.feed_cache import set_cached_feeds
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: fetch_feeds_for_region(region_id)),
                timeout=90,
            )
            if result:
                set_cached_feeds(region_id, result)
                log.info("Feeds refreshed", region=region_id)
        except Exception as e:
            log.warning("Feed refresh failed", error=str(e)[:80])

    except Exception as e:
        log.error("Scraper refresh batch failed", error=str(e))


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
