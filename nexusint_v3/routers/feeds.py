"""
routers/feeds.py — Live news feed routes.

Shows cached feeds (refreshed every 15 min by the background scheduler).
Falls back to live fetch if cache is empty for the requested region.
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.clients import list_clients
from intelligence.feeds import fetch_feeds_for_region, classify_headlines, FeedResult
from core.feed_cache import get_cached_feeds, set_cached_feeds, cache_status

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    clients       = list_clients()
    client_id     = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def feeds_page(request: Request):
    require_session(request)
    sidebar = _sidebar(request)

    # Pakistan + global context feeds (single cache key)
    region_id = "pakistan"

    cached, age = get_cached_feeds(region_id)
    items        = []
    cache_note   = ""
    last_fetched = ""

    if cached:
        items        = classify_headlines(list(cached.items), business_context="", region_context=region_id)
        age_min      = age // 60
        cache_note   = f"Live feed — refreshed {age_min}m ago"
        last_fetched = datetime.utcnow().strftime("%H:%M UTC")
    else:
        cache_note = "Feeds loading in background — check back in a moment"

    items.sort(key=lambda x: (
        0 if x.classification == "THREAT" else 1 if x.classification == "OPPORTUNITY" else 2,
        -x.magnitude,
    ))

    return templates.TemplateResponse("feeds.html", {
        "request":      request,
        "active_page":  "feeds",
        "items":        items,
        "feed_count":   len(items),
        "cache_note":   cache_note,
        "last_fetched": last_fetched,
        "region_id":    region_id,
        "cache_status": cache_status(),
        **sidebar,
    })


@router.post("/fetch", response_class=HTMLResponse)
async def fetch_feeds(
    request: Request,
    feed_region: str = Form("pakistan"),
):
    require_session(request)
    sidebar = _sidebar(request)
    feed_region = "pakistan"   # single feed set

    # Use cache if available, otherwise live fetch
    cached, age = get_cached_feeds(feed_region)
    if cached:
        result      = cached
        cache_label = f"cached {age // 60}m ago"
    else:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: fetch_feeds_for_region(feed_region)
        )
        set_cached_feeds(feed_region, result)
        cache_label = "live"

    items = classify_headlines(list(result.items), business_context="", region_context=feed_region)
    items.sort(key=lambda x: (
        0 if x.classification == "THREAT" else 1 if x.classification == "OPPORTUNITY" else 2,
        -x.magnitude,
    ))

    last_fetched = datetime.utcnow().strftime("%H:%M UTC")

    html_items = ""
    for item in items:
        cls_class   = f"feed-{item.classification.lower()}"
        badge_class = (
            "badge-red"   if item.classification == "THREAT"      else
            "badge-green" if item.classification == "OPPORTUNITY"  else
            "badge-gray"
        )
        summary_html = (
            f'<div style="font-size:0.82em; color:var(--text-muted); margin:4px 0 6px">'
            f'{item.summary[:200]}</div>'
            if item.summary else ""
        )
        html_items += f"""
        <div class="feed-item {cls_class}"
             data-classification="{item.classification}"
             data-category="{item.category}"
             data-feed-id="{item.feed_id}">
          <div class="feed-item-title">
            <a href="{item.url}" target="_blank" rel="noopener">{item.title}</a>
          </div>
          {summary_html}
          <div class="feed-item-meta">
            <span>{item.feed_label}</span>
            {f'<span>{item.published[:10]}</span>' if item.published else ''}
            <span class="badge {badge_class}">{item.classification}</span>
            {f'<span style="color:var(--text-dim)">{item.magnitude}/10</span>' if item.magnitude > 0 else ''}
            <span style="color:var(--text-dim); margin-left:auto">{cache_label} · {last_fetched}</span>
          </div>
        </div>
        """

    if not items:
        html_items = """
        <div class="empty-state">
          <div class="empty-icon">📡</div>
          <div class="empty-text">No headlines found. The background scheduler will populate this region shortly.</div>
        </div>
        """

    return HTMLResponse(html_items)
