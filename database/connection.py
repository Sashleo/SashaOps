"""
database/connection.py — SQLCipher encrypted database connection manager.

The database file is AES-256 encrypted at rest.
No plaintext data ever touches disk.
The master password unlocks the database on every startup.
"""

import os
from contextlib import contextmanager
import structlog

from config.settings import settings

log = structlog.get_logger()

# ── Module-level connection ───────────────────────────────────────────────────
_connection = None


def init_database() -> None:
    """
    Initialise the encrypted database connection.
    Must be called once on app startup before any database operations.
    Creates the database file if it does not exist.
    """
    global _connection

    try:
        from pysqlcipher3 import dbapi2 as sqlite
    except ImportError:
        raise SystemExit(
            "FATAL: pysqlcipher3 is not installed — encrypted database unavailable.\n"
            "SashaOps requires SQLCipher for AES-256 database encryption.\n"
            "Build and run via the provided Dockerfile:\n"
            "  docker compose up --build\n"
            "Do NOT run without encryption — client data must be protected at rest."
        )

    db_path = settings.DATABASE_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    _connection = sqlite.connect(db_path, check_same_thread=False)

    # Set the encryption key (SQLCipher-specific pragma).
    # This must be the first operation on a new or existing encrypted database.
    # A wrong password (or any SQLCipher failure) MUST hard-fail — never degrade to
    # plaintext. The only acceptable non-encrypted path is the missing-pysqlcipher3
    # SystemExit above (which itself refuses to run).
    try:
        _password = settings.DB_MASTER_PASSWORD.replace("'", "''")
        _connection.execute(f"PRAGMA key = '{_password}'")
        # Verify the key is correct by touching the encrypted header.
        _connection.execute("SELECT count(*) FROM sqlite_master")
        log.info("Encrypted database opened", path=db_path)
    except Exception as e:
        raise SystemExit(
            "FATAL: Database key rejected or SQLCipher failure — "
            f"check DB_MASTER_PASSWORD ({type(e).__name__}). "
            "Refusing to run without encryption."
        )

    # Performance settings
    _connection.execute("PRAGMA journal_mode=WAL")   # Write-Ahead Logging
    _connection.execute("PRAGMA synchronous=NORMAL")
    _connection.execute("PRAGMA foreign_keys=ON")
    _connection.row_factory = sqlite.Row


def get_db():
    """
    Get the database connection.
    Used as a FastAPI dependency.
    """
    if _connection is None:
        raise RuntimeError("Database not initialised. Call init_database() first.")
    return _connection


@contextmanager
def db_transaction():
    """
    Context manager for database transactions.
    Automatically commits on success, rolls back on exception.
    """
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Database transaction rolled back", error=str(e))
        raise


def close_database() -> None:
    """Close the database connection on shutdown."""
    global _connection
    if _connection:
        _connection.close()
        _connection = None
        log.info("Database connection closed")
