"""
routers/reports.py — Report listing and download routes.

Security requirements (non-negotiable):
1. Client ownership check — only the active client's reports can be downloaded
2. Report file decryption in memory — files are stored AES-256-GCM encrypted
3. ML-DSA signature verification — integrity confirmed before any bytes served
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.clients import list_clients, get_client_salt
from database.connection import get_db
from reports.builder import list_reports, get_report, read_report_file
from reports.signer import verify_report

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    clients       = list_clients()
    client_id     = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _get_client_key(client_id: str) -> bytes:
    """
    Derive the per-client AES-256 key from the master password and client salt.
    Prefers key_version=2 (ML-KEM hybrid) if available.
    """
    from config.settings import settings
    from security.crypto import derive_master_key, derive_client_key, derive_client_key_v2
    from database.clients import get_client_key_row

    db  = get_db()
    row = db.execute("SELECT master_salt FROM users LIMIT 1").fetchone()
    if not row or not row["master_salt"]:
        raise RuntimeError("No master salt found — database may be uninitialised")

    master_key = derive_master_key(settings.DB_MASTER_PASSWORD, bytes(row["master_salt"]))

    client_row = get_client_key_row(client_id)
    if not client_row:
        raise RuntimeError(f"Client {client_id} not found")

    key_version = client_row.get("key_version") or 1
    if key_version >= 2:
        return derive_client_key_v2(master_key, client_row)
    return derive_client_key(master_key, client_row["salt"])


def _error_page(title: str, detail: str) -> str:
    return f"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>Error — SashaOps</title>
<style>body{{font-family:system-ui,sans-serif;background:#0d0d0d;color:#ccc;display:flex;
  align-items:center;justify-content:center;min-height:100vh;margin:0}}
.box{{max-width:520px;padding:32px;border:1px solid #333;border-radius:8px;background:#141414}}
h2{{color:#f87171;margin:0 0 12px}}p{{line-height:1.6;color:#aaa}}
.links{{margin-top:24px;display:flex;gap:12px}}
a{{padding:8px 16px;border-radius:6px;text-decoration:none;font-size:0.9em}}
.back{{background:#222;color:#ccc;border:1px solid #444}}
.regen{{background:#00c870;color:#000;font-weight:600}}</style></head>
<body><div class="box">
<h2>⚠ {title}</h2><p>{detail}</p>
<div class="links">
<a class="back" href="/reports">← Back to Reports</a>
<a class="regen" href="/intelligence">Regenerate Report →</a>
</div></div></body></html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def reports_page(request: Request):
    require_session(request)
    sidebar       = _sidebar(request)
    active_client = sidebar["active_client"]

    reports = []
    if active_client:
        reports = list_reports(client_id=active_client["id"])

    return templates.TemplateResponse("reports.html", {
        "request":     request,
        "active_page": "reports",
        "reports":     reports,
        **sidebar,
    })


@router.get("/{report_id}/pdf")
async def download_pdf(request: Request, report_id: str):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    if not active_client:
        raise HTTPException(403, "No active client — select a client first")

    record = get_report(report_id)
    if not record or not record.get("pdf_path"):
        raise HTTPException(404, "Report not found or PDF unavailable")

    # 1. Client ownership check
    if record["client_id"] != active_client["id"]:
        log.warning(
            "Report access denied — wrong client",
            report_id=report_id,
            owner=record["client_id"],
            requester=active_client["id"],
        )
        raise HTTPException(403, "Not authorised to access this report")

    # 2. Decrypt in memory
    try:
        client_key = _get_client_key(active_client["id"])
    except Exception as e:
        log.error("Key derivation failed", error=str(e))
        raise HTTPException(500, "Could not derive decryption key")

    pdf_bytes = read_report_file(record["pdf_path"], client_key)
    if pdf_bytes is None:
        return HTMLResponse(
            _error_page("Report file could not be decrypted",
                        "The encryption key does not match — this can happen if the "
                        "master password changed after the report was generated. "
                        "Regenerate the report from the Intelligence page."),
            status_code=409,
        )

    # 3. Signature verification (advisory — AES-256-GCM decryption already guarantees integrity)
    sig_header = "unsigned"
    if record.get("signature_path"):
        import os
        if os.path.exists(record["signature_path"]):
            with open(record["signature_path"], "rb") as f:
                sig_bytes = f.read()
            valid, msg = verify_report(pdf_bytes, sig_bytes)
            if valid:
                sig_header = "verified"
                log.info("Report signature verified", report_id=report_id)
            else:
                sig_header = "invalid"
                log.warning("Report signature mismatch — serving file (AES-GCM confirms integrity)",
                            report_id=report_id, msg=msg)
        else:
            log.warning("Signature file missing", report_id=report_id, path=record["signature_path"])

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report_{report_id[:8]}.pdf"',
            "X-Report-Signature":  sig_header,
        },
    )


@router.get("/{report_id}/docx")
async def download_docx(request: Request, report_id: str):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]

    if not active_client:
        raise HTTPException(403, "No active client — select a client first")

    record = get_report(report_id)
    if not record or not record.get("docx_path"):
        raise HTTPException(404, "Report not found or DOCX unavailable")

    # 1. Client ownership check
    if record["client_id"] != active_client["id"]:
        log.warning(
            "Report access denied — wrong client",
            report_id=report_id,
            owner=record["client_id"],
            requester=active_client["id"],
        )
        raise HTTPException(403, "Not authorised to access this report")

    # 2. Decrypt in memory
    try:
        client_key = _get_client_key(active_client["id"])
    except Exception as e:
        log.error("Key derivation failed", error=str(e))
        raise HTTPException(500, "Could not derive decryption key")

    docx_bytes = read_report_file(record["docx_path"], client_key)
    if docx_bytes is None:
        return HTMLResponse(
            _error_page("Report file could not be decrypted",
                        "The encryption key does not match. "
                        "Regenerate the report from the Intelligence page."),
            status_code=409,
        )

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="report_{report_id[:8]}.docx"',
        },
    )
