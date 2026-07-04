"""
core/ai_client.py — Google Gemini API wrapper.

Provides:
- Streaming analysis via Gemini with Google Search grounding
- Non-streaming analysis for background tasks
- Lightweight fast calls for classification/summarisation
- Token usage tracking
- Error handling with friendly messages

Primary model: gemini-2.0-flash (free tier, generous limits)
Search grounding: enabled — Gemini searches Google before answering
"""

import os
import time
import threading
from typing import AsyncGenerator, Optional, TYPE_CHECKING
import structlog

if TYPE_CHECKING:
    from security.sanitiser import PromptSanitiser

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from config.settings import settings
from core.privacy_wrapper import build_secure_prompt

log = structlog.get_logger()


def _init_gemini():
    """Initialise the Gemini client."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)


_init_gemini()


# ── Google Search grounding tool ──────────────────────────────────────────────
# google-generativeai 0.8.x exposes `google_search_retrieval` (not the 2.0-only
# `google_search`). Passing the wrong key makes the SDK treat it as a function
# declaration and raise "Unknown field for FunctionDeclaration".
SEARCH_TOOL = {
    "google_search_retrieval": {}
}


def _is_tool_error(err: Exception) -> bool:
    """True if the error is about the grounding tool/format (not quota/safety)."""
    msg = str(err).lower()
    return any(s in msg for s in (
        "functiondeclaration", "google_search", "google_search_retrieval",
        "tool", "not supported for this model",
    )) and "quota" not in msg and "429" not in msg


# Once we learn this SDK+model combination can't ground, stop attempting it so we
# don't waste a (quota-limited) API call per module discovering the same thing.
_grounding_unsupported = False


def _generate_grounded(model: "genai.GenerativeModel", prompt, use_search: bool):
    """
    Generate content with optional Google Search grounding. If grounding is
    requested but the SDK/model rejects the tool, retry once WITHOUT tools so a
    grounding mismatch can never fail a module. After the first such failure,
    grounding is skipped for the rest of the process. Quota (429) errors re-raise.
    """
    global _grounding_unsupported
    if not use_search or _grounding_unsupported:
        return model.generate_content(prompt)
    try:
        return model.generate_content(prompt, tools=[SEARCH_TOOL])
    except Exception as e:
        if _is_tool_error(e):
            _grounding_unsupported = True
            log.warning("Search grounding unsupported on this SDK/model — "
                        "running without it for the rest of the session",
                        error=str(e)[:120])
            return model.generate_content(prompt)
        raise


# ── Singleton model (created once, reused across all parallel calls) ──────────
_analysis_model: Optional[genai.GenerativeModel] = None
_model_lock = threading.Lock()


def get_analysis_model() -> genai.GenerativeModel:
    """Get (or lazily create) the singleton Gemini analysis model."""
    global _analysis_model
    if _analysis_model is None:
        with _model_lock:
            if _analysis_model is None:
                _analysis_model = genai.GenerativeModel(
                    model_name=settings.GEMINI_MODEL_ANALYSIS,
                    generation_config=GenerationConfig(
                        temperature=settings.GEMINI_TEMPERATURE_ANALYSIS,
                        max_output_tokens=settings.GEMINI_MAX_TOKENS,
                    ),
                )
    return _analysis_model


def call_gemini_raw(
    sanitised_prompt: str,
    module_id: str = "",
    use_search: bool = True,
) -> str:
    """
    Fire a raw Gemini call on an already-sanitised prompt.

    Returns unsanitised AI text — the caller must call sanitiser.restore()
    on the result. Used by the parallel Stage 2 engine so sanitise/restore
    happen sequentially in the main thread while only the HTTP I/O is parallelised.

    use_search=False disables Google Search grounding — set this for modules
    that already have pre-computed numbers injected and do not need live web data.
    """
    model = get_analysis_model()
    try:
        response = _generate_grounded(model, sanitised_prompt, use_search)
        text = response.text if hasattr(response, "text") else ""
        if not text and hasattr(response, "candidates"):
            text = "".join(
                part.text
                for c in response.candidates
                for part in c.content.parts
                if hasattr(part, "text")
            )
        log.info("Gemini raw call complete", module=module_id, chars=len(text),
                 search_grounding=use_search)
        return text
    except Exception as e:
        log.error("Gemini raw call failed", module=module_id, error=str(e))
        raise


def get_fast_model():
    """Get the Gemini model for lightweight tasks."""
    return genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL_FAST,
        generation_config=GenerationConfig(
            temperature=settings.GEMINI_TEMPERATURE_SYNTHESIS,
            max_output_tokens=1024,
        ),
    )


async def stream_analysis(
    system_prompt: str,
    user_prompt: str,
    module_id: str = "",
    business: str = "",
    client_id: str = "",
    client_name: str = "",
    memory_context: str = "",
    region_modifier: str = "",
    sanitiser: Optional["PromptSanitiser"] = None,
    ai_mode: str = "cloud",
) -> AsyncGenerator[str, None]:
    """
    Stream a Gemini analysis response.
    Yields text chunks as they arrive.

    Uses Google Search grounding for live web data.
    Applies privacy wrapper + prompt sanitiser (if cloud mode) to every prompt.
    """
    start_time = time.time()

    # Build secure prompt with privacy wrapper
    secured_system = build_secure_prompt(
        base_system=system_prompt,
        client_id=client_id,
        client_name=client_name,
        memory_context=memory_context,
        region_modifier=region_modifier,
    )

    raw_prompt = f"{secured_system}\n\n{user_prompt}"

    # Prompt sanitisation is MANDATORY for cloud mode.
    # If no sanitiser was provided for a cloud call, refuse to proceed.
    if ai_mode == "cloud":
        if sanitiser is None:
            raise RuntimeError(
                "SECURITY: Cloud AI call blocked — no PromptSanitiser provided. "
                "All cloud prompts must be sanitised before leaving this machine. "
                "Build a sanitiser via build_sanitiser_for_client() and pass it here."
            )
        full_prompt = sanitiser.sanitise(raw_prompt)
        log.debug("Prompt sanitised for stream", module=module_id,
                  entities=sanitiser.get_mapping_summary()["total_entities_anonymised"])
    else:
        full_prompt = raw_prompt

    try:
        model = get_analysis_model()

        # Enable Google Search grounding; fall back to no tools on a grounding
        # mismatch so a tool/format error can never break the stream.
        try:
            response = model.generate_content(full_prompt, tools=[SEARCH_TOOL], stream=True)
        except Exception as tool_err:
            if _is_tool_error(tool_err):
                log.warning("Search grounding unavailable (stream) — no tools",
                            error=str(tool_err)[:120])
                response = model.generate_content(full_prompt, stream=True)
            else:
                raise

        full_text = ""
        for chunk in response:
            if chunk.text:
                full_text += chunk.text
                yield chunk.text

        # Restore real values after full text is accumulated.
        # For streaming, yield a correction sentinel so the caller can replace
        # placeholder-containing text with the restored version.
        if ai_mode == "cloud" and sanitiser is not None:
            restored = sanitiser.restore(full_text)
            if restored != full_text:
                yield "\n\n<!-- sanitiser-restore -->"  # sentinel for caller to replace full text

        duration_ms = int((time.time() - start_time) * 1000)
        log.info(
            "Gemini analysis complete",
            module=module_id,
            business=business,
            duration_ms=duration_ms,
            chars=len(full_text),
        )

    except Exception as e:
        error_msg = _friendly_error(e)
        log.error("Gemini stream failed", error=str(e), module=module_id)
        yield f"\n\n⚠️ Analysis error: {error_msg}"


def run_analysis(
    system_prompt: str,
    user_prompt: str,
    module_id: str = "",
    business: str = "",
    client_id: str = "",
    client_name: str = "",
    memory_context: str = "",
    region_modifier: str = "",
    sanitiser: Optional["PromptSanitiser"] = None,
    ai_mode: str = "cloud",
) -> str:
    """
    Non-streaming Gemini analysis.
    Used for background tasks (run all modules, scheduled briefings).
    Returns full text when complete — with real values restored.
    """
    secured_system = build_secure_prompt(
        base_system=system_prompt,
        client_id=client_id,
        client_name=client_name,
        memory_context=memory_context,
        region_modifier=region_modifier,
    )

    raw_prompt = f"{secured_system}\n\n{user_prompt}"

    # Prompt sanitisation is MANDATORY for cloud mode — no exceptions.
    if ai_mode == "cloud":
        if sanitiser is None:
            raise RuntimeError(
                "SECURITY: Cloud AI call blocked — no PromptSanitiser provided. "
                "Build a sanitiser via build_sanitiser_for_client() and pass it here."
            )
        full_prompt = sanitiser.sanitise(raw_prompt)
        log.debug("Prompt sanitised", module=module_id,
                  entities=sanitiser.get_mapping_summary()["total_entities_anonymised"])
    else:
        full_prompt = raw_prompt

    try:
        model = get_analysis_model()
        response = _generate_grounded(model, full_prompt, use_search=True)

        text = response.text if hasattr(response, "text") else ""
        if not text and hasattr(response, "candidates"):
            text = "".join(
                part.text
                for c in response.candidates
                for part in c.content.parts
                if hasattr(part, "text")
            )

        # Restore real entity names before returning to caller
        if ai_mode == "cloud" and sanitiser is not None:
            text = sanitiser.restore(text)

        log.info("Gemini analysis complete", module=module_id, chars=len(text))
        return text

    except Exception as e:
        log.error("Gemini analysis failed", error=str(e), module=module_id)
        raise


def run_fast(prompt: str, sanitiser=None) -> str:
    """
    Fire a Gemini call with minimal overhead.
    Pass sanitiser whenever the prompt may contain Class S data (company
    names, financial figures, client identifiers).
    """
    if sanitiser is not None:
        prompt = sanitiser.sanitise(prompt)
    try:
        model = get_fast_model()
        response = model.generate_content(prompt)
        text = response.text or ""
        if sanitiser is not None:
            text = sanitiser.restore(text)
        return text
    except Exception as e:
        log.warning("run_fast failed", error=str(e))
        return ""


def extract_memory_insights(analysis_text: str, module_id: str, business: str) -> list[str]:
    """
    Ask Gemini to extract 2-3 key insights from an analysis result.
    These are stored in the memory system for future analyses.
    Returns list of insight strings.
    """
    # Never send business name to Gemini — use {{COMPANY}} token (Class S)
    prompt = f"""
