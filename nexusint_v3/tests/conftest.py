"""
Shared pytest fixtures.

These tests run against the live encrypted database (inside the container, where
DB_MASTER_PASSWORD and the DB file exist). DB-touching tests use the temp_client
fixture, which inserts a throwaway client row and removes it afterwards so the
clients FOREIGN KEY on analysis_runs is satisfied without polluting real data.
"""

import uuid
from datetime import datetime

import pytest


@pytest.fixture(scope="session", autouse=True)
def _db():
    """Initialise the encrypted DB connection once for the whole test session."""
    from database.connection import init_database
    init_database()


@pytest.fixture
def temp_client():
    """Insert a throwaway client and yield its id; delete it (and its runs) after."""
    from database.connection import get_db
    db = get_db()
    cid = f"test-{uuid.uuid4()}"
    db.execute(
        "INSERT INTO clients (id, name, salt, created_at) VALUES (?,?,?,?)",
        (cid, "PyTest Co", b"\x00" * 16, datetime.utcnow().isoformat()),
    )
    db.commit()
    try:
        yield cid
    finally:
        db.execute("DELETE FROM analysis_runs WHERE client_id = ?", (cid,))
        db.execute("DELETE FROM clients WHERE id = ?", (cid,))
        db.commit()
