"""
ml/relevance.py — Headline relevance ranking via sentence-transformers.

Model: sentence-transformers/all-MiniLM-L6-v2 (~80MB, CPU-only).
Used in Stage 1 to filter the feed headline pool down to the top-k most
relevant items before they enter the analysis context.

The model is downloaded at Docker build time; no runtime network calls.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger()

_model = None
_model_loaded = False


def _model_name() -> str:
    try:
        from config.settings import settings
        return settings.EMBEDDING_MODEL
    except Exception:
        return "sentence-transformers/all-MiniLM-L6-v2"


def _load_model():
    global _model, _model_loaded
    if _model_loaded:
        return _model
    name = _model_name()
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(name)
        log.info("ml.relevance: model loaded", model=name)
    except Exception as e:
        log.warning("ml.relevance: model unavailable — falling back to full headline list",
                    model=name, error=str(e))
        _model = None
    _model_loaded = True
    return _model


def rank(
    headlines: list[str],
    company_context: str,
    top_k: int = 15,
) -> list[str]:
    """
    Rank headlines by semantic similarity to company_context and return top_k.

    Args:
        headlines:       List of news headline strings.
        company_context: Short description: "{company} {sector} {geography}".
        top_k:           Maximum number of headlines to return.

    Returns:
        Top-k most relevant headlines, or the original list if model unavailable.
    """
    if not headlines:
        return headlines

    model = _load_model()
    if model is None:
        return headlines[:top_k]

    try:
        import torch  # noqa: F401 — ensures torch is available before encoding
        context_emb = model.encode(company_context, convert_to_tensor=True)
        headline_embs = model.encode(headlines, convert_to_tensor=True, batch_size=64)

        from sentence_transformers import util as st_util
        scores = st_util.cos_sim(context_emb, headline_embs)[0]
        top_indices = scores.argsort(descending=True)[:top_k].tolist()
        return [headlines[i] for i in sorted(top_indices)]
    except Exception as e:
        log.warning("ml.relevance: ranking failed — returning original list", error=str(e))
        return headlines[:top_k]
