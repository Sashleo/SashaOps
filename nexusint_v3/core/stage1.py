"""
core/stage1.py — Stage 1 background worker: parallel data collection.

Collects all data sources (feeds, market data, DDG search, website scrape, gov
scrapers, news brief, local financials) concurrently with per-task timeouts, then
checkpoints a DataCollectionSummary. In EXPRESS mode it chains into Stage 2.

Split out of routers/intelligence.py — logic unchanged. `_guess_ticker` lives here
because Stage 1 is its first consumer; Stage 2 and the router import it from here.
"""

import asyncio

import structlog

from config.modules import MODULES
from config.settings import settings
from core.run_manager import (
    load_run, complete_stage1, fail_run,
    check_cancelled, RunCancelled, RunStage, DataCollectionSummary,
)
from core.feed_cache import get_cached_feeds, set_cached_feeds
from intelligence.feeds import fetch_feeds_for_region, classify_headlines
from intelligence.stocks import fetch_region_market_data
from intelligence.search import multi_search, format_search_results
from intelligence.browser import extract_company_website_content
from intelligence.news_analyser import format_brief_for_prompt
from intelligence.extended_analysis import EXTENDED_MODULES
from database.exchange_data import get_forex_summary_text, get_index_summary_text
from intelligence.financial_model import format_ratios_for_prompt

log = structlog.get_logger()

ALL_MODULES = {**MODULES, **EXTENDED_MODULES}


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


async def run_stage1(run_id, client_id, business_name, region_id, module_list,
                     client_key, document_ids=None):
    """
    Stage 1: Collect all data sources in parallel.

    Independent tasks (feeds, market data, DDG search, website scrape) run
    concurrently with hard per-task timeouts so a single slow source cannot
    block the entire stage.
    """
    try:
        check_cancelled(run_id)
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
            feed_result = await _bounded(_fetch_feeds, timeout_s=60)  # bounds cold Stage 1
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
        # sample_headlines is (re)built by relevance after industry/ticker resolve below.
        summary.sample_headlines = [
            f"{item.title} [{item.feed_label}]"
            for item in sorted(feed_result.items, key=lambda x: x.magnitude, reverse=True)[:5]
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
        _local_check.__name__ = "local_financial_check"

        market_res, search_res, website_res, gov_res, local_check_res = await asyncio.gather(
            _bounded(_market,      timeout_s=20),
            _bounded(_queries,     timeout_s=30),
            _bounded(_website,     timeout_s=35),
            _bounded(_gov_data,    timeout_s=25),
            _bounded(_local_check, timeout_s=25),
            return_exceptions=False,
        )

        check_cancelled(run_id)

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
                        "PSX report - treat current ratio, debt/equity, Altman Z and ROE "
                        "as indicative, not exact.]\n" + summary.ratios_text
                    )
            except Exception:
                summary.ratios_text = ""

        # ── News brief — built LOCALLY (deterministic relevance, no Gemini) ───
        # Score every headline against the subject company + its sector drivers, then
        # assemble the brief. Also re-rank sample_headlines by true relevance now that
        # ticker + industry are resolved (the label promises "by relevance").
        if feed_result.items:
            try:
                from intelligence.news_analyser import build_local_brief
                from intelligence.news_relevance import is_relevant
                brief = build_local_brief(
                    business_name, region_id, feed_result,
                    ticker=ticker or "", industry=industry or "", sector=psx_sector or "",
                )
                summary.news_brief       = format_brief_for_prompt(brief)
                summary.analyst_note     = brief.analyst_note or ""
                summary.market_sentiment = brief.market_sentiment or ""
                ranked = sorted(feed_result.items,
                                key=lambda x: getattr(x, "relevance_score", 0.0), reverse=True)
                summary.sample_headlines = [
                    f"{it.title} [{it.feed_label}]"
                    + ("  (relevant)" if is_relevant(it) else "")
                    for it in ranked[:5]
                ]
            except Exception as e:
                log.warning("Local news brief failed", error=str(e)[:120])

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
                    # M4 — scrub any embedded prompt-injection lines from extracted text.
                    from core.sanitiser import strip_prompt_injection
                    summary.document_text       = strip_prompt_injection(doc_text)
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

        check_cancelled(run_id)

        run_state = load_run(run_id)
        if run_state:
            run_state = complete_stage1(run_state, summary)

            # In EXPRESS mode, auto-advance to Stage 2
            if run_state.stage == RunStage.STAGE2_ANALYSING:
                from core.stage2 import run_stage2
                await run_stage2(
                    run_id, client_id, business_name, region_id,
                    run_state.modules_pending, client_key,
                )

    except (RunCancelled, asyncio.CancelledError):
        log.info("Stage 1 cancelled by user - aborting", run_id=run_id)
        raise
    except Exception as e:
        log.error("Stage 1 crashed", run_id=run_id, error=str(e), exc_info=True)
        fail_run(run_id, f"Stage 1 error: {e}")
