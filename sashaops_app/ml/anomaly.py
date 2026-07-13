"""
ml/anomaly.py — Price anomaly detection via IsolationForest.

Takes a price series (list of floats, daily closing prices) and returns
the dates/indices where anomalous price behaviour was detected.

Output feeds the {{ANOMALIES}} token in the token sheet for Stage 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import structlog

log = structlog.get_logger()


@dataclass
class AnomalyResult:
    count:   int
    dates:   list[str]  # ISO date strings or index labels
    summary: str        # human-readable: "3 anomalies detected on 2026-04-12, 2026-05-01, ..."


def detect(
    prices:       list[float],
    dates:        Optional[list[str]] = None,
    contamination: float = 0.05,
) -> Optional[AnomalyResult]:
    """
    Detect anomalous price points using IsolationForest.

    Args:
        prices:        Daily closing price series (chronological order).
        dates:         Corresponding date strings (ISO format). Auto-indexed if None.
        contamination: Expected proportion of anomalies (default 5%).

    Returns:
        AnomalyResult, or None if sklearn unavailable or too few data points.
    """
    if len(prices) < 30:
        log.debug("ml.anomaly: insufficient data points", count=len(prices))
        return None

    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest

        X = np.array(prices).reshape(-1, 1)
        clf = IsolationForest(contamination=contamination, random_state=42, n_jobs=1)
        preds = clf.fit_predict(X)

        anomaly_indices = [i for i, p in enumerate(preds) if p == -1]

        if dates:
            anomaly_dates = [dates[i] for i in anomaly_indices if i < len(dates)]
        else:
            anomaly_dates = [str(i) for i in anomaly_indices]

        count = len(anomaly_dates)
        if count == 0:
            summary = "No price anomalies detected in the analysis window."
        elif count <= 3:
            summary = f"{count} price anomal{'y' if count == 1 else 'ies'} detected: {', '.join(anomaly_dates[:3])}."
        else:
            summary = f"{count} price anomalies detected; most recent: {', '.join(anomaly_dates[-3:])}."

        return AnomalyResult(count=count, dates=anomaly_dates, summary=summary)

    except Exception as e:
        log.warning("ml.anomaly: detection failed", error=str(e))
        return None
