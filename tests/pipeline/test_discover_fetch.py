"""Tests for S1 Discover and S2 Fetch.

Acceptance for M2:
- Every PDF is stored once by hash.
- A second run makes 0 HTTP requests (already_fetched for every URL).
- The fetch log is complete (fetch_log row for every attempted URL).
- Failed outcomes are retried on the next run.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pipeline.discover import (
    _ann_type, _date_key, _period_end_from_title, _validate_ann_structure,
    discover_one,
)
from pipeline.store import create_pipeline_tables, upsert_fetch_log


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
    assert _date_key("Apr 28, 2026") == date(2026, 4, 28)


def test_date_key_iso_format():
    assert _date_key("2026-04-28") == date(2026, 4, 28)


def test_date_key_unparseable_sorts_last():
    assert _date_key("garbage") == date.min


# ── _period_end_from_title ────────────────────────────────────────────────────

def test_period_end_annual_june():
    assert _period_end_from_title(
        "Transmission of Annual Report for the Year Ended June 30, 2025"
    ) == "2025-06-30"


def test_period_end_annual_december():
    assert _period_end_from_title(
        "Announcement of Financial Results for the Year Ended December 31, 2024"
    ) == "2024-12-31"


def test_period_end_quarterly():
    assert _period_end_from_title(
        "Transmission of Quarterly Report for the Period Ended March 31, 2026"
    ) == "2026-03-31"


def test_period_end_none_for_board_meeting():
    assert _period_end_from_title("Board of Directors Meeting") is None
    assert _period_end_from_title("") is None
    assert _period_end_from_title(None) is None


# ── _validate_ann_structure ───────────────────────────────────────────────────

def test_validate_empty_list_is_ok():
    assert _validate_ann_structure([]) is None


def test_validate_normal_announcements_ok():
    anns = [
        {"date": "Jun 15, 2025", "title": "Annual Report",
         "pdf_url": "https://psx.com.pk/doc/a.pdf"},
        {"date": "Oct 10, 2024", "title": "Q1 Results", "pdf_url": None},
    ]
    assert _validate_ann_structure(anns) is None


def test_validate_missing_keys_triggers_source_changed():
    anns = [{"col_a": "2025", "col_b": "Some text"}]  # wrong table structure
    result = _validate_ann_structure(anns)
    assert result is not None
    assert result.startswith("source_changed:")
    assert "missing keys" in result


def test_validate_unparseable_dates_triggers_source_changed():
    # All dates are garbage — wrong table parsed
    anns = [
        {"date": "N/A", "title": "Row 1", "pdf_url": None},
        {"date": "N/A", "title": "Row 2", "pdf_url": None},
        {"date": "N/A", "title": "Row 3", "pdf_url": None},
    ]
    result = _validate_ann_structure(anns)
    assert result is not None
    assert result.startswith("source_changed:")


def test_validate_extra_table_with_bad_dates():
    # Simulates a page where an extra table is inserted before the announcements
    # table, so the scraper picks up the wrong one (all dates unparseable).
    wrong_table_rows = [
        {"date": "Director Name", "title": "Role", "pdf_url": None},
        {"date": "Ms. Xyz", "title": "CEO", "pdf_url": None},
        {"date": "Mr. Abc", "title": "CFO", "pdf_url": None},
    ]
    result = _validate_ann_structure(wrong_table_rows)
    assert result is not None
    assert result.startswith("source_changed:")


# ── discover_one ──────────────────────────────────────────────────────────────

def test_discover_one_found(monkeypatch):
    anns = [
        {"pdf_url": "https://psx.com.pk/doc/a.pdf",
         "title": "Transmission of Annual Report for the Year Ended Jun 30, 2025",
         "date": "Jun 15, 2025"},
        {"pdf_url": "https://psx.com.pk/doc/b.pdf",
         "title": "Quarterly Report for the period ended Sep 30, 2024",
         "date": "Oct 10, 2024"},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **_kw: {"financial_announcements": anns},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "found"
    # Only annual_report selected; interim not included when annual_report exists.
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["ann_type"] == "annual_report"
    assert result["candidates"][0]["period_end"] == "2025-06-30"


def test_discover_one_picks_two_annual_reports_when_visible(monkeypatch):
    anns = [
        {"pdf_url": "https://psx.com.pk/doc/a.pdf",
         "title": "Transmission of Annual Report for the Year Ended Jun 30, 2025",
         "date": "Jul 1, 2025"},
        {"pdf_url": "https://psx.com.pk/doc/b.pdf",
         "title": "Transmission of Annual Report for the Year Ended Jun 30, 2024",
         "date": "Jul 1, 2024"},
        {"pdf_url": "https://psx.com.pk/doc/c.pdf",
         "title": "Quarterly Report for the period ended Sep 30, 2024",
         "date": "Oct 10, 2024"},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **_kw: {"financial_announcements": anns},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "found"
    assert len(result["candidates"]) == 2
    assert all(c["ann_type"] == "annual_report" for c in result["candidates"])


def test_discover_one_no_report_announcements(monkeypatch):
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **_kw: {"financial_announcements": [
            {"pdf_url": "https://psx.com.pk/doc/x.pdf",
             "title": "Board meeting notice", "date": "May 1, 2025"},
        ]},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "no_report_announcements"
    assert result["candidates"] == []


def test_discover_one_fetch_failed(monkeypatch):
    def _boom(ticker, **_kw):
        raise ConnectionError("timeout")
    monkeypatch.setattr("pipeline.discover.fetch_psx_company", _boom)
    result = discover_one("LUCK")
    assert result["outcome"].startswith("fetch_failed:")
    assert result["candidates"] == []


def test_discover_one_skips_announcements_without_pdf_url(monkeypatch):
    anns = [
        {"pdf_url": None, "title": "Transmission of Annual Report for the Year Ended Jun 30, 2025",
         "date": "Jun 15, 2025"},
        {"pdf_url": "https://psx.com.pk/doc/b.pdf",
         "title": "Transmission of Annual Report for the Year Ended Jun 30, 2024",
         "date": "Jun 1, 2024"},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **_kw: {"financial_announcements": anns},
    )
    result = discover_one("LUCK")
    assert result["outcome"] == "found"
    assert len(result["candidates"]) == 1


def test_discover_one_source_changed(monkeypatch):
    # Wrong table: all dates are board-member names, not filing dates
    wrong = [
        {"date": "Director Name", "title": "Role", "pdf_url": None},
        {"date": "Ms. Xyz", "title": "CEO", "pdf_url": None},
        {"date": "Mr. Abc", "title": "CFO", "pdf_url": None},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **_kw: {"financial_announcements": wrong},
    )
    result = discover_one("LUCK")
    assert result["outcome"].startswith("source_changed:")
    assert result["candidates"] == []


def test_discover_one_falls_back_to_interim_when_no_annual_report(monkeypatch):
    # TRG-like: only quarterly reports visible
    anns = [
        {"pdf_url": "https://psx.com.pk/doc/q.pdf",
         "title": "Transmission of Quarterly Report for the Period Ended March 31, 2026",
         "date": "Apr 29, 2026"},
    ]
    monkeypatch.setattr(
        "pipeline.discover.fetch_psx_company",
        lambda ticker, **_kw: {"financial_announcements": anns},
    )
    result = discover_one("TRG")
    assert result["outcome"] == "found"
    assert result["candidates"][0]["ann_type"] == "interim"


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
        "ticker":     "LUCK",
        "url":        "https://psx.com.pk/doc/luck_ar_2025.pdf",
        "title":      "Transmission of Annual Report for the Year Ended June 30, 2025",
        "ann_date":   "Jun 15, 2025",
        "period_end": "2025-06-30",
        "ann_type":   "annual_report",
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
    # filings row written
    row = db.execute("SELECT * FROM filings WHERE doc_sha256=?", (sha256,)).fetchone()
    assert row is not None
    assert row["ticker"] == "LUCK"
    # fetch_log row written
    log_row = db.execute("SELECT * FROM fetch_log WHERE url=?",
                         (candidate["url"],)).fetchone()
    assert log_row is not None
    assert log_row["outcome"] == "stored"
    assert log_row["doc_sha256"] == sha256


def test_fetch_one_already_fetched_on_second_run(db, candidate, monkeypatch,
                                                  tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    download_calls = []

    def _tracked_download(url):
        download_calls.append(url)
        return _fake_pdf(b"LUCK2025")

    monkeypatch.setattr(pf, "_download", _tracked_download)

    r1 = pf.fetch_one(db, candidate)
    assert r1["outcome"] == "stored"
    assert len(download_calls) == 1

    r2 = pf.fetch_one(db, candidate)
    assert r2["outcome"] == "already_fetched"
    # No new HTTP request was made
    assert len(download_calls) == 1


def test_fetch_one_retries_failed_outcome(db, candidate, monkeypatch, tmp_path):
    """A URL with a failed outcome in fetch_log must be retried on the next call."""
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")

    # Simulate a prior failed attempt already recorded in fetch_log
    upsert_fetch_log(db,
        url=candidate["url"], ticker=candidate["ticker"],
        attempted_at=datetime.now(timezone.utc).isoformat(),
        outcome="http_error", doc_sha256=None, bytes=None,
    )
    db.commit()

    download_calls = []

    def _now_works(url):
        download_calls.append(url)
        return _fake_pdf(b"LUCK2025_retry")

    monkeypatch.setattr(pf, "_download", _now_works)
    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "stored"
    assert len(download_calls) == 1


def test_fetch_one_rate_limited_is_retried_next_run(db, candidate, monkeypatch,
                                                     tmp_path):
    """rate_limited outcome must be retried (not treated as already_fetched)."""
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")

    upsert_fetch_log(db,
        url=candidate["url"], ticker=candidate["ticker"],
        attempted_at=datetime.now(timezone.utc).isoformat(),
        outcome="rate_limited", doc_sha256=None, bytes=None,
    )
    db.commit()

    monkeypatch.setattr(pf, "_download", lambda url: _fake_pdf(b"recovered"))
    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "stored"


def test_fetch_one_rate_limited_recorded_in_fetch_log(db, candidate, monkeypatch,
                                                       tmp_path):
    """When _download returns rate_limited, fetch_log records it."""
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: "rate_limited")

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "rate_limited"

    log_row = db.execute("SELECT * FROM fetch_log WHERE url=?",
                         (candidate["url"],)).fetchone()
    assert log_row["outcome"] == "rate_limited"
    assert log_row["doc_sha256"] is None


def test_fetch_one_duplicate_same_hash_different_url(db, candidate, monkeypatch,
                                                      tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    content = _fake_pdf(b"shared_content")
    monkeypatch.setattr(pf, "_download", lambda url: content)

    c2 = {**candidate, "url": "https://psx.com.pk/doc/luck_ar_2025_v2.pdf"}

    r1 = pf.fetch_one(db, candidate)
    r2 = pf.fetch_one(db, c2)

    assert r1["outcome"] == "stored"
    assert r2["outcome"] == "duplicate"
    assert r2["doc_sha256"] == r1["doc_sha256"]
    # Only one filings row
    assert db.execute("SELECT count(*) FROM filings").fetchone()[0] == 1
    # Both URLs in fetch_log
    assert db.execute("SELECT count(*) FROM fetch_log").fetchone()[0] == 2


def test_fetch_one_not_pdf(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: b"<html>not a pdf</html>")

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "not_pdf"
    assert db.execute("SELECT count(*) FROM filings").fetchone()[0] == 0
    # fetch_log row still written for the failure
    log_row = db.execute("SELECT * FROM fetch_log WHERE url=?",
                         (candidate["url"],)).fetchone()
    assert log_row is not None
    assert log_row["outcome"] == "not_pdf"


def test_fetch_one_too_large(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    big = b"%PDF-1.4 " + b"x" * (pf.MAX_FILE_BYTES + 1)
    monkeypatch.setattr(pf, "_download", lambda url: big)

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "too_large"
    log_row = db.execute("SELECT * FROM fetch_log WHERE url=?",
                         (candidate["url"],)).fetchone()
    assert log_row["outcome"] == "too_large"


def test_fetch_one_http_error(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    monkeypatch.setattr(pf, "_download", lambda url: "http_404")

    result = pf.fetch_one(db, candidate)
    assert result["outcome"] == "http_404"
    log_row = db.execute("SELECT * FROM fetch_log WHERE url=?",
                         (candidate["url"],)).fetchone()
    assert log_row["outcome"] == "http_404"
    assert log_row["doc_sha256"] is None


def test_bronze_path_uses_sha256_prefix(db, candidate, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    content = _fake_pdf(b"predictable")
    monkeypatch.setattr(pf, "_download", lambda url: content)

    result = pf.fetch_one(db, candidate)
    import hashlib
    sha256 = hashlib.sha256(content).hexdigest()
    expected = tmp_path / "bronze" / sha256[:2] / f"{sha256}.pdf"
    assert Path(result["file_path"]) == expected
    assert expected.exists()


def test_fetch_log_written_for_every_failure_outcome(db, monkeypatch, tmp_path):
    import pipeline.fetch as pf
    monkeypatch.setattr(pf, "BRONZE_DIR", tmp_path / "bronze")
    urls_tested = [
        ("https://psx.com.pk/doc/a.pdf", lambda _: b"<html>"),    # not_pdf
        ("https://psx.com.pk/doc/b.pdf", lambda _: "http_403"),   # http_403
        ("https://psx.com.pk/doc/c.pdf", lambda _: "rate_limited"),  # rate_limited
    ]
    for url, dl in urls_tested:
        monkeypatch.setattr(pf, "_download", dl)
        pf.fetch_one(db, {**{"ticker": "LUCK", "title": "", "ann_date": "",
                              "period_end": None, "ann_type": "annual_report"},
                         "url": url})
    count = db.execute("SELECT count(*) FROM fetch_log").fetchone()[0]
    assert count == len(urls_tested)
