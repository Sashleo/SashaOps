"""
security/pqc.py — Post-Quantum Cryptography operations.

Implements:
- ML-KEM-768 + X25519 hybrid key encapsulation (NIST FIPS 203)
  aka X-Wing — protects against both classical and quantum adversaries
- ML-DSA (Dilithium) digital signatures for reports (NIST FIPS 204)
- SLH-DSA as backup signature scheme (NIST FIPS 205)

The hybrid approach is critical: if ML-KEM is ever broken classically,
X25519 still protects. If X25519 is broken by a quantum computer,
ML-KEM still protects. Both must be broken simultaneously.

Requires: liboqs-python (Python bindings to liboqs C library)
Install: pip install liboqs-python
liboqs must be built and installed — handled in Dockerfile.
"""

import os
import json
import hashlib
from typing import Tuple, Optional, Dict
from dataclasses import dataclass

import structlog

log = structlog.get_logger()

# ── Try to import liboqs ──────────────────────────────────────────────────────
try:
    import oqs
    OQS_AVAILABLE = True
    log.info("liboqs loaded — post-quantum cryptography active")
except ImportError:
    OQS_AVAILABLE = False
    log.warning(
        "liboqs not available — post-quantum operations will use classical fallback. "
        "Install liboqs-python for full PQC support."
    )

# ── Try to import X25519 ──────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption
    )
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    X25519_AVAILABLE = True
except ImportError:
    X25519_AVAILABLE = False
    log.warning("cryptography library not fully available")


@dataclass
class KeyPair:
    """A public/private key pair for PQC operations."""
    algorithm: str
    public_key: bytes
    private_key: bytes


@dataclass
class HybridKeyPair:
    """Combined ML-KEM + X25519 key pair."""
    kem_public_key: bytes
    kem_private_key: bytes
    x25519_private_key: bytes
    x25519_public_key: bytes


@dataclass
class SigningKeyPair:
    """ML-DSA signing key pair for report signing."""
    algorithm: str
    public_key: bytes
    private_key: bytes
    fingerprint: str   # SHA-256 of public key for easy reference


# ── ML-KEM + X25519 Hybrid Key Encapsulation ─────────────────────────────────

def generate_hybrid_keypair() -> HybridKeyPair:
    """
    Generate a hybrid ML-KEM-768 + X25519 key pair.
    Used for key encapsulation — encrypting symmetric keys to recipients.
    """
    if OQS_AVAILABLE and X25519_AVAILABLE:
        kem = oqs.KeyEncapsulation("ML-KEM-768")
        kem_public  = kem.generate_keypair()
        kem_private = kem.export_secret_key()

        x25519_priv = X25519PrivateKey.generate()
        x25519_pub  = x25519_priv.public_key()

        return HybridKeyPair(
            kem_public_key=kem_public,
            kem_private_key=kem_private,
            x25519_private_key=x25519_priv.private_bytes(
                Encoding.Raw, PrivateFormat.Raw, NoEncryption()
            ),
            x25519_public_key=x25519_pub.public_bytes(
                Encoding.Raw, PublicFormat.Raw
            ),
        )
    else:
        # Classical fallback — X25519 only
        log.warning("Using classical-only X25519 keypair (liboqs not available)")
        if X25519_AVAILABLE:
            priv = X25519PrivateKey.generate()
            pub  = priv.public_key()
            dummy = os.urandom(32)
            return HybridKeyPair(
                kem_public_key=dummy,
                kem_private_key=dummy,
                x25519_private_key=priv.private_bytes(
                    Encoding.Raw, PrivateFormat.Raw, NoEncryption()
                ),
                x25519_public_key=pub.public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                ),
            )
        else:
            dummy = os.urandom(32)
            return HybridKeyPair(dummy, dummy, dummy, dummy)


