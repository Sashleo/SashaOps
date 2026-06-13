"""
intelligence/query_templates.py — Per-module, keyword-injected search queries.

No AI. Plain templates with {company}/{industry}/{keyword}/{sector_regulator}
placeholders, filled at runtime from the detected industry + extracted keywords.
Replaces generic company searches with targeted, Pakistan-specific queries that are
more likely to surface the exact data each analysis module needs.
"""

from __future__ import annotations

from typing import List

# Industry → primary sector regulator (used in regulatory/risk queries)
SECTOR_REGULATORS: dict[str, str] = {
    "energy":   "NEPRA",
    "oil_gas":  "OGRA",
    "telecom":  "PTA",
    "banking":  "SBP",
    "pharma":   "DRAP",
    "cement":   "Competition Commission of Pakistan",
    "fmcg":     "Punjab Food Authority",
    "auto":     "Engineering Development Board",
}

QUERY_TEMPLATES: dict[str, List[str]] = {
    "overview": [
        "{company} Pakistan business overview",
        "{company} PSX {industry} products revenue",
        "{company} latest news Pakistan 2024 2025",
    ],
    "competitors": [
        "{company} competitors Pakistan {industry}",
        "{industry} sector Pakistan top PSX listed companies",
        "{keyword} market Pakistan competitive landscape",
    ],
    "financial": [
        "{company} financial results annual report Pakistan {industry}",
        "{company} PSX profit revenue margins",
        "{industry} Pakistan sector profitability 2024",
    ],
    "valuation": [
        "{company} valuation PSX {industry}",
        "{industry} Pakistan EV EBITDA P/E peer multiples",
        "{company} stock analysis target price PSX",
    ],
    "market_position": [
        "{company} market share Pakistan {industry}",
        "{industry} sector Pakistan market size growth 2024 2025",
        "{keyword} Pakistan production dispatch volumes",
    ],
    "regulatory": [
        "{company} SECP compliance Pakistan",
        "{sector_regulator} Pakistan {industry} regulations 2024 2025",
        "Pakistan {industry} sector policy {keyword}",
    ],
    "risk": [
        "{company} risk factors Pakistan {industry}",
        "Pakistan {industry} sector challenges {keyword}",
        "Pakistan macro risk inflation SBP policy rate {industry}",
    ],
    "esg": [
        "{company} ESG sustainability Pakistan",
        "{industry} Pakistan environmental compliance {keyword}",
        "Pakistan SECP ESG disclosure listed companies",
    ],
    "management": [
        "{company} board of directors leadership Pakistan",
        "{company} CEO management team Pakistan",
        "{company} corporate governance SECP Pakistan",
    ],
    "macro": [
        "Pakistan economy GDP SBP {industry} outlook 2024 2025",
        "Pakistan IMF programme impact {industry} sector",
        "Pakistan {industry} sector growth forecast",
    ],
    "supply_chain": [
        "{company} supply chain raw material Pakistan {keyword}",
        "{industry} Pakistan input cost import dependency",
        "Pakistan {keyword} import duty FBR customs tariff",
    ],
    "competitive": [
        "{company} competitive advantage Pakistan {industry}",
        "{industry} Pakistan market leaders PSX",
        "{keyword} Pakistan industry rivalry",
    ],
}

_DEFAULT = [
    "{company} Pakistan {industry}",
    "{company} PSX business intelligence",
    "{industry} Pakistan sector {keyword}",
]


def build_queries_for_module(
    module_id: str,
    company: str,
    industry: str = "general",
    keywords: List[str] | None = None,
) -> List[str]:
    """Fill the module's templates with company/industry/keyword. Returns up to 3 queries."""
    keywords = keywords or []
    keyword = keywords[0] if keywords else industry
    regulator = SECTOR_REGULATORS.get(industry, "SECP")
    ind = industry if industry != "general" else ""

    templates = QUERY_TEMPLATES.get(module_id, _DEFAULT)
    out: List[str] = []
    for t in templates:
        q = (t.replace("{company}", company)
              .replace("{industry}", ind)
              .replace("{keyword}", keyword)
              .replace("{sector_regulator}", regulator))
        q = " ".join(q.split())  # collapse whitespace from empty {industry}
        if q and q not in out:
            out.append(q)
    return out[:3]
