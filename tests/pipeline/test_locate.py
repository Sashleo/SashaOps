"""Tests for S4 Locate.

M4 acceptance: Statement pages, basis, period ends and scale found for every
text-layer annual report, or a stated reason.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from pipeline.locate import (
    _detect_basis, _detect_months, _extract_period_ends_from_text,
    locate_one, LOCATOR_VERSION,
)
from pipeline.store import create_pipeline_tables, upsert_filing


# ── Pure unit tests ───────────────────────────────────────────────────────────

def test_extract_period_ends_as_at():
    dates = _extract_period_ends_from_text(
        "Unconsolidated Statement of Financial Position\n"
        "as at June 30, 2026\n"
        "Note 2026 2025\n"
    )
    assert "2026-06-30" in dates


def test_extract_period_ends_for_the_year_ended():
    dates = _extract_period_ends_from_text(
        "Statement of Profit or Loss\n"
        "For the year ended June 30, 2026\n"
    )
    assert "2026-06-30" in dates


def test_extract_period_ends_with_dash():
    dates = _extract_period_ends_from_text(
        "For the year ended - June 30, 2026\n"
    )
    assert "2026-06-30" in dates


def test_extract_period_ends_returns_newest_first():
    dates = _extract_period_ends_from_text(
        "as at June 30, 2026\n"
        "Note 2026 2025\n"
        "as at June 30, 2025\n"
    )
    assert dates[0] == "2026-06-30"
    assert "2025-06-30" in dates


def test_extract_period_ends_no_date():
    assert _extract_period_ends_from_text("Balance Sheet\nAssets\nLiabilities\n") == []


def test_detect_basis_unconsolidated():
    assert _detect_basis("Unconsolidated Statement of Financial Position") == "unconsolidated"


def test_detect_basis_consolidated():
    assert _detect_basis("Consolidated Statement of Financial Position") == "consolidated"


def test_detect_basis_defaults_unconsolidated():
    assert _detect_basis("Statement of Financial Position") == "unconsolidated"


def test_detect_months_annual():
    assert _detect_months("For the year ended June 30, 2026", "annual_report") == 12


def test_detect_months_six_months():
    assert _detect_months("For the six months ended March 31, 2026", "interim_report") == 6


def test_detect_months_nine_months():
    assert _detect_months("For the nine months ended March 31, 2026", "interim_report") == 9


# ── locate_one integration tests (in-memory DB) ───────────────────────────────

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_pipeline_tables(conn)
    return conn


def _insert_filing(db, doc_sha256: str, file_path: str,
                   doc_class: str = "annual_report", text_pages: int = 100,
                   ann_title: str = "Annual Report"):
    upsert_filing(
        db,
        doc_sha256=doc_sha256,
        ticker="LUCK",
        source_url="https://psx.com.pk/doc/a.pdf",
        ann_title=ann_title,
        ann_date="Sep 4, 2026",
        file_path=file_path,
        file_bytes=5000000,
        pages=200,
        fetched_at="2026-10-23T10:00:00",
        doc_class=doc_class,
        text_pages=text_pages,
        classifier_version="1.1",
    )
    db.commit()


def _make_fake_locator_result():
    return {
        "balance_sheet": [44],
        "income": [45],
        "cashflow": [46],
        "_page_types": {44: "statement", 45: "statement", 46: "statement"},
    }


def _fake_get_page_text(path, idx):
    pages = {
        44: "Unconsolidated Statement of Financial Position\nas at June 30, 2026\n"
            "Note 2026 2025\nAssets 1,000,000 900,000\nLiabilities 500,000 450,000\n"
            "(Rupees in '000')\n",
        45: "Unconsolidated Statement of Profit or Loss\nFor the year ended June 30, 2026\n"
            "Revenue 5,000,000 4,500,000\n(Rupees in '000')\n",
        46: "Unconsolidated Statement of Cash Flows\nFor the year ended June 30, 2026\n"
            "Operating activities 1,200,000 1,100,000\n(Rupees in '000')\n",
    }
    return pages.get(idx, "")


def test_locate_one_located(db, tmp_path):
    pdf = tmp_path / "ar.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "abc123", str(pdf))

    # locate_statement_pages is a local import inside locate_one; patch at source.
    # detect_scale returns (factor, evidence); upsert_doc_statement is also local.
    with patch("intelligence.statement_locator.locate_statement_pages",
               return_value=_make_fake_locator_result()), \
         patch("pipeline.locate._get_page_text", side_effect=_fake_get_page_text), \
         patch("intelligence.statement_parser.detect_scale", return_value=(1000, "'000'")):
        result = locate_one(db, "abc123")

    assert result["outcome"] == "located"
    assert len(result["statements"]) == 3
    types = {s["statement_type"] for s in result["statements"]}
    assert types == {"balance_sheet", "income", "cashflow"}

    rows = db.execute(
        "SELECT statement_type, basis, period_ends_json FROM doc_statements "
        "WHERE doc_sha256='abc123'"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["basis"] == "unconsolidated"
        periods = json.loads(row["period_ends_json"])
        assert "2026-06-30" in periods


def test_locate_one_already_located(db, tmp_path):
    pdf = tmp_path / "ar.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "def456", str(pdf))
    db.execute(
        "INSERT INTO doc_statements (doc_sha256, statement_type, basis, "
        "pdf_page_start, pdf_page_end, located_by, period_ends_json, "
        "scale, locator_version) "
        "VALUES ('def456', 'balance_sheet', 'unconsolidated', 10, 10, "
        "'classifier', '[]', NULL, ?)",
        (LOCATOR_VERSION,),
    )
    db.commit()

    # already_located check happens before any locator call — no import to patch
    result = locate_one(db, "def456")

    assert result["outcome"] == "already_located"


def test_locate_one_skipped_for_scanned(db, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "scan001", str(pdf), doc_class="scanned", text_pages=0)

    result = locate_one(db, "scan001")
    assert result["outcome"] == "skipped:scanned"


def test_locate_one_skipped_for_results_sheet(db, tmp_path):
    pdf = tmp_path / "rs.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "rs001", str(pdf), doc_class="results_sheet")

    result = locate_one(db, "rs001")
    assert result["outcome"].startswith("skipped:")


def test_locate_one_file_missing(db):
    _insert_filing(db, "mis001", "/nonexistent/path/doc.pdf")
    result = locate_one(db, "mis001")
    assert result["outcome"] == "failed:file_missing"
