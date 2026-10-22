"""M1: pipeline tables are created by create_pipeline_tables, and pipeline status
works correctly on an empty store."""
from __future__ import annotations
import sqlite3
from unittest.mock import patch

from pipeline.store import PIPELINE_TABLE_NAMES, create_pipeline_tables, get_status


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_pipeline_tables_created():
    db = _fresh_db()
    create_pipeline_tables(db)
    existing = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for name in PIPELINE_TABLE_NAMES:
        assert name in existing, f"Table {name!r} not found after create_pipeline_tables"
    db.close()


def test_status_on_empty_store():
    db = _fresh_db()
    create_pipeline_tables(db)
    s = get_status(db)
    assert s["last_run"] is None
    assert s["filings"] == 0
    assert s["statements"] == 0
    assert s["verified"] == 0
    assert s["quarantined"] == 0
    assert s["facts"] == 0
    db.close()


def test_status_counts_reflect_inserts():
    db = _fresh_db()
    create_pipeline_tables(db)
    db.execute(
        "INSERT INTO filings (doc_sha256, ticker, source_url, file_path, file_bytes, fetched_at) "
        "VALUES ('abc', 'LUCK', 'http://example.com', '/tmp/f.pdf', 1024, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO statements "
        "(statement_id, doc_sha256, ticker, period_end, months, basis, "
        " statement_type, status, parser_version, run_id, created_at) "
        "VALUES ('sid1', 'abc', 'LUCK', '2026-06-30', 12, 'consolidated', "
        "        'PL', 'verified', 'v0.1', 'r1', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO statements "
        "(statement_id, doc_sha256, ticker, period_end, months, basis, "
        " statement_type, status, parser_version, run_id, created_at) "
        "VALUES ('sid2', 'abc', 'LUCK', '2026-06-30', 12, 'consolidated', "
        "        'BS', 'quarantined', 'v0.1', 'r1', '2026-01-01')"
    )
    db.commit()
    s = get_status(db)
    assert s["filings"] == 1
    assert s["statements"] == 2
    assert s["verified"] == 1
    assert s["quarantined"] == 1
    db.close()


def test_status_cmd_on_empty_store(capsys):
    db = _fresh_db()
    create_pipeline_tables(db)
    with patch("database.connection.init_database"), \
         patch("database.connection.get_db", return_value=db):
        from pipeline.__main__ import cmd_status
        ret = cmd_status(None)
    out = capsys.readouterr().out
    assert ret == 0
    assert "No pipeline runs recorded" in out
    assert "Filings: 0" in out
    assert "Facts: 0" in out
