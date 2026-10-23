"""Tests for S3 Classify.

M3 acceptance:
- Each of the 20 has a class.
- LUCK and HUBC are not scanned (they are text PDFs).
- Scanned PDFs are detected by the text layer only.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from pipeline.classify import _infer_class, classify_one, CLASSIFIER_VERSION
from pipeline.store import create_pipeline_tables, upsert_filing


# ── _infer_class (pure logic, no I/O) ────────────────────────────────────────

def test_infer_full_set_annual_title():
    assert _infer_class(
        total_pages=250, text_pages=200,
        found={"balance_sheet": True, "income": True, "cashflow": True},
        ann_title="Transmission of Annual Report for the year ended - June 30, 2026",
    ) == "annual_report"


def test_infer_full_set_interim_title():
    assert _infer_class(
        total_pages=60, text_pages=55,
        found={"balance_sheet": True, "income": True, "cashflow": True},
        ann_title="Transmission of Quarterly Report for the Period Ended March 31, 2026",
    ) == "interim_report"


def test_infer_scanned_when_no_text_pages():
    assert _infer_class(
        total_pages=200, text_pages=0, found={}, ann_title="Annual Report 2026"
    ) == "scanned"


def test_infer_results_sheet_short_income_only():
    assert _infer_class(
        total_pages=10, text_pages=10,
        found={"income": True},
        ann_title="Announcement of Financial Results for the Year Ended June 30, 2026",
    ) == "results_sheet"


def test_infer_cover_letter_very_short_no_headings():
    assert _infer_class(
        total_pages=3, text_pages=3,
        found={},
        ann_title="Transmission of Annual Report",
    ) == "cover_letter"


def test_infer_unknown_when_no_clues():
    assert _infer_class(
        total_pages=40, text_pages=15,
        found={},
        ann_title="",
    ) == "unknown"


def test_infer_annual_when_bs_only_but_annual_title():
    # Income heading beyond probe window but title is unambiguous
    assert _infer_class(
        total_pages=428, text_pages=69,
        found={"balance_sheet": True},
        ann_title="Transmission of Annual Report for the year ended - June 30, 2026",
    ) == "annual_report"


def test_infer_annual_when_bs_cf_but_annual_title():
    # Income heading not in probe window but BS + CF found
    assert _infer_class(
        total_pages=238, text_pages=78,
        found={"balance_sheet": True, "cashflow": True},
        ann_title="Annual Report 2026",
    ) == "annual_report"


def test_infer_annual_when_full_set_no_title_hint():
    # No interim signals in title → defaults to annual_report
    assert _infer_class(
        total_pages=180, text_pages=160,
        found={"balance_sheet": True, "income": True, "cashflow": True},
        ann_title="Annual Report 2026",
    ) == "annual_report"


# ── classify_one (integration, in-memory DB) ─────────────────────────────────

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_pipeline_tables(conn)
    return conn


def _insert_filing(db, doc_sha256: str, file_path: str, ann_title: str = "",
                   pages: int = 100):
    upsert_filing(
        db,
        doc_sha256=doc_sha256,
        ticker="LUCK",
        source_url="https://psx.com.pk/doc/a.pdf",
        ann_title=ann_title,
        ann_date="Sep 4, 2026",
        file_path=file_path,
        file_bytes=1000000,
        pages=pages,
        fetched_at="2026-10-23T10:00:00",
    )
    db.commit()


def _fake_scan(file_path: str):
    """Returns (total_pages, text_pages, found) for a typical annual report."""
    return (250, 220, {"balance_sheet": True, "income": True, "cashflow": True})


def test_classify_one_classified(db, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "abc123", str(pdf),
                   ann_title="Transmission of Annual Report for the year ended - June 30, 2026")

    with patch("pipeline.classify._scan_pdf", side_effect=_fake_scan):
        result = classify_one(db, "abc123")

    assert result["outcome"] == "classified"
    assert result["doc_class"] == "annual_report"
    assert result["text_pages"] == 220

    row = db.execute("SELECT doc_class, text_pages, classifier_version FROM filings "
                     "WHERE doc_sha256='abc123'").fetchone()
    assert row["doc_class"] == "annual_report"
    assert row["text_pages"] == 220
    assert row["classifier_version"] == CLASSIFIER_VERSION


def test_classify_one_already_classified(db, tmp_path):
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "def456", str(pdf))
    db.execute(
        "UPDATE filings SET doc_class='annual_report', text_pages=180, "
        "classifier_version=? WHERE doc_sha256='def456'",
        (CLASSIFIER_VERSION,),
    )
    db.commit()

    with patch("pipeline.classify._scan_pdf") as mock_scan:
        result = classify_one(db, "def456")

    assert result["outcome"] == "already_classified"
    assert result["doc_class"] == "annual_report"
    mock_scan.assert_not_called()


def test_classify_one_not_found(db):
    result = classify_one(db, "nonexistent")
    assert result["outcome"] == "not_found"


def test_classify_one_file_missing(db):
    _insert_filing(db, "ghi789", "/nonexistent/path/doc.pdf")
    result = classify_one(db, "ghi789")
    assert result["outcome"] == "failed:file_missing"


def test_classify_one_scanned_pdf(db, tmp_path):
    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "scan001", str(pdf),
                   ann_title="Annual Report 2026", pages=200)

    with patch("pipeline.classify._scan_pdf", return_value=(200, 0, {})):
        result = classify_one(db, "scan001")

    assert result["outcome"] == "classified"
    assert result["doc_class"] == "scanned"


def test_classify_one_results_sheet(db, tmp_path):
    pdf = tmp_path / "results.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    _insert_filing(db, "res001", str(pdf),
                   ann_title="Announcement of Financial Results for the Year Ended June 30, 2026",
                   pages=10)

    with patch("pipeline.classify._scan_pdf", return_value=(10, 10, {"income": True})):
        result = classify_one(db, "res001")

    assert result["outcome"] == "classified"
    assert result["doc_class"] == "results_sheet"
