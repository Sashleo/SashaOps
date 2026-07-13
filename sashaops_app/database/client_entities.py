"""
database/client_entities.py — Persisted entity registry per client.

Stores entities discovered by the prompt sanitiser during analysis runs.
On subsequent runs, known entities are loaded into the sanitiser before
analysis begins, giving them stable unique placeholders for accurate restore.

This enables incremental accuracy improvement: the more analyses run for a
client, the richer their entity corpus becomes.
"""

from datetime import datetime
from typing import List

import structlog

from database.connection import get_db

log = structlog.get_logger()


def save_discovered_entities(client_id: str, entities: List[dict]) -> int:
    """
    Persist newly discovered entities for a client.
    Silently skips duplicates (INSERT OR IGNORE).

    Args:
        client_id: The client whose analysis discovered these entities
        entities: List of {"entity_value": str, "entity_type": str}

    Returns:
        Number of new entities saved (0 if all were already known)
    """
    if not entities:
        return 0

    db  = get_db()
    now = datetime.utcnow().isoformat()
    saved = 0

    for entity in entities:
        value = entity.get("entity_value", "").strip()
        etype = entity.get("entity_type", "unknown").strip()
        if not value:
            continue
        try:
            db.execute(
                """
                INSERT OR IGNORE INTO client_entities
                (client_id, entity_type, entity_value, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (client_id, etype, value, now),
            )
            saved += 1
        except Exception as e:
            log.warning("Failed to save entity", client_id=client_id, value=value, error=str(e))

    db.commit()
    if saved:
        log.info("Client entities saved", client_id=client_id, count=saved)
    return saved


def load_client_entities(client_id: str) -> List[dict]:
    """
    Load all known entities for a client, ordered by type then value.
    Returns list of {"entity_value": str, "entity_type": str}.
    """
    try:
        db   = get_db()
        rows = db.execute(
            """
            SELECT entity_type, entity_value
            FROM client_entities
            WHERE client_id = ?
            ORDER BY entity_type, entity_value
            """,
            (client_id,),
        ).fetchall()
        return [{"entity_type": row["entity_type"], "entity_value": row["entity_value"]}
                for row in rows]
    except Exception as e:
        log.warning("Failed to load client entities", client_id=client_id, error=str(e))
        return []


def get_entity_count(client_id: str) -> int:
    """Return total number of known entities for a client."""
    try:
        db  = get_db()
        row = db.execute(
            "SELECT COUNT(*) FROM client_entities WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
