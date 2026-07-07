"""
core/sanitiser.py — TokenSheet: per-run value tokenisation + PII scrubbing.

Every value that would enter a Gemini prompt is replaced with a {{TOKEN_N}} placeholder.
Gemini writes prose using only tokens. restore() substitutes real values back locally.
This ensures no raw figures, names, or ratios appear verbatim in external API calls.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

import structlog

_log = structlog.get_logger()

# ── Prompt-injection defence (M4) ───────────────────────────────────────────
# Imperative patterns an attacker might embed in an uploaded document to hijack a
# downstream prompt. Any line matching these is dropped from extracted text before
# it can reach a prompt. Document text is Class X (already excluded from Gemini);
# this is defence-in-depth for any local text that could reach a template/prompt.
_INJECTION_RE = re.compile(
    r"ignore\s+(all\s+)?previous|system\s+prompt|you\s+are\s+now|disregard\s+.*instructions",
    re.I,
)


def strip_prompt_injection(text: str) -> str:
    """Drop lines containing imperative prompt-injection patterns. Logs when hit."""
    if not text:
        return text
    out, dropped = [], 0
    for line in text.split("\n"):
        if _INJECTION_RE.search(line):
            dropped += 1
            continue
        out.append(line)
    if dropped:
        _log.warning("Prompt-injection lines stripped from extracted text", lines=dropped)
    return "\n".join(out)


# ── PII scrubbing patterns ──────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I), "[EMAIL]"),
    (re.compile(r"\b(?:\+92|0)3\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b\d{13}\b"), "[CNIC]"),
    (re.compile(r"\bNTN[-\s]?\d{7,}\b", re.I), "[NTN]"),
]


def scrub_pii(text: str) -> str:
    """Replace known PII patterns with safe placeholders."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── TokenSheet ──────────────────────────────────────────────────────────────

@dataclass
class TokenSheet:
    """
    Bijective map: real value ↔ {{TOKEN_N}} placeholder, scoped per analysis run.

    Usage:
        sheet = TokenSheet()
        tok = sheet.add("Lucky Cement")       # → "{{TOKEN_0}}"
        tok = sheet.add(1.82)                 # → "{{TOKEN_1}}"
        prompt = f"Analyse {{COMPANY}} with ratio {{RATIO_CR}}"
        # build prompt using sheet.add() for every real value...
        restored = sheet.restore(gemini_response)
    """

    _lock:    threading.Lock          = field(default_factory=threading.Lock, repr=False, compare=False)
    _forward: dict[str, str]          = field(default_factory=dict)   # value_key → token
    _reverse: dict[str, str]          = field(default_factory=dict)   # token → value
    _counter: int                     = field(default=0)

    def _key(self, value: Any) -> str:
        return str(value)

    def add(self, value: Any, hint: str = "") -> str:
        """Register a value and return its {{TOKEN_N}} string."""
        key = self._key(value)
        with self._lock:
            if key in self._forward:
                return self._forward[key]
            suffix = f"_{hint.upper()}" if hint else f"_{self._counter}"
            token = f"{{{{TOKEN{suffix}}}}}"
            # guard against hint collisions
            while token in self._reverse:
                self._counter += 1
                token = f"{{{{TOKEN_{self._counter}}}}}"
            self._forward[key] = token
            self._reverse[token] = str(value)
            self._counter += 1
            return token

    def restore(self, text: str) -> str:
        """Replace all {{TOKEN_*}} placeholders with real values."""
        with self._lock:
            for token, value in self._reverse.items():
                text = text.replace(token, value)
        return text

    def masked_view(self) -> dict[str, str]:
        """Return token → '***' for debug display (no real values exposed)."""
        with self._lock:
            return {tok: "***" for tok in self._reverse}

    def token_for(self, value: Any) -> str | None:
        """Look up the token for a value, or None if not registered."""
        key = self._key(value)
        with self._lock:
            return self._forward.get(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._forward)


# ── Convenience: build a token sheet from a financial dict ──────────────────

def tokenise_financials(financials: dict, sheet: TokenSheet | None = None) -> tuple[TokenSheet, dict[str, str]]:
    """
    Register all values in a financials dict into a token sheet.
    Returns (sheet, token_dict) where token_dict maps the same keys to tokens.
    """
    if sheet is None:
        sheet = TokenSheet()
    token_dict: dict[str, str] = {}
    for k, v in financials.items():
        if v is not None:
            hint = k.upper()[:16]  # keep hint readable but short
            token_dict[k] = sheet.add(v, hint=hint)
    return sheet, token_dict
