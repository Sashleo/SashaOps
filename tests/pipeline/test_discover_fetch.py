"""Tests for S1 Discover and S2 Fetch.

Acceptance for M2:
- Every PDF is stored once by hash.
- A second run fetches nothing (returns "duplicate").
- The fetch log is complete (filings row present for every stored PDF).
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from pipeline.discover import _ann_type, _date_key, discover_one
from pipeline.store import create_pipeline_tables


# ── _ann_type ─────────────────────────────────────────────────────────────────

def test_ann_type_transmission():
    assert _ann_type("Transmission of Annual Report for the Year Ended June 30, 2025") \
        == "annual_report"


def test_ann_type_annual_results():
    assert _ann_type("Announcement of Financial Results for the Year Ended June 30, 2025") \
        == "annual_results"


def test_ann_type_quarterly():
    assert _ann_type("Quarterly Report for the period ended September 30, 2025") \
        == "interim"


def test_ann_type_unrelated_returns_none():
    assert _ann_type("Board of Directors Meeting") is None
    assert _ann_type("") is None
    assert _ann_type(None) is None


# ── _date_key ─────────────────────────────────────────────────────────────────

def test_date_key_psx_format():
    from datetime import date
    assert _date_key("Apr 28, 2026") == date(2026, 4, 28)


def test_date_key_iso_format():
    from datetime import date
    assert _date_key("2026-04-28") == date(2026, 4, 28)


def test_date_key_unparseable_sorts_last():
    from datetime import date
    assert _date_key("garbage") == date.min


# ── discover_one ──────────────────────────────────────────────────────────────

def test_discover_one_found(monkeypatch):
    anns = [
        {"pdf_url": "https://psx.com.pk/doc/a.pdf",
         "title": "Transmission of Annual Report 2025", "date": "Jun 15, 2025"},
        {"pdf_url": "https://psx.com.pk/doc/b.pdf",
         "title": "Quarterly Report Q1 2025", "date": "Oct 10, 2024"},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **kw: {"financial_announcements": anns},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "found"
    assert len(result["candidates"]) == 2
    # Newest first: annual_report is Jun 2025, quarterly is Oct 2024.
    assert result["candidates"][0]["ann_type"] == "annual_report"
    assert result["candidates"][1]["ann_type"] == "interim"


def test_discover_one_no_report_announcements(monkeypatch):
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **kw: {"financial_announcements": [
            {"pdf_url": "https://psx.com.pk/doc/x.pdf",
             "title": "Board meeting notice", "date": "May 1, 2025"},
        ]},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "no_report_announcements"
    assert result["candidates"] == []


def test_discover_one_fetch_failed(monkeypatch):
    def _boom(ticker, **kw):
        raise ConnectionError("timeout")
    monkeypatch.setattr("pipeline.discover.fetch_psx_company", _boom)
    result = discover_one("LUCK")
    assert result["outcome"].startswith("fetch_failed:")
    assert result["candidates"] == []


def test_discover_one_skips_announcements_without_pdf_url(monkeypatch):
    anns = [
        {"pdf_url": None, "title": "Transmission of Annual Report 2025",
         "date": "Jun 15, 2025"},
        {"pdf_url": "https://psx.com.pk/doc/b.pdf",
         "title": "Annual Results 2025", "date": "Jun 1, 2025"},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **kw: {"financial_announcements": anns},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "found"
    assert len(result["candidates"]) == 1


# ── fetch_one ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_pipeline_tables(conn)
    return conn


@pytest.fixture
def candidate():
    return {
        "ticker":   "LUCK",
        "url":      "https://psx.com.pk/doc/luck_ar_2025.pdf",
        "title":    "Transmission of Annual Report 2025",
        "ann_date": "Jun 15, 2025",
        "ann_type": "annual_report",
    }


def _fake_pdf(content: bytes = b"") -> bytes:
    return b"%PDF-1.4 " + content


def test_fetch_one_stores_new_pdf(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: _fake_pdf(b"LUCK2025"))

    result = pf.fetch_one(db, candidate)

    assert result["outcome"] == "stored"
    sha256 = result["doc_sha256"]
    assert sha256 is not None
    assert Path(result["file_path"]).exists()
    # DB row written
    row = db.execute("SELECT * FROM filings WHERE doc_sha256=?", (sha256,)).fetchone()
    assert row is not None
    assert row["ticker"] == "LUCK"
    assert row["source_url"] == candidate["url"]


def test_fetch_one_duplicate_on_second_run(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: _fake_pdf(b"LUCK2025"))

    r1 = pf.fetch_one(db, candidate)
    assert r1["outcome"] == "stored"

    r2 = pf.fetch_one(db, candidate)
    assert r2["outcome"] == "duplicate"
    assert r2["doc_sha256"] == r1["doc_sha256"]

    # Exactly one filings row — no double-write.
    count = db.execute("SELECT count(*) FROM filings").fetchone()[0]
    assert count == 1


def test_fetch_one_not_pdf(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: b"<html>not a pdf</html>")

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "not_pdf"
    assert db.execute("SELECT count(*) FROM filings").fetchone()[0] == 0


def test_fetch_one_too_large(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    big = b"%PDF-1.4 " + b"x" * (pf.MAX_FILE_BYTES + 1)
    monkeypatch.setattr(pf, "_download", lambda url: big)

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "too_large"


def test_fetch_one_http_error(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: "http_404")

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "http_404"


def test_different_tickers_same_pdf_hash_is_duplicate(db, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    content = _fake_pdf(b"shared_content")
    monkeypatch.setattr(pf, "_download", lambda url: content)

    c1 = {"ticker": "LUCK", "url": "https://psx.com.pk/doc/a.pdf",
          "title": "AR 2025", "ann_date": "Jun 2025", "ann_type": "annual_report"}
    c2 = {"ticker": "LUCK", "url": "https://psx.com.pk/doc/b.pdf",   # different URL
          "title": "AR 2025", "ann_date": "Jun 2025", "ann_type": "annual_report"}

    r1 = pf.fetch_one(db, c1)
    r2 = pf.fetch_one(db, c2)

    assert r1["outcome"] == "stored"
    assert r2["outcome"] == "duplicate"
    # Still only one row.
    assert db.execute("SELECT count(*) FROM filings").fetchone()[0] == 1


def test_bronze_path_uses_sha256_prefix(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    content = _fake_pdf(b"predictable")
    monkeypatch.setattr(pf, "_download", lambda url: content)

    result = pf.fetch_one(db, candidate)
    sha256 = result["doc_sha256"]
    expected = tmp_path / "bronze" / sha256[:2] / f"{sha256}.pdf"
    assert Path(result["file_path"]) == expected
    assert expected.exists()
