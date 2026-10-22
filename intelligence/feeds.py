"""
intelligence/feeds.py — RSS feed fetching for Pakistani news sources.

Fetches feeds from config/feeds/pakistan.json and returns items newest first.
No classification, no scoring.
"""

import json
import os
import time
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

import feedparser
import structlog


log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Feed definition (replaces config.regions.RSSFeed)
# ---------------------------------------------------------------------------

@dataclass
class RSSFeed:
    id: str
    label: str
    url: str


def _load_feeds() -> list[RSSFeed]:
    path = os.path.join(os.path.dirname(__file__), "..", "config", "feeds", "pakistan.json")
    try:
        with open(path) as f:
            data = json.load(f)
        return [RSSFeed(**entry) for entry in data.get("feeds", [])]
    except Exception as e:
        log.warning("Failed to load feed config", error=str(e)[:100])
        return []


def get_feeds() -> list[RSSFeed]:
    return _load_feeds()


def list_region_ids() -> list[str]:
    return ["pakistan"]


def get_all_feeds_for_region(region_id: str) -> list[RSSFeed]:
    return _load_feeds()

FEED_TIMEOUT_SECONDS = 12
MAX_CONCURRENT_FEEDS = 18   # fetch nearly all feeds in one wave to cut wall-clock time


@dataclass
class FeedItem:
    id: str
    feed_id: str
    feed_label: str
    title: str
    summary: str
    url: str
    published: str


@dataclass
class FeedSourceResult:
    """Per-feed fetch outcome — shown in the Stage 1 briefing card."""
    feed_id: str
    label: str
    status: str           # "ok" | "timeout" | "error"
    headline_count: int
    latency_ms: int
    error_msg: str = ""


@dataclass
class FeedResult:
    items: List[FeedItem] = field(default_factory=list)
    sources_fetched: List[str] = field(default_factory=list)
    sources_failed: List[str] = field(default_factory=list)
    source_details: List[FeedSourceResult] = field(default_factory=list)
    total_headlines: int = 0
    fetch_duration_ms: int = 0


def fetch_feeds_for_region(
    region_id: str,
    max_age_hours: int = 48,
    max_per_feed: int = 20,
) -> FeedResult:
    """
    Fetch all RSS feeds for the given region plus global feeds.
    All feeds run concurrently with a hard 12-second per-feed timeout.
    Slow feeds do not block fast ones.

    Returns:
        FeedResult with all items, per-source timing, and fetch statistics
    """
    result = FeedResult()
    feeds  = get_all_feeds_for_region(region_id)
    start  = time.time()
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    workers = min(len(feeds), MAX_CONCURRENT_FEEDS)
    if not feeds:
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # Submit all feeds concurrently with per-feed timing
        future_to_feed = {
            executor.submit(_fetch_single_feed_timed, f, cutoff, max_per_feed): f
            for f in feeds
        }

        # Wait for all to complete; any not done after timeout + buffer = timed out
        done, not_done = concurrent.futures.wait(
            list(future_to_feed.keys()),
            timeout=FEED_TIMEOUT_SECONDS + 5,
        )

        for future in done:
            feed_def = future_to_feed[future]
            try:
                items, error, latency_ms = future.result()
            except Exception as exc:
                items, error, latency_ms = [], str(exc), FEED_TIMEOUT_SECONDS * 1000

            _record_feed_result(result, feed_def, items, error, latency_ms)

        for future in not_done:
            feed_def = future_to_feed[future]
            future.cancel()
            _record_feed_result(
                result, feed_def, [],
                f"Timed out after {FEED_TIMEOUT_SECONDS}s",
                FEED_TIMEOUT_SECONDS * 1000,
            )

    result.total_headlines   = len(result.items)
    result.fetch_duration_ms = int((time.time() - start) * 1000)

    log.info(
        "Feeds fetched",
        region=region_id,
        total=result.total_headlines,
        sources_ok=len(result.sources_fetched),
        sources_failed=len(result.sources_failed),
        duration_ms=result.fetch_duration_ms,
    )
    return result


def _fetch_single_feed_timed(
    feed_def: RSSFeed,
    cutoff: datetime,
    max_items: int,
) -> Tuple[List[FeedItem], Optional[str], int]:
    """Fetch one feed and return (items, error, latency_ms)."""
    t0 = time.time()
    items, error = _fetch_single_feed(feed_def, cutoff, max_items)
    return items, error, int((time.time() - t0) * 1000)


def _record_feed_result(
    result: FeedResult,
    feed_def: RSSFeed,
    items: List[FeedItem],
    error: Optional[str],
    latency_ms: int,
) -> None:
    """Update FeedResult in-place with one feed's outcome."""
    if error:
        status = "timeout" if "Timed out" in error else "error"
        result.sources_failed.append(feed_def.id)
        log.warning("Feed fetch failed", feed=feed_def.id, error=error[:120])
    else:
        status = "ok"
        result.sources_fetched.append(feed_def.id)
        result.items.extend(items)

    result.source_details.append(FeedSourceResult(
        feed_id=feed_def.id,
        label=feed_def.label,
        status=status,
        headline_count=len(items),
        latency_ms=latency_ms,
        error_msg=error or "",
    ))


def _fetch_single_feed(
    feed_def: RSSFeed,
    cutoff: datetime,
    max_items: int,
) -> Tuple[List[FeedItem], Optional[str]]:
    """
    Fetch one RSS feed. Returns (items, error).
    Never raises — always returns (data_or_empty, error_or_none).
    """
    try:
        parsed = feedparser.parse(
            feed_def.url,
            agent="SashaOps Intelligence/1.0",
            request_headers={"Connection": "close"},
        )

        if parsed.bozo and not parsed.entries:
            return [], f"Parse error: {getattr(parsed, 'bozo_exception', 'unknown')}"

        items = []
        for entry in parsed.entries[:max_items]:
            # Parse publication date
            pub_str = _parse_date(entry)

            # Filter by age if we have a date
            if pub_str:
                try:
                    pub_dt = datetime.fromisoformat(pub_str[:19])
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass

            item = FeedItem(
                id=getattr(entry, "id", "") or getattr(entry, "link", ""),
                feed_id=feed_def.id,
                feed_label=feed_def.label,
                title=_clean(getattr(entry, "title", "")),
                summary=_clean(getattr(entry, "summary", "")[:400]),
                url=getattr(entry, "link", ""),
                published=pub_str or "",
            )
            items.append(item)

        return items, None

    except Exception as e:
        return [], str(e)


def _parse_date(entry) -> Optional[str]:
    """Extract publication date from feed entry."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = datetime(*val[:6])
                return dt.isoformat()
            except Exception:
                pass
    return None


def _clean(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
