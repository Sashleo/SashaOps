"""Tests for S5 Extract."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from pipeline.extract import EXTRACTOR_VERSION, extract_one
from pipeline.store import create_pipeline_tables, upsert_filing


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_pipeline_tables(conn)
    return conn


def _insert_filing(db, doc_sha256: str, file_path: str,
                   doc_class: str = "annual_report", ticker: str = "NCL"):
    upsert_filing(
        db,
        doc_sha256=doc_sha256,
        ticker=ticker,
        source_url="https://psx.com.pk/doc/a.pdf",
        ann_title="Annual Report 2026",
        ann_date="Sep 4, 2026",
        file_path=file_path,
        file_bytes=5000000,
        pages=300,
        fetched_at="2026-10-23T10:00:00",
        doc_class=doc_class,
        text_pages=250,
        classifier_version="1.1",
    )
    db.commit()


def _insert_doc_statement(db, doc_sha256: str, statement_type: str,
                           basis: str = "consolidated", page: int = 44,
                           period_end: str = "2026-06-30",
                           prior_end: str = "2025-06-30"):
    period_ends = [period_end, prior_end] if prior_end else [period_end]
    db.execute(
        "INSERT OR REPLACE INTO doc_statements "
        "(doc_sha256, statement_type, basis, pdf_page_start, pdf_page_end, "
        "located_by, period_ends_json, scale, locator_version) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (doc_sha256, statement_type, basis, page, page,
         "classifier", json.dumps(period_ends), None, "1.3"),
    )
    db.commit()


_FAKE_PARSE_RESULT = {
    "total_assets": {
        "raw": 78304481287.0, "value": 78304481287.0, "scale": 1,
        "all_columns": [78304481287.0, 70000000000.0],
        "source_line": "TOTAL ASSETS 78,304,481,287 70,000,000,000",
    },
    "total_equity": {
        "raw": 22874221754.0, "value": 22874221754.0, "scale": 1,
        "all_columns": [22874221754.0, 20000000000.0],
        "source_line": "Total equity 22,874,221,754 20,000,000,000",
    },
    "total_liabilities": {
        "raw": 55430259533.0, "value": 55430259533.0, "scale": 1,
        "all_columns": [55430259533.0, 50000000000.0],
        "source_line": "Total liabilities 55,430,259,533 50,000,000,000",
    },
    "_unit_scale": 1,
    "_balance_check": {"ok": True, "pct_diff": 0.0},
}


def test_extract_one_extracted(db, tmp_path):
    pdf = tmp_path / "ar.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "sha001", str(pdf))
    _insert_doc_statement(db, "sha001", "balance_sheet")

    with patch("pipeline.extract._read_pages", return_value="some text"), \
         patch("intelligence.statement_parser.parse_statements",
               return_value=_FAKE_PARSE_RESULT):
        result = extract_one(db, "sha001")

    assert result["outcome"] == "extracted"
    assert result["statements_written"] >= 1
    assert result["items_written"] >= 3

    items = db.execute(
        "SELECT field, value_rupees, column_no FROM line_items WHERE doc_sha256='sha001'"
    ).fetchall()
    fields_cur = {r["field"]: r["value_rupees"] for r in items if r["column_no"] == 0}
    assert fields_cur["total_assets"] == 78304481287
    assert fields_cur["total_equity"] == 22874221754


def test_extract_one_writes_prior_year(db, tmp_path):
    pdf = tmp_path / "ar.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "sha002", str(pdf))
    _insert_doc_statement(db, "sha002", "balance_sheet")

    with patch("pipeline.extract._read_pages", return_value="some text"), \
         patch("intelligence.statement_parser.parse_statements",
               return_value=_FAKE_PARSE_RESULT):
        extract_one(db, "sha002")

    prior = db.execute(
        "SELECT field, value_rupees FROM line_items "
        "WHERE doc_sha256='sha002' AND column_no=1"
    ).fetchall()
    fields = {r["field"]: r["value_rupees"] for r in prior}
    assert fields["total_assets"] == 70000000000


def test_extract_one_already_extracted(db, tmp_path):
    pdf = tmp_path / "ar.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "sha003", str(pdf))
    db.execute(
        "INSERT INTO statements "
        "(statement_id, doc_sha256, ticker, period_end, months, basis, "
        "statement_type, status, parser_version, run_id, created_at) "
        "VALUES ('sid','sha003','NCL','2026-06-30',12,'consolidated',"
        "'balance_sheet','extracted',?,?,?)",
        (EXTRACTOR_VERSION, "run1", "2026-10-23T00:00:00"),
    )
    db.commit()

    with patch("intelligence.statement_parser.parse_statements") as mock_p:
        result = extract_one(db, "sha003")

    assert result["outcome"] == "already_extracted"
    mock_p.assert_not_called()


def test_extract_one_skipped_for_scanned(db, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "sha004", str(pdf), doc_class="scanned")
    result = extract_one(db, "sha004")
    assert result["outcome"] == "skipped:scanned"


def test_extract_one_file_missing(db):
    _insert_filing(db, "sha005", "/nonexistent/ar.pdf")
    result = extract_one(db, "sha005")
    assert result["outcome"] == "failed:file_missing"


def test_extract_one_no_period_end_skips_statement(db, tmp_path):
    pdf = tmp_path / "ar.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "sha006", str(pdf))
    # Insert a doc_statement with empty period_ends_json
    db.execute(
        "INSERT INTO doc_statements "
        "(doc_sha256, statement_type, basis, pdf_page_start, pdf_page_end, "
        "located_by, period_ends_json, scale, locator_version) "
        "VALUES ('sha006','balance_sheet','consolidated',44,44,'classifier','[]',NULL,'1.3')",
    )
    db.commit()

    with patch("pipeline.extract._read_pages", return_value="some text"), \
         patch("intelligence.statement_parser.parse_statements",
               return_value=_FAKE_PARSE_RESULT):
        result = extract_one(db, "sha006")

    # Statement skipped (no period_end) → no data → failed:no_data
    assert result["outcome"] == "failed:no_data"
