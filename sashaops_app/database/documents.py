"""
database/documents.py — Document metadata and content storage.

The actual file bytes are stored encrypted on disk.
The database holds metadata + encrypted summary.
"""

import os
import uuid
from datetime import datetime
from typing import Optional, List

import structlog

from database.connection import get_db
from security.crypto import encrypt, decrypt, encrypt_bytes, decrypt_bytes
from config.settings import settings

log = structlog.get_logger()

ALLOWED_EXTENSIONS = {"pdf", "docx", "xlsx", "csv", "txt"}
MAX_FILE_SIZE_MB   = 50


def save_document(
    client_id: str,
    filename: str,
    file_bytes: bytes,
    doc_type: str,
    client_key: bytes,
) -> dict:
    """
    Save an uploaded document. Encrypts the file bytes and stores on disk.
    Metadata goes to the database.
    Returns the document metadata dict.
    """
    db  = get_db()
    doc_id  = str(uuid.uuid4())
    now     = datetime.utcnow().isoformat()

    # Save encrypted file to disk
    client_dir = os.path.join(settings.DOCUMENTS_PATH, client_id)
    os.makedirs(client_dir, exist_ok=True)

    encrypted_bytes  = encrypt_bytes(file_bytes, client_key)
    content_path     = os.path.join(client_dir, f"{doc_id}.enc")

    with open(content_path, "wb") as f:
        f.write(encrypted_bytes)

    db.execute(
        """
        INSERT INTO documents
        (id, client_id, filename, doc_type, file_size, content_path,
         summary_encrypted, upload_at, last_analysed)
        VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)
        """,
        (
            doc_id,
            client_id,
            filename,
            doc_type,
            len(file_bytes),
            content_path,
            now,
        ),
    )
    db.commit()

    log.info(
        "Document saved",
        doc_id=doc_id,
        client_id=client_id,
        filename=filename,
        size=len(file_bytes),
    )
    return get_document_meta(doc_id)


