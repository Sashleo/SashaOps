"""
reports/builder.py — Report assembly and generation pipeline.

Takes analysis results from the database and assembles them into
PDF and Word reports with post-quantum digital signatures.
"""

import os
import re
import json
from typing import Optional, List
import structlog


from database.connection import get_db
from security.crypto import decrypt_bytes

log = structlog.get_logger()

REPORT_OUTPUT_PATH = "/app/data/reports"


def read_report_file(file_path: str, client_key: bytes) -> Optional[bytes]:
    """
    Read a report file from disk and decrypt it in memory.

    Handles two cases:
    - Encrypted file (.enc suffix): decrypt with client_key
    - Legacy plaintext file (no .enc): serve raw bytes with a log warning

    Returns decrypted bytes, or None if file missing/corrupted.
    """
    if not file_path or not os.path.exists(file_path):
        return None
    with open(file_path, "rb") as f:
        raw = f.read()
    if file_path.endswith(".enc"):
        decrypted = decrypt_bytes(raw, client_key)
        if decrypted is None:
            log.error("Report decryption failed — wrong key or tampered file", path=file_path)
        return decrypted
    # Legacy plaintext file — warn and serve as-is
    log.warning("Serving legacy unencrypted report", path=file_path)
    return raw


def list_reports(client_id: str, limit: int = 50) -> List[dict]:
    """List reports for a client."""
    db = get_db()
    rows = db.execute(
        """
        SELECT id, business_name, report_type, modules_included,
               pdf_path, docx_path, signature_path, signing_algo, created_at
        FROM reports
        WHERE client_id = ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        if d.get("modules_included"):
            try:
                d["modules_included"] = json.loads(d["modules_included"])
            except Exception:
                d["modules_included"] = []
        d["pdf_available"]  = d.get("pdf_path")  is not None
        d["docx_available"] = d.get("docx_path") is not None
        d["signed"]         = d.get("signature_path") is not None
        results.append(d)

    return results


def get_report(report_id: str) -> Optional[dict]:
    """Get a single report record."""
    db  = get_db()
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("modules_included"):
        try:
            d["modules_included"] = json.loads(d["modules_included"])
        except Exception:
            d["modules_included"] = []
    d["pdf_available"]  = d.get("pdf_path")  is not None
    d["docx_available"] = d.get("docx_path") is not None
    d["signed"]         = d.get("signature_path") is not None
    return d


def _slugify(text: str) -> str:
    """Convert a name to a safe filename slug: lowercase, hyphens, max 40 chars."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:40]


