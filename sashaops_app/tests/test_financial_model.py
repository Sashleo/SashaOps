"""Tests for the local financial analysis engine — no DB, no AI."""
import pytest
from intelligence.financial_model import _safe_div, analyse_from_document, _compute_health_score


def test_health_score_confidence_weighting():
    """A full statement yields high confidence; a bare P&L yields low confidence."""
    full = {
        "revenue": 1000, "gross_profit": 400, "net_income": 150, "total_assets": 2000,
        "shareholders_equity": 800, "current_assets": 600, "current_liabilities": 300,
        "total_debt": 500, "ebit": 200, "ebitda": 250, "revenue_prior_year": 850,
        "revenue_2y_ago": 700, "retained_earnings": 300,
    }
    a = analyse_from_document(full, "Full Co")
    assert 0 <= a.health_confidence <= 1.0
    assert "profitability" in a.health_basis

    bare = {"revenue": 1000, "net_income": 100}     # no balance sheet
    b = analyse_from_document(bare, "Bare Co")
    assert b.health_confidence < a.health_confidence   # less data → lower confidence
    assert "liquidity" in b.health_missing


def test_safe_div_zero_denominator_returns_none():
    assert _safe_div(10, 0) is None
    assert _safe_div(10, None) is None
    assert _safe_div(None, 5) is None


def test_safe_div_normal():
    assert _safe_div(10, 2) == pytest.approx(5.0)
    assert _safe_div(3, 4) == pytest.approx(0.75)


def test_known_financials_produce_expected_ratios():
    financials = {
        "revenue":             1_000,
        "gross_profit":          400,
        "net_income":            150,
        "total_assets":        2_000,
        "shareholders_equity":   800,
        "current_assets":        600,
        "current_liabilities":   300,
        "total_debt":            500,
        "ebit":                  200,
        "ebitda":                250,
    }
    a = analyse_from_document(financials, "Known Co")
    assert a.liquidity.current_ratio    == pytest.approx(2.0,    rel=0.01)
    assert a.profitability.net_margin   == pytest.approx(0.15,   rel=0.01)
    assert a.profitability.roe          == pytest.approx(0.1875, rel=0.01)
    assert a.profitability.gross_margin == pytest.approx(0.40,   rel=0.01)


def test_empty_dict_returns_all_none_no_crash():
    a = analyse_from_document({}, "Empty Co")
    assert a.liquidity.current_ratio is None
    assert a.profitability.roe is None
    assert a.profitability.net_margin is None
    assert a.company_name == "Empty Co"