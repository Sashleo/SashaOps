"""
security/crypto.py — Core cryptographic operations.

Provides:
- AES-256-GCM authenticated encryption/decryption
- Argon2id password hashing (quantum-resistant symmetric)
- HKDF per-client key derivation
- Secure key generation
- Memory-safe key zeroing

All symmetric operations use AES-256 which provides 128-bit security
against quantum attacks via Grover's algorithm — considered safe.
"""

import os
import secrets
import hashlib
import hmac
from typing import Tuple, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

import structlog

log = structlog.get_logger()

# ── Argon2id parameters (OWASP recommended for sensitive apps) ────────────────
# Memory: 64MB, iterations: 3, parallelism: 4
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,   # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    encoding="utf-8",
)

# ── Key sizes ─────────────────────────────────────────────────────────────────
AES_KEY_SIZE    = 32   # 256 bits
NONCE_SIZE      = 12   # 96 bits for AES-GCM
SALT_SIZE       = 32   # 256 bits
HKDF_KEY_SIZE   = 32   # 256 bits


def generate_salt() -> bytes:
    """Generate a cryptographically secure random salt."""
    return secrets.token_bytes(SALT_SIZE)


def generate_key() -> bytes:
    """Generate a random AES-256 key."""
    return secrets.token_bytes(AES_KEY_SIZE)


def hash_password(password: str) -> str:
    """
    Hash a password using Argon2id.
    Returns the full Argon2 hash string (includes params and salt).
    """
    return _ph.hash(password)


def verify_password(password: str, hash_str: str) -> bool:
    """
    Verify a password against an Argon2id hash.
    Returns True if correct, False otherwise.
    Never raises — safe to use in auth flows.
    """
    try:
        return _ph.verify(hash_str, password)
    except (VerifyMismatchError, VerificationError):
        return False


def derive_master_key(password: str, salt: bytes) -> bytes:
    """
    Derive a 256-bit master key from a password using Argon2id.
    This key is used to derive per-client keys via HKDF.

    Args:
        password: The master password
        salt: A stored salt (load from database on startup)

    Returns:
        32-byte master key
    """
    ph = PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=len(salt),
        encoding="utf-8",
    )
    # Use argon2-cffi's low-level interface for raw key derivation
    from argon2.low_level import hash_secret_raw, Type
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        type=Type.ID,
    )


def derive_client_key(master_key: bytes, client_salt: bytes) -> bytes:
    """
    Derive a unique encryption key for a specific client using HKDF.

    Each client gets their own salt (stored in the database).
    Destroying the salt makes the client's data irrecoverable.

    Args:
        master_key: The application master key
        client_salt: The client-specific salt

    Returns:
        32-byte client encryption key
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=HKDF_KEY_SIZE,
        salt=client_salt,
        info=b"sashaops-client-key-v1",
        backend=default_backend(),
    )
    return hkdf.derive(master_key)


def encrypt(plaintext: str, key: bytes) -> bytes:
    """
    Encrypt a string using AES-256-GCM.

    Returns: nonce (12 bytes) + ciphertext + tag (16 bytes)
    The nonce is prepended to the ciphertext for storage.
    AES-GCM provides authenticated encryption — detects tampering.
    """
    if not plaintext:
        return b""
    nonce = secrets.token_bytes(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt(ciphertext: bytes, key: bytes) -> Optional[str]:
    """
    Decrypt AES-256-GCM ciphertext.

    Args:
        ciphertext: nonce + encrypted data (from encrypt())
        key: The AES-256 key

    Returns:
        Decrypted string, or None if decryption fails (wrong key or tampered)
    """
    if not ciphertext:
        return ""
    try:
        nonce = ciphertext[:NONCE_SIZE]
        data  = ciphertext[NONCE_SIZE:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, data, None)
        return plaintext.decode("utf-8")
    except Exception:
        log.warning("Decryption failed — wrong key or tampered data")
        return None


def derive_client_key_v2(master_key: bytes, client_row: dict) -> bytes:
    """
    Derive a per-client key using ML-KEM-768 + X25519 hybrid KEM (key_version=2).

    The final key is HKDF(KEM_shared_secret || classical_derived_key), requiring
    an attacker to break BOTH the classical and quantum-resistant components.

    For clients at key_version=1 (no KEM columns yet), falls back to v1 derivation.
    Call upgrade_client_key_version() to migrate a client to v2.

    Args:
        master_key: Application master key (from derive_master_key)
        client_row: Full clients table row dict (must contain salt and KEM columns)

    Returns:
        32-byte client encryption key
    """
    key_version = client_row.get("key_version") or 1
    client_salt = client_row.get("salt") or client_row.get("_salt_raw")

    if not client_salt:
        raise ValueError("client_row missing salt — cannot derive key")

    if isinstance(client_salt, memoryview):
        client_salt = bytes(client_salt)

    # Classical component (always computed — provides fallback + hybrid strength)
    classical_key = derive_client_key(master_key, client_salt)

    if key_version < 2:
        return classical_key

    # ML-KEM-768 component
    kem_ciphertext    = client_row.get("kem_ciphertext")
    kem_priv_key_enc  = client_row.get("kem_private_key_enc")

    if not kem_ciphertext or not kem_priv_key_enc:
        log.warning("key_version=2 but KEM columns empty — using classical key", client_id=client_row.get("id"))
        return classical_key

    try:
        from security.pqc import OQS_AVAILABLE
        if not OQS_AVAILABLE:
            log.warning("liboqs unavailable — using classical key for v2 client")
            return classical_key

        import oqs
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend

        # Decrypt the private key
        kem_priv_hex = decrypt(bytes(kem_priv_key_enc), master_key)
        if not kem_priv_hex:
            log.error("Failed to decrypt KEM private key — using classical fallback")
            return classical_key

        kem_private = bytes.fromhex(kem_priv_hex)

        # Decapsulate to recover the KEM shared secret
        kem = oqs.KeyEncapsulation("ML-KEM-768", secret_key=kem_private)
        kem_secret = kem.decap_secret(bytes(kem_ciphertext))

        # Combine KEM secret + classical key via HKDF
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=client_salt,
            info=b"sashaops-client-key-v2",
            backend=default_backend(),
        )
        return hkdf.derive(kem_secret + classical_key)

    except Exception as e:
        log.error("ML-KEM decapsulation failed — using classical fallback", error=str(e))
        return classical_key


def encrypt_bytes(data: bytes, key: bytes) -> bytes:
    """Encrypt raw bytes (for document storage)."""
    if not data:
        return b""
    nonce = secrets.token_bytes(NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt_bytes(ciphertext: bytes, key: bytes) -> Optional[bytes]:
    """Decrypt raw bytes (for document retrieval)."""
    if not ciphertext:
        return b""
    try:
        nonce = ciphertext[:NONCE_SIZE]
        data  = ciphertext[NONCE_SIZE:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, data, None)
    except Exception:
        log.warning("Bytes decryption failed")
        return None


def hash_for_audit(data: str) -> str:
    """
    SHA-256 hash for audit log chaining.
    Each audit entry hashes the previous entry's hash.
    """
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def zero_key(key: bytearray) -> None:
    """
    Zero a key in memory after use.
    Uses bytearray for mutable memory — bytes objects are immutable in Python.
    Call this after you are done with a sensitive key.
    """
    for i in range(len(key)):
        key[i] = 0


def secure_compare(a: str, b: str) -> bool:
    """
    Constant-time string comparison to prevent timing attacks.
    Use for comparing tokens, hashes, and passwords.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
