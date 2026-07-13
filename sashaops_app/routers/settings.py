"""
routers/settings.py — Security and account settings routes.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session, get_session
from security.audit import verify_chain
from database.clients import list_clients
from database.connection import get_db
from config.settings import settings

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    clients       = list_clients()
    client_id     = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _security_status() -> dict:
    """Detect which security features are active."""
    sqlcipher = False
    try:
        from database.connection import get_db as gdb
        db = gdb()
        db.execute("PRAGMA cipher_version")
        sqlcipher = True
    except Exception:
        pass

    pqc_available = False
    try:
        import oqs  # noqa: F401
        pqc_available = True
    except ImportError:
        pass

    signing_key_exists = False
    try:
        db  = get_db()
        row = db.execute("SELECT id FROM pqc_keys WHERE key_type = 'signing' LIMIT 1").fetchone()
        signing_key_exists = bool(row)
    except Exception:
        pass

    db = get_db()
    try:
        row = db.execute("SELECT fingerprint FROM app_settings LIMIT 1").fetchone()
        fingerprint = row[0] if row else "not set"
    except Exception:
        fingerprint = "not set"

    return {
        "sqlcipher":          sqlcipher,
        "pqc_available":      pqc_available,
        "signing_key_exists": signing_key_exists,
        "fingerprint":        fingerprint,
        "nist_compliant":     sqlcipher and pqc_available,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def settings_page(request: Request):
    require_session(request)
    sidebar = _sidebar(request)
    session = get_session(request.cookies.get("sashaops_session", ""))

    totp_enabled = bool(session and session.get("totp_enabled")) if session else False
    sec = _security_status()

    return templates.TemplateResponse("settings.html", {
        "request":          request,
        "active_page":      "settings",
        # Flattened security status — matches template variable names
        "db_encrypted":     sec["sqlcipher"],
        "pqc_available":    sec["pqc_available"],
        "signing_key_exists": sec["signing_key_exists"],
        "fingerprint":      sec["fingerprint"],
        "nist_compliant":   sec["nist_compliant"],
        # App info
        "version":   settings.APP_VERSION,
        "db_path":   settings.DATABASE_PATH,
        "totp_enabled": totp_enabled,
        **sidebar,
    })


@router.post("/verify-audit", response_class=HTMLResponse)
async def verify_audit(request: Request):
    require_session(request)
    db = get_db()
    valid, broken_id = verify_chain(db)
    error = f"entry {broken_id}" if broken_id else None
    if valid:
        return HTMLResponse(
            '<div class="alert alert-success">Audit chain verified - all entries intact, no tampering detected.</div>'
        )
    return HTMLResponse(
        f'<div class="alert alert-error">Audit chain integrity check failed at {error}.</div>'
    )


@router.post("/change-password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    require_session(request)

    if new_password != confirm_password:
        return HTMLResponse('<div class="alert alert-error">New passwords do not match.</div>')

    if len(new_password) < 12:
        return HTMLResponse('<div class="alert alert-error">Password must be at least 12 characters.</div>')

    # Verify current password
    from security.crypto import verify_password, hash_password
    db       = get_db()
    session  = get_session(request.cookies.get("sashaops_session", ""))
    username = session.get("username") if session else None

    if not username:
        return HTMLResponse('<div class="alert alert-error">Session error. Please log in again.</div>')

    row = db.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not verify_password(current_password, row[0]):
        return HTMLResponse('<div class="alert alert-error">Current password is incorrect.</div>')

    new_hash = hash_password(new_password)
    db.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, username))

    log.info("Password changed", username=username)
    return HTMLResponse('<div class="alert alert-success">Password updated successfully.</div>')


@router.post("/setup-totp", response_class=HTMLResponse)
async def setup_totp(request: Request):
    require_session(request)

    from security.auth import generate_totp_secret, get_totp_uri
    session  = get_session(request.cookies.get("sashaops_session", ""))
    username = session.get("username") if session else None

    if not username:
        return HTMLResponse('<div class="alert alert-error">Session error.</div>')

    secret  = generate_totp_secret()
    uri     = get_totp_uri(secret)

    # Store pending secret — user must confirm with a code before it activates
    db = get_db()
    db.execute(
        "UPDATE users SET totp_secret_pending = ? WHERE username = ?",
        (secret, username)
    )

    # Generate QR code as data URL
    try:
        import qrcode, base64, io
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64  = base64.b64encode(buf.getvalue()).decode()
        qr_html = f'<img src="data:image/png;base64,{b64}" alt="TOTP QR Code" style="width:180px;height:180px;border:1px solid var(--border);border-radius:8px">'
    except Exception:
        qr_html = f'<div style="font-family:monospace; word-break:break-all; font-size:0.75em; padding:12px; background:var(--surface-hover); border-radius:8px">{secret}</div>'

    return HTMLResponse(f"""
    <div style="margin-top:16px">
      <p style="margin-bottom:12px; color:var(--text-muted)">
        Scan this QR code with your authenticator app, then enter the 6-digit code to confirm.
      </p>
      {qr_html}
      <form hx-post="/settings/confirm-totp" hx-target="#totp-result" hx-swap="innerHTML" style="margin-top:16px; display:flex; gap:8px; align-items:flex-end">
        <div class="form-group" style="margin:0">
          <label class="form-label">Confirm code</label>
          <input class="form-input" type="text" name="totp_code" maxlength="6" pattern="[0-9]{{6}}" placeholder="000000" required autocomplete="off">
        </div>
        <button type="submit" class="btn btn-primary">Activate 2FA</button>
      </form>
      <div id="totp-result"></div>
    </div>
    """)


@router.post("/confirm-totp", response_class=HTMLResponse)
async def confirm_totp(request: Request, totp_code: str = Form(...)):
    require_session(request)

    import pyotp
    session  = get_session(request.cookies.get("sashaops_session", ""))
    username = session.get("username") if session else None

    if not username:
        return HTMLResponse('<div class="alert alert-error">Session error.</div>')

    db  = get_db()
    row = db.execute("SELECT totp_secret_pending FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not row[0]:
        return HTMLResponse('<div class="alert alert-error">No pending TOTP setup.</div>')

    secret = row[0]
    totp   = pyotp.TOTP(secret)

    if not totp.verify(totp_code, valid_window=1):
        return HTMLResponse('<div class="alert alert-error">Invalid code. Try again.</div>')

    db.execute(
        "UPDATE users SET totp_secret = ?, totp_secret_pending = NULL, totp_enabled = 1 WHERE username = ?",
        (secret, username)
    )
    log.info("TOTP activated", username=username)
    return HTMLResponse('<div class="alert alert-success">Two-factor authentication activated.</div>')


@router.post("/disable-totp", response_class=HTMLResponse)
async def disable_totp(
    request: Request,
    totp_code: str = Form(...),
):
    require_session(request)

    import pyotp
    session  = get_session(request.cookies.get("sashaops_session", ""))
    username = session.get("username") if session else None

    if not username:
        return HTMLResponse('<div class="alert alert-error">Session error.</div>')

    db  = get_db()
    row = db.execute("SELECT totp_secret FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not row[0]:
        return HTMLResponse('<div class="alert alert-error">2FA is not enabled.</div>')

    totp = pyotp.TOTP(row[0])
    if not totp.verify(totp_code, valid_window=1):
        return HTMLResponse('<div class="alert alert-error">Invalid code.</div>')

    db.execute(
        "UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE username = ?",
        (username,)
    )
    log.info("TOTP disabled", username=username)
    return HTMLResponse('<div class="alert alert-success">Two-factor authentication disabled.</div>')
