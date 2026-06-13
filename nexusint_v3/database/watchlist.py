"""
database/watchlist.py — Personal PSX watchlist CRUD.

Not client-specific — shared personal list for the app owner.
Pakistan-only: keyed by PSX symbol. The exchange_id parameter is accepted for
call-site compatibility but ignored (always PSX).
"""

from datetime import datetime, timezone
from typing import List, Optional

from database.connection import get_db


def add_to_watchlist(exchange_id: Optional[str], ticker: str,
                     company_name: str = "", notes: str = "") -> bool:
    """Add a PSX symbol to the watchlist. Returns True if inserted."""
    db = get_db()
    try:
        db.execute(
            """INSERT OR IGNORE INTO psx_watchlist (symbol, notes, added_at)
               VALUES (?, ?, ?)""",
            (ticker.upper(), notes,
             datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        )
        db.commit()
        return True
    except Exception:
        return False


def remove_from_watchlist(exchange_id: Optional[str], ticker: str) -> bool:
    """Remove a symbol from the watchlist. Returns True if a row was deleted."""
    db = get_db()
    cursor = db.execute(
        "DELETE FROM psx_watchlist WHERE symbol = ?",
        (ticker.upper(),),
    )
    db.commit()
    return cursor.rowcount > 0


def get_watchlist() -> List[dict]:
    """Return all watchlist entries ordered by most recently added."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM psx_watchlist ORDER BY added_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ticker"] = d.get("symbol")
        d["exchange_id"] = "KAR"
        out.append(d)
    return out


def is_watched(exchange_id: Optional[str], ticker: str) -> bool:
    """Return True if a symbol is already on the watchlist."""
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM psx_watchlist WHERE symbol = ?",
        (ticker.upper(),),
    ).fetchone()
    return row is not None


def update_watchlist_notes(exchange_id: Optional[str], ticker: str, notes: str) -> bool:
    """Update the personal notes for a watchlist entry."""
    db = get_db()
    cursor = db.execute(
        "UPDATE psx_watchlist SET notes = ? WHERE symbol = ?",
        (notes, ticker.upper()),
    )
    db.commit()
    return cursor.rowcount > 0