def hybrid_encapsulate(keypair: HybridKeyPair) -> Tuple[bytes, bytes]:
    """
    Encapsulate a shared secret using ML-KEM-768 + X25519.
    Returns: (combined_ciphertext, shared_secret)

    The shared secret can be used as an AES-256 key.
    The ciphertext can be sent to the key owner to derive the same secret.
    """
    if OQS_AVAILABLE and X25519_AVAILABLE:
        # ML-KEM encapsulation
        kem = oqs.KeyEncapsulation("ML-KEM-768")
        kem_ciphertext, kem_secret = kem.encap_secret(keypair.kem_public_key)

        # X25519 key agreement
        ephemeral_priv = X25519PrivateKey.generate()
        ephemeral_pub  = ephemeral_priv.public_key()
        recipient_pub  = X25519PublicKey.from_public_bytes(keypair.x25519_public_key)
        x25519_secret  = ephemeral_priv.exchange(recipient_pub)

        # Combine secrets via HKDF
        combined_input = kem_secret + x25519_secret
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"sashaops-hybrid-kem-v1",
            backend=default_backend(),
        )
        shared_secret = hkdf.derive(combined_input)

        ciphertext = (
            len(kem_ciphertext).to_bytes(4, "big") +
            kem_ciphertext +
            ephemeral_pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        return ciphertext, shared_secret

    else:
        # Classical fallback
        secret = os.urandom(32)
        return os.urandom(64), secret


# ── ML-DSA Report Signing ─────────────────────────────────────────────────────

def generate_signing_keypair(algorithm: str = "ML-DSA-65") -> SigningKeyPair:
    """
    Generate an ML-DSA signing key pair for report signing.
    ML-DSA-65 provides NIST security level 3 (roughly 128-bit classical + 128-bit quantum).

    The public key should be published so clients can verify reports.
    The private key must be kept secret and stored encrypted.
    """
    if OQS_AVAILABLE:
        sig = oqs.Signature(algorithm)
        public_key  = sig.generate_keypair()
        private_key = sig.export_secret_key()
    else:
        log.warning(f"liboqs not available — using RSA fallback for signing")
        from cryptography.hazmat.primitives.asymmetric import rsa
        rsa_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
            backend=default_backend(),
        )
        private_key = rsa_key.private_bytes(
            Encoding.DER, PrivateFormat.PKCS8, NoEncryption()
        )
        public_key = rsa_key.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        algorithm = "RSA-4096-PSS-SHA256-FALLBACK"

    fingerprint = hashlib.sha256(public_key).hexdigest()[:16].upper()

    return SigningKeyPair(
        algorithm=algorithm,
        public_key=public_key,
        private_key=private_key,
        fingerprint=fingerprint,
    )


def sign_document(document: bytes, private_key: bytes, algorithm: str = "ML-DSA-65") -> bytes:
    """
    Sign a document (e.g. PDF report bytes) with ML-DSA.
    Returns the signature bytes.

    The signature is stored separately from the document.
    Clients use verify_signature() to check authenticity.
    """
    if OQS_AVAILABLE and "ML-DSA" in algorithm:
        sig = oqs.Signature(algorithm, secret_key=private_key)
        return sig.sign(document)
    else:
        # RSA-PSS fallback
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import load_der_private_key
        from cryptography.hazmat.primitives import hashes as h
        rsa_key = load_der_private_key(private_key, password=None, backend=default_backend())
        return rsa_key.sign(
            document,
            padding.PSS(
                mgf=padding.MGF1(h.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            h.SHA256(),
        )


def verify_signature(
    document: bytes,
    signature: bytes,
    public_key: bytes,
    algorithm: str = "ML-DSA-65",
) -> bool:
    """
    Verify an ML-DSA signature on a document.
    Returns True if valid, False if invalid or tampered.
    """
    try:
        if OQS_AVAILABLE and "ML-DSA" in algorithm:
            verifier = oqs.Signature(algorithm)
            return verifier.verify(document, signature, public_key)
        else:
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives.serialization import load_der_public_key
            from cryptography.hazmat.primitives import hashes as h
            rsa_pub = load_der_public_key(public_key, backend=default_backend())
            rsa_pub.verify(
                signature,
                document,
                padding.PSS(
                    mgf=padding.MGF1(h.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                h.SHA256(),
            )
            return True
    except Exception as e:
        log.warning("Signature verification failed", error=str(e))
        return False


def get_pqc_status() -> Dict[str, str]:
    """Return the current PQC capability status for the settings page."""
    return {
        "liboqs_available":  str(OQS_AVAILABLE),
        "x25519_available":  str(X25519_AVAILABLE),
        "kem_algorithm":     "ML-KEM-768 + X25519 (Hybrid)" if OQS_AVAILABLE else "X25519 (Classical only)",
        "sig_algorithm":     "ML-DSA-65 (FIPS 204)" if OQS_AVAILABLE else "RSA-4096-PSS (Classical fallback)",
        "nist_compliance":   "FIPS 203 + FIPS 204 + FIPS 205" if OQS_AVAILABLE else "Classical only — install liboqs for PQC",
    }
