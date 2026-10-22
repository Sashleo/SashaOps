"""
Regression tests for reporting-basis and balance-sheet plausibility guards.

Both cases are real failures found while building Phase 3's multi-period history
against live data, and both would have corrupted every downstream ratio, trend and
chart while looking entirely plausible on screen.
"""

from bs4 import BeautifulSoup

from intelligence.exchange_crawlers.psx import _parse_financial_table
from intelligence.psx_financials import (
    _same_reporting_basis, _plausible_balance_sheet_value, build_period_rows,
    _build_doc_financials,
)


# ── Consolidated vs standalone (LUCK, verified 2026-07-29) ────────────────────

def test_consolidated_report_is_not_same_basis_as_standalone_summary():
    # PSX publishes STANDALONE Sales 124,511,744 for LUCK FY2025; the annual
    # report's statements parse to the CONSOLIDATED 310,459,264. Adopting the
    # report wholesale swapped the income statement onto a different entity from
    # every earlier period, manufacturing a fake +169% revenue jump.
    assert _same_reporting_basis(
        {"revenue": 124_511_744.0}, {"revenue": 310_459_264.0}) is False


def test_same_basis_same_units_is_accepted():
    assert _same_reporting_basis(
        {"revenue": 124_511_744.0}, {"revenue": 124_511_744.0}) is True


def test_same_basis_with_thousands_unit_difference_is_accepted():
    # The report may print actual rupees where the PSX summary prints thousands.
    # That is a unit difference, not a different reporting entity.
    assert _same_reporting_basis(
        {"revenue": 124_511_744.0}, {"revenue": 124_511_744_000.0}) is True


def test_missing_revenue_blocks_wholesale_adoption():
    assert _same_reporting_basis({"revenue": None}, {"revenue": 1.0}) is False
    assert _same_reporting_basis({"revenue": 1.0}, {}) is False


# ── Absurd balance-sheet values (HBL, verified 2026-07-29) ───────────────────

def test_year_misparsed_as_equity_is_rejected():
    # HBL stored total_equity = 2024.0 against total assets of 3,849,063: a fiscal
    # YEAR read off the statement header. Equity at 0.05% of assets is impossible
    # for a listed bank and would wreck ROE, equity multiplier, DuPont and Altman X4.
    assert _plausible_balance_sheet_value(
        "shareholders_equity", 2024.0, 3_849_063.0) is False


def test_real_bank_equity_is_accepted():
    assert _plausible_balance_sheet_value(
        "shareholders_equity", 300_000.0, 3_849_063.0) is True


def test_component_larger_than_total_assets_is_rejected():
    assert _plausible_balance_sheet_value(
        "current_assets", 38_490_630.0, 3_849_063.0) is False


def test_guard_is_inert_without_total_assets():
    # No basis for comparison means no rejection - missing data must not be
    # converted into a false positive.
    assert _plausible_balance_sheet_value("shareholders_equity", 2024.0, None) is True


# ── Multi-period history extraction ──────────────────────────────────────────

def test_build_period_rows_splits_every_period():
    table = {
        "Sales": {"2025": 124_511_744.0, "2024": 115_324_942.0, "2023": 95_832_147.0},
        "Profit after Taxation": {"2025": 33_092_162.0, "2024": 28_106_539.0},
        "EPS": {"2025": 22.59},
    }
    rows = build_period_rows(table)
    assert set(rows) == {"2025", "2024", "2023"}
    assert rows["2025"]["revenue"] == 124_511_744.0
    assert rows["2025"]["net_income"] == 33_092_162.0
    assert rows["2024"]["revenue"] == 115_324_942.0
    # 2023 has revenue only - it must still be kept, not dropped for being partial.
    assert rows["2023"]["revenue"] == 95_832_147.0
    assert "net_income" not in rows["2023"]


def test_build_period_rows_ignores_unmapped_metrics():
    rows = build_period_rows({"Some Unmapped Ratio (%)": {"2025": 12.3}})
    assert rows == {}


# ── Single-period table guard (crash regression 2026-10-12) ──────────────────
# PSX occasionally returns a financials table with only ONE period column (newly
# listed companies, companies in suspension). The parser must never crash on this;
# it must return usable data for that single year.

def _table_from_html(html: str):
    return BeautifulSoup(html, "lxml").find("table")


def test_single_period_table_parses_without_crash():
    # Minimal single-period HTML financial table (newly listed company).
    html = """<table>
      <tr><th>Metric</th><th>2026</th></tr>
      <tr><td>Net Sales</td><td>5,000,000</td></tr>
      <tr><td>Profit after taxation</td><td>500,000</td></tr>
      <tr><td>EPS</td><td>2.50</td></tr>
    </table>"""
    result = _parse_financial_table(_table_from_html(html))
    assert result["Net Sales"] == {"2026": 5_000_000.0}
    assert result["Profit after taxation"] == {"2026": 500_000.0}


def test_single_period_table_flows_through_doc_builder():
    # Confirm the single-period table produces a usable doc with revenue + fiscal year.
    table = {
        "Net Sales": {"2026": 5_000_000.0},
        "Profit after taxation": {"2026": 500_000.0},
        "EPS": {"2026": 2.5},
    }
    doc = _build_doc_financials(table)
    assert doc["revenue"] == 5_000_000.0
    assert doc["net_income"] == 500_000.0
    assert doc["eps"] == 2.5
    assert doc["_fiscal_year"] == "2026"
    # No revenue_prior_year / revenue_2y_ago when there is only one period.
    assert "revenue_prior_year" not in doc
    assert "revenue_2y_ago" not in doc


def test_zero_period_table_returns_empty():
    # A company in suspension may return a table with NO period columns at all.
    html = """<table>
      <tr><th>Metric</th></tr>
      <tr><td>Net Sales</td></tr>
    </table>"""
    result = _parse_financial_table(_table_from_html(html))
    # Parser must not crash; metrics with no period values come back as {}.
    assert result == {"Net Sales": {}}


def test_header_only_table_returns_empty():
    # Edge case: table with period headers but no data rows.
    html = """<table>
      <tr><th>Metric</th><th>2026</th><th>2025</th></tr>
    </table>"""
    result = _parse_financial_table(_table_from_html(html))
    assert result == {}
