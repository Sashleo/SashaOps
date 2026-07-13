"""
intelligence/verdicts.py — Interpretation tokens for the token-contract system (§5.2).

Every numeric value token MUST be accompanied by a locally-computed interpretation
token that states WHAT the number MEANS, not just what it is.
The meaning is pre-computed so reports state it plainly, never re-derive it.

All functions are pure (no DB calls, no AI calls). Input: a financials/context dict.
Output: a dict of interpretation strings that become tokens in the token sheet.

Public entry point:
    compute_verdicts(financials, sector_medians, history) -> dict[str, str]

The returned dict has string keys matching token hint names (e.g. "LIQ_VERDICT")
and string values that describe the finding (e.g. "above sector median, stable").
The caller registers each value via `sheet.add(value, hint=key)`.
"""

from __future__ import annotations

from typing import Optional


# ── Threshold tables ───────────────────────────────────────────────────────

# Altman Z-Score zones (original model)
_Z_SAFE    = 2.99
_Z_GREY_LO = 1.81

# Generic liquidity thresholds (current ratio)
_CR_HEALTHY = 1.5
_CR_TIGHT   = 1.0

# Debt/equity thresholds (conservative Pakistan manufacturing context)
_DE_LOW     = 0.5
_DE_HIGH    = 2.0

# Gross margin thresholds
_GM_STRONG  = 0.30
_GM_THIN    = 0.10

# Revenue growth assessment bands
_GROWTH_STRONG = 0.15   # 15%+
_GROWTH_WEAK   = -0.05  # declining


# ── Helper: direction label ────────────────────────────────────────────────

def _trend(current: float, previous: Optional[float], label_up: str = "improving",
           label_down: str = "deteriorating", label_flat: str = "stable",
           threshold: float = 0.02) -> str:
    """Return a simple trend description comparing current to previous."""
    if previous is None or previous == 0:
        return label_flat
    change = (current - previous) / abs(previous)
    if change > threshold:
        return label_up
    if change < -threshold:
        return label_down
    return label_flat


def _vs_median(value: float, median: Optional[float], unit: str = "") -> str:
    """Return 'above sector median', 'below sector median', or 'near sector median'."""
    if median is None:
        return "no sector comparison available"
    diff_pct = (value - median) / (abs(median) + 1e-9) * 100
    if diff_pct > 10:
        return f"above sector median{' ' + unit if unit else ''}"
    if diff_pct < -10:
        return f"below sector median{' ' + unit if unit else ''}"
    return f"near sector median{' ' + unit if unit else ''}"


# ── Individual verdict functions ───────────────────────────────────────────

def liquidity_verdict(
    current_ratio: Optional[float],
    quick_ratio: Optional[float] = None,
    median_cr: Optional[float] = None,
    prev_cr: Optional[float] = None,
) -> str:
    """LIQ_VERDICT — short interpretation of the company's short-term liquidity."""
    if current_ratio is None:
        return "liquidity data unavailable"

    parts: list[str] = []

    if current_ratio >= _CR_HEALTHY:
        parts.append("adequate short-term liquidity")
    elif current_ratio >= _CR_TIGHT:
        parts.append("tight but manageable liquidity")
    else:
        parts.append("strained short-term liquidity")

    parts.append(_vs_median(current_ratio, median_cr))

    if prev_cr is not None:
        parts.append(_trend(current_ratio, prev_cr, "improving", "deteriorating"))

    if quick_ratio is not None and quick_ratio < 0.5:
        parts.append("quick ratio signals inventory dependency")

    return ", ".join(parts)


def altman_z_verdict(z_score: Optional[float], prev_z: Optional[float] = None) -> str:
    """Z_ZONE — Altman Z-Score zone and trend."""
    if z_score is None:
        return "Z-Score not computable - insufficient financial data"

    if z_score >= _Z_SAFE:
        zone = "safe zone (low distress risk)"
    elif z_score >= _Z_GREY_LO:
        zone = "grey zone (moderate distress risk - monitor)"
    else:
        zone = "distress zone (high bankruptcy risk - urgent attention)"

    trend_str = ""
    if prev_z is not None:
        trend_str = ", " + _trend(z_score, prev_z, "trending toward safety",
                                   "trending toward distress")

    return zone + trend_str


