"""
core/output_validator.py — Validates Gemini module responses against the token sheet.

Checks:
  1. All required tokens are present in the response.
  2. No raw digit runs outside known token values (with allowed exceptions).
  3. No Title Case proper nouns outside the allowlist (with positional exemptions).

On violation: caller should retry ONCE with the violation appended.
On second violation: caller falls back to narrative_fallbacks.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.sanitiser import TokenSheet
from core.validator_allowlist import build_runtime_allowlist


# ── Result type ────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed:     bool
    violations: list[str]           = field(default_factory=list)
    strategy:   str                 = "pass"    # pass | retry | fallback

    def violation_summary(self) -> str:
        return "; ".join(self.violations)


# ── Digit-run patterns and exceptions ─────────────────────────────────────

# Matches 4+ consecutive digit runs (catches raw PKR figures, line items, etc.)
_DIGIT_RUN = re.compile(r"\b\d{4,}\b")

# Years 1980–2035 are always safe (policy dates, fiscal years, etc.)
_YEAR_PAT  = re.compile(r"\b(19[89]\d|20[0-2]\d|2030|2031|2032|2033|2034|2035)\b")

# Ordinal/list numbers 1–20 at line/sentence start: "1. ...", "12. ...", "2) ..."
_ORDINAL_LINE = re.compile(r"(?:^|\n)\s*\d{1,2}[.)]\s")


def _is_allowed_digit_run(match: re.Match, text: str, token_values: set[str]) -> bool:
    """Return True if this 4-digit match is an allowed exception."""
    digit_str = match.group()

    # 1. It IS a known token value
    if any(digit_str in val for val in token_values):
        return True

    # 2. It's a year (1980–2035)
    if _YEAR_PAT.fullmatch(digit_str):
        return True

    # 3. It appears in an ordinal list position (1. 2. 12) etc.) — check if it's
    #    1-2 digits before a dot/paren at line start (already excluded by 4-digit
    #    match; this handles 2020, 2021 — already covered by year check above).
    #    Nothing more to add here given the length requirement is already 4 digits.

    # 4. Percentage that exactly matches a token value: "12.5%" where 12.5 is known
    #    Check the char after the match — if it's "%" and the value is in tokens
    end = match.end()
    if end < len(text) and text[end] == "%":
        pct_val = digit_str + "%"
        if any(pct_val in val for val in token_values):
            return True

    return False


# ── Title Case patterns and exceptions ────────────────────────────────────

# Title Case word: starts uppercase, has ≥2 lowercase letters
_TITLE_CASE_WORD = re.compile(r"\b([A-Z][a-z]{2,})\b")

# Sentence-start position: word follows ". ", "! ", "? " or is at string start
# We use a broader approach: track which char-offsets are sentence-start positions
def _sentence_start_offsets(text: str) -> set[int]:
    """Return set of character offsets that are at the start of a sentence."""
    offsets: set[int] = {0}
    for m in re.finditer(r"[.!?]\s+", text):
        offsets.add(m.end())
    # Also paragraph starts
    for m in re.finditer(r"\n\s*", text):
        offsets.add(m.end())
    return offsets


# ── Validator ──────────────────────────────────────────────────────────────

def validate(
    response:            str,
    token_sheet:         TokenSheet,
    required_tokens:     Optional[list[str]] = None,
    public_institutions: Optional[list[str]] = None,
) -> ValidationResult:
    """
    Validate a Gemini module response.

    Args:
        response:            Raw text returned by Gemini.
        token_sheet:         The TokenSheet used to build the prompt.
        required_tokens:     Token strings that must be present in the response.
        public_institutions: Class P institution names surfaced during Stage 1;
                             added to the allowlist for this call.

    Returns:
        ValidationResult with passed=True if no violations found.
    """
    violations: list[str] = []
    allowlist = build_runtime_allowlist(public_institutions)

    # Add token placeholder names themselves to the allowlist
    with token_sheet._lock:
        for tok in token_sheet._reverse:
            # {{TOKEN_COMPANY}} → strip braces, add "Company" to allowlist
            inner = tok.strip("{}")  # e.g. TOKEN_COMPANY
            for part in inner.split("_"):
                if part and part[0].isupper():
                    allowlist.add(part)

    known_values: set[str] = set(token_sheet._reverse.values())

    # ── 1. Required tokens present ────────────────────────────────────────
    if required_tokens:
        for tok in required_tokens:
            if tok not in response:
                violations.append(f"required token missing: {tok}")

    # ── 2. No raw digit runs outside allowed exceptions ───────────────────
    for match in _DIGIT_RUN.finditer(response):
        if not _is_allowed_digit_run(match, response, known_values):
            violations.append(
                f"raw digit run outside token values: {match.group()!r} "
                f"(pos {match.start()})"
            )

    # ── 3. No unexpected Title Case proper nouns ──────────────────────────
    sentence_starts = _sentence_start_offsets(response)

    for match in _TITLE_CASE_WORD.finditer(response):
        word = match.group(1)
        if word in allowlist:
            continue
        # Positional exemption: word is at a sentence-start position
        if match.start() in sentence_starts:
            continue
        violations.append(f"unexpected proper noun outside allowlist: {word!r}")

    if violations:
        return ValidationResult(passed=False, violations=violations, strategy="retry")
    return ValidationResult(passed=True, strategy="pass")


def build_retry_prompt(original_prompt: str, result: ValidationResult) -> str:
    """Append violation description to prompt for a single retry."""
    return (
        original_prompt
        + "\n\n[CORRECTION REQUIRED] Your previous response contained the following "
        "privacy violations. Use ONLY the {{TOKEN_*}} placeholders provided — "
        "no raw figures, no company names, no proper nouns outside the given context. "
        "Violations:\n"
        + result.violation_summary()
    )
