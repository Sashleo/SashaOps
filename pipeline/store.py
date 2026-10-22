"""pipeline/store.py — All SQL for the pipeline tables.

Every stage accesses the database only through this module.
No SQL appears in any other pipeline file.
"""
from __future__ import annotations
from typing import Optional
import structlog

log = structlog.get_logger()

# ── DDL ───────────────────────────────────────────────────────────────────────

PIPELINE_TABLES = """
-- ── Bronze: one row per distinct downloaded filing, keyed by content SHA-256 ─
CREATE TABLE IF NOT EXISTS filings (
    doc_sha256          TEXT    PRIMARY KEY,
    ticker              TEXT    NOT NULL,
    source_url          TEXT    NOT NULL,
    ann_title           TEXT,
    ann_date            TEXT,
    file_path           TEXT    NOT NULL,
    file_bytes          INTEGER NOT NULL,
    pages               INTEGER,
    fetched_at          TEXT    NOT NULL,
    doc_class           TEXT,
    text_pages          INTEGER,
    classifier_version  TEXT
);
CREATE INDEX IF NOT EXISTS ix_filings_ticker   ON filings(ticker);
CREATE INDEX IF NOT EXISTS ix_filings_ann_date ON filings(ann_date);

-- ── S4 located statements ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doc_statements (
    doc_sha256          TEXT    NOT NULL,
    statement_type      TEXT    NOT NULL,
    basis               TEXT    NOT NULL,
    pdf_page_start      INTEGER,
    pdf_page_end        INTEGER,
    printed_page        TEXT,
    located_by          TEXT,
    period_ends_json    TEXT,
    scale               TEXT,
    locator_version     TEXT,
    PRIMARY KEY (doc_sha256, statement_type, basis),
    FOREIGN KEY (doc_sha256) REFERENCES filings(doc_sha256)
);

-- ── Silver: S5 extract + S6 map ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS line_items (
    doc_sha256          TEXT    NOT NULL,
    statement_type      TEXT    NOT NULL,
    basis               TEXT    NOT NULL,
    pdf_page            INTEGER NOT NULL,
    line_no             INTEGER NOT NULL,
    column_no           INTEGER NOT NULL,
    label_raw           TEXT,
    label_norm          TEXT,
    field               TEXT,
    value_rupees        INTEGER,
    printed_text        TEXT,
    is_dash             INTEGER NOT NULL DEFAULT 0,
    is_bracketed        INTEGER NOT NULL DEFAULT 0,
    scale               TEXT,
    period_end          TEXT,
    months              INTEGER,
    x0                  REAL,
    x1                  REAL,
    top                 REAL,
    parser_version      TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    PRIMARY KEY (doc_sha256, statement_type, basis, pdf_page, line_no, column_no),
    FOREIGN KEY (doc_sha256) REFERENCES filings(doc_sha256)
);
CREATE INDEX IF NOT EXISTS ix_line_items_field ON line_items(field);

-- ── Statements: S7 output, one row per extracted period+basis+type triple ───
CREATE TABLE IF NOT EXISTS statements (
    statement_id        TEXT    PRIMARY KEY,
    doc_sha256          TEXT    NOT NULL,
    ticker              TEXT    NOT NULL,
    period_end          TEXT    NOT NULL,
    months              INTEGER NOT NULL,
    basis               TEXT    NOT NULL,
    statement_type      TEXT    NOT NULL,
    status              TEXT    NOT NULL,
    checks_json         TEXT,
    parser_version      TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    created_at          TEXT    NOT NULL,
    FOREIGN KEY (doc_sha256) REFERENCES filings(doc_sha256)
);
CREATE INDEX IF NOT EXISTS ix_statements_ticker ON statements(ticker, period_end);
CREATE INDEX IF NOT EXISTS ix_statements_status ON statements(status);

-- ── Gold: one row per (ticker, period, basis, field) ────────────────────────
CREATE TABLE IF NOT EXISTS facts (
    ticker              TEXT    NOT NULL,
    period_end          TEXT    NOT NULL,
    months              INTEGER NOT NULL,
    basis               TEXT    NOT NULL,
    field               TEXT    NOT NULL,
    statement_id        TEXT    NOT NULL,
    value_rupees        INTEGER,
    source              TEXT    NOT NULL,
    is_current          INTEGER NOT NULL DEFAULT 1,
    restated            INTEGER NOT NULL DEFAULT 0,
    published_at        TEXT    NOT NULL,
    PRIMARY KEY (ticker, period_end, months, basis, field, statement_id),
    FOREIGN KEY (statement_id) REFERENCES statements(statement_id)
);
CREATE INDEX IF NOT EXISTS ix_facts_ticker ON facts(ticker, period_end, is_current);

-- ── Runs: one row per pipeline invocation ────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT    PRIMARY KEY,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    git_commit          TEXT,
    parser_version      TEXT,
    stages_json         TEXT,
    counts_json         TEXT,
    duration_ms         INTEGER,
    is_locked           INTEGER NOT NULL DEFAULT 0
);
"""

