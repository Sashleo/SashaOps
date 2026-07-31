"""
Tests for targeted statement-page extraction.

Locks in the behaviours that make this cheaper AND safer than whole-document OCR:
title matching that does not chase cross-references, two-column parsing that
captures the prior period, and a reconciliation gate that rejects rather than
ships an unbalanced statement.
"""

from intelligence.statement_locator import (
    _match_type, _is_consolidated, expand_pages, parse_two_column, _reconciles,
    RECONCILE_TOLERANCE,
)


# ── Page title matching ───────────────────────────────────────────────────────

def test_matches_statement_of_financial_position():
    assert _match_type("Statement of Financial Position\nAs at June 30, 2025") \
        == "balance_sheet"


def test_matches_balance_sheet_and_cash_flows_and_audit():
    assert _match_type("Balance Sheet") == "balance_sheet"
    assert _match_type("Statement of Cash Flows") == "cashflow"
    assert _match_type("Independent Auditor's Report") == "audit"
    assert _match_type("Profit and Loss Account") == "income"


def test_long_prose_line_is_not_treated_as_a_title():
    # A cross-reference in the notes must not send high-DPI OCR to the wrong page.
    prose = ("These figures should be read together with the statement of "
             "financial position presented on page 84 of this annual report.")
    assert _match_type(prose) is None


def test_no_match_returns_none():
    assert _match_type("Chairman's Review") is None
    assert _match_type("") is None


# ── Consolidated vs unconsolidated ────────────────────────────────────────────

def test_consolidated_detected():
    assert _is_consolidated("Consolidated Statement of Financial Position") is True


def test_unconsolidated_is_not_consolidated():
    # "unconsolidated" contains "consolidated" as a substring; the standalone
    # statement must not be misread as the group one.
    assert _is_consolidated("Unconsolidated Statement of Financial Position") is False


# ── Page span ─────────────────────────────────────────────────────────────────

def test_expand_pages_covers_following_pages_without_duplicates():
    assert expand_pages([10], span=2) == [10, 11, 12]
    assert expand_pages([10, 11], span=2) == [10, 11, 12, 13]


# ── Two-column parsing ────────────────────────────────────────────────────────

_STATEMENT = """Statement of Financial Position
Total current assets 1,500,000 1,200,000
Total assets 5,000,000 4,400,000
Total equity 3,000,000 2,700,000
Total current liabilities 900,000 800,000
Total liabilities 2,000,000 1,700,000
"""


def test_current_and_prior_periods_are_both_captured():
    p = parse_two_column(_STATEMENT)
    assert p["total_assets"]["current"] == 5_000_000
    assert p["total_assets"]["prior"] == 4_400_000
    assert p["total_equity"]["prior"] == 2_700_000


def test_prior_is_none_when_only_one_column_present():
    p = parse_two_column("Total assets 5,000,000\n")
    assert p["total_assets"]["current"] == 5_000_000
    assert p["total_assets"]["prior"] is None


# ── Reconciliation gate ───────────────────────────────────────────────────────

def test_balanced_statement_reconciles():
    ok, why = _reconciles({"total_assets": 5_000_000, "total_equity": 3_000_000,
                          "total_liabilities": 2_000_000})
    assert ok is True


def test_unbalanced_statement_is_rejected():
    ok, why = _reconciles({"total_assets": 5_000_000, "total_equity": 3_000_000,
                          "total_liabilities": 1_000_000})
    assert ok is False
    assert "%" in why


def test_incomplete_statement_is_neither_pass_nor_fail_and_names_the_gap():
    ok, why = _reconciles({"total_assets": 5_000_000})
    assert ok is None
    assert "total_equity" in why and "total_liabilities" in why


def test_tolerance_is_half_a_percent():
    assert RECONCILE_TOLERANCE == 0.005
    # Just inside tolerance passes; a larger gap does not.
    ok, _ = _reconciles({"total_assets": 1_000_000, "total_equity": 600_000,
                        "total_liabilities": 396_000})
    assert ok is True