def get_document_bytes(
    doc_id: str,
    client_key: bytes,
    client_id: Optional[str] = None,
) -> Optional[bytes]:
    """
    Read and decrypt a document's bytes from disk.
    When client_id is provided the query is scoped to that client (preferred).
    Returns None on any failure — never raises.
    """
    db = get_db()
    if client_id is not None:
        row = db.execute(
            "SELECT content_path FROM documents WHERE id = ? AND client_id = ?",
            (doc_id, client_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT content_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()

    if not row or not os.path.exists(row["content_path"]):
        return None

    with open(row["content_path"], "rb") as f:
        encrypted = f.read()

    return decrypt_bytes(encrypted, client_key)


def get_document_meta(doc_id: str) -> Optional[dict]:
    """Get document metadata (no file bytes)."""
    db  = get_db()
    row = db.execute(
        """
        SELECT id, client_id, filename, doc_type, file_size,
               upload_at, last_analysed
        FROM documents WHERE id = ?
        """,
        (doc_id,),
    ).fetchone()
    return dict(row) if row else None


def list_documents(client_id: str) -> List[dict]:
    """List all documents for a client (metadata only)."""
    db   = get_db()
    rows = db.execute(
        """
        SELECT id, filename, doc_type, file_size, upload_at, last_analysed
        FROM documents
        WHERE client_id = ?
        ORDER BY upload_at DESC
        """,
        (client_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def save_document_summary(
    doc_id: str,
    summary_text: str,
    client_key: bytes,
) -> None:
    """Save an AI-generated summary for a document."""
    db = get_db()
    encrypted_summary = encrypt(summary_text, client_key)
    db.execute(
        """
        UPDATE documents
        SET summary_encrypted = ?, last_analysed = ?
        WHERE id = ?
        """,
        (encrypted_summary, datetime.utcnow().isoformat(), doc_id),
    )
    db.commit()


def get_document_summary(doc_id: str, client_key: bytes) -> Optional[str]:
    """Retrieve and decrypt a document's summary."""
    db  = get_db()
    row = db.execute(
        "SELECT summary_encrypted FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if not row or not row["summary_encrypted"]:
        return None
    return decrypt(bytes(row["summary_encrypted"]), client_key)


def delete_document(doc_id: str, client_id: Optional[str] = None) -> bool:
    """
    Delete a document from disk and database. If client_id is given, the delete is
    scoped to that client (a client can only delete its own documents).
    """
    db  = get_db()
    if client_id is not None:
        row = db.execute(
            "SELECT content_path FROM documents WHERE id = ? AND client_id = ?",
            (doc_id, client_id),
        ).fetchone()
        if not row:
            return False
    else:
        row = db.execute(
            "SELECT content_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()

    if row:
        try:
            os.remove(row["content_path"])
        except (FileNotFoundError, TypeError):
            pass

    if client_id is not None:
        db.execute("DELETE FROM documents WHERE id = ? AND client_id = ?", (doc_id, client_id))
    else:
        db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    db.commit()
    log.info("Document deleted", doc_id=doc_id, client_id=client_id)
    return True


def delete_documents(client_id: str, doc_ids: Optional[List[str]] = None) -> int:
    """
    Delete documents for a client. If doc_ids is None, deletes ALL of the client's
    documents. Returns the number of documents deleted. Always client-scoped.
    """
    docs = list_documents(client_id)
    target = {d["id"] for d in docs}
    if doc_ids is not None:
        target &= set(doc_ids)
    count = 0
    for did in target:
        if delete_document(did, client_id=client_id):
            count += 1
    return count


def get_document_text(doc_id: str, client_key: bytes, client_id: Optional[str] = None,
                      max_chars: int = 20000) -> str:
    """
    Decrypt a document and extract its text (PDF/DOCX/XLSX/CSV/TXT). Client-scoped
    when client_id is provided. Returns "" on any failure. No data leaves the machine.
    """
    db = get_db()
    if client_id is not None:
        row = db.execute(
            "SELECT doc_type, client_id FROM documents WHERE id = ? AND client_id = ?",
            (doc_id, client_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT doc_type, client_id FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    if not row:
        return ""

    raw = get_document_bytes(doc_id, client_key, client_id=client_id)
    if not raw:
        return ""
    return _extract_document_text(raw, row["doc_type"])[:max_chars]


def _extract_document_text(file_bytes: bytes, doc_type: str) -> str:
    """Extract text from decrypted document bytes. Pure local processing."""
    try:
        if doc_type == "pdf":
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages[:30])
        if doc_type == "docx":
            from docx import Document
            import io
            return "\n".join(p.text for p in Document(io.BytesIO(file_bytes)).paragraphs)
        if doc_type in ("xlsx", "csv"):
            import pandas as pd, io
            df = (pd.read_excel(io.BytesIO(file_bytes), nrows=300) if doc_type == "xlsx"
                  else pd.read_csv(io.BytesIO(file_bytes), nrows=300))
            return df.to_string()
        return file_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("Document text extraction failed", doc_type=doc_type, error=str(e)[:100])
        return ""


def get_documents_text_for_client(client_id: str, client_key: bytes,
                                  doc_ids: Optional[List[str]] = None,
                                  total_cap: int = 50000) -> tuple:
    """
    Concatenate extracted text from a client's documents for analysis injection.
    If doc_ids is given, only those are used; otherwise all of the client's docs.
    Returns (combined_text, [filenames_used]). Local-first: nothing leaves the machine
    here — sanitisation happens later before any AI call.
    """
    docs = list_documents(client_id)
    if doc_ids:
        wanted = set(doc_ids)
        docs = [d for d in docs if d["id"] in wanted]

    parts, names = [], []
    budget = total_cap
    for d in docs:
        if budget <= 0:
            break
        text = get_document_text(d["id"], client_key, client_id=client_id,
                                 max_chars=min(20000, budget))
        if text:
            header = f"\n\n----- DOCUMENT: {d['filename']} -----\n"
            parts.append(header + text)
            names.append(d["filename"])
            budget -= len(text)
    return "".join(parts), names


def detect_doc_type(filename: str) -> str:
    """Detect document type from filename extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"
    return ext if ext in ALLOWED_EXTENSIONS else "unknown"
