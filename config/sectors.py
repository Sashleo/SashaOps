"""
config/sectors.py — Sector classification and alias resolution.

Maps PSX sector names and shorthand keys to canonical sector keys.
"""

from __future__ import annotations
from typing import Optional


BANKING_SECTORS = {
    "banks", "banking", "commercial banks", "investment banks",
    "microfinance", "financial services", "modarabas", "leasing",
}


def is_bank(sector: str) -> bool:
    return any(b in (sector or "").lower() for b in BANKING_SECTORS)


SECTOR_ALIASES = {
    "commercial banks": "banking", "bank": "banking", "banking": "banking",
    "modarabas": "banking", "leasing": "banking", "inv. banks": "banking",
    "investment banks": "banking", "microfinance": "banking",
    "pharmaceuticals": "pharmaceuticals", "pharma": "pharmaceuticals",
    "cement": "cement",
    "oil & gas exploration": "oil_gas", "oil and gas exploration": "oil_gas",
    "oil_gas": "oil_gas", "exploration": "oil_gas",
    "oil & gas marketing": "oil_marketing", "oil and gas marketing": "oil_marketing",
    "refinery": "oil_marketing", "oil_marketing": "oil_marketing",
    "fertilizer": "fertilizer", "fertiliser": "fertilizer",
    "power generation": "power", "power": "power", "energy": "power",
    "textile": "textile", "apparel": "textile", "synthetic & rayon": "textile",
    "synthetic": "textile", "woollen": "textile", "jute": "textile",
    "leather": "textile", "spinning": "textile", "weaving": "textile",
    "technology & communication": "technology", "technology": "technology",
    "tech": "technology", "software": "technology",
    "telecom": "telecom", "telecommunication": "telecom",
    "automobile assembler": "automobile", "automobile parts": "automobile",
    "automobile": "automobile", "auto": "automobile",
    "property": "real_estate", "real estate": "real_estate",
    "real estate investment trust": "real_estate", "reit": "real_estate",
    "real_estate": "real_estate",
    "insurance": "insurance",
    "sugar & allied": "sugar", "sugar": "sugar",
}

CANONICAL_SECTORS = set(SECTOR_ALIASES.values())

_KEY_TO_PSX = {
    "banking": "COMMERCIAL BANKS",
    "cement": "CEMENT",
    "pharmaceuticals": "PHARMACEUTICALS",
    "oil_gas": "OIL & GAS EXPLORATION COMPANIES",
    "fertilizer": "FERTILIZER",
    "power": "POWER GENERATION & DISTRIBUTION",
    "technology": "TECHNOLOGY & COMMUNICATION",
}


def resolve_sector(sector: str) -> Optional[str]:
    """Map a raw sector string (PSX name or shorthand) to a canonical key."""
    s = (sector or "").strip().lower()
    if not s:
        return None
    if s in CANONICAL_SECTORS:
        return s
    if s in SECTOR_ALIASES:
        return SECTOR_ALIASES[s]
    for alias, key in SECTOR_ALIASES.items():
        if alias in s:
            return key
    return None


def psx_sector_name(key: str) -> Optional[str]:
    """Return the PSX-style sector name for a canonical key, if known."""
    return _KEY_TO_PSX.get(key)
