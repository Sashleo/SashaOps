"""
ml/trends.py — Trend forecasting via statsmodels ARIMA.

Fits a simple ARIMA(1,1,1) model on an index or forex time series and
produces a 90-day directional forecast with a confidence band.

Output feeds {{TREND_DIR}} and {{TREND_BAND}} tokens for the Economy module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

log = structlog.get_logger()


@dataclass
class TrendResult:
    direction:    str    # "upward" | "downward" | "sideways"
    forecast_90d: float  # predicted value 90 days out
    lower_band:   float  # 80% confidence interval lower bound
    upper_band:   float  # 80% confidence interval upper bound
    summary:      str    # human-readable sentence


def forecast(
    series:    list[float],
    horizon:   int = 90,
    order:     tuple = (1, 1, 1),
) -> Optional[TrendResult]:
    """
    Forecast the next `horizon` steps using ARIMA.

    Args:
        series:  Chronological numeric series (index levels, exchange rates, etc.).
        horizon: Steps to forecast (default 90 days).
        order:   ARIMA (p, d, q) order (default 1,1,1).

    Returns:
        TrendResult, or None if statsmodels unavailable or too few data points.
    """
    if len(series) < 60:
        log.debug("ml.trends: insufficient data points", count=len(series))
        return None

    try:
        import warnings
        import numpy as np
        from statsmodels.tsa.arima.model import ARIMA

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ARIMA(series, order=order)
            fit = model.fit()
            forecast_res = fit.get_forecast(steps=horizon)
            mean_forecast = forecast_res.predicted_mean
            conf_int = forecast_res.conf_int(alpha=0.2)  # 80% CI

        last_value = series[-1]
        future_value = float(mean_forecast.iloc[-1])
        lower = float(conf_int.iloc[-1, 0])
        upper = float(conf_int.iloc[-1, 1])

        change_pct = (future_value - last_value) / (abs(last_value) + 1e-9) * 100

        if change_pct > 2:
            direction = "upward"
        elif change_pct < -2:
            direction = "downward"
        else:
            direction = "sideways"

        summary = (
            f"90-day {direction} trend projected: "
            f"{last_value:.2f} → {future_value:.2f} "
            f"(80% CI: {lower:.2f}–{upper:.2f})."
        )

        return TrendResult(
            direction=direction,
            forecast_90d=round(future_value, 4),
            lower_band=round(lower, 4),
            upper_band=round(upper, 4),
            summary=summary,
        )

    except Exception as e:
        log.warning("ml.trends: forecasting failed", error=str(e))
        return None
