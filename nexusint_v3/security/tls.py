"""
security/tls.py — Self-signed TLS certificate generation for SashaOps.

Generates a self-signed RSA-4096 certificate on first startup.
Stored in /app/data/certs/ alongside the encrypted database.

The certificate is valid for 10 years and covers localhost / 127.0.0.1.
Self-signed means browsers will show a security warning — the operator
must add the certificate to their trust store or accept the warning.
This is expected for a locally-deployed private intelligence tool.
"""

import ipaddress
import datetime
import os
import structlog

log = structlog.get_logger()

CERT_DIR  = "/app/data/certs"
CERT_PATH = f"{CERT_DIR}/sashaops.crt"
KEY_PATH  = f"{CERT_DIR}/sashaops.key"


def ensure_tls_cert() -> tuple:
    """
    Generate a self-signed RSA-4096 TLS certificate if not already present.

    Returns:
        (cert_path, key_path) — paths to the PEM-encoded certificate and key

    The certificate covers:
    - CN=sashaops.local
    - SAN: localhost, sashaops.local, 127.0.0.1
    - Validity: 10 years
    - Key: RSA-4096 (classical — TLS uses classical crypto; PQC TLS is not yet
      widely supported in Python HTTPS stacks)
    """
    os.makedirs(CERT_DIR, exist_ok=True)

    if os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH):
        log.info("TLS certificate already present", path=CERT_PATH)
        return CERT_PATH, KEY_PATH

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        log.info("Generating self-signed TLS certificate (RSA-4096)...")

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "sashaops.local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SashaOps"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Private Intelligence"),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(
                datetime.datetime.utcnow() + datetime.timedelta(days=3650)
            )
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.DNSName("sashaops.local"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
            .sign(private_key, hashes.SHA256())
        )

        with open(CERT_PATH, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        with open(KEY_PATH, "wb") as f:
            f.write(
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )

        # Restrict key file permissions (Unix only — no-op on Windows)
        try:
            os.chmod(KEY_PATH, 0o600)
        except Exception:
            pass

        log.info(
            "TLS certificate generated",
            cert=CERT_PATH,
            key=KEY_PATH,
            valid_years=10,
        )
        return CERT_PATH, KEY_PATH

    except Exception as e:
        log.error("TLS certificate generation failed", error=str(e))
        raise RuntimeError(
            f"FATAL: Could not generate TLS certificate — {e}. "
            "Install the cryptography package and ensure /app/data/certs/ is writable."
        )
