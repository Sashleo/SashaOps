"""
intelligence/validation.py — Plausibility bounds for computed financial metrics.

A parse error (wrong column, unit mismatch, OCR noise) can produce a mathematically
valid but nonsensical ratio (e.g. a 4000% net margin, a current ratio of 900). Such
values must never be displayed as a number, never feed the health score / DCF /
verdicts, and must be logged with their raw inputs for debugging.

validate_metric(name, value, is_bank, sector) -> {"value", "status", "note"}
  status: "ok" | "implausible" | "missing"

Bound priority (highest first):
  1. is_bank=True  → BANK_OVERRIDES
  2. sector match  → SECTOR_PROFILE_BOUNDS via _SECTOR_PROFILE mapping
  3. global        → PLAUSIBILITY_BOUNDS
  4. no entry      → no bounds, always ok
"""

from __future__ import annotations

from typing import Optional

import structlog

log = structlog.get_logger()

# (min, max) inclusive bounds in DECIMAL form (0.35 = 35%).
# These are the global fallback for unmapped or miscellaneous sectors.
PLAUSIBILITY_BOUNDS = {
    "gross_margin":      (-0.5, 1.0),
    "net_margin":        (-1.0, 2.0),   # wide fallback; sector profiles tighten this
    "roe":               (-1.0, 1.50),
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

# Maps exact PSX sector_name values to a profile key.
_SECTOR_PROFILE: dict[str, str] = {
    # Investment / capital-markets — investment returns can be multiples of fee revenue
    "INV. BANKS / INV. COS. / SECURITIES COS.": "investment",
    "MODARABAS":                                 "investment",
    "LEASING COMPANIES":                         "investment",
    "CLOSE - END MUTUAL FUND":                   "investment",
    "EXCHANGE TRADED FUNDS":                     "investment",
    "GEM BOARD- MODARABAS":                      "investment",
    # Property / REIT — rental income and development gain structures
    "REAL ESTATE INVESTMENT TRUST":              "property",
    "PROPERTY":                                  "property",
    # Insurance — premium + investment income mix
    "INSURANCE":                                 "insurance",
    # Oil & gas exploration — high margin once producing
    "OIL & GAS EXPLORATION COMPANIES":           "og_exploration",
    # Oil & gas marketing — commodity pass-through, razor-thin by design
    "OIL & GAS MARKETING COMPANIES":             "og_marketing",
    # Refinery — moderate, crack-spread driven
    "REFINERY":                                  "refinery",
    # High-margin consumer / services
    "TOBACCO":                                   "high_margin",
    "PHARMACEUTICALS":                           "high_margin",
    "TECHNOLOGY & COMMUNICATION":                "high_margin",
    "GEM BOARD- TECHNOLOGY & COMMUNICATION":     "high_margin",
    # Power — utility-rate regulated / IPP capacity agreements
    "POWER GENERATION & DISTRIBUTION":           "power",
    "GEM BOARD- POWER GENERATION & DISTRIBUTION": "power",
    # Fertilizer — high margins from subsidised gas feedstock
    "FERTILIZER":                                "fertilizer",
    # Manufacturing — high asset intensity, tight margins
    "TEXTILE SPINNING":                          "manufacturing",
    "TEXTILE COMPOSITE":                         "manufacturing",
    "TEXTILE WEAVING":                           "manufacturing",
    "SYNTHETIC & RAYON":                         "manufacturing",
    "APPAREL":                                   "manufacturing",
    "WOOLLEN":                                   "manufacturing",
    "JUTE":                                      "manufacturing",
    "SUGAR & ALLIED INDUSTRIES":                 "manufacturing",
    "VANASPATI & ALLIED INDUSTRIES":             "manufacturing",
    "FOOD & PERSONAL CARE PRODUCTS":             "manufacturing",
    "ENGINEERING":                               "manufacturing",
    "AUTOMOBILE ASSEMBLER":                      "manufacturing",
    "AUTOMOBILE PARTS & ACCESSORIES":            "manufacturing",
    "PAPER, BOARD & PACKAGING":                  "manufacturing",
    "GEM BOARD- PAPER, BOARD & PACKAGING":       "manufacturing",
    "LEATHER & TANNERIES":                       "manufacturing",
    "GLASS & CERAMICS":                          "manufacturing",
    "CABLE & ELECTRICAL GOODS":                  "manufacturing",
    "TRANSPORT":                                 "manufacturing",
    "CEMENT":                                    "manufacturing",
    "CHEMICAL":                                  "manufacturing",
}

# Per-profile overrides. Only the metrics that differ from PLAUSIBILITY_BOUNDS
# need to be listed; missing keys fall through to PLAUSIBILITY_BOUNDS.
SECTOR_PROFILE_BOUNDS: dict[str, dict] = {
    "investment": {
        "gross_margin": (-0.5, 1.0),
        "net_margin":   (-2.0, 10.0),   # dividends/gains can be many × fee revenue
        "roe":          (-2.0,  5.0),
        "roa":          (-0.5,  0.50),
    },
    "property": {
        "gross_margin": (-0.2, 1.0),
        "net_margin":   (-0.5, 2.0),    # large development gains in a single year
        "roe":          (-0.5, 1.5),
    },
    "insurance": {
        "gross_margin": (-0.5, 1.0),    # loss ratio > 1 is possible
        "net_margin":   (-0.5, 0.80),
        "roe":          (-0.5, 0.80),
    },
    "og_exploration": {
        "gross_margin": (-0.2, 0.90),
        "net_margin":   (-0.5, 0.85),
        "roe":          (-0.3, 0.65),
    },
    "og_marketing": {
        "gross_margin": (-0.1, 0.15),
        "net_margin":   (-0.2, 0.12),
    },
    "refinery": {
        "gross_margin": (-0.3, 0.30),
        "net_margin":   (-0.5, 0.25),
    },
    "high_margin": {
        "gross_margin": (-0.1, 0.90),
        "net_margin":   (-0.5, 0.70),
        "roe":          (-0.5, 0.80),
    },
    "power": {
        "gross_margin": (-0.1, 0.80),
        "net_margin":   (-0.5, 0.65),
        "roe":          (-0.3, 0.55),
    },
    "fertilizer": {
        "gross_margin": (-0.1, 0.65),
        "net_margin":   (-0.3, 0.55),
        "roe":          (-0.3, 0.60),
    },
    "manufacturing": {
        "gross_margin": (-0.3, 0.55),
        "net_margin":   (-0.5, 0.40),
        "roe":          (-0.5, 0.50),
    },
}


def _bounds(name: str, is_bank: bool, sector: str = ""):
    if is_bank and name in BANK_OVERRIDES:
        return BANK_OVERRIDES[name]
    profile = _SECTOR_PROFILE.get(sector.strip().upper() if sector else "")
    if profile:
        sector_b = SECTOR_PROFILE_BOUNDS.get(profile, {}).get(name)
        if sector_b is not None:
            return sector_b
    return PLAUSIBILITY_BOUNDS.get(name)


def validate_metric(name: str, value, is_bank: bool = False,
                    raw_inputs: Optional[dict] = None, sector: str = "") -> dict:
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

    b = _bounds(name, is_bank, sector)
    if b is None:
        return {"value": v, "status": "ok", "note": ""}

    lo, hi = b
    if lo <= v <= hi:
        return {"value": v, "status": "ok", "note": ""}

    log.warning("Implausible metric excluded", metric=name, value=v,
                bounds=(lo, hi), is_bank=is_bank, sector=sector or "unknown",
                raw_inputs=raw_inputs or {})
    return {"value": None, "status": "implausible",
            "note": f"{name}={v:.4g} outside plausible range [{lo}, {hi}] - data error suspected"}


def validate_all(metrics: dict, is_bank: bool = False,
                 raw_inputs: Optional[dict] = None, sector: str = "") -> dict:
    """Validate a dict of {metric_name: value}. Returns {name: validate_metric(...)}."""
    return {k: validate_metric(k, v, is_bank, raw_inputs, sector) for k, v in metrics.items()}
