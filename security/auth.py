"""
security/auth.py — Authentication, session management, and middleware.

Provides:
- Login with Argon2id password verification
- TOTP two-factor authentication via pyotp
- DB-backed session store (SQLCipher table, AES-256-GCM encrypted payload)
- Device binding: SHA-256(IP + User-Agent) checked on every request
- Session hijack detection → audit log event
- Login attempt tracking and lockout
- FastAPI middleware for session enforcement
"""

import json
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict

import pyotp
import qrcode
import io
import base64
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from security.crypto import hash_password, verify_password, encrypt, decrypt
from config.settings import settings

log = structlog.get_logger()

# ── Session encryption key (derived from APP_SECRET_KEY via HKDF) ─────────────
# Computed lazily on first use — DB may not be up when this module loads.
_session_key: Optional[bytes] = None

# ── Login attempt tracking ────────────────────────────────────────────────────
_login_attempts: Dict[str, dict] = {}  # ip -> {count, lockout_until}

# ── Public routes (no auth required) ─────────────────────────────────────────
_PUBLIC_EXACT   = {"/", "/login", "/health", "/ready"}
_PUBLIC_PREFIXES = {"/static"}


def _get_session_key() -> bytes:
    """Derive the session payload encryption key from the app secret key."""
    global _session_key
    if _session_key is None:
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"sashaops-session-key-v1",
            backend=default_backend(),
        )
        _session_key = hkdf.derive(settings.APP_SECRET_KEY.encode("utf-8"))
    return _session_key


def _device_hash(ip: str, user_agent: str) -> str:
    """Compute the device binding hash for a request."""
    return hashlib.sha256(f"{ip}:{user_agent}".encode("utf-8")).hexdigest()[:32]


def _log_hijack_attempt(token_hash: str, ip: str) -> None:
    """Write a session hijack attempt to the audit log."""
    try:
        from database.connection import get_db
        from security.audit import log_event
        log_event(
            get_db(),
            event_type="session_hijack_attempt",
            user_id="unknown",
            details={"token_prefix": token_hash[:8], "ip": ip},
            ip_address=ip,
        )
    except Exception:
        pass  # Don't let audit failure block security response


# ── Session management ────────────────────────────────────────────────────────

def create_session(
    user_id: str,
    client_id: Optional[str] = None,
    request: Optional[Request] = None,
) -> str:
    """
    Create a new session token and persist it to the encrypted DB.

    Session payload is AES-256-GCM encrypted before storage.
    Device binding hash (IP + UA) is stored for hijack detection.

    Returns the raw token (stored as a cookie on the client).
    """
    from database.connection import get_db

    token      = secrets.token_urlsafe(48)   # 288-bit random, URL-safe
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now        = datetime.utcnow()
    expires_at = now + timedelta(hours=settings.SESSION_EXPIRE_HOURS)

    # Device binding
    if request:
        ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "unknown")
        ip_ua_hash = _device_hash(ip, ua)
    else:
        ip_ua_hash = "none"

    # Encrypt session payload
    payload = json.dumps({
        "user_id":    user_id,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "last_seen":  now.isoformat(),
    })
    payload_enc = encrypt(payload, _get_session_key())

    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO sessions
        (token_hash, payload_enc, client_id, ip_ua_hash, created_at, expires_at, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token_hash,
            payload_enc,
            client_id,
            ip_ua_hash,
            now.isoformat(),
            expires_at.isoformat(),
            now.isoformat(),
        ),
    )
    db.commit()

    log.info("Session created", user_id=user_id)
    return token


def get_session(token: str, request: Optional[Request] = None) -> Optional[dict]:
    """
    Validate a session token and return session data.

    Performs:
    1. Token hash lookup in sessions table
    2. Expiry check — deletes expired sessions
    3. Device binding check — detects session hijacking
    4. Payload decryption
    5. last_seen update

    Returns None if token is invalid, expired, or hijacked.
    """
    if not token:
        return None

    from database.connection import get_db

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    try:
        db  = get_db()
        row = db.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    except Exception:
        return None

    if not row:
        return None

    # Check expiry
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except Exception:
        return None

    if datetime.utcnow() > expires_at:
        try:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            db.commit()
        except Exception:
            pass
        return None

    # Device binding check — only when request is provided and binding was recorded
    stored_hash = row["ip_ua_hash"]
    if request and stored_hash != "none":
        ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "unknown")
        current_hash = _device_hash(ip, ua)
        if current_hash != stored_hash:
            # Mismatch — possible session hijack
            try:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                db.commit()
            except Exception:
                pass
            _log_hijack_attempt(token_hash, ip)
            log.warning("Session hijack attempt detected", ip=ip, token_prefix=token_hash[:8])
            return None

    # Decrypt payload
    try:
        payload_json = decrypt(bytes(row["payload_enc"]), _get_session_key())
    except Exception:
        return None

    if not payload_json:
        return None

    try:
        session = json.loads(payload_json)
    except Exception:
        return None

    # Merge client_id from its own column (updated independently)
    session["client_id"] = row["client_id"]

    # Update last_seen (non-blocking — ignore failures)
    try:
        db.execute(
            "UPDATE sessions SET last_seen = ? WHERE token_hash = ?",
            (datetime.utcnow().isoformat(), token_hash),
        )
        db.commit()
    except Exception:
        pass

    return session


