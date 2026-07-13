"""
ml/sentiment.py — Financial sentiment scoring via FinBERT.

Model: yiyanghkust/finbert-tone (fine-tuned BERT for financial tone).
Labels: Positive, Negative, Neutral.

Outputs go into the token sheet as {{SENTIMENT_LABEL}} and {{SENTIMENT_CONF}}
so reports can state a sentiment summary from structured scores.

The model is downloaded at Docker build time; no runtime network calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

log = structlog.get_logger()

_pipeline = None
_pipeline_loaded = False


def _model_name() -> str:
    try:
        from config.settings import settings
        return settings.SENTIMENT_MODEL
    except Exception:
        return "yiyanghkust/finbert-tone"


@dataclass
class SentimentScore:
    label:      str    # Positive | Negative | Neutral
    confidence: float  # 0.0 – 1.0
    text:       str


def _load_pipeline():
    global _pipeline, _pipeline_loaded
    if _pipeline_loaded:
        return _pipeline
    model_name = _model_name()
    try:
        from transformers import pipeline
        _pipeline = pipeline(
            "text-classification",
            model=model_name,
            device=-1,          # CPU
            truncation=True,
            max_length=512,
            top_k=1,
        )
        log.info("ml.sentiment: model loaded", model=model_name)
    except Exception as e:
        log.warning("ml.sentiment: model unavailable — sentiment scoring disabled",
                    model=model_name, error=str(e))
        _pipeline = None
    _pipeline_loaded = True
    return _pipeline


def score(texts: list[str]) -> list[SentimentScore]:
    """
    Score each text for financial sentiment.

    Args:
        texts: List of headline or sentence strings.

    Returns:
        List of SentimentScore (one per input). Returns Neutral/0.0 on failure.
    """
    if not texts:
        return []

    pipe = _load_pipeline()
    if pipe is None:
        return [SentimentScore("Neutral", 0.0, t) for t in texts]

    try:
        results = pipe(texts, batch_size=32)
        scores: list[SentimentScore] = []
        for text, result in zip(texts, results):
            # pipeline with top_k=1 returns [[{label, score}]]
            top = result[0] if isinstance(result, list) else result
            scores.append(SentimentScore(
                label=top["label"].capitalize(),
                confidence=round(top["score"], 4),
                text=text,
            ))
        return scores
    except Exception as e:
        log.warning("ml.sentiment: scoring failed", error=str(e))
        return [SentimentScore("Neutral", 0.0, t) for t in texts]


def aggregate(scores: list[SentimentScore]) -> Optional[SentimentScore]:
    """
    Aggregate a list of scores into a single dominant sentiment.
    Returns the label with the highest mean confidence, or None if empty.
    """
    if not scores:
        return None

    from collections import defaultdict
    buckets: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        buckets[s.label].append(s.confidence)

    dominant_label = max(buckets, key=lambda lbl: sum(buckets[lbl]) / len(buckets[lbl]))
    mean_conf = sum(buckets[dominant_label]) / len(buckets[dominant_label])
    return SentimentScore(dominant_label, round(mean_conf, 4), "")
