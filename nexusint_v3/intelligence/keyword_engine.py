"""
intelligence/keyword_engine.py — Pure-Python industry detection + keyword extraction.

No AI, no external calls. Given company text (website / PSX profile) and the PSX
sector name, detect the industry and surface the most relevant keywords. The detected
industry drives which government/association scrapers run and which query templates fire.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List

# ── Industry taxonomy: industry_key → signal terms ───────────────────────────────
INDUSTRY_TAXONOMY: dict[str, List[str]] = {
    "cement":     ["cement", "clinker", "aggregate", "rmc", "ready mix", "opc", "grey cement"],
    "textile":    ["textile", "fabric", "yarn", "knitting", "garment", "apparel", "weaving", "spinning", "denim"],
    "energy":     ["power", "electricity", "generation", "thermal", "renewable", "nepra", "wapda", "ipp", "grid", "megawatt"],
    "banking":    ["bank", "deposit", "lending", "credit", "microfinance", "nbfc", "branch banking", "islamic banking"],
    "telecom":    ["telecom", "broadband", "mobile", "spectrum", "pta", "4g", "5g", "lte", "cellular", "fiber"],
    "pharma":     ["pharmaceutical", "medicine", "drug", "healthcare", "drap", "api", "formulation", "tablet", "vaccine"],
    "fmcg":       ["consumer goods", "fmcg", "food", "beverage", "dairy", "personal care", "snack", "confectionery"],
    "oil_gas":    ["petroleum", "oil", "gas", "exploration", "ogra", "refinery", "pipeline", "lng", "lubricant", "fuel"],
    "tech":       ["software", "it services", "pseb", "saas", "digital", "fintech", "bpo", "call centre", "cloud", "platform"],
    "steel":      ["steel", "iron", "alloy", "rolling mill", "rebar", "billet", "galvanized"],
    "sugar":      ["sugar", "cane", "molasses", "distillery", "ethanol", "crushing"],
    "fertilizer": ["fertilizer", "urea", "npk", "dap", "agriculture input", "crop nutrient", "ammonia"],
    "auto":       ["automotive", "vehicle", "car", "assembler", "motorcycle", "tractor", "oem", "auto parts"],
    "chemicals":  ["chemical", "pvc", "polymer", "resin", "pigment", "chlor-alkali", "caustic soda", "paint", "coating"],
}

# ── Maps industry → relevant association scraper IDs (gov_scrapers) ───────────────
INDUSTRY_SCRAPERS: dict[str, List[str]] = {
    "cement":  ["assoc_apcma"],
    "textile": ["assoc_aptma"],
    "auto":    ["assoc_pama"],
    "sugar":   ["assoc_psma"],
    "pharma":  ["assoc_ppma"],
}

# ── PSX sector name → industry key (PSX sectorName is authoritative) ─────────────
_PSX_SECTOR_HINTS: dict[str, str] = {
    "cement": "cement",
    "textile": "textile", "textile composite": "textile", "textile spinning": "textile",
    "textile weaving": "textile", "woollen": "textile", "synthetic": "textile",
    "power generation": "energy", "power": "energy",
    "commercial banks": "banking", "modarabas": "banking", "leasing": "banking",
    "investment banks": "banking", "microfinance": "banking",
    "technology": "tech", "technology & communication": "tech",
    "pharmaceuticals": "pharma",
    "food": "fmcg", "food & personal care": "fmcg", "tobacco": "fmcg",
    "oil & gas exploration": "oil_gas", "oil & gas marketing": "oil_gas",
    "refinery": "oil_gas",
    "engineering": "steel", "steel": "steel",
    "sugar": "sugar", "sugar & allied": "sugar",
    "fertilizer": "fertilizer",
    "automobile assembler": "auto", "automobile parts": "auto", "auto": "auto",
    "chemical": "chemicals", "paper & board": "chemicals",
}

_STOPWORDS = {
    "the", "and", "for", "with", "our", "are", "this", "that", "from", "has", "have",
    "you", "your", "all", "will", "more", "about", "their", "they", "its", "was",
    "company", "limited", "ltd", "pvt", "private", "pakistan", "psx", "page", "home",
    "contact", "services", "products", "solutions", "business", "group", "industries",
}


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z\-]{2,}", (text or "").lower())]


def detect_industry(text: str, psx_sector: str = "") -> str:
    """
    Return the best-matching INDUSTRY_TAXONOMY key, or 'general'.
    The PSX sector name (when present) is authoritative; website text is the tiebreak.
    """
    sector = (psx_sector or "").strip().lower()
    if sector:
        # exact then substring match against PSX sector hints
        if sector in _PSX_SECTOR_HINTS:
            return _PSX_SECTOR_HINTS[sector]
        for hint, ind in _PSX_SECTOR_HINTS.items():
            if hint in sector:
                return ind

    blob = (text or "").lower()
    scores: Counter = Counter()
    for industry, terms in INDUSTRY_TAXONOMY.items():
        for term in terms:
            occ = blob.count(term)
            if occ:
                # multi-word terms weigh more
                scores[industry] += occ * (2 if " " in term else 1)

    if scores:
        return scores.most_common(1)[0][0]
    return "general"


def extract_keywords(text: str, psx_sector: str = "", limit: int = 10) -> List[str]:
    """
    Return up to `limit` industry-relevant keywords. Combines:
      1. matched taxonomy terms for the detected industry (highest priority)
      2. high-frequency salient tokens from the text
    """
    industry = detect_industry(text, psx_sector)
    keywords: List[str] = []

    if industry != "general":
        blob = (text or "").lower()
        for term in INDUSTRY_TAXONOMY[industry]:
            if term in blob and term not in keywords:
                keywords.append(term)

    # frequency-based salient terms from the body text
    counts = Counter(t for t in _tokens(text) if t not in _STOPWORDS)
    for word, _ in counts.most_common(40):
        if len(keywords) >= limit:
            break
        if word not in keywords:
            keywords.append(word)

    return keywords[:limit]


def select_industry_scrapers(industry: str) -> List[str]:
    """Return association scraper IDs relevant to the detected industry."""
    return INDUSTRY_SCRAPERS.get(industry, [])
