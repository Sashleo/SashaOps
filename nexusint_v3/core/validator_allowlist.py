"""
core/validator_allowlist.py — Proper-noun allowlist for OutputValidator.

This is the single maintained list. Add terms here rather than touching output_validator.py.
The validator treats any word in this set as safe for Title Case regardless of position.

Sections:
  FRAMEWORK_TERMS   — analytical framework/model names
  CALENDAR_TERMS    — months, weekdays, fiscal periods
  INSTITUTION_NAMES — public institutions always Class P
  TOKEN_TERMS       — token placeholder labels (auto-populated at runtime)
  EXTRA             — anything else the validator was rejecting legitimately
"""

from __future__ import annotations

# ── Analytical frameworks and financial terminology ────────────────────────
FRAMEWORK_TERMS: set[str] = {
    # Strategy frameworks
    "Porter", "Porters",
    "SWOT", "PESTLE", "PESTEL",
    "BMC", "VRIO",
    # Financial models
    "DuPont", "Altman",
    "DCF", "EBITDA", "WACC", "CAPM",
    "EBIT", "EBITA", "EBITDAR",
    "Herfindahl", "Hirschman",
    "Basel", "IFRS", "GAAP",
    # Market terms
    "PSX", "KSE",
    "NSE", "BSE",  # regional context
    "IMF", "ADB",
    # Common analytical words that start Title Case in prose
    "Score", "Zone", "Stage", "Tier", "Track", "Phase",
    "Verdict", "Signal", "Index", "Ratio",
    "Express", "Gated",
}

# ── Calendar terms ─────────────────────────────────────────────────────────
CALENDAR_TERMS: set[str] = {
    # Months
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    # Days
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    # Fiscal / period labels
    "Q1", "Q2", "Q3", "Q4",
    "H1", "H2",
    "FY", "CY",
    "YTD", "QTD", "MTD",
}

# ── Public institutions (always Class P — never the analysis subject) ──────
INSTITUTION_NAMES: set[str] = {
    # Pakistani government and regulators
    "Pakistan", "Pakistani",
    "SBP", "FBR", "NEPRA", "OGRA", "PBS", "PTA",
    "SECP", "NTC", "PBC",
    "Ministry", "Government",
    # International
    "IMF", "World", "Bank", "ADB", "IFC", "AIIB",
    "WTO", "OECD", "UN",
    # Major cities / provinces (geography that doesn't identify the subject)
    "Karachi", "Lahore", "Islamabad", "Peshawar", "Quetta",
    "Punjab", "Sindh", "Balochistan",
    # Misc public context
    "KSE", "PSX", "Gemini", "SashaOps",
}

# ── Positional exemption handled in validator, not here ───────────────────
# A capitalised word immediately after ". " or at paragraph/line start is
# exempt. That logic lives in output_validator.py.

# ── Master set (union of all sections) ─────────────────────────────────────
ALLOWLIST: set[str] = FRAMEWORK_TERMS | CALENDAR_TERMS | INSTITUTION_NAMES


def build_runtime_allowlist(public_institutions: list[str] | None = None) -> set[str]:
    """
    Return the full allowlist for a specific run, optionally adding the
    Class P institution names surfaced during Stage 1 (e.g. company peers
    that are public and appear in web context).
    """
    result = set(ALLOWLIST)
    if public_institutions:
        for name in public_institutions:
            # Only add individual Title-Case words, not multi-word phrases
            for word in name.split():
                if word and word[0].isupper():
                    result.add(word)
    return result
