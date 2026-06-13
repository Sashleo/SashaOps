"""
routers/auth.py — Login, logout, and first-run setup routes.
"""

import uuid
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import (
    verify_totp, create_session, invalidate_session,
    check_lockout, record_failed_attempt, clear_attempts,
    generate_totp_secret, generate_totp_qr_base64, get_totp_uri,
)
from security.crypto import hash_password, verify_password
from database.connection import get_db
from config.settings import settings

log      = structlog.get_logger()
router   = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_or_create_admin(db) -> Optional[dict]:
    """Get admin user, or return None if none exists yet."""
    row = db.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    return dict(row) if row else None


def _create_admin(db, password: str) -> dict:
    """Create the initial admin user."""
    user_id   = str(uuid.uuid4())
    pwd_hash  = hash_password(password)
    import secrets as s
    master_salt = s.token_bytes(32)
    now = datetime.utcnow().isoformat()

    db.execute(
        """
        INSERT INTO users (id, username, password_hash, totp_secret, totp_enabled,
                           master_salt, created_at)
        VALUES (?, 'admin', ?, NULL, 0, ?, ?)
        """,
        (user_id, pwd_hash, master_salt, now),
    )
    db.commit()
    return _get_or_create_admin(db)


# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # If already logged in, redirect to dashboard
    token   = request.cookies.get("sashaops_session")
    from security.auth import get_session
    session = get_session(token) if token else None
    if session:
        return RedirectResponse(url="/dashboard", status_code=302)

    db    = get_db()
    admin = _get_or_create_admin(db)

    if not admin:
        # First-run: show setup page
        return templates.TemplateResponse("login.html", {
            "request":  request,
            "version":  settings.APP_VERSION,
            "first_run": True,
        })

    return templates.TemplateResponse("login.html", {
        "request": request,
        "version": settings.APP_VERSION,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    totp_code: Optional[str] = Form(None),
):
    ip = request.client.host if request.client else "unknown"

    # Check lockout
    lockout = check_lockout(ip)
    if lockout:
        return templates.TemplateResponse("login.html", {
            "request":         request,
            "version":         settings.APP_VERSION,
            "lockout_seconds": lockout,
        })

    db    = get_db()
    admin = _get_or_create_admin(db)

    # First run: create admin account
    if not admin:
        if len(password) < 8:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "version": settings.APP_VERSION,
                "error":   "Password must be at least 8 characters.",
                "first_run": True,
            })
        admin = _create_admin(db, password)
        log.info("Admin account created on first run")

    # Verify credentials
    if username != "admin" or not verify_password(password, admin["password_hash"]):
        locked = record_failed_attempt(ip)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "version": settings.APP_VERSION,
            "error":   "Invalid username or password." + (" Account locked." if locked else ""),
        })

    # Verify TOTP if enabled
    if admin["totp_enabled"] and admin["totp_secret"]:
        if not totp_code:
            return templates.TemplateResponse("login.html", {
                "request":       request,
                "version":       settings.APP_VERSION,
                "totp_required": True,
            })
        if not verify_totp(admin["totp_secret"], totp_code):
            record_failed_attempt(ip)
            return templates.TemplateResponse("login.html", {
                "request":       request,
                "version":       settings.APP_VERSION,
                "error":         "Invalid 2FA code.",
                "totp_required": True,
            })

    clear_attempts(ip)

    # Update last_login
    db.execute(
        "UPDATE users SET last_login = ? WHERE username = 'admin'",
        (datetime.utcnow().isoformat(),),
    )
    db.commit()

    # Create session with device binding
    session_token = create_session(user_id="admin", request=request)

    log.info("Admin logged in", ip=ip)

    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(
        key="sashaops_session",
        value=session_token,
        httponly=True,
        samesite="strict",
        secure=True,
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
    )
    return resp


# ── Logout ────────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("sashaops_session")
    if token:
        invalidate_session(token)
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("sashaops_session")
    return resp
