"""
database/private_entries.py — Named saved entries for the Private Analyser.
Multiple entries per client, each with a user-chosen name.
"""

import json
import uuid
from datetime import datetime, timezone

from database.connection import get_db


def save_entry(client_id: str, form_data: dict, entry_name: str = "") -> str:
    """Create a new named entry. Returns the new entry id."""
    eid = uuid.uuid4().hex
    name = (entry_name or form_data.get("company_name") or "Untitled").strip()[:80]
    company_name = form_data.get("company_name", "")
    data_json = json.dumps({k: v for k, v in form_data.items()})
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        """INSERT INTO private_entries (id, client_id, entry_name, company_name, data_json, saved_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (eid, client_id, name, company_name, data_json, now),
    )
    db.commit()
    return eid


def update_entry(entry_id: str, form_data: dict, entry_name: str = "") -> None:
    """Overwrite an existing entry's data (and optionally rename it)."""
    data_json = json.dumps({k: v for k, v in form_data.items()})
    company_name = form_data.get("company_name", "")
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    if entry_name:
        db.execute(
            "UPDATE private_entries SET entry_name=?, company_name=?, data_json=?, saved_at=? WHERE id=?",
            (entry_name.strip()[:80], company_name, data_json, now, entry_id),
        )
    else:
        db.execute(
            "UPDATE private_entries SET company_name=?, data_json=?, saved_at=? WHERE id=?",
            (company_name, data_json, now, entry_id),
        )
    db.commit()


def list_entries(client_id: str) -> list[dict]:
    """Return all entries for a client, newest first."""
    rows = get_db().execute(
        "SELECT id, entry_name, company_name, saved_at FROM private_entries "
        "WHERE client_id=? ORDER BY saved_at DESC",
        (client_id,),
    ).fetchall()
    return [{"id": r[0], "name": r[1], "company_name": r[2], "saved_at": r[3][:16]} for r in rows]


def load_entry(entry_id: str) -> dict:
    """Load one entry's form data by its id."""
    row = get_db().execute(
        "SELECT data_json FROM private_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0])
    except Exception:
        return {}


def delete_entry(entry_id: str) -> None:
    db = get_db()
    db.execute("DELETE FROM private_entries WHERE id=?", (entry_id,))
    db.commit()


def rename_entry(entry_id: str, new_name: str) -> None:
    db = get_db()
    db.execute("UPDATE private_entries SET entry_name=? WHERE id=?",
               (new_name.strip()[:80], entry_id))
    db.commit()
