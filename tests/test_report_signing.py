"""
C1: Reports must be signed. Tests that must fail before the fix and pass after.

1. sign_report + verify_report roundtrip returns True.
2. Tampered bytes fail verification.
3. download_pdf refuses (409) when signature is invalid.
"""

import sys
import types
from unittest.mock import MagicMock, patch


# ── Minimal PQC stub so tests run without liboqs installed ─────────────────────
# conftest.py patches oqs at module level; we need sign_document / verify_signature
# to behave deterministically for these unit tests.

def _make_pqc_stub():
    """Return a security.pqc stub that uses HMAC-SHA256 as a stand-in signature."""
    import hmac, hashlib

    _KEY = b"test-signing-key-32-bytes-padded"

    class KeyPair:
        algorithm = "ML-DSA-87-stub"
        public_key = b"pub"
        private_key = _KEY
        fingerprint = "testfp"

    def generate_signing_keypair():
        return KeyPair()

    def sign_document(doc: bytes, private_key: bytes, algorithm: str) -> bytes:
        return hmac.new(private_key, doc, hashlib.sha256).digest()

    def verify_signature(doc: bytes, sig: bytes, public_key: bytes, algorithm: str) -> bool:
        expected = hmac.new(_KEY, doc, hashlib.sha256).digest()
        return hmac.compare_digest(sig, expected)

    mod = types.ModuleType("security.pqc")
    mod.generate_signing_keypair = generate_signing_keypair
    mod.sign_document = sign_document
    mod.verify_signature = verify_signature
    sys.modules["security.pqc"] = mod
    return mod

_make_pqc_stub()


# ── Signing roundtrip ──────────────────────────────────────────────────────────

def _make_signer_db():
    """Return a mock DB that has one active signing key."""
    _KEY = b"test-signing-key-32-bytes-padded"

    # Simulate encrypt/decrypt as identity for this test
    from unittest.mock import patch
    from reports.signer import sign_report, verify_report

    # Patch encrypt/decrypt to be identity transforms
    with patch("reports.signer.encrypt", side_effect=lambda v, k: v), \
         patch("reports.signer.decrypt", side_effect=lambda v, k: v.hex() if isinstance(v, bytes) else v):
        pass  # just verifying the patches exist; actual patching happens in tests below

    return sign_report, verify_report


def test_sign_then_verify_roundtrip():
    """Signed document must verify successfully."""
    from reports.signer import sign_report, verify_report

    _KEY = b"test-signing-key-32-bytes-padded"
    master_key = b"\x00" * 32

    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "private_key_encrypted": _KEY,
        "algorithm": "ML-DSA-87-stub",
        "fingerprint": "testfp",
        "public_key": b"pub",
    }[k]

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = mock_row

    doc = b"This is a PSX analysis report PDF content."

    with patch("reports.signer.get_db", return_value=mock_db), \
         patch("reports.signer.decrypt", side_effect=lambda v, k: v.hex() if isinstance(v, bytes) else v):
        sig_bytes, err = sign_report(doc, master_key)

    assert err is None, f"sign_report returned error: {err}"
    assert sig_bytes is not None

    mock_db2 = MagicMock()
    mock_db2.execute.return_value.fetchone.return_value = mock_row

    with patch("reports.signer.get_db", return_value=mock_db2):
        valid, msg = verify_report(doc, sig_bytes)

    assert valid is True, f"verify_report returned False: {msg}"


def test_tampered_document_fails_verification():
    """Tampered bytes must not verify."""
    from reports.signer import sign_report, verify_report

    _KEY = b"test-signing-key-32-bytes-padded"
    master_key = b"\x00" * 32

    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "private_key_encrypted": _KEY,
        "algorithm": "ML-DSA-87-stub",
        "fingerprint": "testfp",
        "public_key": b"pub",
    }[k]

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = mock_row

    doc = b"Original report content."
    tampered = b"Tampered report content."

    with patch("reports.signer.get_db", return_value=mock_db), \
         patch("reports.signer.decrypt", side_effect=lambda v, k: v.hex() if isinstance(v, bytes) else v):
        sig_bytes, err = sign_report(doc, master_key)

    assert err is None

    mock_db2 = MagicMock()
    mock_db2.execute.return_value.fetchone.return_value = mock_row

    with patch("reports.signer.get_db", return_value=mock_db2):
        valid, msg = verify_report(tampered, sig_bytes)

    assert valid is False, "Tampered document should NOT verify"


# ── Router refuses tampered file ───────────────────────────────────────────────

def test_download_refuses_on_invalid_signature(monkeypatch):
    """download_pdf must return 409 when signature verification fails, not serve the file."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    # Minimal app with just the reports router
    import routers.reports as rr

    app = FastAPI()
    app.include_router(rr.router, prefix="/reports")

    # Fake session so require_session passes

    def _fake_session(request):
        pass

    monkeypatch.setattr(rr, "require_session", _fake_session)

    # Active client
    monkeypatch.setattr(rr, "_sidebar", lambda _req: {
        "clients": [{"id": "c1", "name": "Test"}],
        "active_client": {"id": "c1", "name": "Test"},
    })

    # Report record with a signature_path
    monkeypatch.setattr(rr, "get_report", lambda rid: {
        "id": rid, "client_id": "c1", "pdf_path": "/fake/report.pdf.enc",
        "docx_path": None, "signature_path": "/fake/report.sig",
        "signed": True,
    })

    # Decryption succeeds
    monkeypatch.setattr(rr, "read_report_file", lambda path, key: b"PDF content")

    # Key derivation succeeds
    monkeypatch.setattr(rr, "_get_client_key", lambda cid: b"\x00" * 32)

    # sig file exists and contains bytes
    import builtins
    real_open = builtins.open

    def _fake_open(path, mode="r", *a, **kw):
        if path == "/fake/report.sig":
            import io
            return io.BytesIO(b"invalidsigbytes")
        return real_open(path, mode, *a, **kw)

    import os
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(builtins, "open", _fake_open)

    # verify_report returns failure
    monkeypatch.setattr(rr, "verify_report", lambda doc, sig: (False, "bad signature"))

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/reports/abc123/pdf")

    # Must refuse with 409, not 200
    assert resp.status_code == 409, (
        f"Expected 409 for invalid signature, got {resp.status_code}. "
        "The router is serving tampered files — fix routers/reports.py"
    )
