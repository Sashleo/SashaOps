"""
core/stage2.py — Stage 2 background worker: module analysis engine.

Sanitisation flow (two layers, strict order):

OUTBOUND (building prompts):
1. security/sanitiser.py — entity anonymisation
   (Company A, Market X, Currency Z, the primary regulator)
2. core/sanitiser.py TokenSheet — value tokenisation
   ({{TOKEN_CR}}, {{TOKEN_ZSCORE}})
3. Both applied BEFORE the prompt reaches Gemini

INBOUND (processing responses):
1. core/sanitiser.py TokenSheet.restore() — FIRST
   (replaces {{TOKEN_CR}} with 1.42)
2. security/sanitiser.py.restore() — SECOND
   (replaces Company A with Systems Limited)

Order matters. Reversing the restore order may corrupt values if a token value
happens to contain an entity placeholder string.

Split out of routers/intelligence.py — logic unchanged.
"""

import asyncio
from collections import namedtuple

import structlog

from config.modules import MODULES
from config.regions import get_region
from config.settings import settings
from core.run_manager import (
    load_run, complete_stage2, fail_run,
    mark_module_running, mark_module_complete, mark_module_failed,
    check_cancelled, RunCancelled, RunStage, AnalysisSummary,
)
from core.ai_client import call_gemini_raw
from core.privacy_wrapper import build_secure_prompt
from security.sanitiser import build_sanitiser_for_client
from core.memory_engine import build_memory_context, extract_and_store
from core.feed_cache import get_cached_feeds, set_cached_feeds
from core.sanitiser import TokenSheet, tokenise_financials
from core.output_validator import validate as _validate_response, build_retry_prompt
from core.narrative_fallbacks import render_fallback
from database.clients import get_client
from database.analyses import save_analysis
from intelligence.feeds import fetch_feeds_for_region, get_feed_summary_text
from intelligence.news_analyser import format_brief_for_prompt  # noqa: F401 (kept for parity)
from intelligence.extended_analysis import EXTENDED_MODULES
from intelligence.financial_model import format_ratios_for_prompt
from intelligence.verdicts import compute_verdicts
from database.exchange_data import get_index_summary_text
from core.stage1 import _guess_ticker

log = structlog.get_logger()

ALL_MODULES = {**MODULES, **EXTENDED_MODULES}

_ModuleJob = namedtuple("_ModuleJob", ["mod_id", "module", "sanitised_prompt", "use_search"])
_S2_BACKOFF = [0, 5, 15, 45]


async def run_stage2(run_id, client_id, business_name, region_id, module_list, client_key):
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
        check_cancelled(run_id)
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
                for hint, verdict_text in compute_verdicts(local_result, sector=sector_of_business).items():
                    run_token_sheet.add(verdict_text, hint=hint)
            except Exception as _vex:
                log.warning("verdicts compute failed - interpretation tokens skipped",
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
            "AI quota exceeded - your Gemini API key has no available quota. "
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
                            f"_Computed locally_\n\n"
                            f"Data collection succeeded but template rendering failed: "
                            f"{str(_le)[:120]}"
                        ), None
                    log.warning("Local module render failed - falling through to Gemini",
                                mod_id=job.mod_id, error=str(_le)[:120])

            if settings.LOCAL_ONLY:
                # Reached only if _use_local_path was False (shouldn't happen when LOCAL_ONLY=True)
                # Return a graceful no-data card instead of calling Gemini.
                return (
                    f"## {job.module.label}\n\n"
                    f"_Computed locally_\n\n"
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
                            log.warning("Rate limited - waiting before retry",
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
                log.warning("Response failed validation - attempting corrective retry",
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
        # Check for cancellation before every module so a stuck/long run stops
        # promptly when the user hits Cancel.
        raw_outcomes = []
        for _job in jobs:
            check_cancelled(run_id)
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
                        f"**{job.module.label} - Local Quantitative Analysis**\n\n"
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
                    sanitiser=sanitiser,   # SECURITY: tokenise Class-S before Gemini
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
                from core.stage3 import run_stage3
                await run_stage3(
                    run_id, client_id, business_name,
                    run_state.modules_complete, client_key,
                )

    except (RunCancelled, asyncio.CancelledError):
        log.info("Stage 2 cancelled by user - aborting", run_id=run_id)
        raise
    except Exception as e:
        log.error("Stage 2 crashed", run_id=run_id, error=str(e), exc_info=True)
        fail_run(run_id, f"Stage 2 error: {e}")
