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
    fail_run,
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

    # Run Stage 1 in background
    background_tasks.add_task(
        _run_stage1,
        run_state.run_id, client_id, business_name, region_id, module_list, client_key,
        doc_ids,
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
        background_tasks.add_task(
            _run_stage2,
            run_id, run_state.client_id, run_state.business_name,
            "pakistan", run_state.modules_pending, client_key,
        )
    elif run_state.stage == RunStage.STAGE2_COMPLETE:
        mods = selected_modules.split(",") if selected_modules else None
        run_state = approve_stage2(run_id, mods)
        background_tasks.add_task(
            _run_stage3, run_id, run_state.client_id, run_state.business_name,
            run_state.modules_complete, client_key,
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
    background_tasks.add_task(
        _run_stage3, run_id, run_state.client_id, run_state.business_name,
        run_state.modules_complete or run_state.modules_requested, client_key,
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


# ── Background stage workers ──────────────────────────────────────────────────

async def _run_stage1(run_id, client_id, business_name, region_id, module_list,
                      client_key, document_ids=None):
    """
    Stage 1: Collect all data sources in parallel.

    Independent tasks (feeds, market data, DDG search, website scrape) run
    concurrently with hard per-task timeouts so a single slow source cannot
    block the entire stage.
    """
    try:
        loop        = asyncio.get_running_loop()
        summary     = DataCollectionSummary()

        # ── Helper: run a blocking callable with a hard timeout ───────────────
        async def _bounded(fn, timeout_s: float):
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, fn),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                log.warning("Stage 1 task timed out", task=fn.__name__ if hasattr(fn, '__name__') else repr(fn)[:40], timeout=timeout_s)
                return None
            except Exception as exc:
                log.warning("Stage 1 task failed", error=str(exc)[:120])
                return None

        # ── Phase A: feeds (needed by news brief in Phase B) ─────────────────
        cached, cache_age = get_cached_feeds(region_id)
        if cached:
            feed_result = cached
            log.info("Using cached feeds", region=region_id, age_min=cache_age // 60)
        else:
            def _fetch_feeds():
                return fetch_feeds_for_region(region_id)
            _fetch_feeds.__name__ = "fetch_feeds"
            feed_result = await _bounded(_fetch_feeds, timeout_s=90)
            if feed_result is None:
                from intelligence.feeds import FeedResult
                feed_result = FeedResult()
            set_cached_feeds(region_id, feed_result)

        # Process feed results
        summary.headlines_collected = feed_result.total_headlines
        summary.sources_found.extend(feed_result.sources_fetched)
        summary.sources_failed.extend(feed_result.sources_failed)
        summary.source_details = [
            {
                "feed_id":        s.feed_id,
                "label":          s.label,
                "status":         s.status,
                "headline_count": s.headline_count,
                "latency_ms":     s.latency_ms,
                "error_msg":      s.error_msg,
            }
            for s in feed_result.source_details
        ]
        if feed_result.items:
            classify_headlines(feed_result.items, business_name, region_id)
        sorted_items = sorted(feed_result.items, key=lambda x: x.magnitude, reverse=True)
        summary.sample_headlines = [
            f"{item.title} [{item.feed_label}]" for item in sorted_items[:5]
        ]

        # ── Pre-step: resolve PSX ticker + profile (fast, drives the rest) ────
        from intelligence.psx_company_profile import get_psx_company_profile
        from intelligence.keyword_engine import detect_industry
        from intelligence.gov_scrapers import get_scrapers_for_run
        from intelligence.query_templates import build_queries_for_module

        ticker = _guess_ticker(business_name)
        psx_profile = {}
        if ticker:
            try:
                psx_profile = await _bounded(
                    (lambda: get_psx_company_profile(ticker)), timeout_s=12
                ) or {}
            except Exception:
                psx_profile = {}
        psx_sector  = psx_profile.get("sector") or ""
        psx_url     = psx_profile.get("website_url")
        # Provisional industry from PSX sector (authoritative); refined after website scrape
        industry    = detect_industry("", psx_sector)

        # ── Phase B: all remaining sources run IN PARALLEL ───────────────────
        def _market():
            return fetch_region_market_data(region_id)

        def _queries():
            # Keyword-driven, Pakistan-specific queries (no AI). Keyword refinement
            # happens after the website scrape; here we seed from industry + sector.
            kws = [k for k in (psx_sector.lower().split() if psx_sector else []) if len(k) > 3]
            qs = []
            for mod_id in ("overview", "market_position", "regulatory"):
                qs.extend(build_queries_for_module(mod_id, business_name, industry, kws))
            return multi_search(qs[:5])

        def _website():
            return extract_company_website_content(business_name, ticker=ticker, psx_url=psx_url)

        def _gov_data():
            # Run scrapers in parallel (max 6 threads) instead of sequentially.
            # Each scraper has its own 24h cache, so parallel is safe.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            scrapers = get_scrapers_for_run(industry, include_ministries=True)
            results: dict = {}
            if not scrapers:
                return results
            with ThreadPoolExecutor(max_workers=6) as ex:
                futures = {ex.submit(scraper.fetch): scraper.scraper_id
                           for scraper in scrapers}
                for fut in as_completed(futures, timeout=20):
                    sid = futures[fut]
                    try:
                        data = fut.result(timeout=0)
                        if data:
                            results[sid] = data
                    except Exception:
                        pass
            return results

        def _news_brief():
            if not feed_result.items:
                return None
            return analyse_news_for_business(business_name, region_id, feed_result)

        def _local_check():
            """PSX financial analysis from local database."""
            if not ticker:
                return None
            try:
                from intelligence.financial_model import run_local_analysis_psx
                return run_local_analysis_psx(ticker)
            except Exception as e:
                log.info("PSX financial analysis unavailable", ticker=ticker, reason=str(e)[:80])
                return None

        _market.__name__      = "fetch_market_data"
        _queries.__name__     = "keyword_search"
        _website.__name__     = "website_scrape"
        _gov_data.__name__    = "gov_scrapers"
        _news_brief.__name__  = "news_brief"
        _local_check.__name__ = "local_financial_check"

        market_res, search_res, website_res, gov_res, news_res, local_check_res = await asyncio.gather(
            _bounded(_market,      timeout_s=20),
            _bounded(_queries,     timeout_s=30),
            _bounded(_website,     timeout_s=35),
            _bounded(_gov_data,    timeout_s=25),
            _bounded(_news_brief,  timeout_s=45),
            _bounded(_local_check, timeout_s=25),
            return_exceptions=False,
        )

        # ── Unpack market data ────────────────────────────────────────────────
        if market_res is not None:
            market_data, _ = market_res if isinstance(market_res, tuple) else (market_res, None)
            summary.stock_data_available = bool(market_data and market_data.indices)

        # ── Exchange DB data (instant, no I/O) ────────────────────────────────
        summary.forex_text = get_forex_summary_text(region_id)
        summary.index_text = get_index_summary_text(region_id)
        if summary.forex_text:
            summary.sources_found.append(f"central_bank_forex:{region_id}")
        if summary.index_text:
            summary.sources_found.append(f"exchange_index:{region_id}")

        # ── Unpack DDG results ────────────────────────────────────────────────
        if search_res:
            summary.search_results_count = len(search_res)
            summary.search_snippets      = format_search_results(search_res)
        else:
            summary.search_results_count = 0

        # ── Unpack website scrape (text, keywords, industry, error) ───────────
        website_text = None
        website_keywords: list = []
        if website_res is not None and isinstance(website_res, tuple) and len(website_res) == 4:
            website_text, website_keywords, web_industry, _ = website_res
            if website_text:
                summary.website_chars_scraped = len(website_text)
                summary.website_snippet       = website_text[:3000]
            # Website text gives a more specific industry signal than PSX sector alone
            if web_industry and web_industry != "general":
                industry = web_industry

        # ── Store industry, keywords, PSX profile, gov data on the summary ────
        summary.industry_tag      = industry
        summary.industry_keywords = website_keywords or (
            [k for k in psx_sector.lower().split() if len(k) > 3] if psx_sector else []
        )
        summary.psx_profile       = psx_profile or {}
        if gov_res:
            summary.gov_data = gov_res
            summary.sources_found.append(f"gov_scrapers:{len(gov_res)}")

        # ── Local financial ratios + PSX summary text for Stage 2 injection ───
        if local_check_res:
            try:
                summary.ratios_text = format_ratios_for_prompt(local_check_res)
                if local_check_res.get("ocr_derived"):
                    summary.ratios_text = (
                        "[DATA NOTE: balance-sheet figures were OCR-read from a scanned "
                        "PSX report — treat current ratio, debt/equity, Altman Z and ROE "
                        "as indicative, not exact.]\n" + summary.ratios_text
                    )
            except Exception:
                summary.ratios_text = ""

        # ── Unpack news brief ─────────────────────────────────────────────────
        if news_res is not None:
            summary.news_brief       = format_brief_for_prompt(news_res)
            summary.analyst_note     = news_res.analyst_note or ""
            summary.market_sentiment = news_res.market_sentiment or ""

        # ── Client documents (private/unlisted company analysis) ──────────────
        # Decrypt + extract text from the client's selected (or all) documents.
        # All processing is local — nothing leaves the machine here.
        # Sanitisation happens later before any AI call.
        if client_key:
            try:
                from database.documents import get_documents_text_for_client
                doc_text, doc_names = await _bounded(
                    (lambda: get_documents_text_for_client(
                        client_id, client_key, doc_ids=document_ids)),
                    timeout_s=30,
                ) or ("", [])
                if doc_text:
                    summary.document_text       = doc_text
                    summary.document_names      = doc_names
                    summary.documents_available = len(doc_names)
                    summary.sources_found.append(f"documents:{len(doc_names)}")
            except Exception as e:
                log.info("Document load skipped", error=str(e)[:100])

        # ── Document intelligence — local NLP + financial extraction ─────────
        # For unlisted/private companies: extract company context (industry,
        # geography, keywords) and run the local financial analysis engine on any
        # financial statements found in uploaded documents.
        # No AI calls. No external network. Output matches PSX track format.
        if summary.document_text and client_key:
            try:
                from intelligence.document_financials import (
                    extract_company_context,
                    run_document_financial_analysis,
                )
                from database.documents import get_document_bytes, list_documents

                # 1. NLP company context — drives smarter search queries
                def _nlp_context():
                    return extract_company_context(summary.document_text)

                _nlp_context.__name__ = "nlp_context"
                ctx = await _bounded(_nlp_context, timeout_s=20)
                if ctx:
                    if ctx.industry and ctx.industry != "general":
                        summary.industry_tag = ctx.industry
                        log.info("Industry detected from documents", industry=ctx.industry)
                    if ctx.keywords:
                        summary.detected_keywords = ctx.keywords[:15]
                        summary.industry_keywords = ctx.keywords[:10]

                # 2. Financial extraction — try each selected document in order
                if not ticker:  # only run for unlisted companies
                    target_ids = document_ids or [d["id"] for d in list_documents(client_id)]
                    for doc_id in target_ids[:5]:  # cap at 5 docs
                        def _fin_analysis(did=doc_id):
                            raw = get_document_bytes(did, client_key, client_id=client_id)
                            if not raw:
                                return None
                            # detect doc_type from DB
                            from database.documents import get_document_meta
                            meta = get_document_meta(did)
                            dt = (meta or {}).get("doc_type", "pdf")
                            return run_document_financial_analysis(
                                raw, summary.document_text, dt, business_name
                            )

                        _fin_analysis.__name__ = "document_financial_analysis"
                        result = await _bounded(_fin_analysis, timeout_s=45)
                        if result:
                            summary.local_financial_result = result
                            summary.sources_found.append("document_financials:parsed")
                            log.info("Local financial analysis from document complete",
                                     business=business_name)
                            break  # first successful doc wins

            except Exception as e:
                log.info("Document intelligence skipped", error=str(e)[:120])

        # ── Data quality score ────────────────────────────────────────────────
        total_sources = len(summary.sources_found) + len(summary.sources_failed)
        feed_score    = len(summary.sources_found) / max(total_sources, 1)
        market_bonus  = 0.3 if summary.stock_data_available else 0.0
        forex_bonus   = 0.1 if summary.forex_text else 0.0
        summary.data_quality_score = min(1.0, feed_score * 0.7 + market_bonus + forex_bonus)

        if feed_result.sources_failed:
            summary.gaps.append(f"{len(feed_result.sources_failed)} RSS feeds failed")
        if not summary.stock_data_available and not summary.index_text:
            summary.gaps.append("Market data unavailable")
        if summary.search_results_count == 0:
            summary.gaps.append("Web search returned no results")
        if summary.website_chars_scraped == 0:
            summary.gaps.append("Company website not scraped")

        # ── Targeted search pass using NLP-extracted keywords ────────────────
        # Runs after document intelligence so we can seed from detected industry/products.
        # Supplements the Phase B search which ran before documents were loaded.
        if summary.detected_keywords and not ticker:
            try:
                def _doc_search():
                    kws = summary.detected_keywords[:8]
                    qs = []
                    for mod_id in ("overview", "market_position", "regulatory"):
                        qs.extend(build_queries_for_module(
                            mod_id, business_name, summary.industry_tag, kws
                        ))
                    return multi_search(qs[:4])

                _doc_search.__name__ = "doc_keyword_search"
                extra_search = await _bounded(_doc_search, timeout_s=20)
                if extra_search:
                    existing = summary.search_snippets or ""
                    extra_snippets = getattr(extra_search, "snippets_text", "") or ""
                    if extra_snippets:
                        summary.search_snippets = (existing + "\n\n" + extra_snippets).strip()
                        log.info("Document keyword search added supplementary results",
                                 industry=summary.industry_tag)
            except Exception:
                pass

        # ── Financial module readiness check ─────────────────────────────────
        needs_local = any(
            getattr(ALL_MODULES.get(m), "local_data_sufficient", False)
            for m in module_list
        )
        has_doc_financials = bool(summary.local_financial_result)
        if needs_local and local_check_res is None and not has_doc_financials:
            if summary.document_text:
                # Documents uploaded but no financial tables found in them
                summary.gaps.append(
                    "Financial tables not found in uploaded documents. "
                    "Financial and valuation modules will provide qualitative analysis only. "
                    "Upload a PDF or spreadsheet with P&L / balance sheet data for quantitative analysis."
                )
            else:
                # No PSX data, no documents at all
                summary.gaps.append(
                    "No financial data available (company not PSX-listed and no documents uploaded). "
                    "Financial and valuation modules are skipped in Express mode. "
                    "Upload financial statements or switch to Gated mode for web-based estimates."
                )
        elif needs_local and has_doc_financials:
            # Documents provided good financial data — positive confirmation
            summary.sources_found.append("financials:document_extracted")
            log.info("Financial modules will use locally extracted document data", business=business_name)

        run_state = load_run(run_id)
        if run_state:
            run_state = complete_stage1(run_state, summary)

            # In EXPRESS mode, auto-advance to Stage 2
            if run_state.stage == RunStage.STAGE2_ANALYSING:
                await _run_stage2(
                    run_id, client_id, business_name, region_id,
                    run_state.modules_pending, client_key,
                )

    except Exception as e:
        log.error("Stage 1 failed", run_id=run_id, error=str(e))
        fail_run(run_id, f"Stage 1 error: {e}")


_ModuleJob = namedtuple("_ModuleJob", ["mod_id", "module", "sanitised_prompt", "use_search"])
_S2_BACKOFF = [0, 5, 15, 45]


async def _run_stage2(run_id, client_id, business_name, region_id, module_list, client_key):
    """
    Stage 2: Run all analysis modules against collected data.

    Three-phase parallel engine:
      Phase A (sequential) — Build and sanitise every module prompt. No I/O.
      Phase B (parallel)   — Fire all Gemini API calls simultaneously via asyncio.gather.
      Phase C (sequential) — Restore entity names + checkpoint results to DB.

    The sanitiser is only touched in Phase A (sanitise) and Phase C (restore),
    never in parallel threads — this is required for thread safety.
    """
    try:
        loop       = asyncio.get_running_loop()
        region     = get_region(region_id)
        client_row = get_client(client_id) or {}
        industry   = client_row.get("industry")

        sanitiser = build_sanitiser_for_client(
            business_name=business_name,
            region_id=region_id,
            industry=industry,
            client_id=client_id,
        )
        ai_mode    = settings.AI_MODE
        run_state_ = load_run(run_id)
        run_mode_  = run_state_.mode if run_state_ else None

        # ── Stage 1 enrichments ───────────────────────────────────────────────
        # (financial ratios are added separately from local_result below, so they
        #  are not duplicated here.)
        data_summary       = run_state_.data_summary if run_state_ else None
        industry_tag       = getattr(data_summary, "industry_tag", "general") if data_summary else "general"
        _psx_profile       = getattr(data_summary, "psx_profile", {}) if data_summary else {}
        sector_of_business = (_psx_profile.get("sector") or
                              getattr(data_summary, "sector", "") or
                              industry_tag or "")
        extra_context      = ""
        if data_summary:
            if data_summary.news_brief:
                extra_context += f"\n\n{data_summary.news_brief}"
            if data_summary.search_snippets:
                extra_context += f"\n\n{data_summary.search_snippets}"
            if data_summary.website_snippet:
                extra_context += f"\n\nCOMPANY WEBSITE CONTENT:\n{data_summary.website_snippet[:2500]}"
            if getattr(data_summary, "document_text", ""):
                names = ", ".join(getattr(data_summary, "document_names", []))
                extra_context += (
                    f"\n\nUPLOADED CLIENT DOCUMENTS ({names}) — use as qualitative context;\n"
                    f"quantitative financial data has been separately extracted and "
                    f"pre-computed above:\n"
                    f"{data_summary.document_text[:30000]}"
                )
            if data_summary.forex_text:
                extra_context += f"\n\n{data_summary.forex_text}"
            if data_summary.index_text:
                extra_context += f"\n\n{data_summary.index_text}"

        # ── Pakistan macro + global sector benchmark (deterministic) ──────────
        try:
            from database.macro_data import get_macro_summary_text
            macro_text = get_macro_summary_text(industry_tag)
            if macro_text:
                extra_context += f"\n\n{macro_text}"
        except Exception:
            pass

        # PSX sector peer benchmark — local DB query, no external API
        try:
            from database.exchange_data import get_psx_sector_peers_text
            if industry_tag and industry_tag != "general":
                peers_text = get_psx_sector_peers_text(industry_tag)
                if peers_text:
                    extra_context += f"\n\n{peers_text}"
        except Exception:
            pass

        # ── Setup: local analysis + feeds + market data run in parallel ───────
        def _local_blocking():
            # Track B: document-extracted financials (unlisted/private companies).
            # Checked first so unlisted companies get quantitative analysis without PSX.
            doc_fin = getattr(data_summary, "local_financial_result", None)
            if doc_fin:
                log.info("Stage 2 using document-extracted financials", business=business_name)
                return doc_fin

            # Track A: PSX ticker path (listed companies).
            ticker = _guess_ticker(business_name)
            if not ticker:
                return None
            try:
                from intelligence.financial_model import run_local_analysis_psx
                return run_local_analysis_psx(ticker)
            except Exception as e:
                log.info("PSX financial analysis unavailable", ticker=ticker, reason=str(e)[:100])
                return None

        async def _get_local():
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, _local_blocking),
                    timeout=25,
                )
            except Exception as e:
                log.info("Local analysis unavailable in Stage 2", reason=str(e))
                return None

        async def _get_feeds():
            cached, _ = get_cached_feeds(region_id)
            if cached:
                return cached
            try:
                res = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: fetch_feeds_for_region(region_id)),
                    timeout=90,
                )
                if res:
                    set_cached_feeds(region_id, res)
                return res
            except Exception:
                return None

        local_result, feed_result = await asyncio.gather(
            _get_local(), _get_feeds()
        )

        feed_text   = get_feed_summary_text(feed_result) if feed_result else ""
        # Read KSE index from DB — populated by the PSX exchange scraper.
        market_text = get_index_summary_text(region_id) or ""
        ratios_text = format_ratios_for_prompt(local_result) if local_result else ""
        if ratios_text:
            log.info("Local ratios injected into Stage 2 context",
                     health=local_result.get("health_score"))

        # ── §5.1-§5.2 TokenSheet + interpretation tokens (per-run, in-memory) ─
        run_token_sheet = TokenSheet()
        if local_result:
            run_token_sheet.add(business_name, hint="COMPANY")
            run_token_sheet, _ = tokenise_financials(local_result, run_token_sheet)
            try:
                for hint, verdict_text in compute_verdicts(local_result).items():
                    run_token_sheet.add(verdict_text, hint=hint)
            except Exception as _vex:
                log.warning("verdicts compute failed — interpretation tokens skipped",
                             error=str(_vex))

        # ── Security gate: local data unavailable for financial/valuation ──────
        # local_result is set from document-extracted financials (Track B) or PSX (Track A).
        # Only skip financial modules when there is TRULY no quantitative data available
        # (no PSX ticker AND no documents uploaded AND no doc extraction succeeded).
        needs_local     = any(
            getattr(ALL_MODULES.get(m), "local_data_sufficient", False)
            for m in module_list
        )
        local_available = local_result is not None
        has_docs        = bool(getattr(data_summary, "document_text", ""))
        if needs_local and not local_available:
            if run_mode_ and run_mode_.value == "express" and not has_docs:
                # Express mode + no docs + no PSX data: skip financial/valuation.
                # If documents were uploaded, document intelligence has already run and
                # local_result would be set if financials were found — so we only skip
                # when there are genuinely no data sources for these modules.
                skipped = [m for m in module_list
                           if getattr(ALL_MODULES.get(m), "local_data_sufficient", False)]
                module_list = [m for m in module_list
                               if not getattr(ALL_MODULES.get(m), "local_data_sufficient", False)]
                for s in skipped:
                    mark_module_failed(run_id, s,
                                       "Skipped: no PSX listing and no documents uploaded. "
                                       "Upload financial statements to enable this module.")
                log.info("Express mode: financial modules skipped (no data source)", skipped=skipped)
            # Gated mode: user saw the gap message at Stage 1 and chose to proceed.
            # Run modules — they'll use document text + web search for qualitative analysis.

        # ── Phase A: build + sanitise all module prompts (sequential) ─────────
        jobs: list[_ModuleJob] = []
        context_block = (
            f"{feed_text[:4000]}\n\n"
            f"{market_text}\n\n"
            f"{ratios_text}\n\n"
            f"{extra_context}"
        )

        for mod_id in list(module_list):
            module = ALL_MODULES.get(mod_id)
            if not module:
                mark_module_failed(run_id, mod_id, f"Unknown module: {mod_id}")
                continue

            memory_ctx = (
                build_memory_context(
                    client_id=client_id,
                    client_key=client_key,
                    module_id=mod_id,
                    industry_tag=industry,
                )
                if client_key else ""
            )
            full_ctx   = f"{memory_ctx}\n\n{context_block}"
            raw_prompt = module.prompt_builder(
                business_name,
                region.name if region else "",
                full_ctx,
            )
            secured = build_secure_prompt(
                base_system=module.system_prompt,
                client_id=client_id,
                client_name=client_row.get("name", ""),
                region_modifier=region.prompt_modifier if region else "",
            )
            sanitised = sanitiser.sanitise(f"{secured}\n\n{raw_prompt}")

            # Disable Google Search grounding for modules that have pre-computed
            # local data injected — no need to query external APIs for numbers
            # we already have.
            use_search = module.requires_web_search and not (
                module.local_data_sufficient and local_available
            )
            jobs.append(_ModuleJob(mod_id, module, sanitised, use_search))

        # ── Phase B: sequential Gemini calls with rate-limit backoff ─────────────
        # Free tier: 20 req/day for gemini-2.5-flash. Fire one at a time so a
        # single 429 doesn't kill all remaining modules — wait the retry_delay
        # the API tells us, then continue.
        import re as _re

        _QUOTA_ERR = (
            "AI quota exceeded — your Gemini API key has no available quota. "
            "Check GOOGLE_API_KEY billing/limits at ai.google.dev/gemini-api/docs/rate-limits."
        )

        async def _call_with_retry(job: _ModuleJob):
            mark_module_running(run_id, job.mod_id)
            last_err = None
            raw = ""

            # ── LOCAL_ONLY / local_data_sufficient fast path ──────────────────
            # When LOCAL_ONLY=True (default), ALL modules skip Gemini and use the
            # local renderer. When LOCAL_ONLY=False, only financial/valuation modules
            # (local_data_sufficient=True) get the local fast path; others go to Gemini.
            _use_local_path = (
                settings.LOCAL_ONLY
                or (getattr(job.module, "local_data_sufficient", False) and local_available)
            )
            if _use_local_path:
                try:
                    _captured_result   = local_result
                    _captured_industry = industry_tag or ""
                    _captured_mod_id   = job.mod_id
                    _captured_ds       = data_summary
                    _captured_bn       = business_name
                    _captured_sector   = sector_of_business

                    def _local_gen():
                        from core.narrative_fallbacks import render_local_module
                        from database.exchange_data import get_psx_sector_peers_text as _peers_fn
                        _ticker    = (_captured_result or {}).get("ticker") or ""
                        _peers     = ""
                        if _captured_industry and _captured_industry != "general":
                            try:
                                _peers = _peers_fn(_captured_industry) or ""
                            except Exception:
                                pass

                        # FinBERT sentiment only for sentiment module (CPU-intensive)
                        _sentiment = None
                        if _captured_mod_id in ("sentiment", "financial"):
                            try:
                                from intelligence.financial_model import score_announcements_sentiment as _sas
                                if _ticker:
                                    _sentiment = _sas(_ticker)
                            except Exception:
                                pass

                        return render_local_module(
                            mod_id        = _captured_mod_id,
                            local_result  = _captured_result,
                            data_summary  = _captured_ds,
                            business_name = _captured_bn,
                            sector        = _captured_sector,
                            industry_tag  = _captured_industry,
                            peers_text    = _peers or None,
                            sentiment_scores = _sentiment,
                        )

                    _local_report = await asyncio.wait_for(
                        loop.run_in_executor(None, _local_gen),
                        timeout=90,
                    )
                    log.info("Local module rendered",
                             mod_id=job.mod_id, local_only=settings.LOCAL_ONLY)
                    return _local_report, None
                except Exception as _le:
                    if settings.LOCAL_ONLY:
                        # LOCAL_ONLY: never fall through to Gemini — return error card
                        log.warning("Local module render failed",
                                    mod_id=job.mod_id, error=str(_le)[:120])
                        return (
                            f"## {job.module.label}\n\n"
                            f"_⚙️ Computed locally_\n\n"
                            f"Data collection succeeded but template rendering failed: "
                            f"{str(_le)[:120]}"
                        ), None
                    log.warning("Local module render failed — falling through to Gemini",
                                mod_id=job.mod_id, error=str(_le)[:120])

            if settings.LOCAL_ONLY:
                # Reached only if _use_local_path was False (shouldn't happen when LOCAL_ONLY=True)
                # Return a graceful no-data card instead of calling Gemini.
                return (
                    f"## {job.module.label}\n\n"
                    f"_⚙️ Computed locally_\n\n"
                    f"No local data available for this module. "
                    f"Upload documents or ensure PSX ticker is set."
                ), None

            # B-1: Network retry loop — respects retry_delay from 429 responses
            for attempt in range(4):
                if attempt:
                    await asyncio.sleep(_S2_BACKOFF[attempt])
                try:
                    raw = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda s=job.sanitised_prompt, mid=job.mod_id, gs=job.use_search:
                                call_gemini_raw(s, module_id=mid, use_search=gs),
                        ),
                        timeout=120,
                    )
                    if not raw:
                        raise ValueError("Empty response")
                    break  # success — proceed to validation
                except Exception as exc:
                    msg = str(exc)
                    if "quota" in msg.lower() or "429" in msg:
                        if attempt < 3:
                            # Parse retry_delay from API error (e.g. "seconds: 33")
                            m = _re.search(r'retry_delay.*?seconds:\s*(\d+)', msg, _re.DOTALL)
                            wait_s = max(int(m.group(1)) + 5, 35) if m else 65
                            log.warning("Rate limited — waiting before retry",
                                        mod_id=job.mod_id, wait_s=wait_s, attempt=attempt + 1)
                            await asyncio.sleep(wait_s)
                            continue
                        return "", _QUOTA_ERR
                    last_err = msg[:140]
                    log.warning("Module attempt failed",
                                mod_id=job.mod_id, attempt=attempt + 1, error=last_err)
            else:
                return "", last_err  # all 4 attempts failed

            # B-2: §5.3 OutputValidator — one corrective retry, then narrative fallback
            v_result = _validate_response(raw, run_token_sheet)
            if not v_result.passed:
                log.warning("Response failed validation — attempting corrective retry",
                            mod_id=job.mod_id, violations=v_result.violations[:3])
                retry_prompt = build_retry_prompt(job.sanitised_prompt, v_result)
                try:
                    raw2 = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda s=retry_prompt, mid=job.mod_id, gs=job.use_search:
                                call_gemini_raw(s, module_id=mid, use_search=gs),
                        ),
                        timeout=120,
                    )
                    v2 = _validate_response(raw2 or "", run_token_sheet)
                    if v2.passed and raw2:
                        return raw2, None
                except Exception as _retry_exc:
                    log.warning("Validation retry call failed",
                                mod_id=job.mod_id, error=str(_retry_exc)[:80])
                # §5.4 Narrative fallback — deterministic, always valid
                log.warning("Using narrative fallback template",
                            mod_id=job.mod_id, violations=v_result.violations[:3])
                return render_fallback(job.mod_id, run_token_sheet), None

            return raw, None

        # Run sequentially — free-tier rate limits make parallel worthless;
        # each call waits its turn so a 429 doesn't cascade across all modules.
        raw_outcomes = []
        for _job in jobs:
            raw_outcomes.append(await _call_with_retry(_job))

        # ── Phase C: restore + checkpoint (sequential) ────────────────────────
        key_findings: list = []
        for job, (raw_text, error) in zip(jobs, raw_outcomes):
            if error or not raw_text:
                # Local-first fallback: if the AI failed but this module has
                # pre-computed PSX financial data, emit the deterministic analysis
                # so the module still produces value with no AI dependency.
                if (getattr(job.module, "local_data_sufficient", False)
                        and local_available and ratios_text):
                    fallback = (
                        f"**{job.module.label} — Local Quantitative Analysis**\n\n"
                        f"_(Computed locally from PSX financial statements. AI commentary "
                        f"unavailable: {error})_\n\n{ratios_text}"
                    )
                    if client_key:
                        save_analysis(
                            client_id=client_id, business_name=business_name,
                            module_id=job.mod_id, result_text=fallback,
                            client_key=client_key, region_id=region_id,
                            model_used="local-financial-model",
                        )
                    mark_module_complete(run_id, job.mod_id)
                    key_findings.append({"module": job.mod_id,
                                         "finding": "Local financial analysis (AI unavailable)"})
                    continue
                mark_module_failed(run_id, job.mod_id, error or "Empty result")
                continue

            # §5.1 restore: Class S tokens first, then PII scrubber
            result = run_token_sheet.restore(raw_text)
            result = sanitiser.restore(result)

            if client_key:
                _model_used = (
                    "local-engine"
                    if settings.LOCAL_ONLY
                    else settings.GEMINI_MODEL_ANALYSIS
                )
                save_analysis(
                    client_id=client_id,
                    business_name=business_name,
                    module_id=job.mod_id,
                    result_text=result,
                    client_key=client_key,
                    region_id=region_id,
                    model_used=_model_used,
                )
                extract_and_store(
                    analysis_text=result,
                    module_id=job.mod_id,
                    business_name=business_name,
                    client_id=client_id,
                    client_name=client_row.get("name", ""),
                    client_key=client_key,
                    industry_tag=industry,
                )

            mark_module_complete(run_id, job.mod_id)
            first_line = result[:200].split("\n")[0].strip()
            if first_line:
                key_findings.append({"module": job.mod_id, "finding": first_line[:150]})

        # Audit: log what was anonymised (categories + counts only — no real values)
        try:
            from database.connection import get_db as _get_db
            from security.audit import log_event as _log_event
            _log_event(
                _get_db(), "analysis_sanitised", client_id, client_id,
                details={
                    "run_id": run_id,
                    "sanitisation_active": settings.ENABLE_PROMPT_SANITISATION,
                    "ai_mode": ai_mode,
                    "mapping_summary": sanitiser.get_mapping_summary(),
                },
            )
        except Exception:
            pass  # Audit failure must never break the analysis run

        # Build analysis summary
        run_state = load_run(run_id)
        if run_state:
            a_summary = AnalysisSummary(
                modules_complete=len(run_state.modules_complete),
                modules_failed=len(run_state.modules_failed),
                total_modules=len(run_state.modules_requested),
                key_findings=key_findings,
                overall_confidence=len(run_state.modules_complete) / max(len(run_state.modules_requested), 1),
                failed_module_reasons=run_state.modules_failed,
            )
            run_state = complete_stage2(run_state, a_summary)

            # Persist any newly discovered entities to the client registry
            # so future runs benefit from a more accurate sanitisation corpus
            try:
                discovered = sanitiser.get_discovered_entities()
                if discovered:
                    from database.client_entities import save_discovered_entities
                    n = save_discovered_entities(client_id, discovered)
                    log.info("Entity registry updated", client_id=client_id, new_entities=n)
            except Exception as e:
                log.warning("Failed to persist discovered entities", error=str(e))

            if run_state.stage == RunStage.STAGE3_REPORTING:
                await _run_stage3(
                    run_id, client_id, business_name,
                    run_state.modules_complete, client_key,
                )

    except Exception as e:
        log.error("Stage 2 failed", run_id=run_id, error=str(e))
        fail_run(run_id, f"Stage 2 error: {e}")