def valuation_verdict(
    dcf_base: Optional[float],
    market_price: Optional[float] = None,
    pe_ratio: Optional[float] = None,
    median_pe: Optional[float] = None,
) -> str:
    """VAL_VERDICT — valuation interpretation vs DCF and peer PE."""
    parts: list[str] = []

    if dcf_base is not None and market_price is not None and market_price > 0:
        premium_pct = (market_price - dcf_base) / dcf_base * 100
        if premium_pct > 15:
            parts.append(f"trading {abs(premium_pct):.0f}% above base-case fair value (stretched)")
        elif premium_pct < -15:
            parts.append(f"trading {abs(premium_pct):.0f}% below base-case fair value (potential upside)")
        else:
            parts.append("trading near base-case fair value")
    elif dcf_base is not None:
        parts.append("DCF estimate available; no market price for comparison")

    if pe_ratio is not None and median_pe is not None:
        parts.append(_vs_median(pe_ratio, median_pe, "(P/E)"))
    elif pe_ratio is not None:
        if pe_ratio > 30:
            parts.append("elevated P/E - growth or overvaluation implied")
        elif pe_ratio < 8:
            parts.append("low P/E - value opportunity or concern about earnings quality")

    return ", ".join(parts) if parts else "valuation data unavailable"


def growth_verdict(
    rev_growth: Optional[float],
    median_growth: Optional[float] = None,
    prev_growth: Optional[float] = None,
) -> str:
    """GROWTH_VERDICT — revenue growth context."""
    if rev_growth is None:
        return "revenue growth data unavailable"

    if rev_growth >= _GROWTH_STRONG:
        level = "strong revenue growth"
    elif rev_growth >= 0:
        level = "modest revenue growth"
    else:
        level = "revenue contraction"

    parts = [f"{level} ({rev_growth:+.1%})"]
    parts.append(_vs_median(rev_growth, median_growth))

    if prev_growth is not None:
        accel = _trend(rev_growth, prev_growth, "accelerating", "decelerating")
        if accel != "stable":
            parts.append(accel)

    return ", ".join(parts)


def leverage_verdict(
    debt_equity: Optional[float],
    median_de: Optional[float] = None,
    interest_coverage: Optional[float] = None,
) -> str:
    """LEV_VERDICT — leverage and debt service interpretation."""
    if debt_equity is None:
        return "leverage data unavailable"

    if debt_equity <= _DE_LOW:
        level = "conservatively financed"
    elif debt_equity <= _DE_HIGH:
        level = "moderate leverage"
    else:
        level = "highly leveraged"

    parts = [level]
    parts.append(_vs_median(debt_equity, median_de))

    if interest_coverage is not None:
        if interest_coverage < 1.5:
            parts.append("interest coverage weak (potential debt service stress)")
        elif interest_coverage > 5:
            parts.append("strong interest coverage")

    return ", ".join(parts)


def sentiment_verdict(
    dominant_label: Optional[str],
    confidence: Optional[float],
    headline_count: int = 0,
) -> str:
    """SENT_VERDICT — sentiment interpretation."""
    if dominant_label is None:
        return "insufficient news data for sentiment assessment"

    label_lower = dominant_label.lower()
    if label_lower == "positive":
        tone = "favourable market tone"
    elif label_lower == "negative":
        tone = "cautious or negative market tone"
    else:
        tone = "neutral market tone"

    conf_str = ""
    if confidence is not None:
        if confidence >= 0.75:
            conf_str = " (high confidence)"
        elif confidence >= 0.50:
            conf_str = " (moderate confidence)"
        else:
            conf_str = " (low confidence - mixed signals)"

    count_str = f" across {headline_count} headlines" if headline_count > 0 else ""
    return f"{tone}{conf_str}{count_str}"


