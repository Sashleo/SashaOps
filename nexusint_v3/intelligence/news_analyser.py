"""
intelligence/news_analyser.py — AI-powered news analysis per client.

Takes raw feed items and runs them through Gemini to produce a structured
intelligence brief: key themes, threats, opportunities, and business impact.
"""

import json
import re
from typing import Optional
from dataclasses import dataclass, field

import structlog

from intelligence.feeds import FeedResult, FeedItem, get_feed_summary_text
from core.ai_client import run_fast

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


def _parse_json_response(text: str) -> dict:
    """Extract JSON dict from Gemini response, handling code blocks and trailing text."""
    text = (text or "").strip()
    # Strip ```json ... ``` or ``` ... ``` wrappers
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1  # skip ```json or ```
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()
    # Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Regex: find the outermost { ... } block
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group())
    raise json.JSONDecodeError("No JSON object found", text, 0)


def analyse_news_for_business(
    business_name: str,
    region_id: str,
    feed_result: FeedResult,
    industry: Optional[str] = None,
) -> NewsIntelligenceBrief:
    """
    Run AI analysis on collected news headlines for a specific business.

    This identifies which headlines are actually relevant to the business
    and synthesises them into an actionable intelligence brief.
    """
    raw_headlines = [
        item.title for item in (feed_result.items or [])
        if getattr(item, "title", "")
    ][:15]

    brief = NewsIntelligenceBrief(
        business_name=business_name,
        region_id=region_id,
        source_count=len(feed_result.sources_fetched),
        headline_count=feed_result.total_headlines,
        raw_headlines=raw_headlines,
    )

    if not feed_result.items:
        brief.analyst_note = "No news data available for analysis."
        return brief

    # Build news summary for AI
    news_text = get_feed_summary_text(feed_result, max_items=40)

    industry_context = f" in the {industry} industry" if industry else ""
    region_label     = region_id.replace("_", " ").title()

    prompt = f"""
You are a senior business intelligence analyst. Review these {brief.headline_count} headlines
and produce a brief specifically for "{business_name}"{industry_context} operating in {region_label}.

NEWS HEADLINES:
{news_text}

CLASSIFICATION RULES — apply strictly:
- THREAT: Only if the news could directly reduce revenues, increase costs, restrict operations,
  or damage competitive position for THIS specific business or sector. Examples: competitor wins
  major contract, regulation banning their product, currency depreciation if they import inputs,
  raw material price spike. Generic bad news (crime, politics unrelated to sector) is NOT a threat.
- OPPORTUNITY: Only if the news creates a realistic opening for THIS business to grow, enter a
  new market, gain share, or reduce costs. Examples: competitor exits market, new government
  infrastructure spending in their sector, falling input prices, new export corridor opening.
  Generic positive news unrelated to the business is NOT an opportunity.
- Ignore all news that has no plausible direct connection to this specific business.

Respond with ONLY this JSON object (no markdown, no code blocks, no extra text):
{{
    "key_themes": ["theme1", "theme2", "theme3"],
    "top_threats": [
        {{"title": "specific threat to THIS business", "magnitude": 7, "source": "source name", "timeframe": "immediate|1-3 months|3-6 months|6-12 months", "why": "one sentence explaining the direct connection"}}
    ],
    "top_opportunities": [
        {{"title": "specific opportunity for THIS business", "magnitude": 6, "source": "source name", "timeframe": "1-3 months|3-6 months|6-12 months", "why": "one sentence explaining the direct connection"}}
    ],
    "market_sentiment": "Cautious|Negative|Neutral|Positive|Bullish",
    "sentiment_score": -30,
    "critical_developments": ["development1", "development2"],
    "analyst_note": "2-3 sentences explaining what the overall news environment means for this specific business right now"
}}
"""

    try:
        result_text = run_fast(prompt)

        if not result_text:
            brief.analyst_note = "News analysis could not be completed."
            return brief

        parsed = _parse_json_response(result_text)

        brief.key_themes          = parsed.get("key_themes", [])
        brief.top_threats         = parsed.get("top_threats", [])
        brief.top_opportunities   = parsed.get("top_opportunities", [])
        brief.market_sentiment    = parsed.get("market_sentiment", "Neutral")
        brief.sentiment_score     = int(parsed.get("sentiment_score", 0))
        brief.critical_developments = parsed.get("critical_developments", [])
        brief.analyst_note        = parsed.get("analyst_note", "")

    except (json.JSONDecodeError, TypeError, ValueError):
        brief.analyst_note = "News analysis summary unavailable — check Gemini API."
        log.warning("News analyser returned non-JSON", business=business_name)
    except Exception as e:
        brief.analyst_note = f"News analysis incomplete: {str(e)[:120]}"
        log.error("News analysis failed", business=business_name, error=str(e))

    return brief


def build_local_brief(
    business_name: str,
    region_id: str,
    feed_result,
    ticker: str = "",
    industry: str = "",
    sector: str = "",
) -> NewsIntelligenceBrief:
    """
    Build a news intelligence brief *deterministically and locally* — no Gemini.

    Scores every headline with intelligence/news_relevance (entity + sector-driver +
    MiniLM semantic), keeps only company/sector-relevant items, and derives directional
    sentiment from matched driver impacts. This is the LOCAL_ONLY news path and replaces
    the Gemini `analyse_news_for_business` call that was failing on every run.
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
            note += "  (＋ tailwind)"; pos += 1
        elif getattr(it, "impact", None) == -1:
            note += "  (－ headwind)"; neg += 1
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
            lines.append(f"  🔴 [{mag}/10] {title}")

    if brief.top_opportunities:
        lines.append("\nTop Opportunities:")
        for o in brief.top_opportunities[:3]:
            mag = o.get("magnitude", 0) if isinstance(o, dict) else 0
            title = o.get("title", o) if isinstance(o, dict) else str(o)
            lines.append(f"  🟢 [{mag}/10] {title}")

    if brief.analyst_note:
        lines.append(f"\nAnalyst Note: {brief.analyst_note}")

    return "\n".join(lines)
