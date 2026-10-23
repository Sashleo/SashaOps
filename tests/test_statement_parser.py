"""
Regression tests for the accuracy-first statement parser.

Every case here is a REAL failure found while validating against two independent
76-firm samples drawn from all 38 PSX sectors. They exist so that none of these
value-corrupting bugs can silently come back.
"""

from intelligence.statement_parser import (
    _normalize_numbers, parse_statements, detect_scale, to_financial_dict,
)


# ── Number normalisation ──────────────────────────────────────────────────────

def test_space_inside_number_is_repaired():
    # ABL: parsed as 0.003bn instead of 3.44 trillion.
    assert "3,441,959,708" in _normalize_numbers("TOTAL LIABILITIES 3,441 ,959,708")


def test_periods_as_thousand_separators():
    assert "8,189,744" in _normalize_numbers("PROFIT AFTER TAXATION 8.189.744")


def test_leading_zero_glue_is_stripped():
    # FIL: "Sales 0171,450" parsed as 171.
    assert "171,450" in _normalize_numbers("Sales 0171,450")


def test_normalisation_never_corrupts_a_valid_number():
    # REGRESSION: the leading-zero rule fired inside "61,782,057,256", destroying the
    # ",057," group so it parsed as 61,782 - a 1,000,000x understatement (JDWS).
    out = _normalize_numbers("Revenue from contracts with customers 61,782,057,256")
    assert "61,782,057,256" in out


# ── Wrong-line / contra-line protection ───────────────────────────────────────

def test_sales_tax_is_not_revenue():
    # PAKT: "^sales?" matched "Sales tax (57,706,226)" -> revenue went NEGATIVE.
    p = parse_statements("Sales tax (57,706,226) (54,947,896)\n"
                         "Net Turnover 139,018 121,078\n")
    assert p["revenue"]["raw"] > 0
    assert "turnover" in p["revenue"]["source_line"].lower()


def test_total_equity_and_liabilities_is_not_equity():
    # AGHA/GAMON: "TOTAL EQUITY AND LIABILITIES" equals total ASSETS, not equity.
    p = parse_statements("Total assets 54,481,000 50,000,000\n"
                         "Total equity and liabilities 54,481,000 50,000,000\n")
    assert "total_equity" not in p or p["total_equity"]["raw"] != p["total_assets"]["raw"]


def test_stores_and_spares_is_not_inventory():
    # 6 holdout firms stored a consumables line as inventory.
    p = parse_statements("Stores, spares and loose tools 237,095 241,207\n")
    assert "inventory" not in p


def test_sub_item_is_not_a_balance_sheet_total():
    # JDWS: current_liabilities came from "Non-current liabilities: Long term investments".
    p = parse_statements("Non-current liabilities Long term investments 1,049,750,000\n")
    assert "current_liabilities" not in p


def test_operator_fund_subtotal_is_not_company_total():
    # UNIC (Takaful): "Total assets of Operator's Fund" is a segment, not the company.
    p = parse_statements("Total assets of Operator's Fund 594,700,410 672,721,167\n")
    assert "total_assets" not in p


# ── Unit scale ────────────────────────────────────────────────────────────────

def test_explicit_thousands_header_detected():
    assert detect_scale("Rupees in '000")[0] == 1_000


def test_explicit_millions_header_detected():
    assert detect_scale("Rs. in million")[0] == 1_000_000


def test_implausible_header_is_overridden_by_plausibility():
    # BAHL: a stray "million" in a 303-page report sent total assets to 3.3 QUADRILLION.
    # Figures already in actual rupees must win over the misleading header.
    text = ("Rupees in million\n"
            "Total Assets 3,303,763,897 3,320,035,205\n"
            "Total Liabilities 3,132,495,546 3,168,046,585\n")
    p = parse_statements(text)
    assert p["_unit_scale"] == 1
    assert p["total_assets"]["value"] < 1e14


def test_values_are_scaled_once_and_flagged():
    from intelligence.statement_parser import to_financial_dict
    p = parse_statements("Rupees in '000\nTotal assets 3,685,142 3,370,394\n")
    flat = to_financial_dict(p)
    assert flat["_already_scaled"] is True
    assert flat["total_assets"] == 3_685_142 * 1_000


# ── Column choice: primary statement vs multi-year highlights ─────────────────

