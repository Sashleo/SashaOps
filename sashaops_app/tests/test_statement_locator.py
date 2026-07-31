"""
Tests for targeted statement-page extraction.

Locks in the behaviours that make this cheaper AND safer than whole-document OCR:
title matching that does not chase cross-references, two-column parsing that
captures the prior period, and a reconciliation gate that rejects rather than
ships an unbalanced statement.
"""

from intelligence.statement_locator import (
    _match_type, _is_consolidated, expand_pages, parse_two_column, _reconciles,
    _scan_order, RECONCILE_TOLERANCE, MAX_LOCATE_OCR_PAGES,
)


# ── Page title matching ───────────────────────────────────────────────────────

_TABLE_ROWS = ("Total assets 5,000,000 4,400,000\n"
               "Total equity 3,000,000 2,700,000\n"
               "Total liabilities 2,000,000 1,700,000\n"
               "Cash 100,000 90,000\n"
               "Inventory 250,000 240,000\n"
               "Receivables 300,000 280,000\n")


def test_matches_statement_of_financial_position():
    assert _match_type("Statement of Financial Position\nAs at June 30, 2025\n"
                      + _TABLE_ROWS) == "balance_sheet"


def test_matches_balance_sheet_and_cash_flows_and_income():
    assert _match_type("Balance Sheet\n" + _TABLE_ROWS) == "balance_sheet"
    assert _match_type("Statement of Cash Flows\n" + _TABLE_ROWS) == "cashflow"
    assert _match_type("Profit and Loss Account\n" + _TABLE_ROWS) == "income"


def test_auditors_report_matches_without_numeric_density():
    # The auditor's report is genuinely prose, so the table test must not apply.
    assert _match_type("Independent Auditor's Report\nTo the members of the "
                      "Company\nWe have audited the annexed statements.") == "audit"


def test_title_without_a_table_is_rejected():
    # Verified on FFC: pages 48 and 77 matched a title pattern but are a SWOT page
    # and an MD&A ratio commentary. Neither carries a statement, and OCR of their
    # multi-column layout emits short fragments that defeat a line-length test.
    narrative = ("Statement of financial position\n"
                 "Operating in a mature industry with relatively stagnant demand\n"
                 "Provision of concessionary gas rates to competitors")
    assert _match_type(narrative) is None


def test_title_far_down_the_page_is_a_mention_not_a_heading():
    body = "\n".join(f"Narrative line {i}" for i in range(20))
    assert _match_type(body + "\nstatement of financial position\n" + _TABLE_ROWS) is None


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


# ── Scan order ────────────────────────────────────────────────────────────────

def test_scan_order_reaches_the_statement_zone_inside_the_ocr_budget():
    # Annual reports open with narrative and put the audited statements past the
    # middle. Scanning front-to-back spent the whole budget on the narrative: in a
    # 608-page report the statements sit around 40-50% and were never reached.
    for n_pages, fraction in ((608, 0.40), (608, 0.50), (176, 0.375), (221, 0.45)):
        order = _scan_order(list(range(n_pages)), n_pages)
        target = int(n_pages * fraction)
        assert order.index(target) < MAX_LOCATE_OCR_PAGES, (
            f"{n_pages}-page doc: statement at {fraction:.0%} not reached in budget")


def test_scan_order_still_covers_every_page():
    # Reordering must not drop pages: a statement outside the expected zone is
    # still found, just later.
    order = _scan_order(list(range(300)), 300)
    assert sorted(order) == list(range(300))


def test_scan_order_is_safe_on_empty_input():
    assert _scan_order([], 0) == []


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


# ── Coherence: the identity alone is not sufficient ───────────────────────────

def test_subtotal_exceeding_its_total_is_rejected_even_when_the_identity_holds():
    # ABOT passed reconciliation at 0.00% while reporting current_assets of
    # 30,993mn against total_assets of 47mn. All three identity lines were misread
    # at the same scale, so the identity held and the nonsense passed.
    ok, why = _reconciles({"total_assets": 47, "total_equity": 30,
                          "total_liabilities": 17, "current_assets": 30_993})
    assert ok is False
    assert "inconsistent scales" in why


def test_current_liabilities_exceeding_total_liabilities_is_rejected():
    ok, why = _reconciles({"total_assets": 5_000_000, "total_equity": 3_000_000,
                          "total_liabilities": 2_000_000,
                          "current_liabilities": 2_500_000})
    assert ok is False
    assert "inconsistent scales" in why


def test_coherent_statement_still_reconciles():
    ok, _ = _reconciles({"total_assets": 5_000_000, "total_equity": 3_000_000,
                        "total_liabilities": 2_000_000, "current_assets": 1_500_000,
                        "current_liabilities": 900_000})
    assert ok is True
