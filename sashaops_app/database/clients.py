"""
database/clients.py — Client CRUD operations.

Each client has a unique per-client salt for key derivation.
Destroying the salt makes the client's data irrecoverable.
"""

import json
import uuid
import secrets
from datetime import datetime
from typing import Optional, List

import structlog

from database.connection import get_db
from security.crypto import generate_salt, encrypt, decrypt

log = structlog.get_logger()


def create_client(
    name: str,
    industry: Optional[str],
    accent_color: str = "#00ff87",
    notes: Optional[str] = None,
    master_key: Optional[bytes] = None,
) -> dict:
    """
    Create a new client record.
    Generates a unique salt + ML-KEM-768/X25519 hybrid keypair for key derivation.
    New clients are created at key_version=2 (quantum-resistant).
    Returns the new client as a dict.
    """
    db          = get_db()
    client_id   = str(uuid.uuid4())
    client_salt = generate_salt()
    now         = datetime.utcnow().isoformat()

    notes_encrypted = None
    if notes and master_key:
        notes_encrypted = encrypt(notes, master_key)

    # Generate ML-KEM-768 + X25519 hybrid keypair
    kem_public_key    = None
    kem_private_enc   = None
    kem_ciphertext_b  = None
    key_version       = 1  # fallback if PQC unavailable

    if master_key:
        try:
            from security.pqc import generate_hybrid_keypair, hybrid_encapsulate, OQS_AVAILABLE
            if OQS_AVAILABLE:
                keypair = generate_hybrid_keypair()
                # Encapsulate immediately — store the ciphertext so decapsulation is possible later
                ciphertext, _ = hybrid_encapsulate(keypair)
                # Encrypt the private key with the master key for storage
                priv_hex    = keypair.kem_private_key.hex()
                kem_private_enc = encrypt(priv_hex, master_key)
                kem_public_key  = keypair.kem_public_key
                kem_ciphertext_b = ciphertext
                key_version = 2
                log.info("ML-KEM-768 keypair generated for client", client_id=client_id)
        except Exception as e:
            log.warning("KEM keypair generation failed — falling back to classical", error=str(e))

    db.execute(
        """
        INSERT INTO clients
        (id, name, industry, accent_color,
         salt, notes_encrypted, created_at, last_accessed, is_active,
         kem_public_key, kem_private_key_enc, kem_ciphertext, key_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            client_id,
            name,
            industry,
            accent_color,
            client_salt,
            notes_encrypted,
            now,
            now,
            kem_public_key,
            kem_private_enc,
            kem_ciphertext_b,
            key_version,
        ),
    )
    db.commit()

    log.info("Client created", client_id=client_id, name=name, key_version=key_version)
    return get_client(client_id)


def get_client(client_id: str) -> Optional[dict]:
    """Get a single client by ID."""
    db  = get_db()
    row = db.execute(
        "SELECT * FROM clients WHERE id = ? AND is_active = 1", (client_id,)
    ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_clients() -> List[dict]:
    """List all active clients, most recently accessed first."""
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM clients WHERE is_active = 1 ORDER BY last_accessed DESC"
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_client(
    client_id: str,
    name: Optional[str] = None,
    industry: Optional[str] = None,
    accent_color: Optional[str] = None,
) -> Optional[dict]:
    """Update mutable client fields."""
    db      = get_db()
    updates = []
    params  = []

    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if industry is not None:
        updates.append("industry = ?")
        params.append(industry)
    if accent_color is not None:
        updates.append("accent_color = ?")
        params.append(accent_color)

    if not updates:
        return get_client(client_id)

    params.append(client_id)
    db.execute(
        f"UPDATE clients SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()
    return get_client(client_id)


def touch_client(client_id: str) -> None:
    """Update last_accessed timestamp when client is opened."""
    db = get_db()
    db.execute(
        "UPDATE clients SET last_accessed = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), client_id),
    )
    db.commit()


def delete_client(client_id: str) -> bool:
    """
    Soft-delete a client (marks is_active = 0).
    The salt is NOT destroyed here — call purge_client() to irrecoverably
    destroy the client's data including their salt.
    """
    db = get_db()
    db.execute(
        "UPDATE clients SET is_active = 0 WHERE id = ?", (client_id,)
    )
    db.commit()
    log.info("Client soft-deleted", client_id=client_id)
    return True


def purge_client(client_id: str) -> bool:
    """
    Permanently delete a client and their salt.
    Destroying the salt makes all encrypted data irrecoverable.
    This is the data-right-to-erasure endpoint.
    """
    db = get_db()
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.execute("DELETE FROM analyses WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM memory_entries WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM documents WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM reports WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM analysis_runs WHERE client_id = ?", (client_id,))
    db.commit()
    log.info("Client purged", client_id=client_id)
    return True


def get_client_salt(client_id: str) -> Optional[bytes]:
    """Get the per-client key derivation salt."""
    db  = get_db()
    row = db.execute(
        "SELECT salt FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    return bytes(row["salt"]) if row else None


def get_client_key_row(client_id: str) -> Optional[dict]:
    """
    Get all key-derivation columns for a client (salt + KEM columns + key_version).
    Used by derive_client_key_v2() to reconstruct the per-client key.
    """
    db  = get_db()
    row = db.execute(
        """
        SELECT id, salt, kem_public_key, kem_private_key_enc,
               kem_ciphertext, key_version
        FROM clients WHERE id = ?
        """,
        (client_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("salt"):
        d["salt"] = bytes(d["salt"])
    return d


def upgrade_client_key_version(client_id: str, master_key: bytes) -> bool:
    """
    Upgrade an existing key_version=1 client to ML-KEM-768 hybrid (key_version=2).
    Generates a new keypair and encapsulates a ciphertext.
    The actual re-encryption of data is handled by the caller when data is next accessed.

    Returns True if upgrade succeeded, False if already at v2 or PQC unavailable.
    """
    db  = get_db()
    row = db.execute(
        "SELECT key_version FROM clients WHERE id = ?", (client_id,)
    ).fetchone()

    if not row or (row["key_version"] or 1) >= 2:
        return False

    try:
        from security.pqc import generate_hybrid_keypair, hybrid_encapsulate, OQS_AVAILABLE
        if not OQS_AVAILABLE:
            return False

        keypair  = generate_hybrid_keypair()
        ciphertext, _ = hybrid_encapsulate(keypair)
        priv_hex = keypair.kem_private_key.hex()
        kem_private_enc = encrypt(priv_hex, master_key)

        db.execute(
            """
            UPDATE clients SET
                kem_public_key = ?, kem_private_key_enc = ?,
                kem_ciphertext = ?, key_version = 2
            WHERE id = ?
            """,
            (keypair.kem_public_key, kem_private_enc, ciphertext, client_id),
        )
        db.commit()
        log.info("Client upgraded to key_version=2", client_id=client_id)
        return True

    except Exception as e:
        log.error("Client key upgrade failed", client_id=client_id, error=str(e))
        return False


def _row_to_dict(row) -> dict:
    d = dict(row)
    # Remove encrypted blob from public dict
    d.pop("notes_encrypted", None)
    d.pop("salt", None)
    return d
