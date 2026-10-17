"""
database/dcf_settings.py — Persistent DCF rate overrides.

Stores user-set overrides for DCF valuation assumptions in dcf_assumptions table.
Only rows where override_value IS NOT NULL are respected; NULL = use live/fallback.
"""

from __future__ import annotations
from datetime import datetime, timezone
from database.connection import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_override(key: str) -> float | None:
    """Return override value for key, or None if not set / cleared."""
    db = get_db()
    row = db.execute(
        "SELECT override_value FROM dcf_assumptions WHERE key = ?", (key,)
    ).fetchone()
    if row is None or row["override_value"] is None:
        return None
    return float(row["override_value"])


def set_override(key: str, value: float) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO dcf_assumptions (key, override_value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
             override_value=excluded.override_value, updated_at=excluded.updated_at""",
        (key, value, _now()),
    )
    db.commit()


def reset_override(key: str) -> None:
    """Clear the manual override (restores live/fallback resolution)."""
    db = get_db()
    db.execute(
        "UPDATE dcf_assumptions SET override_value = NULL, updated_at = ? WHERE key = ?",
        (_now(), key),
    )
    db.commit()


def get_all_overrides() -> dict[str, float | None]:
    """Return dict of all stored keys → override value (None if cleared)."""
    db = get_db()
    rows = db.execute("SELECT key, override_value FROM dcf_assumptions").fetchall()
    return {
        r["key"]: (float(r["override_value"]) if r["override_value"] is not None else None)
        for r in rows
    }
