"""
routers/newsfeed.py — Newsfeed page showing RSS headlines from Pakistani sources.

Headlines are fetched from the feed cache (background-refreshed every 15 min)
and classified using keyword heuristics. No AI, no external API calls.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session

log = structlog.get_logger()
router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    from database.clients import list_clients
    session = getattr(request.state, "session", {}) or {}
    clients = list_clients()
    cid = session.get("client_id")
    return {"clients": clients,
            "active_client": next((c for c in clients if c["id"] == cid), None)}


def _load_headlines(source: str = "") -> dict:
    from core.feed_cache import get_cached_feeds, cache_status
    from intelligence.feeds import get_feeds

    feeds = get_feeds()
    sources = [{"id": f.id, "label": f.label} for f in feeds]

    cached, age = get_cached_feeds("pakistan")
    items = []
    if cached:
        items = list(cached.items)

    if source:
        items = [i for i in items if i.feed_id == source]

    seen = set()
    deduped = []
    for item in items:
        key = item.title.strip().lower()[:80]
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    deduped.sort(key=lambda x: x.published or "", reverse=True)

    status = cache_status()
    pk = status.get("pakistan", {})

    return {
        "items": deduped[:100],
        "sources": sources,
        "selected_source": source,
        "cache_age_min": pk.get("age_minutes"),
        "cache_fresh": pk.get("fresh", False),
        "total_sources": len(feeds),
        "sources_ok": pk.get("sources_ok", 0),
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def page(request: Request, source: str = ""):
    require_session(request)
    data = _load_headlines(source)
    return templates.TemplateResponse("newsfeed.html", {
        "request": request,
        "active_page": "newsfeed",
        **data,
        **_sidebar(request),
    })
