"""
intelligence/news_analyser.py - Local, deterministic news intelligence brief.

Builds a structured brief (key themes, threats, opportunities, market sentiment)
from relevance-filtered, FinBERT-scored feed items. No AI, no external calls.
"""

from typing import Optional
from dataclasses import dataclass, field

import structlog

from intelligence.feeds import FeedResult, FeedItem, get_feed_summary_text

log = structlog.get_logger()


@dataclass
class NewsIntelligenceBrief:
    """Structured intelligence brief derived from news analysis."""
    business_name: str
    region_id: str
    key_themes: list = field(default_factory=list)
    top_threats: list = field(default_factory=list)
    top_opportunities: list = field(default_factory=list)
    market_sentiment: str = "Neutral"
    sentiment_score: int = 0     # -100 to +100
    critical_developments: list = field(default_factory=list)
    analyst_note: str = ""
    source_count: int = 0
    headline_count: int = 0
    raw_headlines: list = field(default_factory=list)  # fallback: top N raw feed titles


def build_local_brief(
    business_name: str,
    region_id: str,
    feed_result,
    ticker: str = "",
    industry: str = "",
    sector: str = "",
) -> NewsIntelligenceBrief:
    """
    Build a news intelligence brief deterministically and locally. No AI.

    Scores every headline with intelligence/news_relevance (entity + sector-driver +
    MiniLM semantic), keeps only company/sector-relevant items, and derives directional
    sentiment from matched driver impacts. This is the LOCAL_ONLY news path and replaces
    the former AI news call. This is the only news-brief path now.
    """
    brief = NewsIntelligenceBrief(
        business_name=business_name,
        region_id=region_id,
        source_count=len(getattr(feed_result, "sources_fetched", []) or []),
        headline_count=getattr(feed_result, "total_headlines", 0) or 0,
    )
    items = list(getattr(feed_result, "items", []) or [])
    if not items:
        brief.analyst_note = "No news headlines were collected in this window."
        return brief

    try:
        from intelligence.news_relevance import score_items, is_relevant
        score_items(items, business_name=business_name, ticker=ticker,
                    industry=industry, sector=sector)
    except Exception as e:
        log.warning("build_local_brief: scoring failed", error=str(e)[:100])
        brief.raw_headlines = [it.title for it in items[:10] if it.title]
        brief.analyst_note = "Relevance scoring unavailable; showing recent headlines."
        return brief

    ranked   = sorted(items, key=lambda x: getattr(x, "relevance_score", 0.0), reverse=True)
    relevant = [it for it in ranked if is_relevant(it)]

    pos = neg = 0
    devs: list[str] = []
    for it in relevant[:8]:
        note = it.title
        if getattr(it, "impact", None) == 1:
            note += "  (tailwind)"; pos += 1
        elif getattr(it, "impact", None) == -1:
            note += "  (headwind)"; neg += 1
        devs.append(note)
    brief.critical_developments = devs
    brief.raw_headlines = [it.title for it in ranked[:10] if it.title]

    themes: list[str] = []
    for it in relevant[:15]:
        for t in getattr(it, "relevance_tags", []) or []:
            if t.startswith("driver:"):
                theme = t.split(":", 1)[1]
                if theme not in themes:
                    themes.append(theme)
    brief.key_themes = themes[:6]

    if pos or neg:
        net = pos - neg
        brief.sentiment_score  = max(-100, min(100, int(net / max(pos + neg, 1) * 100)))
        brief.market_sentiment = ("Positive" if brief.sentiment_score > 10
                                  else "Negative" if brief.sentiment_score < -10 else "Neutral")

    subject = business_name or industry or "the subject"
    if relevant:
        brief.analyst_note = (
            f"{len(relevant)} of {len(items)} headlines are relevant to {subject}. "
            + (f"{pos} tailwind / {neg} headwind driver signals detected."
               if (pos or neg) else "No clear directional driver signals in this window.")
        )
    else:
        brief.analyst_note = (
            f"No company- or sector-specific news among {len(items)} headlines this window. "
            "General market headlines shown for context only."
        )
    return brief


def format_brief_for_prompt(brief: NewsIntelligenceBrief) -> str:
    """Format a news intelligence brief for injection into an analysis prompt."""
    lines = [
        f"NEWS INTELLIGENCE BRIEF for {brief.business_name}",
        f"Region: {brief.region_id} | Sources: {brief.source_count} | Headlines: {brief.headline_count}",
        f"Market Sentiment: {brief.market_sentiment} ({brief.sentiment_score:+d}/100)",
        "",
    ]

    if brief.key_themes:
        lines.append(f"Key Themes: {', '.join(brief.key_themes)}")

    if brief.critical_developments:
        lines.append("\nCritical Developments:")
        for dev in brief.critical_developments[:5]:
            lines.append(f"  • {dev}")
    elif brief.raw_headlines:
        lines.append("\nRecent Headlines:")
        for h in brief.raw_headlines[:10]:
            lines.append(f"  • {h}")

    if brief.top_threats:
        lines.append("\nTop Threats:")
        for t in brief.top_threats[:3]:
            mag = t.get("magnitude", 0) if isinstance(t, dict) else 0
            title = t.get("title", t) if isinstance(t, dict) else str(t)
            lines.append(f"  [THREAT {mag}/10] {title}")

    if brief.top_opportunities:
        lines.append("\nTop Opportunities:")
        for o in brief.top_opportunities[:3]:
            mag = o.get("magnitude", 0) if isinstance(o, dict) else 0
            title = o.get("title", o) if isinstance(o, dict) else str(o)
            lines.append(f"  [OPPORTUNITY {mag}/10] {title}")

    if brief.analyst_note:
        lines.append(f"\nAnalyst Note: {brief.analyst_note}")

    return "\n".join(lines)
