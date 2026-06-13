"""
config/regions.py — Pakistan-only configuration.

SashaOps is a Pakistan-focused business-intelligence platform. There is exactly one
operating market: Pakistan. Global news/macro feeds are fetched as silent background
context (GLOBAL_CONTEXT_FEEDS) to improve evaluation quality — they are NOT a
selectable region.

Backward-compatible helpers (get_region, get_all_feeds_for_region, list_region_ids,
REGIONS) are retained so existing call sites keep working, but they all resolve to
Pakistan only.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RSSFeed:
    id: str
    label: str
    url: str
    category: str   # world | business | tech | politics | geopolitics
    bias: str       # independent | government | business


@dataclass
class Region:
    id: str
    name: str
    flag: str
    currency: str
    currency_symbol: str
    exchange_id: str
    exchange_name: str
    index_tickers: List[str]
    feeds: List[RSSFeed]
    regulatory_bodies: List[str]
    central_bank: str
    business_registry: str
    social_platforms: List[str]
    prompt_modifier: str
    exchange_url: str = ""
    exchange_data_url: str = ""


# ── Global context feeds (always fetched; NOT a selectable region) ────────────────
# Verified working endpoints only. These provide world-economy / macro context that
# sharpens Pakistani analysis (commodity prices, global rates, sector trends).
GLOBAL_CONTEXT_FEEDS: List[RSSFeed] = [
    RSSFeed("bbc_world",      "BBC World",       "https://feeds.bbci.co.uk/news/world/rss.xml",                          "world",       "independent"),
    RSSFeed("ft_global",      "Financial Times", "https://www.ft.com/rss/home",                                          "business",    "business"),
    RSSFeed("economist",      "The Economist",   "https://www.economist.com/international/rss.xml",                      "business",    "independent"),
    RSSFeed("wsj_markets",    "WSJ Markets",     "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",                        "business",    "business"),
    RSSFeed("aljazeera",      "Al Jazeera",      "https://www.aljazeera.com/xml/rss/all.xml",                            "world",       "independent"),
    RSSFeed("foreignaffairs", "Foreign Affairs", "https://www.foreignaffairs.com/rss.xml",                              "geopolitics", "independent"),
    RSSFeed("cnbc_economy",   "CNBC Economy",    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", "business", "business"),
    RSSFeed("apnews",         "AP News",         "https://feedx.net/rss/ap.xml",                                         "world",       "independent"),
    RSSFeed("techcrunch",     "TechCrunch",      "https://techcrunch.com/feed/",                                         "tech",        "independent"),
]


# ── Pakistan — the single operating market ────────────────────────────────────────
# Pakistan-specific feeds. Verified working endpoints only.
PAKISTAN_FEEDS: List[RSSFeed] = [
    RSSFeed("dawn_pk",      "Dawn",                   "https://www.dawn.com/feeds/home",            "world",    "independent"),
    RSSFeed("dawn_biz_pk",  "Dawn Business",          "https://www.dawn.com/feeds/business",        "business", "independent"),
    RSSFeed("tribune_pk",   "Express Tribune",        "https://tribune.com.pk/feed/business",       "business", "independent"),
    RSSFeed("geo_pk",       "Geo News",               "https://www.geo.tv/rss/1/1",                 "world",    "government"),
    RSSFeed("thenews_pk",   "The News International", "https://www.thenews.com.pk/rss/1/1",         "world",    "independent"),
    RSSFeed("ary_pk",       "ARY News",               "https://arynews.tv/feed/",                   "world",    "independent"),
    RSSFeed("brecorder_pk", "Business Recorder",      "https://www.brecorder.com/feeds/latest-news","business", "business"),
    RSSFeed("propakistani", "ProPakistani",           "https://propakistani.pk/feed/",              "tech",     "independent"),
    RSSFeed("bol_pk",       "BOL News",               "https://www.bolnews.com/feed/",              "world",    "independent"),
]


PAKISTAN = Region(
    id="pakistan",
    name="Pakistan",
    flag="🇵🇰",
    currency="PKR",
    currency_symbol="₨",
    exchange_id="KAR",
    exchange_name="Pakistan Stock Exchange (PSX)",
    index_tickers=[],   # KSE indices come from the PSX scraper, not yfinance
    feeds=PAKISTAN_FEEDS,
    regulatory_bodies=["SECP", "SBP (State Bank of Pakistan)", "FBR", "OGRA", "NEPRA", "PTA", "CCP"],
    central_bank="State Bank of Pakistan (SBP)",
    business_registry="https://www.secp.gov.pk",
    social_platforms=["Twitter/X Pakistan", "Facebook", "WhatsApp", "TikTok Pakistan", "YouTube", "LinkedIn"],
    prompt_modifier=(
        "This analysis is for the Pakistani market. Use PKR for all figures. "
        "Reference SECP regulations, SBP monetary policy, FBR tax framework. "
        "Consider: import dependency on USD, FX controls and SBP directives, "
        "PSX market dynamics, CPEC infrastructure opportunities, "
        "SME sector via SMEDA, IT sector growth and PSEB incentives. "
        "Note informal economy size (~36% of GDP). Reference local competitors "
        "by their PSX ticker where listed. Consider political risk and IMF programme context. "
        "Sector regulators: NEPRA (power), OGRA (oil & gas), PTA (telecom), DRAP (pharma), "
        "SBP (banking), CCP (competition)."
    ),
    exchange_url="https://www.psx.com.pk",
    exchange_data_url="https://www.psx.com.pk/",
)


# ── Backward-compatible single-region registry ────────────────────────────────────
REGIONS: dict[str, Region] = {"pakistan": PAKISTAN}


def get_pakistan() -> Region:
    """Return the Pakistan region (the only operating market)."""
    return PAKISTAN


def get_region(region_id: Optional[str] = None) -> Region:
    """Return Pakistan. region_id is ignored — kept for call-site compatibility."""
    return PAKISTAN


def get_all_feeds() -> List[RSSFeed]:
    """Pakistan feeds + global context feeds."""
    return PAKISTAN.feeds + GLOBAL_CONTEXT_FEEDS


def get_all_feeds_for_region(region_id: Optional[str] = None) -> List[RSSFeed]:
    """Compatibility alias for get_all_feeds()."""
    return get_all_feeds()


def list_region_ids() -> List[str]:
    """Only Pakistan."""
    return ["pakistan"]
