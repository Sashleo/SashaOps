"""
Regression tests for PSX financials data guards.

The reporting-basis and balance-sheet plausibility guards (_same_reporting_basis,
_plausible_balance_sheet_value) were helpers for the old PDF-report enrichment step,
which was deleted as part of the V2 pipeline plan (M1–M7). Their tests are removed
here; the functions are gone and must not be re-added until M7 publishes verified
figures via the Gold table.

The guard that remains: psx_financials must never carry a PDF-derived _source.
"""

import pathlib

from bs4 import BeautifulSoup

from intelligence.exchange_crawlers.psx import _parse_financial_table
from intelligence.psx_financials import (
    build_period_rows,
    _build_doc_financials,
)


# ── V2 pipeline guard: no PDF-derived source must reach psx_financials ────────

def test_psx_annual_report_source_strings_deleted_from_psx_financials():
    """
    Until M7 publishes verified figures, psx_financials must not carry any
    PDF-derived _source tag. Assert the forbidden strings are absent from the
    module source — a static proof that the report step is gone.
    """
    src = (pathlib.Path(__file__).parent.parent
           / "intelligence" / "psx_financials.py").read_text(encoding="utf-8")
    forbidden = [
        "psx_annual_report",          # wholesale-adoption source tags
        "psx_summary+report_bs",      # partial-merge source tag
        "fetch_psx_report_financials", # import of the PDF step
        "_SkipReport",                # internal skip-signal class
        "_same_reporting_basis",      # reporting-basis guard
        "_plausible_balance_sheet_value",  # value plausibility guard
        "_report_is_sane",            # report sanity guard
        "_report_is_reliable",        # report reliability guard
    ]
    for s in forbidden:
        assert s not in src, (
            f"Forbidden string '{s}' found in psx_financials.py — "
            f"the report step must stay deleted until M7 publishes Gold figures"
        )


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
