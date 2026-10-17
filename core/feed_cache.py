"""
core/feed_cache.py — In-memory RSS feed cache with background refresh.

All regions are refreshed every REFRESH_INTERVAL seconds.
When an analysis run starts, Stage 1 pulls from cache if fresh enough
(< MAX_AGE seconds old) instead of waiting for a live fetch.

Cache survives for the lifetime of the process. On container restart,
it repopulates within one refresh cycle (~15 min) or on first demand.
"""

import asyncio
import time
from typing import Dict, Optional, Tuple
import structlog

from intelligence.feeds import FeedResult, fetch_feeds_for_region, list_region_ids

log = structlog.get_logger()

REFRESH_INTERVAL = 15 * 60   # Refresh every 15 minutes
MAX_AGE          = 30 * 60   # Use cache if younger than 30 minutes
REGION_DELAY     = 3         # Seconds between region fetches to spread load

# Cache: region_id → (FeedResult, unix_timestamp)
_cache: Dict[str, Tuple[FeedResult, float]] = {}


def get_cached_feeds(region_id: str) -> Tuple[Optional[FeedResult], int]:
    """
    Return (FeedResult, age_seconds) if cached and not stale.
    Returns (None, -1) if not cached or too old to use.
    """
    entry = _cache.get(region_id)
    if not entry:
        return None, -1
    result, ts = entry
    age = int(time.time() - ts)
    if age > MAX_AGE:
        return None, age
    return result, age


def set_cached_feeds(region_id: str, result: FeedResult) -> None:
    _cache[region_id] = (result, time.time())


def cache_status() -> Dict[str, dict]:
    """Return cache status for all regions — used by feeds page."""
    now    = time.time()
    status = {}
    for region_id, (result, ts) in _cache.items():
        age = int(now - ts)
        status[region_id] = {
            "headlines":  result.total_headlines,
            "sources_ok": len(result.sources_fetched),
            "age_minutes": age // 60,
            "fresh":       age < MAX_AGE,
        }
    return status


async def _refresh_region(region_id: str) -> None:
    """Fetch feeds for one region and update the cache."""
    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: fetch_feeds_for_region(region_id)
        )
        set_cached_feeds(region_id, result)
        log.info(
            "Feed cache refreshed",
            region=region_id,
            headlines=result.total_headlines,
            sources_ok=len(result.sources_fetched),
            sources_failed=len(result.sources_failed),
        )
    except Exception as e:
        log.warning("Feed cache refresh failed", region=region_id, error=str(e))


async def run_feed_scheduler() -> None:
    """
    Background task: keep all region feeds fresh.

    On startup: fetches each region once with a stagger delay so we
    don't fire 130 HTTP requests simultaneously at boot.
    Then loops every REFRESH_INTERVAL, refreshing each region in turn.
    """
    regions = list_region_ids()
    log.info("Feed scheduler started", regions=len(regions))

    # Initial population — stagger to avoid boot hammering
    for region_id in regions:
        await _refresh_region(region_id)
        await asyncio.sleep(REGION_DELAY)

    log.info("Feed cache populated", regions=len(regions))

    # Continuous refresh loop
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        for region_id in regions:
            await _refresh_region(region_id)
            await asyncio.sleep(REGION_DELAY)