def margin_verdict(
    gross_margin: Optional[float],
    net_margin: Optional[float] = None,
    median_gm: Optional[float] = None,
    prev_gm: Optional[float] = None,
) -> str:
    """MARGIN_VERDICT — profitability margin interpretation."""
    if gross_margin is None:
        return "margin data unavailable"

    if gross_margin >= _GM_STRONG:
        level = "healthy gross margins"
    elif gross_margin >= _GM_THIN:
        level = "thin but positive margins"
    else:
        level = "margin compression - cost pressure"

    parts = [level]
    parts.append(_vs_median(gross_margin, median_gm))

    if prev_gm is not None:
        parts.append(_trend(gross_margin, prev_gm, "expanding", "contracting"))

    if net_margin is not None and gross_margin > 0:
        overhead_ratio = 1 - net_margin / gross_margin
        if overhead_ratio > 0.7:
            parts.append("high overhead absorption narrowing net margin")

    return ", ".join(parts)


# ── Master entry point ─────────────────────────────────────────────────────

def _load_sector_medians(sector: str) -> dict:
    """Build a medians dict from the M9 psx_sector_medians table for a sector.

    Only metrics with >= the minimum peer count are included; sectors/metrics with
    too few valid peers are simply absent, and the verdict functions then skip the
    peer comparison (stating no benchmark) rather than comparing against noise.
    """
    out: dict = {}
    if not sector:
        return out
    try:
        from intelligence.sector_medians import get_sector_median
        for metric, key in (("current_ratio", "current_ratio"),
                            ("debt_to_equity", "de_ratio"),
                            ("net_margin", "net_margin"),
                            ("roe", "roe"),
                            ("gross_margin", "gross_margin"),
                            ("pe", "pe_ratio")):
            m = get_sector_median(sector, metric)
            if m:
                out[key] = m["median"]
    except Exception:
        pass
    return out


def compute_verdicts(
    financials:      dict,
    sector_medians:  dict | None = None,
    history:         dict | None = None,
    sector:          str = "",
) -> dict[str, str]:
    """
    Compute all interpretation tokens for a given financials dict.

    Args:
        financials:     Flat dict from financial_model.py (ratios, scores, etc.)
        sector_medians: Optional dict of sector-level median values
                        {"current_ratio": 1.4, "de_ratio": 1.1, ...}
        history:        Optional dict of prior-period values for trend calculation
                        {"current_ratio": 1.3, "altman_z": 2.2, ...}

    Returns:
        Dict of hint → interpretation string. Each entry becomes a token via
        `sheet.add(value, hint=key)` in the calling code.
    """
    medians = sector_medians or _load_sector_medians(sector)
    hist    = history or {}

    verdicts: dict[str, str] = {}

    verdicts["LIQ_VERDICT"] = liquidity_verdict(
        current_ratio = financials.get("current_ratio"),
        quick_ratio   = financials.get("quick_ratio"),
        median_cr     = medians.get("current_ratio"),
        prev_cr       = hist.get("current_ratio"),
    )

    verdicts["Z_ZONE"] = altman_z_verdict(
        z_score = financials.get("altman_z"),
        prev_z  = hist.get("altman_z"),
    )

    verdicts["VAL_VERDICT"] = valuation_verdict(
        dcf_base     = financials.get("dcf_base"),
        market_price = financials.get("market_price"),
        pe_ratio     = financials.get("pe_ratio"),
        median_pe    = medians.get("pe_ratio"),
    )

    verdicts["GROWTH_VERDICT"] = growth_verdict(
        rev_growth    = financials.get("revenue_growth"),
        median_growth = medians.get("revenue_growth"),
        prev_growth   = hist.get("revenue_growth"),
    )

    verdicts["LEV_VERDICT"] = leverage_verdict(
        debt_equity       = financials.get("debt_equity"),
        median_de         = medians.get("debt_equity"),
        interest_coverage = financials.get("interest_coverage"),
    )

    verdicts["MARGIN_VERDICT"] = margin_verdict(
        gross_margin = financials.get("gross_margin"),
        net_margin   = financials.get("net_margin"),
        median_gm    = medians.get("gross_margin"),
        prev_gm      = hist.get("gross_margin"),
    )

    return verdicts
