"""
routers/settings.py — Security and account settings routes.
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session, get_session
from database.clients import list_clients, create_client, update_client, delete_client, purge_client
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

    from security.audit import verify_chain_detailed
    chain = verify_chain_detailed(get_db())

    return templates.TemplateResponse(request, "settings.html", {
        "active_page":      "settings",
        "db_encrypted":     sec["sqlcipher"],
        "pqc_available":    sec["pqc_available"],
        "signing_key_exists": sec["signing_key_exists"],
        "fingerprint":      sec["fingerprint"],
        "nist_compliant":   sec["nist_compliant"],
        "version":          settings.APP_VERSION,
        "db_path":          settings.DATABASE_PATH,
        "totp_enabled":     totp_enabled,
        "clients":          list_clients(),
        "chain":            chain,
        "r":                chain,
        **sidebar,
    })


@router.post("/verify-audit", response_class=HTMLResponse)
async def verify_audit(request: Request):
    require_session(request)
    db = get_db()
    from security.audit import verify_chain_detailed
    r = verify_chain_detailed(db)
    return templates.TemplateResponse(request, "partials/audit_report.html", {"r": r})


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
    db.commit()

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


# ── Client management ─────────────────────────────────────────────────────────

def _clients_partial(request: Request) -> HTMLResponse:
    clients = list_clients()
    return templates.TemplateResponse(request, "partials/settings_clients.html", {
        "clients": clients,
    })


@router.get("/clients", response_class=HTMLResponse)
async def clients_partial(request: Request):
    require_session(request)
    return _clients_partial(request)


@router.post("/clients", response_class=HTMLResponse)
async def clients_create(
    request: Request,
    name: str = Form(...),
    industry: str = Form(""),
    accent_color: str = Form("#00ff87"),
):
    require_session(request)
    name = name.strip()
    if not name:
        return HTMLResponse('<p style="color:var(--neg)">Name is required.</p>')
    create_client(name=name, industry=industry.strip() or None, accent_color=accent_color)
    return _clients_partial(request)


@router.post("/clients/{client_id}/update", response_class=HTMLResponse)
async def clients_update(
    request: Request,
    client_id: str,
    name: str = Form(...),
    industry: str = Form(""),
    accent_color: str = Form("#00ff87"),
):
    require_session(request)
    name = name.strip()
    if name:
        update_client(client_id, name=name,
                      industry=industry.strip() or None,
                      accent_color=accent_color)
    return _clients_partial(request)


@router.post("/clients/{client_id}/delete", response_class=HTMLResponse)
async def clients_delete(request: Request, client_id: str):
    require_session(request)
    delete_client(client_id)
    return _clients_partial(request)


@router.post("/clients/{client_id}/purge", response_class=HTMLResponse)
async def clients_purge(request: Request, client_id: str):
    """Hard-delete: removes all client data permanently."""
    require_session(request)
    try:
        purge_client(client_id)
        # Also remove any saved private entries for this client
        from database.private_entries import list_entries, delete_entry
        for e in list_entries(client_id):
            delete_entry(e["id"])
    except Exception as exc:
        log.error("Client purge failed", client_id=client_id, error=str(exc))
        return HTMLResponse(f'<p style="color:var(--neg)">Purge failed: {exc}</p>')
    return _clients_partial(request)


# ── DCF rates panel ────────────────────────────────────────────────────────────

_DCF_ALLOWED_KEYS = frozenset({
    "risk_free_rate", "equity_risk_premium",
    "tax_rate_corporate", "tax_rate_bank", "terminal_growth",
})


def _dcf_rates_partial(request: Request) -> HTMLResponse:
    from intelligence.dcf_rates import get_all_rates_display
    return templates.TemplateResponse(request, "partials/dcf_rates.html", {
        "rates": get_all_rates_display(),
    })


@router.get("/dcf-rates", response_class=HTMLResponse)
async def dcf_rates_partial(request: Request):
    require_session(request)
    return _dcf_rates_partial(request)


@router.post("/dcf-rates/{key}", response_class=HTMLResponse)
async def dcf_rates_set(request: Request, key: str, value: float = Form(...)):
    require_session(request)
    if key not in _DCF_ALLOWED_KEYS:
        return HTMLResponse('<p style="color:var(--neg)">Unknown rate key.</p>', status_code=400)
    from database.dcf_settings import set_override
    set_override(key, round(float(value), 4))
    return _dcf_rates_partial(request)


@router.post("/dcf-rates/{key}/reset", response_class=HTMLResponse)
async def dcf_rates_reset(request: Request, key: str):
    require_session(request)
    if key not in _DCF_ALLOWED_KEYS:
        return HTMLResponse('<p style="color:var(--neg)">Unknown rate key.</p>', status_code=400)
    from database.dcf_settings import reset_override
    reset_override(key)
    return _dcf_rates_partial(request)