You just completed a {module_id} analysis for {{{{COMPANY}}}}.

Analysis excerpt (first 800 chars):
{analysis_text[:800]}

Extract exactly 2-3 KEY INSIGHTS worth remembering for future analyses.

Rules:
- Be specific and factual, not generic
- Focus on: unique findings, surprising data, critical risks, strategic patterns
- Do NOT include company names, specific financial figures, or PII in insights
- Write insights as structural/pattern observations, not specific facts
- Each insight: 1-2 sentences maximum

Return ONLY the insights, one per line, no numbering or bullets.
"""
    result = run_fast(prompt)
    return [
        line.strip()
        for line in result.strip().split("\n")
        if line.strip() and len(line.strip()) > 20
    ][:3]



def _friendly_error(e: Exception) -> str:
    """Convert API errors to user-friendly messages."""
    msg = str(e).lower()
    if "quota" in msg or "429" in msg:
        return "API rate limit reached. Wait 60 seconds and try again."
    if "api_key" in msg or "invalid" in msg:
        return "Invalid API key. Check your GOOGLE_API_KEY in .env"
    if "network" in msg or "connect" in msg:
        return "Network error. Check your internet connection."
    if "safety" in msg or "blocked" in msg:
        return "Request blocked by safety filter. Rephrase your query."
    return "An error occurred. Please try again."
