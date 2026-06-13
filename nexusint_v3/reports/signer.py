"""
reports/signer.py — ML-DSA digital signature for reports.

Signs PDF and DOCX files on generation.
Stores public key in database for verification.
"""

import hashlib
from typing import Optional, Tuple

import structlog

from database.connection import get_db
from security.pqc import generate_signing_keypair, sign_document, verify_signature
from security.crypto import encrypt, decrypt

log = structlog.get_logger()


def ensure_signing_key(master_key: bytes) -> dict:
    """
    Get or create the application signing key pair.
    The private key is stored encrypted in the database.
    If the stored private key can't be decrypted (stale key from a previous
    incorrect master_key derivation), the old entry is retired and a new
    keypair is generated with the current master_key.
    Returns a dict with algorithm and fingerprint info.
    """
    db = get_db()

    row = db.execute(
        "SELECT * FROM pqc_keys WHERE key_type = 'signing' AND is_active = 1 LIMIT 1"
    ).fetchone()

    if row:
        # Verify the private key is actually decryptable with the current master_key.
        # If not, the key was generated under a wrong master_key derivation — retire it.
        test = decrypt(bytes(row["private_key_encrypted"]), master_key)
        if test is not None:
            return {
                "id":          row["id"],
                "algorithm":   row["algorithm"],
                "fingerprint": row["fingerprint"],
                "public_key":  bytes(row["public_key"]),
            }
        # Stale key — retire and fall through to regenerate
        db.execute("UPDATE pqc_keys SET is_active = 0 WHERE id = ?", (row["id"],))
        db.commit()
        log.warning("Signing key was encrypted with a different master_key — regenerating",
                    old_fingerprint=row["fingerprint"])

    # Generate new signing key pair
    from datetime import datetime
    keypair = generate_signing_keypair()

    private_key_enc = encrypt(keypair.private_key.hex(), master_key)

    db.execute(
        """
        INSERT INTO pqc_keys
        (key_type, algorithm, public_key, private_key_encrypted, fingerprint, created_at, is_active)
        VALUES ('signing', ?, ?, ?, ?, ?, 1)
        """,
        (
            keypair.algorithm,
            keypair.public_key,
            private_key_enc,
            keypair.fingerprint,
            datetime.utcnow().isoformat(),
        ),
    )
    db.commit()

    log.info("Signing key generated", algorithm=keypair.algorithm, fingerprint=keypair.fingerprint)
    return {
        "id":          db.execute("SELECT last_insert_rowid()").fetchone()[0],
        "algorithm":   keypair.algorithm,
        "fingerprint": keypair.fingerprint,
        "public_key":  keypair.public_key,
    }


def sign_report(
    document_bytes: bytes,
    master_key: bytes,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Sign a report document. Returns (signature_bytes, error_or_none).
    """
    try:
        db = get_db()
        row = db.execute(
            "SELECT * FROM pqc_keys WHERE key_type = 'signing' AND is_active = 1 LIMIT 1"
        ).fetchone()

        if not row:
            return None, "No signing key found. Run ensure_signing_key() first."

        # Decrypt private key
        priv_key_hex = decrypt(bytes(row["private_key_encrypted"]), master_key)
        if not priv_key_hex:
            return None, "Failed to decrypt signing key"

        private_key = bytes.fromhex(priv_key_hex)
        signature   = sign_document(document_bytes, private_key, row["algorithm"])

        log.info(
            "Report signed",
            algorithm=row["algorithm"],
            fingerprint=row["fingerprint"],
            doc_size=len(document_bytes),
        )
        return signature, None

    except Exception as e:
        log.error("Report signing failed", error=str(e))
        return None, str(e)


def verify_report(
    document_bytes: bytes,
    signature_bytes: bytes,
) -> Tuple[bool, str]:
    """
    Verify a report's signature.
    Returns (is_valid, message).
    """
    try:
        db = get_db()
        row = db.execute(
            "SELECT * FROM pqc_keys WHERE key_type = 'signing' AND is_active = 1 LIMIT 1"
        ).fetchone()

        if not row:
            return False, "No signing key found in database"

        is_valid = verify_signature(
            document_bytes,
            signature_bytes,
            bytes(row["public_key"]),
            row["algorithm"],
        )

        if is_valid:
            return True, f"Valid — signed with {row['algorithm']} key {row['fingerprint']}"
        return False, "Signature verification FAILED — document may have been tampered with"

    except Exception as e:
        return False, f"Verification error: {str(e)}"


def get_public_key_info() -> Optional[dict]:
    """Return public key info for the settings page."""
    db  = get_db()
    row = db.execute(
        "SELECT algorithm, fingerprint, created_at FROM pqc_keys WHERE key_type = 'signing' AND is_active = 1 LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