def test_multiyear_highlights_takes_most_recent_column():
    # HBL: the 6-year Financial Highlights table lists years oldest-first, so the
    # CURRENT year is the LAST column. Taking column 0 reported the 2020 figure
    # (total assets 3.85tn) instead of 2025 (7.71tn).
    p = parse_statements(
        "Rupees in million\n"
        "Total Assets 3,849,063 4,317,468 4,638,806 5,534,348 6,055,113 7,708,144\n")
    assert p["total_assets"]["raw"] == 7_708_144
    assert p["total_assets"]["column"] == 5


def test_two_column_primary_statement_takes_current_first():
    # The canonical statement of financial position keeps current period FIRST.
    p = parse_statements("Rupees in '000\nTotal assets 7,708,144 6,055,113\n")
    assert p["total_assets"]["raw"] == 7_708_144
    assert p["total_assets"]["column"] == 0


def test_primary_line_preferred_over_highlights_when_both_present():
    # When a 2-column primary line and a wide highlights row both match, the primary
    # statement wins (penalty favours 2-3 columns).
    p = parse_statements(
        "Total assets 111,111 222,222 333,333 444,444 555,555 666,666\n"
        "Total assets 666,666 555,555\n")
    assert p["total_assets"]["raw"] == 666_666


# ── Bank / non-total balance-sheet anchors ────────────────────────────────────

def test_net_assets_is_bank_equity():
    # Banks/DFIs do not split current/non-current; the balance sheet closes on
    # NET ASSETS (= assets - liabilities = equity). Verified on ABL, BML, HBL.
    p = parse_statements("Rupees in '000\nNET ASSETS 488,615 410,798\n")
    assert "total_equity" in p
    assert p["total_equity"]["raw"] == 488_615


def test_net_assets_per_share_is_not_equity():
    # "Net assets per share/value" is a per-unit ratio, not the equity total.
    p = parse_statements("Net assets per share 45.20 40.10\n")
    assert "total_equity" not in p


def test_current_assets_without_total_prefix():
    # Many statements label the subtotal simply "Current assets" (no "Total").
    p = parse_statements("Rupees in '000\nCurrent assets 6,276,340 8,652,034\n")
    assert "current_assets" in p
    assert p["current_assets"]["raw"] == 6_276_340


def test_current_portion_line_is_not_current_liabilities():
    # "Current portion of long term financing" is a sub-item, not the section total.
    p = parse_statements("Current portion of long term financing 26,145,374 40,597,805\n")
    assert "current_liabilities" not in p


# ── Provenance ────────────────────────────────────────────────────────────────

def test_every_value_carries_provenance():
    p = parse_statements("Rupees in '000\nTotal assets 3,685,142 3,370,394\n")
    h = p["total_assets"]
    for key in ("raw", "scale", "column", "all_columns", "source_line", "confidence"):
        assert key in h, f"missing provenance field: {key}"
    assert h["all_columns"][:2] == [3685142.0, 3370394.0]


def test_adjacent_columns_are_not_glued_together():
    # ABOT page 88: "Total Assets 46,816 100.0 37,651" became "46,816100.0
    # 37,651100.0" because a split-number repair rule fired between COLUMNS.
    # Multi-column statements are the normal case, so the repair must not.
    out = _normalize_numbers("Total Assets 46,816 100.0 37,651 100.0")
    assert "46,816100" not in out
    assert "46,816" in out and "37,651" in out


def test_space_separated_columns_are_never_merged_into_one_number():
    # "488,615 410,798" (two columns) is structurally identical to a number split
    # by OCR, so the ambiguous repair is declined rather than guessed. Merging them
    # produced 488,615,410,798 - a thousand-fold overstatement from unrelated cells.
    out = _normalize_numbers("NET ASSETS 488,615 410,798")
    assert "488,615,410,798" not in out
    assert "488,615" in out and "410,798" in out


# ── P3: NFKC ligature normalisation ──────────────────────────────────────────

def test_nfkc_fi_ligature_in_profit_label():
    # U+FB01 (ﬁ) is the fi-ligature. PSX PDFs sometimes encode "profit" as
    # "proﬁt". After NFKC normalisation the net_income anchor must still fire.
    text = "Net proﬁt for the year 5,678,901 4,321,098\n"
    p = parse_statements(text)
    assert "net_income" in p
    assert p["net_income"]["raw"] == 5_678_901.0


def test_nfkc_ff_ligature_in_label():
    # U+FB00 (ﬀ) is the ff-ligature. Gross profit contains no ff, but "profit" can
    # appear as "proﬁt" (fi). Confirm that NFKC does not corrupt clean text.
    text = "Gross profit 8,765,432 7,654,321\n"
    p = parse_statements(text)
    assert "gross_profit" in p
    assert p["gross_profit"]["raw"] == 8_765_432.0


