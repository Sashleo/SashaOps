"""
main.py — SashaOps application entry point.

Generates a self-signed TLS certificate on first run, then starts uvicorn
with SSL so all session cookies travel over an encrypted channel.

Run via Docker:
    docker compose up --build

Run locally (for development):
    python main.py
"""

import sys
import uvicorn

from security.tls import ensure_tls_cert


def main():
    # Generate TLS certificate if not already present
    try:
        cert_path, key_path = ensure_tls_cert()
    except RuntimeError as e:
        print(f"\n{e}\n", file=sys.stderr)
        sys.exit(1)

    print(f"SashaOps starting on https://localhost:8000")
    print(f"TLS certificate: {cert_path}")
    print(f"Note: Browser will show a self-signed certificate warning.")
    print(f"      Add the certificate to your trust store to suppress it.")

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path,
        workers=1,
        log_level="info",
        access_log=False,  # Structured logging via structlog
    )


if __name__ == "__main__":
    main()
