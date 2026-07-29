"""
app.py — SashaOps FastAPI Application Entry Point

Mounts all routers, middleware, static files, and templates.
Runs the first-time setup wizard if no database exists.
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
from fastapi.templating import Jinja2Templates
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
    regulatory,
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
        description="Private Business Intelligence — Built for Emerging Markets",
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
    app.include_router(regulatory.router,       prefix="/regulatory", tags=["regulatory"])
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
        # Start background feed cache — keeps all region feeds live
        asyncio.create_task(run_feed_scheduler())
        log.info("Feed scheduler started")
        # Start the central job scheduler — every scraper, its own cadence (Task 2.5)
        asyncio.create_task(run_scheduler())
        log.info("Job scheduler started")

    # ── Global error handler ──────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback as _tb
        log.error(
            "Unhandled exception",
            path=str(request.url.path),
            error=str(exc),
            traceback=_tb.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred. Check server logs."},
        )

    return app


app = create_app()