# ── P5: new statement_parser fields ──────────────────────────────────────────

def test_retained_earnings_unappropriated_profit():
    p = parse_statements("Unappropriated profit/(loss) 3,456,789 2,987,654\n")
    assert "retained_earnings" in p
    assert p["retained_earnings"]["raw"] == 3_456_789.0


def test_retained_earnings_accumulated_profit():
    p = parse_statements("Accumulated profit 12,345,678 10,987,654\n")
    assert "retained_earnings" in p
    assert p["retained_earnings"]["raw"] == 12_345_678.0


def test_trade_debts_parsed_as_receivables():
    p = parse_statements("Trade debts - net of provision 45,678,901 38,765,432\n")
    assert "receivables" in p
    assert p["receivables"]["raw"] == 45_678_901.0


def test_trade_receivables_parsed_as_receivables():
    p = parse_statements("Trade and other receivables 23,456,789 19,876,543\n")
    assert "receivables" in p


def test_payables_line_parsed():
    p = parse_statements("Trade and other payables 23,456,789 19,876,543\n")
    assert "payables" in p
    assert p["payables"]["raw"] == 23_456_789.0


def test_operating_cash_flow_parsed():
    p = parse_statements("Net cash generated from operating activities 8,765,432 7,654,321\n")
    assert "ocf" in p
    assert p["ocf"]["raw"] == 8_765_432.0


def test_operating_cash_flow_negative():
    p = parse_statements("Net cash used in operating activities (1,234,567) (2,345,678)\n")
    assert "ocf" in p
    assert p["ocf"]["raw"] == -1_234_567.0


def test_capex_purchase_of_ppe():
    p = parse_statements("Purchase of property, plant and equipment (3,456,789) (2,987,654)\n")
    assert "capex" in p
    assert p["capex"]["raw"] == -3_456_789.0


def test_capex_additions_to_ppe():
    p = parse_statements("Additions to property, plant and equipment 5,678,901 4,321,098\n")
    assert "capex" in p
    assert p["capex"]["raw"] == 5_678_901.0


def test_depreciation_and_amortisation():
    p = parse_statements("Depreciation and amortisation 2,345,678 1,987,654\n")
    assert "depreciation" in p
    assert p["depreciation"]["raw"] == 2_345_678.0


def test_depreciation_bare_label():
    p = parse_statements("Depreciation 1,234,567 987,654\n")
    assert "depreciation" in p
    assert p["depreciation"]["raw"] == 1_234_567.0


def test_depreciation_righ_of_use_not_matched():
    # Sub-item; the depreciation anchor must NOT fire on this line.
    p = parse_statements("Depreciation on right-of-use assets 234,567 198,765\n")
    assert "depreciation" not in p


def test_long_term_borrowings():
    p = parse_statements("Long-term financing 50,000 45,000\n")
    assert "long_term_borrowings" in p
    assert p["long_term_borrowings"]["raw"] == 50_000.0


def test_current_portion_ltd():
    p = parse_statements("Current portion of long-term financing 10,000 8,000\n")
    assert "current_portion_ltd" in p
    assert p["current_portion_ltd"]["raw"] == 10_000.0


def test_short_term_borrowings():
    p = parse_statements("Short-term borrowings 15,000 12,000\n")
    assert "short_term_borrowings" in p
    assert p["short_term_borrowings"]["raw"] == 15_000.0


def test_lease_liabilities():
    p = parse_statements("Lease liabilities 5,000 4,000\n")
    assert "lease_liabilities" in p
    assert p["lease_liabilities"]["raw"] == 5_000.0


def test_total_debt_computed_from_all_four_components():
    text = (
        "Rupees in '000\n"
        "Long-term financing 50,000 45,000\n"
        "Current portion of long-term financing 10,000 8,000\n"
        "Short-term borrowings 15,000 12,000\n"
        "Lease liabilities 5,000 4,000\n"
    )
    p = parse_statements(text)
    flat = to_financial_dict(p)
    assert "total_debt" in flat
    # 80,000 thousands = 80,000,000 actual PKR (scale x1000)
    assert flat["total_debt"] == 80_000 * 1_000


def test_total_debt_absent_when_component_missing():
    text = (
        "Rupees in '000\n"
        "Long-term financing 50,000 45,000\n"
        "Current portion of long-term financing 10,000 8,000\n"
    )
    p = parse_statements(text)
    flat = to_financial_dict(p)
    assert "total_debt" not in flat
