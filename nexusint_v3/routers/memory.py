"""
routers/memory.py — Memory lab routes.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.clients import list_clients, get_client_salt
from core.memory_engine import (
    get_memory_stats, run_pattern_learning, build_memory_context,
)
from config.settings import settings

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    clients       = list_clients()
    client_id     = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _client_key(client_id: str):
    from security.crypto import derive_client_key, derive_master_key
    salt = get_client_salt(client_id)
    if not salt:
        return None
    master_key = derive_master_key(settings.DB_MASTER_PASSWORD, salt)
    return derive_client_key(master_key, salt)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def memory_page(request: Request):
    require_session(request)
    sidebar       = _sidebar(request)
    active_client = sidebar["active_client"]

    stats    = get_memory_stats(active_client["id"] if active_client else None)
    patterns = []

    from database.connection import get_db
    db = get_db()
    rows = db.execute(
        "SELECT pattern_text, category, confidence, source_count, created_at "
        "FROM learned_patterns ORDER BY confidence DESC, source_count DESC LIMIT 50"
    ).fetchall()
    for row in rows:
        patterns.append({
            "text":         row[0],
            "category":     row[1],
            "confidence":   row[2],
            "source_count": row[3],
            "created_at":   row[4],
        })

    return templates.TemplateResponse("memory.html", {
        "request":     request,
        "active_page": "memory",
        "stats":       stats,
        "patterns":    patterns,
        **sidebar,
    })


@router.post("/learn", response_class=HTMLResponse)
async def trigger_learning(request: Request):
    require_session(request)
    sidebar = _sidebar(request)

    stats = get_memory_stats(None)
    tier2_count = stats.get("tier2_count", 0)
    if tier2_count < 10:
        return HTMLResponse(
            f'<div class="alert alert-warning">Need at least 10 industry entries '
            f'to run pattern learning (currently {tier2_count}). '
            f'Run more analyses first.</div>'
        )

    try:
        new_patterns = run_pattern_learning()
        return HTMLResponse(
            f'<div class="alert alert-success">Pattern learning complete — '
            f'{new_patterns} new patterns extracted.</div>'
        )
    except Exception as e:
        log.error("Pattern learning failed", error=str(e))
        return HTMLResponse(
            f'<div class="alert alert-error">Pattern learning failed: {e}</div>'
        )


@router.post("/clear-client", response_class=HTMLResponse)
async def clear_client_memory(request: Request):
    require_session(request)
    sidebar       = _sidebar(request)
    active_client = sidebar["active_client"]

    if not active_client:
        return HTMLResponse('<div class="alert alert-error">No client selected.</div>')

    from database.connection import get_db
    db = get_db()
    db.execute(
        "DELETE FROM memory_entries WHERE client_id = ?",
        (active_client["id"],)
    )

    log.info("Client memory cleared", client_id=active_client["id"])
    return HTMLResponse(
        f'<div class="alert alert-success">Memory cleared for {active_client["name"]}.</div>'
    )