async def _run_stage3(run_id, client_id, business_name, module_ids, client_key):
    """Stage 3: Generate report."""
    try:
        from reports.builder import build_report
        from reports.signer import ensure_signing_key
        from security.crypto import derive_master_key
        from database.clients import get_client_salt

        from database.connection import get_db as _get_db
        _master_row = _get_db().execute("SELECT master_salt FROM users LIMIT 1").fetchone()
        master_key  = derive_master_key(settings.DB_MASTER_PASSWORD, bytes(_master_row["master_salt"])) if _master_row and _master_row["master_salt"] else b"\x00" * 32

        ensure_signing_key(master_key)

        record, err = build_report(
            client_id=client_id,
            business_name=business_name,
            module_ids=module_ids,
            client_key=client_key,
            master_key=master_key,
        )

        from core.run_manager import complete_run
        run_state = load_run(run_id)
        if run_state:
            complete_run(run_state)

        log.info("Stage 3 complete", run_id=run_id, report=record.get("id"))

    except Exception as e:
        log.error("Stage 3 failed", run_id=run_id, error=str(e))
        fail_run(run_id, f"Stage 3 error: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _guess_ticker(business_name: str, region_id: str = "pakistan") -> str:
    """
    Resolve a PSX ticker (symbol) for a business name via live PSX symbol search.
    Falls back to the local psx_companies table. Returns "" if no match.
    region_id is accepted for call-site compatibility but ignored (Pakistan-only).
    """
    try:
        from intelligence.exchange_crawlers.psx import search_psx_company
        matches = search_psx_company(business_name)
        if matches:
            return matches[0].get("symbol", "") or ""
    except Exception:
        pass

    # Fallback: local PSX company registry
    try:
        from database.exchange_data import search_company
        company = search_company("KAR", business_name)
        if company and company.get("ticker"):
            return company["ticker"]
    except Exception:
        pass

    return ""


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
                    "SELECT symbol, company_name, pe_ratio FROM psx_companies "
                    "WHERE sector_name=? AND symbol!=? AND is_debt=0 "
                    "ORDER BY company_name LIMIT 10",
                    (_co["sector_name"], ticker),
                ).fetchall()
                if _ps:
                    _lines = [f"PSX SECTOR PEERS — {_co['sector_name']}\n"]
                    for p in _ps:
                        _pe = f"  P/E {p['pe_ratio']:.1f}x" if p["pe_ratio"] else "  P/E N/A"
                        _lines.append(f"  {p['symbol']:8} {p['company_name'][:35]}{_pe}")
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