PIPELINE_TABLE_NAMES = (
    "filings", "doc_statements", "line_items", "statements", "facts", "runs",
)


# ── Setup ─────────────────────────────────────────────────────────────────────

def create_pipeline_tables(db) -> None:
    """Create all pipeline tables. Safe to call multiple times (IF NOT EXISTS)."""
    db.executescript(PIPELINE_TABLES)
    log.info("Pipeline tables ready")


# ── Status query ──────────────────────────────────────────────────────────────

def get_status(db) -> dict:
    """Return a dict summarising current pipeline state for `pipeline status`."""
    def _count(sql: str) -> int:
        row = db.execute(sql).fetchone()
        return row[0] if row else 0

    last_run = db.execute(
        "SELECT run_id, started_at, finished_at, counts_json, is_locked "
        "FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    run_dict: Optional[dict] = None
    if last_run:
        run_dict = {
            "run_id":      last_run[0],
            "started_at":  last_run[1],
            "finished_at": last_run[2],
            "counts_json": last_run[3],
            "is_locked":   last_run[4],
        }

    return {
        "last_run":    run_dict,
        "filings":     _count("SELECT count(*) FROM filings"),
        "statements":  _count("SELECT count(*) FROM statements"),
        "verified":    _count("SELECT count(*) FROM statements WHERE status = 'verified'"),
        "quarantined": _count("SELECT count(*) FROM statements WHERE status = 'quarantined'"),
        "facts":       _count("SELECT count(*) FROM facts WHERE is_current = 1"),
    }


# ── Filings ───────────────────────────────────────────────────────────────────

def upsert_filing(db, **kwargs) -> None:
    cols = list(kwargs)
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    db.execute(
        f"INSERT OR REPLACE INTO filings ({col_list}) VALUES ({placeholders})",
        list(kwargs.values()),
    )


# ── Runs ──────────────────────────────────────────────────────────────────────

def start_run(db, run_id: str, started_at: str, git_commit: Optional[str] = None,
              parser_version: Optional[str] = None) -> None:
    db.execute(
        "INSERT INTO runs (run_id, started_at, git_commit, parser_version, is_locked) "
        "VALUES (?, ?, ?, ?, 1)",
        (run_id, started_at, git_commit, parser_version),
    )
    db.commit()


def finish_run(db, run_id: str, finished_at: str, stages_json: Optional[str] = None,
               counts_json: Optional[str] = None, duration_ms: Optional[int] = None) -> None:
    db.execute(
        "UPDATE runs SET finished_at=?, stages_json=?, counts_json=?, "
        "duration_ms=?, is_locked=0 WHERE run_id=?",
        (finished_at, stages_json, counts_json, duration_ms, run_id),
    )
    db.commit()
