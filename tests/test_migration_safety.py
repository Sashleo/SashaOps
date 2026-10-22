"""
Rows written before a schema migration must survive it — not be silently dropped.
Tests for _migrate_private_entries and _migrate_reports_schema.
"""
import uuid


def _make_db():
    """Open an in-memory SQLite connection (no SQLCipher needed for unit tests)."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


# ── private_entries ──────────────────────────────────────────────────────────

def _old_private_entries_schema(db) -> None:
    """Create private_entries in the OLD layout (client_id TEXT PRIMARY KEY)."""
    db.execute("""
        CREATE TABLE private_entries (
            client_id    TEXT PRIMARY KEY,
            entry_name   TEXT NOT NULL,
            company_name TEXT,
            data_json    TEXT NOT NULL,
            saved_at     TEXT NOT NULL
        )
    """)


def test_private_entries_migration_preserves_rows():
    from database.schema import _migrate_private_entries

    db = _make_db()
    _old_private_entries_schema(db)
    db.execute(
        "INSERT INTO private_entries VALUES (?, ?, ?, ?, ?)",
        ("cli-1", "My Entry", "Acme Co", '{"x":1}', "2026-01-01T10:00:00"),
    )
    db.commit()

    _migrate_private_entries(db)
    db.commit()

    rows = db.execute("SELECT * FROM private_entries").fetchall()
    assert len(rows) == 1, "row must survive migration"
    row = rows[0]
    assert row["entry_name"] == "My Entry"
    assert row["company_name"] == "Acme Co"
    assert row["data_json"] == '{"x":1}'
    assert row["saved_at"] == "2026-01-01T10:00:00"
    assert row["client_id"] == "cli-1"
    assert row["id"] == "cli-1", "old client_id becomes the new id"


def test_private_entries_migration_is_idempotent():
    from database.schema import _migrate_private_entries

    db = _make_db()
    _old_private_entries_schema(db)
    db.execute(
        "INSERT INTO private_entries VALUES (?, ?, ?, ?, ?)",
        ("cli-2", "Entry B", None, '{}', "2026-02-01"),
    )
    db.commit()

    _migrate_private_entries(db)
    _migrate_private_entries(db)  # second call must not crash
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM private_entries").fetchone()[0] == 1


def test_private_entries_already_new_schema_is_noop():
    from database.schema import _migrate_private_entries

    db = _make_db()
    db.execute("""
        CREATE TABLE private_entries (
            id           TEXT PRIMARY KEY,
            client_id    TEXT NOT NULL,
            entry_name   TEXT NOT NULL,
            company_name TEXT,
            data_json    TEXT NOT NULL,
            saved_at     TEXT NOT NULL
        )
    """)
    eid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO private_entries VALUES (?, ?, ?, ?, ?, ?)",
        (eid, "cli-3", "Existing", None, '{"y":2}', "2026-03-01"),
    )
    db.commit()

    _migrate_private_entries(db)
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM private_entries").fetchone()[0] == 1


# ── reports ───────────────────────────────────────────────────────────────────

def _old_reports_schema(db) -> None:
    """Create reports in the OLD layout (client_id NOT NULL, no ticker)."""
    db.execute("""
        CREATE TABLE reports (
            id              TEXT PRIMARY KEY,
            client_id       TEXT NOT NULL,
            business_name   TEXT NOT NULL,
            report_type     TEXT NOT NULL,
            modules_included TEXT,
            pdf_path        TEXT,
            docx_path       TEXT,
            signature_path  TEXT,
            signing_algo    TEXT,
            created_at      TEXT NOT NULL,
            delivered_at    TEXT,
            delivery_method TEXT
        )
    """)


def test_reports_migration_preserves_rows():
    from database.schema import _migrate_reports_schema

    db = _make_db()
    _old_reports_schema(db)
    rid = str(uuid.uuid4())
    db.execute(
        """INSERT INTO reports
           (id, client_id, business_name, report_type, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (rid, "cli-1", "Acme Ltd", "psx", "2026-01-15T08:00:00"),
    )
    db.commit()

    _migrate_reports_schema(db)
    db.commit()

    rows = db.execute("SELECT * FROM reports").fetchall()
    assert len(rows) == 1, "row must survive migration"
    row = rows[0]
    assert row["id"] == rid
    assert row["client_id"] == "cli-1"
    assert row["business_name"] == "Acme Ltd"
    assert row["report_type"] == "psx"
    assert row["created_at"] == "2026-01-15T08:00:00"


def test_reports_migration_is_idempotent():
    from database.schema import _migrate_reports_schema

    db = _make_db()
    _old_reports_schema(db)
    rid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO reports (id, client_id, business_name, report_type, created_at) VALUES (?,?,?,?,?)",
        (rid, "cli-1", "B", "psx", "2026-01-01"),
    )
    db.commit()

    _migrate_reports_schema(db)
    _migrate_reports_schema(db)  # must not crash
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
