"""
intelligence/dcf_rates.py — Resolve DCF valuation rates.

Priority for each rate:
  1. Manual override stored in dcf_assumptions table
  2. Live value from DB (SBP policy rate, computed CPI/GDP)
  3. Pakistan-calibrated fallback constant

Entry point: get_dcf_rates(is_bank=False) → dict with keys:
    risk_free_rate, equity_risk_premium, tax_rate, terminal_growth
"""

from __future__ import annotations

# Pakistan-calibrated fallbacks (Oct 2026)
_FALLBACKS: dict[str, float] = {
    "risk_free_rate":      11.5,   # SBP policy rate Sep 2026
    "equity_risk_premium":  5.0,   # Damodaran mature-market ERP; correct with local Rf
    "tax_rate_corporate":  29.0,   # Finance Act TY2026 (non-financial listed)
    "tax_rate_bank":       43.0,   # IT Amendment Ordinance Dec 2024 (banks/DFIs, TY2026)
    "terminal_growth":      8.0,   # ~50% CPI (10.26%) + 50% GDP (~4%) ≈ 7.1%, rounded up
}

_RATE_META: list[dict] = [
    {
        "key":   "risk_free_rate",
        "label": "Risk-free rate",
        "notes": "SBP policy rate. Auto-updated when the SBP scraper runs.",
        "unit":  "%",
    },
    {
        "key":   "equity_risk_premium",
        "label": "Equity risk premium (ERP)",
        "notes": (
            "Mature-market ERP (Damodaran). Use ~5% when pairing with local SBP rate as Rf. "
            "Update Jan/Jul when Damodaran publishes."
        ),
        "unit":  "%",
    },
    {
        "key":   "tax_rate_corporate",
        "label": "Corporate tax — non-financial",
        "notes": "Finance Act TY2026. Listed non-financial companies. Update July when Finance Bill passes.",
        "unit":  "%",
    },
    {
        "key":   "tax_rate_bank",
        "label": "Corporate tax — banks / DFIs",
        "notes": "IT Amendment Ordinance Dec 2024. 43% TY2026; drops to 42% from TY2027.",
        "unit":  "%",
    },
    {
        "key":   "terminal_growth",
        "label": "Terminal growth rate",
        "notes": "50%×CPI + 50%×GDP growth, clamped [6–12%]. Auto-updates when macro data refreshes.",
        "unit":  "%",
    },
]


def _live_risk_free() -> float | None:
    try:
        from database.macro_data import get_latest_macro
        rate = get_latest_macro().get("sbp_policy_rate")
        return float(rate) if rate is not None else None
    except Exception:
        return None


def _computed_terminal_growth() -> float | None:
    """50%×CPI + 50%×GDP, clamped [6, 12]%."""
    try:
        from database.macro_data import get_latest_macro
        macro = get_latest_macro()
        cpi = macro.get("cpi")
        gdp = macro.get("gdp_growth")
        if cpi is None and gdp is None:
            return None
        cpi = float(cpi) if cpi is not None else 8.0
        gdp = float(gdp) if gdp is not None else 4.0
        return round(max(6.0, min(12.0, 0.5 * cpi + 0.5 * gdp)), 2)
    except Exception:
        return None


def get_dcf_rates(is_bank: bool = False) -> dict:
    """
    Return Pakistan-calibrated DCF rates resolved via: override → live → fallback.

    Always-present keys: risk_free_rate, equity_risk_premium, tax_rate, terminal_growth
    Metadata keys (for UI / debugging): _sources, _live_values, _overrides, _is_bank
    """
    try:
        from database.dcf_settings import get_all_overrides
        overrides = get_all_overrides()
    except Exception:
        overrides = {}

    live_rfr = _live_risk_free()
    live_tg  = _computed_terminal_growth()

    _live: dict[str, float | None] = {
        "risk_free_rate":      live_rfr,
        "equity_risk_premium": None,
        "tax_rate_corporate":  None,
        "tax_rate_bank":       None,
        "terminal_growth":     live_tg,
    }
    _sources: dict[str, str] = {}

    def _resolve(key: str) -> float:
        ov = overrides.get(key)
        if ov is not None:
            _sources[key] = "override"
            return ov
        lv = _live.get(key)
        if lv is not None:
            _sources[key] = "live"
            return lv
        _sources[key] = "fallback"
        return _FALLBACKS[key]

    rfr  = _resolve("risk_free_rate")
    erp  = _resolve("equity_risk_premium")
    tg   = _resolve("terminal_growth")
    tkey = "tax_rate_bank" if is_bank else "tax_rate_corporate"
    tax  = _resolve(tkey)
    _sources["tax_rate"] = _sources[tkey]

    return {
        "risk_free_rate":      rfr,
        "equity_risk_premium": erp,
        "tax_rate":            tax,
        "terminal_growth":     tg,
        "_sources":            _sources,
        "_live_values":        _live,
        "_overrides":          overrides,
        "_is_bank":            is_bank,
    }


def get_all_rates_display() -> list[dict]:
    """
    Return a list of rate dicts for the Settings UI.
    Each dict: key, label, notes, unit, fallback, live_value, live_label,
               override_value, resolved, source
    """
    try:
        from database.dcf_settings import get_all_overrides
        overrides = get_all_overrides()
    except Exception:
        overrides = {}

    live_rfr = _live_risk_free()
    live_tg  = _computed_terminal_growth()

    _live: dict[str, float | None] = {
        "risk_free_rate":      live_rfr,
        "equity_risk_premium": None,
        "tax_rate_corporate":  None,
        "tax_rate_bank":       None,
        "terminal_growth":     live_tg,
    }
    _live_labels: dict[str, str | None] = {
        "risk_free_rate":      f"SBP {live_rfr:.2f}%"   if live_rfr is not None else None,
        "equity_risk_premium": None,
        "tax_rate_corporate":  None,
        "tax_rate_bank":       None,
        "terminal_growth":     f"computed {live_tg:.2f}%" if live_tg is not None else None,
    }

    rows = []
    for meta in _RATE_META:
        key = meta["key"]
        ov  = overrides.get(key)
        lv  = _live.get(key)
        if ov is not None:
            source, resolved = "override", ov
        elif lv is not None:
            source, resolved = "live", lv
        else:
            source, resolved = "fallback", _FALLBACKS[key]
        rows.append({
            **meta,
            "fallback":       _FALLBACKS[key],
            "live_value":     lv,
            "live_label":     _live_labels.get(key),
            "override_value": ov,
            "resolved":       resolved,
            "source":         source,
        })
    return rows
