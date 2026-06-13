"""
intelligence/feeds.py — RSS feed fetching and threat classification.

Fetches feeds for a region, classifies each headline as:
  THREAT | OPPORTUNITY | NEUTRAL
with a magnitude score 1-10 and a short rationale.
"""

import time
import json
import concurrent.futures
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

import feedparser
import structlog

from config.regions import get_all_feeds_for_region, RSSFeed

log = structlog.get_logger()

FEED_TIMEOUT_SECONDS = 12
MAX_CONCURRENT_FEEDS = 10


@dataclass
class FeedItem:
    id: str
    feed_id: str
    feed_label: str
    title: str
    summary: str
    url: str
    published: str
    category: str          # business | world | tech | politics | social
    bias: str              # independent | government | business
    classification: str = "NEUTRAL"   # THREAT | OPPORTUNITY | NEUTRAL
    magnitude: int = 0     # 1–10 significance for the subject business
    rationale: str = ""


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
                category=feed_def.category,
                bias=feed_def.bias,
            )
            items.append(item)

        return items, None

    except Exception as e:
        return [], str(e)


def classify_headlines(
    items: List[FeedItem],
    business_context: str,
    region_context: str,
) -> List[FeedItem]:
    """
    Classify feed items as THREAT / OPPORTUNITY / NEUTRAL for the given business.
    Uses keyword heuristics — full AI classification is done in news_analyser.py.

    This fast pass assigns magnitude based on keyword signals.
    """
    threat_keywords     = ["crisis", "collapse", "sanction", "ban", "fine", "fraud",
                           "lawsuit", "shutdown", "default", "recession", "inflation",
                           "unrest", "strike", "protest", "shortage", "disruption",
                           "hack", "breach", "recall", "bankruptcy", "layoff"]
    opportunity_keywords = ["growth", "expansion", "investment", "deal", "contract",
                            "award", "launch", "partnership", "funding", "ipo",
                            "merger", "acquisition", "record", "profit", "rebound",
                            "reform", "digitalisation", "infrastructure", "boost"]

    for item in items:
        text = (item.title + " " + item.summary).lower()

        threat_hits       = sum(1 for kw in threat_keywords if kw in text)
        opportunity_hits  = sum(1 for kw in opportunity_keywords if kw in text)

        if threat_hits > opportunity_hits:
            item.classification = "THREAT"
            item.magnitude      = min(10, 3 + threat_hits * 2)
            item.rationale      = f"Negative signals: {threat_hits} threat keywords detected"
        elif opportunity_hits > threat_hits:
            item.classification = "OPPORTUNITY"
            item.magnitude      = min(10, 3 + opportunity_hits * 2)
            item.rationale      = f"Positive signals: {opportunity_hits} opportunity keywords detected"
        else:
            item.classification = "NEUTRAL"
            item.magnitude      = 2

    return items


def get_feed_summary_text(result: FeedResult, max_items: int = 30) -> str:
    """
    Format feed results as a text block for AI prompt injection.
    Sorted by magnitude descending.
    """
    sorted_items = sorted(result.items, key=lambda x: x.magnitude, reverse=True)[:max_items]

    lines = [f"NEWS INTELLIGENCE ({result.total_headlines} total headlines)\n"]
    for item in sorted_items:
        marker = "🔴" if item.classification == "THREAT" else "🟢" if item.classification == "OPPORTUNITY" else "⚪"
        lines.append(
            f"{marker} [{item.feed_label}] {item.title} | {item.published[:10] if item.published else 'recent'}"
        )
        if item.summary:
            lines.append(f"   {item.summary[:200]}")

    return "\n".join(lines)


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
