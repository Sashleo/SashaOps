"""Tests for S7 Checks — quarantine / verify / fact promotion."""
from __future__ import annotations

import sqlite3

import pytest

from pipeline.checks import (
    _check_bs_balances, _check_cf_sums, _check_pl_chain, check_one,
)
from pipeline.store import create_pipeline_tables


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_pipeline_tables(conn)
    return conn


# ── Pure check functions ───────────────────────────────────────────────────────

def test_bs_balances_passed():
    r = _check_bs_balances({"total_assets": 100, "total_equity": 40, "total_liabilities": 60})
    assert r["result"] == "passed"
    assert r["pct_diff"] == pytest.approx(0.0, abs=0.001)


def test_bs_balances_failed():
    r = _check_bs_balances({"total_assets": 100, "total_equity": 40, "total_liabilities": 50})
    assert r["result"] == "failed"
    assert r["pct_diff"] > 1.0


def test_bs_balances_not_run_missing_equity():
    r = _check_bs_balances({"total_assets": 100, "total_liabilities": 60})
    assert r["result"] == "not_run"


def test_bs_balances_not_run_no_assets():
    r = _check_bs_balances({})
    assert r["result"] == "not_run"


def test_pl_chain_passed():
    r = _check_pl_chain({"revenue": 100, "cost_of_sales": 60, "gross_profit": 40})
    assert r["result"] == "passed"


def test_pl_chain_failed():
    r = _check_pl_chain({"revenue": 100, "cost_of_sales": 60, "gross_profit": 45})
    assert r["result"] == "failed"


def test_pl_chain_not_run_missing_cos():
    r = _check_pl_chain({"revenue": 100, "gross_profit": 40})
    assert r["result"] == "not_run"
    assert "cost_of_sales" in r["reason"]


def test_cf_sums_passed():
    r = _check_cf_sums({
        "operating_cashflow": 50, "investing_cashflow": -30,
        "financing_cashflow": -10, "net_change_in_cash": 10,
    })
    assert r["result"] == "passed"


def test_cf_sums_not_run_missing():
    r = _check_cf_sums({"operating_cashflow": 50})
    assert r["result"] == "not_run"


# ── check_one integration ──────────────────────────────────────────────────────

def _seed_statement(db, sid, ticker="NCL", stype="balance_sheet",
                    basis="consolidated", period_end="2026-06-30"):
    db.execute(
        "INSERT OR REPLACE INTO statements "
        "(statement_id, doc_sha256, ticker, period_end, months, basis, "
        "statement_type, status, parser_version, run_id, created_at) "
        "VALUES (?,?,?,?,12,?,?,?,?,?,?)",
        (sid, "aabbcc", ticker, period_end, basis, stype,
         "extracted", "1.0", "r1", "2026-10-23T00:00:00"),
    )
    db.commit()


def _seed_items(db, doc_sha256, stype, basis, fields):
    for i, (field, value) in enumerate(fields.items()):
        db.execute(
            "INSERT OR REPLACE INTO line_items "
            "(doc_sha256, statement_type, basis, pdf_page, line_no, column_no, "
            "label_raw, label_norm, field, value_rupees, printed_text, "
            "is_dash, is_bracketed, scale, period_end, months, "
            "parser_version, run_id) "
            "VALUES (?,?,?,10,?,0,?,?,?,?,?,0,0,'1000','2026-06-30',12,'1.0','r1')",
            (doc_sha256, stype, basis, i, field, field, field, value, field),
        )
    db.commit()


def test_check_one_verified_promotes_facts(db):
    _seed_statement(db, "sid_ok")
    _seed_items(db, "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 100_000,
        "total_equity": 40_000,
        "total_liabilities": 60_000,
    })
    r = check_one(db, "sid_ok")
    assert r["outcome"] == "verified"
    facts = db.execute("SELECT count(*) FROM facts WHERE statement_id='sid_ok'").fetchone()[0]
    assert facts == 3


def test_check_one_quarantined_not_run_publishes_nothing(db):
    """A statement with a not_run check is quarantined and promotes no facts."""
    _seed_statement(db, "sid_quar", stype="balance_sheet")
    # Only total_assets — equity and liabilities missing → bs_balances = not_run
    _seed_items(db, "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 100_000,
        "cash": 5_000,
    })
    r = check_one(db, "sid_quar")
    assert r["outcome"] == "quarantined"
    assert r["checks"]["bs_balances"]["result"] == "not_run"
    facts = db.execute("SELECT count(*) FROM facts WHERE statement_id='sid_quar'").fetchone()[0]
    assert facts == 0


def test_check_one_quarantined_failed_publishes_nothing(db):
    """A statement with a failed check is quarantined and promotes no facts."""
    _seed_statement(db, "sid_fail")
    _seed_items(db, "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 100_000,
        "total_equity": 40_000,
        "total_liabilities": 50_000,  # 10 % off → failed
    })
    r = check_one(db, "sid_fail")
    assert r["outcome"] == "quarantined"
    assert r["checks"]["bs_balances"]["result"] == "failed"
    facts = db.execute("SELECT count(*) FROM facts WHERE statement_id='sid_fail'").fetchone()[0]
    assert facts == 0


def test_check_one_not_applicable_does_not_quarantine(db):
    """pl_chain and cf_sums are not_applicable for balance_sheet → not quarantined."""
    _seed_statement(db, "sid_na")
    _seed_items(db, "aabbcc", "balance_sheet", "consolidated", {
        "total_assets": 100_000,
        "total_equity": 40_000,
        "total_liabilities": 60_000,
    })
    r = check_one(db, "sid_na")
    assert r["outcome"] == "verified"
    assert r["checks"]["pl_chain"]["result"] == "not_applicable"
    assert r["checks"]["cf_sums"]["result"] == "not_applicable"


def test_check_one_not_found(db):
    assert check_one(db, "no_such_id")["outcome"] == "not_found"
