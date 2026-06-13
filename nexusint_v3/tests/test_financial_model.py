"""Tests for the local financial analysis engine (no AI, pure math)."""

import pytest

from intelligence.financial_model import _safe_div, analyse_from_document


def test_safe_div_zero_denominator_returns_none():
    assert _safe_div(10, 0) is None
    assert _safe_div(10, None) is None
    assert _safe_div(None, 5) is None


def test_safe_div_normal():
    assert _safe_div(10, 2) == 5.0
    assert _safe_div(3, 4) == 0.75


def test_known_financials_produce_expected_ratios():
    """Feed a known set of financials; ratios must land in expected ranges (1% tol)."""
    financials = {
        "revenue":             1000,
        "gross_profit":        400,
        "net_income":          150,
        "total_assets":        2000,
        "shareholders_equity": 800,
        "current_assets":      600,
        "current_liabilities": 300,
        "total_debt":          500,
        "ebit":                200,
        "ebitda":              250,
    }
    a = analyse_from_document(financials, "Known Co")

    # current ratio = 600 / 300 = 2.0
    assert a.liquidity.current_ratio == pytest.approx(2.0, rel=0.01)
    # net margin = 150 / 1000 = 0.15
    assert a.profitability.net_margin == pytest.approx(0.15, rel=0.01)
    # ROE = 150 / 800 = 0.1875
    assert a.profitability.roe == pytest.approx(0.1875, rel=0.01)
    # gross margin = 400 / 1000 = 0.40
    assert a.profitability.gross_margin == pytest.approx(0.40, rel=0.01)


def test_empty_dict_returns_all_none_ratios_no_crash():
    a = analyse_from_document({}, "Empty Co")
    assert a.liquidity.current_ratio is None
    assert a.profitability.roe is None
    assert a.profitability.net_margin is None
    assert a.leverage.debt_to_ebitda is None
    # Must not raise and must still produce a usable object
    assert a.company_name == "Empty Co"
