"""
intelligence/validation.py — Plausibility bounds for computed financial metrics.

A parse error (wrong column, unit mismatch, OCR noise) can produce a mathematically
valid but nonsensical ratio (e.g. a 4000% net margin, a current ratio of 900). Such
values must never be displayed as a number, never feed the health score / DCF /
verdicts, and must be logged with their raw inputs for debugging.

validate_metric(name, value, is_bank) -> {"value", "status", "note"}
  status: "ok" | "implausible" | "missing"
"""

from __future__ import annotations

from typing import Optional

import structlog

log = structlog.get_logger()

# (min, max) inclusive bounds for each ratio in DECIMAL form (0.35 = 35%).
PLAUSIBILITY_BOUNDS = {
    "gross_margin":      (-0.5, 0.95),
    "net_margin":        (-1.0, 0.60),
    "roe":               (-1.0, 0.60),
    "roa":               (-0.5, 0.40),
    "current_ratio":     (0.05, 15.0),
    "quick_ratio":       (0.02, 12.0),
    "debt_to_equity":    (0.0, 15.0),
    "asset_turnover":    (0.01, 5.0),
    "equity_multiplier": (1.0, 25.0),
    "interest_coverage": (-20, 200),
    "altman_z":          (-10, 25),
    "wacc":              (0.05, 0.40),
    "revenue_growth":    (-0.8, 3.0),
}

# Banks carry structurally different leverage / margins.
BANK_OVERRIDES = {
    "roe":               (-0.5, 0.40),
    "debt_to_equity":    (2.0, 30.0),
    "equity_multiplier": (5.0, 30.0),
    "net_margin":        (-0.5, 0.50),
}


def _bounds(name: str, is_bank: bool):
    if is_bank and name in BANK_OVERRIDES:
        return BANK_OVERRIDES[name]
    return PLAUSIBILITY_BOUNDS.get(name)


def validate_metric(name: str, value, is_bank: bool = False, raw_inputs: Optional[dict] = None) -> dict:
    """
    Return {"value", "status", "note"}.
      missing     — value is None / non-numeric
      ok          — within bounds (or no bounds defined for this metric)
      implausible — outside bounds; value is nulled and must not be displayed/used
    """
    if value is None:
        return {"value": None, "status": "missing", "note": "not computed"}
    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"value": None, "status": "missing", "note": "non-numeric"}

    b = _bounds(name, is_bank)
    if b is None:
        return {"value": v, "status": "ok", "note": ""}

    lo, hi = b
    if lo <= v <= hi:
        return {"value": v, "status": "ok", "note": ""}

    # Out of bounds — exclude and log with raw inputs for debugging.
    log.warning("Implausible metric excluded", metric=name, value=v,
                bounds=(lo, hi), is_bank=is_bank, raw_inputs=raw_inputs or {})
    return {"value": None, "status": "implausible",
            "note": f"{name}={v:.4g} outside plausible range [{lo}, {hi}] - data error suspected"}


def validate_all(metrics: dict, is_bank: bool = False, raw_inputs: Optional[dict] = None) -> dict:
    """Validate a dict of {metric_name: value}. Returns {name: validate_metric(...)}."""
    return {k: validate_metric(k, v, is_bank, raw_inputs) for k, v in metrics.items()}
