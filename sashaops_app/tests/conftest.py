"""
conftest.py — Test fixtures and import patches.

Patches the database connection before any test module imports it,
so tests can run outside Docker without pysqlcipher3.
"""
import sys
from unittest.mock import MagicMock, patch

# --- Patch pysqlcipher3 before anything imports it ---
mock_sqlcipher = MagicMock()
mock_sqlcipher.dbapi2 = MagicMock()
mock_conn = MagicMock()
mock_conn.execute.return_value = MagicMock(fetchone=lambda: None, fetchall=lambda: [])
mock_sqlcipher.dbapi2.connect.return_value = mock_conn
sys.modules["pysqlcipher3"] = mock_sqlcipher
sys.modules["pysqlcipher3.dbapi2"] = mock_sqlcipher.dbapi2

# --- Patch database.connection.get_db to return the mock ---
import database.connection as _db_conn
_db_conn._connection = mock_conn
_db_conn.get_db = lambda: mock_conn

# --- Patch liboqs (PQC library) if not installed ---
try:
    import oqs  # noqa
except ImportError:
    sys.modules["oqs"] = MagicMock()