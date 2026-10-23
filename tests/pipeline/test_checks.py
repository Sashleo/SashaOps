"""Tests for S6 Checks."""
from __future__ import annotations

import sqlite3

import pytest

from pipeline.checks import _check_bs, _check_cf, _check_pl, check_one
from pipeline.store import create_pipeline_tables


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_pipeline_tables(conn)
    return conn


# ── Pure check function tests ──────────────────────────────────────────────────

def test_check_bs_passes_when_balanced():
    result = _check_bs({
        "total_assets": 100_000,
        "total_equity": 40_000,
        "total_liabilities": 60_000,
    })
    assert result["ok"] is True
    assert result["pct_diff"] == pytest.approx(0.0, abs=0.001)


def test_check_bs_fails_when_imbalanced():
    result = _check_bs({
        "total_assets": 100_000,
        "total_equity": 40_000,
        "total_liabilities": 50_000,  # 10k short
    })
    assert result["ok"] is False
    assert result["pct_diff"] > 1.0


def test_check_bs_none_when_missing_equity():
    result = _check_bs({"total_assets": 100_000, "total_liabilities": 60_000})
    assert result["ok"] is None


def test_check_bs_none_when_no_assets():
    result = _check_bs({})
    assert result["ok"] is None


def test_check_pl_passes_when_consistent():
    result = _check_pl({
        "revenue": 100_000,
        "cost_of_sales": 60_000,
        "gross_profit": 40_000,
    })
    assert result["ok"] is True


def test_check_pl_fails_when_inconsistent():
    result = _check_pl({
        "revenue": 100_000,
        "cost_of_sales": 60_000,
        "gross_profit": 45_000,  # wrong
    })
    assert result["ok"] is False


def test_check_pl_none_when_missing():
    result = _check_pl({"revenue": 100_000, "gross_profit": 40_000})
    assert result["ok"] is None


def test_check_cf_passes_when_balanced():
    result = _check_cf({
        "operating_cashflow": 50_000,
        "investing_cashflow": -30_000,
        "financing_cashflow": -10_000,
        "net_change_in_cash": 10_000,
    })
    assert result["ok"] is True


def test_check_cf_none_when_missing():
    result = _check_cf({"operating_cashflow": 50_000})
    assert result["ok"] is None


# ── check_one integration tests ───────────────────────────────────────────────

def _seed_statement(db, statement_id: str, ticker: str = "NCL",
                     statement_type: str = "balance_sheet",
                     basis: str = "consolidated",
                     period_end: str = "2026-06-30"):
    db.execute(
        "INSERT OR REPLACE INTO statements "
        "(statement_id, doc_sha256, ticker, period_end, months, basis, "
        "statement_type, status, parser_version, run_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (statement_id, "aabbcc", ticker, period_end, 12,
         basis, statement_type, "extracted", "1.0", "r1", "2026-10-23T00:00:00"),
    )
    db.commit()


def _seed_items(db, statement_id: str, doc_sha256: str,
                statement_type: str, basis: str, fields: dict):
    for i, (field, value) in enumerate(fields.items()):
        db.execute(
            "INSERT OR REPLACE INTO line_items "
            "(doc_sha256, statement_type, basis, pdf_page, line_no, column_no, "
            "label_raw, label_norm, field, value_rupees, printed_text, "
            "is_dash, is_bracketed, scale, period_end, months, "
            "parser_version, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)",
            (doc_sha256, statement_type, basis, 10, i, 0,
             field, field, field, value, field,
             "1", "2026-06-30", 12, "1.0", "r1"),
        )
    db.commit()


def test_check_one_bs_passes(db):
    _seed_statement(db, "sid_ncl_bs", "NCL", "balance_sheet", "consolidated")
    _seed_items(db, "sid_ncl_bs", "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 78_304_481_287,
        "total_equity": 22_874_221_754,
        "total_liabilities": 55_430_259_533,
    })
    result = check_one(db, "sid_ncl_bs")
    assert result["outcome"] == "checked"
    assert result["checks"]["bs_balances"]["ok"] is True


def test_check_one_bs_fails(db):
    _seed_statement(db, "sid_pso_bs", "PSO", "balance_sheet", "consolidated")
    _seed_items(db, "sid_pso_bs", "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 1_090_408_977,
        "total_equity": 278_090_730,
        "total_liabilities": 800_011_608,  # sum = 1,078,102,338 != 1,090,408,977
    })
    result = check_one(db, "sid_pso_bs")
    assert result["outcome"] == "checked"
    assert result["checks"]["bs_balances"]["ok"] is False


def test_check_one_promotes_facts(db):
    _seed_statement(db, "sid_facts", "NCL", "balance_sheet", "consolidated")
    _seed_items(db, "sid_facts", "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 78_304_481_287,
        "total_equity": 22_874_221_754,
    })
    check_one(db, "sid_facts")
    facts = db.execute(
        "SELECT field, value_rupees FROM facts WHERE statement_id='sid_facts'"
    ).fetchall()
    fact_map = {r["field"]: r["value_rupees"] for r in facts}
    assert fact_map["total_assets"] == 78_304_481_287
    assert fact_map["total_equity"] == 22_874_221_754


def test_check_one_not_found(db):
    result = check_one(db, "nonexistent_id")
    assert result["outcome"] == "not_found"


def test_check_one_cashflow_missing_fields(db):
    _seed_statement(db, "sid_cf", "NCL", "cashflow", "consolidated")
    _seed_items(db, "sid_cf", "aabbcc", "cashflow", "consolidated", {
        "ocf": 5_000_000,
        "capex": -2_000_000,
        # missing investing_cashflow, financing_cashflow, net_change_in_cash
    })
    result = check_one(db, "sid_cf")
    assert result["outcome"] == "checked"
    assert result["checks"]["cf_sums"]["ok"] is None
