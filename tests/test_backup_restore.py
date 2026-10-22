"""Backup and restore round-trip tests, with SHA-256 and integrity verification."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(path: str) -> None:
    """Plain SQLite DB with one row (pysqlcipher3 not needed here)."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE kv (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO kv VALUES (1, 'hello')")
    conn.commit()
    conn.close()


def _open_db(db_path: str):
    """Return a real sqlite3 connection that handles VACUUM INTO."""
    return sqlite3.connect(db_path)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_backup_creates_file_and_sha256_sidecar():
    with tempfile.TemporaryDirectory() as tmp:
        db_path    = str(Path(tmp) / "sashaops.db")
        backup_dir = str(Path(tmp) / "backups")
        _make_db(db_path)

        with patch("database.connection.get_db", return_value=_open_db(db_path)), \
             patch("config.settings.settings") as ms:
            ms.BACKUP_COPY_DIR = ""
            from database.backup import backup_database
            dest = backup_database(backup_dir=backup_dir)

        assert dest.exists(), "backup file not created"
        sha_file = dest.with_suffix(".db.sha256")
        assert sha_file.exists(), ".sha256 sidecar not created"
        assert sha_file.read_text().strip() == hashlib.sha256(dest.read_bytes()).hexdigest()


def test_restore_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        db_path    = str(Path(tmp) / "sashaops.db")
        backup_dir = str(Path(tmp) / "backups")
        _make_db(db_path)
        Path(backup_dir).mkdir()

        with patch("database.connection.get_db", return_value=_open_db(db_path)), \
             patch("config.settings.settings") as ms:
            ms.BACKUP_COPY_DIR = ""
            from database.backup import backup_database
            backup = backup_database(backup_dir=backup_dir)

        Path(db_path).write_bytes(b"corrupted-data")

        with patch("config.settings.settings") as ms, \
             patch("database.backup._verify_restored_db"):
            ms.DATABASE_PATH = db_path
            from database.backup import restore_database
            restore_database(str(backup), db_path=db_path)

        rconn = sqlite3.connect(db_path)
        row   = rconn.execute("SELECT val FROM kv WHERE id=1").fetchone()
        rconn.close()
        assert row[0] == "hello"


def test_restore_rejects_corrupt_backup():
    with tempfile.TemporaryDirectory() as tmp:
        db_path    = str(Path(tmp) / "sashaops.db")
        backup_dir = str(Path(tmp) / "backups")
        _make_db(db_path)
        Path(backup_dir).mkdir()

        with patch("database.connection.get_db", return_value=_open_db(db_path)), \
             patch("config.settings.settings") as ms:
            ms.BACKUP_COPY_DIR = ""
            from database.backup import backup_database
            backup = backup_database(backup_dir=backup_dir)

        backup.write_bytes(b"this is not the original")

        with patch("config.settings.settings") as ms:
            ms.DATABASE_PATH = db_path
            from database.backup import restore_database
            with pytest.raises(ValueError, match="SHA-256 mismatch"):
                restore_database(str(backup), db_path=db_path)


def test_restore_refuses_when_sha256_missing():
    with tempfile.TemporaryDirectory() as tmp:
        db_path    = str(Path(tmp) / "sashaops.db")
        backup_dir = str(Path(tmp) / "backups")
        _make_db(db_path)
        Path(backup_dir).mkdir()

        with patch("database.connection.get_db", return_value=_open_db(db_path)), \
             patch("config.settings.settings") as ms:
            ms.BACKUP_COPY_DIR = ""
            from database.backup import backup_database
            backup = backup_database(backup_dir=backup_dir)

        backup.with_suffix(".db.sha256").unlink()

        with patch("config.settings.settings") as ms:
            ms.DATABASE_PATH = db_path
            from database.backup import restore_database
            with pytest.raises(ValueError, match="sidecar not found"):
                restore_database(str(backup), db_path=db_path)


def test_restore_runs_integrity_check():
    with tempfile.TemporaryDirectory() as tmp:
        db_path    = str(Path(tmp) / "sashaops.db")
        backup_dir = str(Path(tmp) / "backups")
        _make_db(db_path)
        Path(backup_dir).mkdir()

        with patch("database.connection.get_db", return_value=_open_db(db_path)), \
             patch("config.settings.settings") as ms:
            ms.BACKUP_COPY_DIR = ""
            from database.backup import backup_database
            backup = backup_database(backup_dir=backup_dir)

        with patch("config.settings.settings") as ms, \
             patch("database.backup._verify_restored_db") as mock_verify:
            ms.DATABASE_PATH      = db_path
            ms.DB_MASTER_PASSWORD = "testpw"
            from database.backup import restore_database
            restore_database(str(backup), db_path=db_path)
        mock_verify.assert_called_once_with(db_path, "testpw")

        from database.backup import _verify_restored_db
        _verify_restored_db(db_path, "ignored", _connect_fn=sqlite3.connect)


def test_verify_key_escapes_single_quote(tmp_path):
    """PRAGMA key must use SQL-escaped password ('' for each ')."""
    db_path = str(tmp_path / "test.db")
    _make_db(db_path)

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [("ok",)]
    mock_dbapi2 = MagicMock()
    mock_dbapi2.connect.return_value = mock_conn
    mock_pkg = MagicMock()
    mock_pkg.dbapi2 = mock_dbapi2

    with patch.dict(sys.modules, {"pysqlcipher3": mock_pkg}):
        from database.backup import _verify_restored_db
        _verify_restored_db(db_path, "pass'word")

    # Confirm PRAGMA key was sent with the escaped password
    pragma_sqls = [c.args[0] for c in mock_conn.execute.call_args_list]
    key_stmts = [s for s in pragma_sqls if "PRAGMA key" in s]
    assert len(key_stmts) == 1, f"Expected one PRAGMA key call, got: {pragma_sqls}"
    assert "pass''word" in key_stmts[0], f"Expected escaped quote in: {key_stmts[0]}"


def test_prune_keeps_14_most_recent():
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(20):
            (Path(tmp) / f"sashaops_20260101_{i:06d}.db").write_bytes(b"x")

        from database.backup import _prune_backups
        _prune_backups(tmp, keep=14)

        assert len(list(Path(tmp).glob("sashaops_*.db"))) == 14


def test_list_backups_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        names = [
            "sashaops_20260101_000001.db",
            "sashaops_20260102_000002.db",
            "sashaops_20260103_000003.db",
        ]
        for name in names:
            (Path(tmp) / name).write_bytes(b"x")
            (Path(tmp) / name.replace(".db", ".db.sha256")).write_text("abc")

        from database.backup import list_backups
        result = list_backups(tmp)

        assert result[0]["name"] == "sashaops_20260103_000003.db"
        assert len(result) == 3
