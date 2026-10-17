"""
Tests for the Phase 3 chart payload builder.

The plan's universal chart rules are enforced in one place, so these lock in the
two that matter most for honesty: a chart never renders below 2 valid points, and
a period whose value failed validation becomes a GAP with a footnote rather than
being interpolated or zeroed.
"""

from intelligence.psx_analyser import PSXAnalysis
from intelligence.psx_charts import (
    chart1_ratio_trends, chart2_revenue_profit, chart5_cash_cycle,
    chart6_altman_gauge, MIN_POINTS, SOURCE_LINE,
)


def _a(**kw) -> PSXAnalysis:
    a = PSXAnalysis(ticker="TEST")
    a.sector = "CEMENT"
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_min_points_is_two():
    assert MIN_POINTS == 2


def test_chart_blocked_with_a_single_period():
    a = _a(annual_periods=["2025"],
           income_statement={"2025": {"revenue": 1e9, "net_income": 1e8}})
    out = chart2_revenue_profit(a)
    assert out["renderable"] is False
    assert "at least 2 periods" in out["reason"]


def test_chart2_renders_with_two_periods_and_carries_units_and_source():
    a = _a(annual_periods=["2025", "2024"],
           income_statement={"2025": {"revenue": 2e9, "net_income": 4e8},
                            "2024": {"revenue": 1e9, "net_income": 2e8}})
    out = chart2_revenue_profit(a)
    assert out["renderable"] is True
    assert out["spec"]["y_label"] == "PKR millions"
    assert out["source"] == SOURCE_LINE
    # Oldest period first.
    assert out["spec"]["labels"] == ["2024", "2025"]
    # Growth annotation is computed from the plotted values, not re-derived.
    assert out["spec"]["annotations"][1] == 100.0


def test_implausible_period_becomes_a_gap_not_a_zero():
    ratios = {
        "2025": {"net_margin": {"value": 0.2, "status": "ok"}},
        "2024": {"net_margin": {"value": None, "status": "implausible"}},
        "2023": {"net_margin": {"value": 0.1, "status": "ok"}},
    }
    a = _a(annual_periods=["2025", "2024", "2023"], ratios=ratios)
    out = chart1_ratio_trends(a)
    assert out["renderable"] is True
    panel = next(p for p in out["spec"]["series"] if p["name"] == "Net margin")
    # 2024 sits in the middle chronologically and must be a gap, never 0.
    assert panel["data"][1] is None
    assert 0 not in [d for d in panel["data"] if d is not None]
    assert any("2024" in f for f in out["footnotes"])


def test_cash_cycle_is_suppressed_for_banks_with_a_reason():
    out = chart5_cash_cycle(_a(is_bank=True))
    assert out["renderable"] is False
    assert "not meaningful for financial institutions" in out["reason"]



def test_altman_gauge_blocked_names_the_missing_fields():
    a = _a(altman={"computable": False, "missing": ["total assets", "total equity"]})
    out = chart6_altman_gauge(a)
    assert out["renderable"] is False
    assert "total assets" in out["reason"]


def test_altman_gauge_uses_four_factor_bands_for_a_bank():
    a = _a(is_bank=True, annual_periods=["2025"],
           altman={"computable": True, "zone": "Safe", "formula_used": "Z'' (4 factor)",
                   "score": {"value": 3.0, "status": "ok"}})
    out = chart6_altman_gauge(a)
    assert out["renderable"] is True
    assert "1.1" in out["spec"]["bands"][0]["name"]
