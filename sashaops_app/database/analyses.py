"""
database/analyses.py — Analysis results storage and retrieval.

Results are encrypted with the client's key before storage.
Every module result is written immediately on completion (checkpoint pattern).
"""

import json
from datetime import datetime
from typing import Optional, List

import structlog

from database.connection import get_db
from security.crypto import encrypt, decrypt

log = structlog.get_logger()


def save_analysis(
    client_id: str,
    business_name: str,
    module_id: str,
    result_text: str,
    client_key: bytes,
    region_id: Optional[str] = None,   # deprecated, ignored (Pakistan-only)
    model_used: Optional[str] = None,
    tokens_used: Optional[int] = None,
    run_duration_ms: Optional[int] = None,
) -> int:
    """
    Save an analysis result. Encrypts the text with the client's key.
    Returns the new row ID. region_id is accepted for call-site compatibility
    but no longer stored — the platform is Pakistan-only.
    """
    db  = get_db()
    now = datetime.utcnow().isoformat()

    result_encrypted = encrypt(result_text, client_key)

    cursor = db.execute(
        """
        INSERT INTO analyses
        (client_id, business_name, module_id, result_encrypted,
         model_used, tokens_used, run_duration_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            client_id,
            business_name,
            module_id,
            result_encrypted,
            model_used,
            tokens_used,
            run_duration_ms,
            now,
        ),
    )
    db.commit()
    log.info(
        "Analysis saved",
        client_id=client_id,
        module=module_id,
        business=business_name,
    )
    return cursor.lastrowid


def get_analysis(
    client_id: str,
    business_name: str,
    module_id: str,
    client_key: bytes,
) -> Optional[str]:
    """
    Get the most recent analysis for a client/business/module combo.
    Decrypts and returns the text.
    """
    db  = get_db()
    row = db.execute(
        """
        SELECT result_encrypted, created_at
        FROM analyses
        WHERE client_id = ? AND business_name = ? AND module_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (client_id, business_name, module_id),
    ).fetchone()

    if not row:
        return None

    return decrypt(bytes(row["result_encrypted"]), client_key)


def get_analysis_meta(
    client_id: str,
    business_name: str,
    module_id: str,
) -> Optional[dict]:
    """Get metadata for an analysis (without decrypting the result)."""
    db  = get_db()
    row = db.execute(
        """
        SELECT id, module_id, model_used, tokens_used,
               run_duration_ms, created_at
        FROM analyses
        WHERE client_id = ? AND business_name = ? AND module_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (client_id, business_name, module_id),
    ).fetchone()

    return dict(row) if row else None


def list_analyses_for_business(
    client_id: str,
    business_name: str,
) -> List[dict]:
    """List all module analyses for a business (metadata only, no decryption)."""
    db   = get_db()
    rows = db.execute(
        """
        SELECT id, module_id, model_used, tokens_used,
               run_duration_ms, created_at
        FROM analyses
        WHERE client_id = ? AND business_name = ?
        ORDER BY created_at DESC
        """,
        (client_id, business_name),
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_businesses_for_client(client_id: str) -> List[str]:
    """Get distinct business names that have been analysed for a client."""
    db   = get_db()
    rows = db.execute(
        """
        SELECT DISTINCT business_name, MAX(created_at) as last_run
        FROM analyses
        WHERE client_id = ?
        GROUP BY business_name
        ORDER BY last_run DESC
        """,
        (client_id,),
    ).fetchall()
    return [row["business_name"] for row in rows]


def get_all_results_for_run(
    client_id: str,
    business_name: str,
    client_key: bytes,
    module_ids: Optional[List[str]] = None,
) -> dict:
    """
    Decrypt and return all analysis results for a business.
    Returns {module_id: result_text}.
    Used for report generation.
    """
    db = get_db()

    query  = """
        SELECT module_id, result_encrypted, created_at
        FROM analyses
        WHERE client_id = ? AND business_name = ?
    """
    params = [client_id, business_name]

    if module_ids:
        placeholders = ",".join("?" * len(module_ids))
        query  += f" AND module_id IN ({placeholders})"
        params += module_ids

    query += " ORDER BY created_at DESC"

    rows = db.execute(query, params).fetchall()

    # Deduplicate: keep only the most recent result per module
    seen    = {}
    results = {}
    for row in rows:
        mod = row["module_id"]
        if mod not in seen:
            seen[mod] = True
            text = decrypt(bytes(row["result_encrypted"]), client_key)
            if text:
                results[mod] = text

    return results


def delete_analyses_for_business(
    client_id: str,
    business_name: str,
) -> int:
    """Delete all analyses for a specific business. Returns number of rows deleted."""
    db = get_db()
    cursor = db.execute(
        "DELETE FROM analyses WHERE client_id = ? AND business_name = ?",
        (client_id, business_name),
    )
    db.commit()
    return cursor.rowcount
