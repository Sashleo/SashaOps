"""
security/audit.py — Hash-chained tamper-evident audit log.

Every action is logged with a SHA-256 hash of the previous entry.
If any entry is modified or deleted, the chain breaks and
verify_chain() will detect it.

Events logged:
- login_success, login_failed, logout
- client_created, client_accessed, client_deleted
- analysis_run, document_uploaded, document_deleted
- report_generated, report_delivered
- memory_cleared, patterns_updated
- data_purge_requested, data_purge_completed
- settings_changed, key_rotated
"""

import hashlib
import json
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass

import structlog

log = structlog.get_logger()

# The genesis hash — the "previous hash" of the very first entry
GENESIS_HASH = "0" * 64


@dataclass
class AuditEntry:
    id: int
    event_type: str
    user_id: str
    client_id: Optional[str]
    details: str          # JSON string — specific event details
    ip_address: Optional[str]
    timestamp: str
    prev_hash: str        # Hash of the previous entry
    entry_hash: str       # SHA-256 of this entry (excluding entry_hash itself)


def _compute_entry_hash(
    event_type: str,
    user_id: str,
    client_id: Optional[str],
    details: str,
    ip_address: Optional[str],
    timestamp: str,
    prev_hash: str,
) -> str:
    """Compute the SHA-256 hash for an audit entry."""
    content = json.dumps({
        "event_type":  event_type,
        "user_id":     user_id,
        "client_id":   client_id,
        "details":     details,
        "ip_address":  ip_address,
        "timestamp":   timestamp,
        "prev_hash":   prev_hash,
    }, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def log_event(
    db_conn,
    event_type: str,
    user_id: str,
    client_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Log an audit event to the database.

    Args:
        db_conn: SQLCipher database connection
        event_type: Type of event (see module docstring)
        user_id: The user who performed the action
        client_id: The affected client (if applicable)
        details: Additional details as a dict
        ip_address: The IP address of the request
    """
    try:
        timestamp   = datetime.utcnow().isoformat()
        details_str = json.dumps(details or {})

        # Get the last entry's hash for chaining
        cursor = db_conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        prev_hash = row[0] if row else GENESIS_HASH

        # Compute this entry's hash
        entry_hash = _compute_entry_hash(
            event_type, user_id, client_id,
            details_str, ip_address, timestamp, prev_hash,
        )

        db_conn.execute(
            """
            INSERT INTO audit_log
            (event_type, user_id, client_id, details, ip_address,
             timestamp, prev_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_type, user_id, client_id, details_str,
             ip_address, timestamp, prev_hash, entry_hash),
        )
        db_conn.commit()

        log.info("Audit event logged", event_type=event_type, user_id=user_id)

    except Exception as e:
        log.error("Failed to log audit event", error=str(e), event_type=event_type)


def verify_chain(db_conn) -> tuple[bool, Optional[int]]:
    """
    Verify the integrity of the entire audit log chain.

    Returns: (is_valid, first_broken_id)
    If valid: (True, None)
    If broken: (False, id_of_first_broken_entry)
    """
    cursor = db_conn.execute(
        """
        SELECT id, event_type, user_id, client_id, details,
               ip_address, timestamp, prev_hash, entry_hash
        FROM audit_log ORDER BY id ASC
        """
    )
    rows = cursor.fetchall()

    if not rows:
        return True, None

    expected_prev = GENESIS_HASH

    for row in rows:
        (id_, event_type, user_id, client_id, details,
         ip_address, timestamp, prev_hash, stored_hash) = row

        # Check prev_hash matches expected
        if prev_hash != expected_prev:
            log.error("Audit chain broken", entry_id=id_)
            return False, id_

        # Recompute hash and verify
        computed_hash = _compute_entry_hash(
            event_type, user_id, client_id,
            details, ip_address, timestamp, prev_hash,
        )
        if computed_hash != stored_hash:
            log.error("Audit entry hash mismatch", entry_id=id_)
            return False, id_

        expected_prev = stored_hash

    return True, None


def verify_chain_detailed(db_conn) -> dict:
    """
    Verify the audit chain and return a full evidence report.
    """
    cursor = db_conn.execute(
        """
        SELECT id, event_type, user_id, client_id, details,
               ip_address, timestamp, prev_hash, entry_hash
        FROM audit_log ORDER BY id ASC
        """
    )
    rows = cursor.fetchall()
    total = len(rows)

    if not rows:
        return {
            "valid": True, "total": 0, "checked": 0,
            "broken_id": None, "reason": None,
            "genesis": GENESIS_HASH, "head_hash": None,
            "first_ts": None, "last_ts": None,
            "event_counts": {}, "sample_hashes": [],
        }

    expected_prev = GENESIS_HASH
    broken_id = None
    reason = None
    event_counts: dict[str, int] = {}
    sample_hashes = []
    checked = 0

    for row in rows:
        (id_, event_type, user_id, client_id, details,
         ip_address, timestamp, prev_hash, stored_hash) = row

        event_counts[event_type] = event_counts.get(event_type, 0) + 1

        if prev_hash != expected_prev:
            broken_id = id_
            reason = f"prev_hash mismatch: expected {expected_prev[:16]}..., got {prev_hash[:16]}..."
            break

        computed_hash = _compute_entry_hash(
            event_type, user_id, client_id,
            details, ip_address, timestamp, prev_hash,
        )
        if computed_hash != stored_hash:
            broken_id = id_
            reason = f"entry_hash mismatch: computed {computed_hash[:16]}..., stored {stored_hash[:16]}..."
            break

        checked += 1
        if checked <= 3 or checked == total or checked % max(total // 5, 1) == 0:
            sample_hashes.append({
                "id": id_, "event": event_type,
                "ts": timestamp, "hash": stored_hash[:24] + "...",
            })

        expected_prev = stored_hash

    return {
        "valid": broken_id is None,
        "total": total,
        "checked": checked,
        "broken_id": broken_id,
        "reason": reason,
        "genesis": GENESIS_HASH[:24] + "...",
        "head_hash": (expected_prev[:24] + "...") if broken_id is None else None,
        "first_ts": rows[0][6] if rows else None,
        "last_ts": rows[-1][6] if rows else None,
        "event_counts": event_counts,
        "sample_hashes": sample_hashes,
    }


def get_recent_events(
    db_conn,
    limit: int = 50,
    client_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> List[dict]:
    """
    Retrieve recent audit events, optionally filtered.
    """
    query  = "SELECT * FROM audit_log WHERE 1=1"
    params = []

    if client_id:
        query += " AND client_id = ?"
        params.append(client_id)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cursor = db_conn.execute(query, params)
    cols   = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
