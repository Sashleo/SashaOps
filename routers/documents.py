"""
routers/documents.py — Document upload and analysis routes.
"""

from typing import Optional

from fastapi import APIRouter, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.clients import list_clients, get_client_salt
from database.documents import (
    save_document, list_documents, delete_document,
    detect_doc_type,
)
from config.settings import settings

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

# Allowed upload kinds → canonical extension. Detection is by magic bytes only.
_ALLOWED_EXT = {"pdf": ".pdf", "zip": ".docx", "png": ".png", "jpeg": ".jpg"}


def _sniff_magic(content: bytes) -> Optional[str]:
    """Identify the real file type from its leading bytes. None if not allowed.
    Returns 'pdf' | 'zip' (docx/xlsx) | 'png' | 'jpeg'."""
    if not content or len(content) < 4:
        return None
    if content[:4] == b"%PDF":
        return "pdf"
    if content[:2] == b"PK":                       # zip container → docx/xlsx
        return "zip"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if content[:3] == b"\xff\xd8\xff":             # JPEG SOI
        return "jpeg"
    return None


def _safe_filename(raw: str, kind: str) -> str:
    """Strip path separators/control chars, cap length, force an allowlisted extension.
    The original name is only ever a display label, never used as a filesystem path."""
    import os
    import re
    base = os.path.basename(raw or "upload")          # drop any path component
    base = re.sub(r"[\x00-\x1f/\\]", "", base)         # control chars + separators
    base = re.sub(r"\.+", ".", base).strip(". ") or "upload"
    stem = base.rsplit(".", 1)[0][:80] or "upload"
    return f"{stem}{_ALLOWED_EXT.get(kind, '.bin')}"


def _sidebar(request: Request) -> dict:
    clients      = list_clients()
    client_id    = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _get_client_key(client_id: str) -> Optional[bytes]:
    from security.crypto import derive_client_key, derive_master_key
    salt = get_client_salt(client_id)
    if not salt:
        return None
    master_key = derive_master_key(settings.DB_MASTER_PASSWORD, salt)
    return derive_client_key(master_key, salt)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def documents_page(request: Request):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    docs = []
    if active_client:
        docs = list_documents(active_client["id"])

    return templates.TemplateResponse(request, "documents.html", {
        "active_page": "documents",
        "documents":  docs,
        **sidebar,
    })


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    if not active_client:
        return RedirectResponse(url="/documents?error=No+client+selected", status_code=302)

    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        return RedirectResponse(url="/documents?error=File+too+large", status_code=302)

    # M1 — validate real file type by magic bytes (not the client-supplied extension),
    # enforce an extension allowlist that matches, and sanitise the stored filename.
    kind = _sniff_magic(content)
    if kind is None:
        return RedirectResponse(
            url="/documents?error=Unsupported+file+type+(only+PDF,+DOCX,+XLSX,+PNG,+JPEG)",
            status_code=302,
        )
    safe_name = _safe_filename(file.filename or "upload", kind)
    filename = safe_name
    doc_type = detect_doc_type(filename)

    client_key = _get_client_key(active_client["id"])
    if not client_key:
        return RedirectResponse(url="/documents?error=Key+error", status_code=302)

    save_document(
        client_id=active_client["id"],
        filename=filename,
        file_bytes=content,
        doc_type=doc_type,
        client_key=client_key,
    )

    return RedirectResponse(url="/documents", status_code=302)


@router.delete("/{doc_id}", response_class=HTMLResponse)
async def delete_doc(request: Request, doc_id: str):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    if not active_client:
        return HTMLResponse("", status_code=400)
    # Client-scoped: a client can only delete its own documents.
    delete_document(doc_id, client_id=active_client["id"])
    return HTMLResponse("")   # Remove row from table


@router.post("/delete-bulk", response_class=HTMLResponse)
async def delete_bulk(request: Request, doc_ids: str = Form(""), scope: str = Form("selected")):
    """Delete selected documents (comma-separated ids) or ALL of the client's documents."""
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    if not active_client:
        return RedirectResponse(url="/documents", status_code=302)

    from database.documents import delete_documents
    if scope == "all":
        n = delete_documents(active_client["id"], doc_ids=None)
    else:
        ids = [d.strip() for d in doc_ids.split(",") if d.strip()]
        n = delete_documents(active_client["id"], doc_ids=ids) if ids else 0
    log.info("Bulk document delete", client_id=active_client["id"], deleted=n, scope=scope)
    return RedirectResponse(url="/documents", status_code=302)


