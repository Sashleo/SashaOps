"""
app.py — SashaOps FastAPI application entry point.

Mounts routers, middleware, static files and templates.
"""

import asyncio
import sys

# numpy.rec is importable as an attribute (numpy.rec) but not as a subpackage
# (import numpy.rec) in some numpy builds. Register it in sys.modules so any
# package that does `import numpy.rec` finds it without ImportError.
try:
    import numpy as _np
    if "numpy.rec" not in sys.modules:
        sys.modules["numpy.rec"] = _np.rec
except Exception:
    pass

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import JSONResponse
import structlog

from config.settings import settings
from database.connection import init_database
from database.schema import run_migrations
from security.auth import SessionMiddleware
from core.feed_cache import run_feed_scheduler
from core.scheduler import run_scheduler

# ── Routers ───────────────────────────────────────────────────────────────────
from routers import (
    auth,
    dashboard,
    psx,
    private,
    newsfeed,
    documents,
    reports,
    settings as settings_router,
    debug as debug_router,
)

log = structlog.get_logger()

# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="SashaOps",
        description="Financial Analyst Workbook for the Pakistani Market",
        version=settings.APP_VERSION,
        docs_url=None,   # Disable public docs in production
        redoc_url=None,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    # HTTPS redirect — any plain-HTTP request is redirected to HTTPS.
    # Uvicorn is started with SSL, so in normal operation all traffic is already
    # HTTPS. This middleware handles any edge case where HTTP slips through.
    app.add_middleware(HTTPSRedirectMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=settings.APP_SECRET_KEY)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://localhost:8000", "https://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Static files ──────────────────────────────────────────────────────────
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(auth.router,             prefix="",           tags=["auth"])
    app.include_router(dashboard.router,        prefix="/dashboard",  tags=["dashboard"])
    app.include_router(psx.router,              prefix="/psx",        tags=["psx"])
    app.include_router(private.router,          prefix="/private",    tags=["private"])
    app.include_router(newsfeed.router,         prefix="/newsfeed",   tags=["newsfeed"])
    app.include_router(documents.router,        prefix="/documents",  tags=["documents"])
    app.include_router(reports.router,          prefix="/reports",    tags=["reports"])
    app.include_router(settings_router.router,  prefix="/settings",   tags=["settings"])
    app.include_router(debug_router.router,     prefix="/debug",      tags=["debug"])

    # ── Health & readiness ──────────────────────────────────────────────────────
    @app.get("/health")
    async def health():
        # Liveness — process is up.
        return {"status": "ok", "version": settings.APP_VERSION}

    @app.get("/ready")
    async def ready():
        # Readiness — the encrypted DB actually answers a query (L3).
        try:
            from database.connection import get_db
            get_db().execute("SELECT 1")
            return {"status": "ready"}
        except Exception as e:
            return JSONResponse({"status": "not-ready", "error": str(e)[:120]}, status_code=503)

    # ── Startup ───────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        log.info("SashaOps starting up", version=settings.APP_VERSION)
        init_database()
        run_migrations()
        log.info("Database initialised")
        try:
            from security.crypto import derive_master_key
            from reports.signer import ensure_signing_key
            from database.connection import get_db
            db = get_db()
            row = db.execute("SELECT master_salt FROM users LIMIT 1").fetchone()
            if row and row["master_salt"]:
                mk = derive_master_key(settings.DB_MASTER_PASSWORD, bytes(row["master_salt"]))
                info = ensure_signing_key(mk)
                log.info("Signing key ready", fingerprint=info["fingerprint"])
        except Exception as e:
            log.warning("Could not initialise signing key", error=str(e)[:120])
        from security.selftest import run_self_test
        failures = run_self_test()
        if failures:
            log.warning("Security self-test failures", count=len(failures))
        # Start background feed cache
        asyncio.create_task(run_feed_scheduler())
        log.info("Feed scheduler started")
        # Start the central job scheduler
        asyncio.create_task(run_scheduler())
        log.info("Job scheduler started")

    # ── Global error handler ──────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback as _tb, uuid as _uuid
        from fastapi.responses import HTMLResponse as _HTML
        rid = _uuid.uuid4().hex[:12]
        log.error(
            "Unhandled exception",
            request_id=rid,
            path=str(request.url.path),
            error=str(exc),
            traceback=_tb.format_exc(),
        )
        accept = request.headers.get("accept", "")
        is_htmx = "HX-Request" in request.headers
        if "text/html" in accept and not is_htmx:
            return _HTML(
                status_code=500,
                content=f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error — SashaOps</title>
<link rel="stylesheet" href="/static/css/tokens.css">
<link rel="stylesheet" href="/static/css/base.css">
</head><body style="max-width:600px;margin:5rem auto;padding:0 1rem">
<h2 style="color:var(--neg)">Something went wrong</h2>
<p>An unexpected error occurred. Reference ID: <code style="font-family:var(--font-mono);background:var(--surface-2);padding:2px 6px;border-radius:4px">{rid}</code></p>
<p style="color:var(--text-muted);font-size:0.9rem">Find the full traceback in the server logs by searching for <code>request_id={rid}</code>.</p>
<p style="margin-top:2rem"><a href="/" style="color:var(--accent)">Return to dashboard</a></p>
</body></html>""",
            )
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred.", "request_id": rid},
        )

    return app


app = create_app()