def invalidate_session(token: str) -> None:
    """Destroy a session (logout)."""
    if not token:
        return
    from database.connection import get_db
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        db = get_db()
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        db.commit()
    except Exception:
        pass


def update_session_client(token: str, client_id: str) -> None:
    """Update the active client for a session."""
    if not token:
        return
    from database.connection import get_db
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        db = get_db()
        db.execute(
            "UPDATE sessions SET client_id = ? WHERE token_hash = ?",
            (client_id, token_hash),
        )
        db.commit()
    except Exception:
        pass


def purge_expired_sessions() -> int:
    """Delete all expired sessions from the DB. Call periodically."""
    from database.connection import get_db
    try:
        db  = get_db()
        cur = db.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (datetime.utcnow().isoformat(),),
        )
        db.commit()
        return cur.rowcount
    except Exception:
        return 0


# ── TOTP ──────────────────────────────────────────────────────────────────────

def generate_totp_secret() -> str:
    """Generate a new TOTP secret key."""
    return pyotp.random_base32()


def get_totp_uri(secret: str) -> str:
    """Get the TOTP URI for QR code generation."""
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name="admin",
        issuer_name="SashaOps",
    )


def generate_totp_qr_base64(secret: str) -> str:
    """
    Generate a QR code image for TOTP setup.
    Returns base64-encoded PNG for embedding in HTML.
    """
    uri = get_totp_uri(secret)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code with 1-step clock tolerance."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


# ── Login attempt tracking ────────────────────────────────────────────────────

def check_lockout(ip: str) -> Optional[int]:
    """
    Check if an IP is locked out.
    Returns remaining lockout seconds, or None if not locked out.
    """
    record = _login_attempts.get(ip)
    if not record:
        return None

    lockout_until = record.get("lockout_until")
    if lockout_until and datetime.utcnow() < datetime.fromisoformat(lockout_until):
        remaining = int((datetime.fromisoformat(lockout_until) - datetime.utcnow()).total_seconds())
        return remaining

    return None


def record_failed_attempt(ip: str) -> bool:
    """
    Record a failed login attempt.
    Returns True if the IP should now be locked out.
    """
    if ip not in _login_attempts:
        _login_attempts[ip] = {"count": 0, "lockout_until": None}

    _login_attempts[ip]["count"] += 1
    count = _login_attempts[ip]["count"]

    log.warning("Failed login attempt", ip=ip, count=count)

    if count >= settings.MAX_LOGIN_ATTEMPTS:
        lockout_until = datetime.utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
        _login_attempts[ip]["lockout_until"] = lockout_until.isoformat()
        _login_attempts[ip]["count"] = 0
        log.warning("IP locked out", ip=ip, until=lockout_until.isoformat())
        return True

    return False


def clear_attempts(ip: str) -> None:
    """Clear failed attempts after successful login."""
    _login_attempts.pop(ip, None)


# ── FastAPI Session Middleware ────────────────────────────────────────────────

class SessionMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces authentication on all non-public routes.
    Attaches session data to request.state for use in route handlers.
    Passes the request to get_session() for device binding validation.
    """

    def __init__(self, app, secret_key: str):
        super().__init__(app)
        self.secret_key = secret_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public routes and static files
        if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            response = await call_next(request)
            _add_security_headers(response)
            return response

        # Get session token from cookie
        token   = request.cookies.get("sashaops_session")
        session = get_session(token, request=request) if token else None

        if not session:
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                return RedirectResponse(url="/login", status_code=302)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        # Attach session to request state
        request.state.session   = session
        request.state.user_id   = session["user_id"]
        request.state.client_id = session.get("client_id")

        response = await call_next(request)
        _add_security_headers(response)
        return response


def _add_security_headers(response) -> None:
    """Add HSTS and security headers to every response."""
    try:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        # M3 — Content-Security-Policy. Inline styles/scripts are used by the HTMX
        # templates so 'unsafe-inline' is required there; everything else is self-only.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        # Never cache dynamic HTML (full pages + HTMX fragments). Without this the
        # browser can serve a stale page after a redeploy/restart, which looks like
        # broken interactivity (e.g. client selection appearing to do nothing).
        # Static assets keep their own caching since they are not text/html.
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"]        = "no-cache"
    except Exception:
        pass


def get_current_session(request: Request) -> Optional[dict]:
    """Helper to get the current session from a request."""
    return getattr(request.state, "session", None)


def require_session(request: Request) -> dict:
    """
    Dependency for FastAPI routes that require authentication.
    Raises HTTP 401 if no valid session found.
    """
    session = get_current_session(request)
    if not session:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Authentication required")
    return session
