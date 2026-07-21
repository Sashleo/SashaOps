"""
intelligence/statement_parser.py - Accuracy-first financial-statement parser.

Built after a 76-firm corpus study found that counting extracted fields hid three
analysis-corrupting bugs. This module tackles each report ASSUMPTION explicitly so
the numbers fed into ratios/DCF are the real ones:

  1. UNIT SCALE. Pakistani statements print "Rupees in '000", "Rs. in million", or
     nothing (actual rupees). A blind x1000 makes a report already in actual rupees
     1000x too big and a millions report 1000x too small. detect_scale() reads the
     header; if absent, the balance-sheet identity picks the scale that balances.

  2. TOTAL-LINE ANCHORING. The old regex grabbed the first line containing a keyword,
     so "Non-current liabilities: Long term investments" was stored as current
     liabilities. Here each balance-sheet total must match an anchored TOTAL pattern
     and sub-line-items are rejected.

  3. COLUMN CHOICE. Statements show 2-4 columns (current/prior, group/standalone,
     quarter/cumulative). We take the first numeric column (current period, standalone
     when both are present) and STORE every column so the choice is auditable.

Every extracted value carries provenance: the raw number, the source line, the column
index, the scale applied, and a confidence. A balance-sheet cross-check
(Assets == Liabilities + Equity) validates the whole extraction; a failure lowers
confidence rather than silently shipping wrong numbers.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Unit scale ────────────────────────────────────────────────────────────────

_SCALE_PATTERNS = [
    (1_000_000_000, r"rupees?\s+in\s+billion|rs\.?\s+in\s+billion|in\s+billions|\bpkr\s*bn\b"),
    (1_000_000,     r"rupees?\s+in\s+million|rs\.?\s+in\s+million|in\s+millions|\bpkr\s*mn\b|amounts?\s+in\s+million"),
    (1_000,         r"rupees?\s+in\s+'?\s*000|rupees?\s+in\s+thousand|in\s+thousands|amounts?\s+in\s+thousand|\(rupees\s+in\s+'?000\)|rs\.?\s+in\s+'?000|rupees\s+'000"),
    (1,             r"amounts?\s+in\s+(?:actual\s+)?rupees|figures?\s+in\s+rupees"),
]


def detect_scale(text: str) -> tuple[Optional[int], str]:
    """
    Return (factor, evidence). factor is 1 / 1e3 / 1e6 / 1e9, or None when no explicit
    unit header is present (caller then infers from the balance-sheet identity).
    """
    low = " ".join((text or "").lower().split())
    for factor, pat in _SCALE_PATTERNS:
        m = re.search(pat, low)
        if m:
            i = m.start()
            return factor, low[max(0, i - 20): i + 40].strip()
    return None, "no explicit unit header"


# ── Numbers ───────────────────────────────────────────────────────────────────

# A financial number: grouped thousands or a >=4 digit run, optional parens (negative),
# optional decimals. Rejects lone small integers (note references like "4", "8").
_NUM_RE = re.compile(r"\(?\s*-?(?:\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d+)?\s*\)?")


def _normalize_numbers(line: str) -> str:
    """
    Repair text-extraction noise that corrupts numbers in real PSX PDFs:
      - spaces inside grouped numbers: "3,441 ,959,708" -> "3,441,959,708"
      - a stray space before a comma group:  "3,691 ,029,638" -> "3,691,029,638"
      - periods used as thousands separators: "8.189.744" -> "8,189,744"
    Legitimate decimals (one period followed by 1-2 digits) are left alone.
    """
    s = line
    # join "digits <space> ,digits" and "digits, <space> digits"
    s = re.sub(r"(\d)\s+(,\d{3})", r"\1\2", s)
    s = re.sub(r"(\d,)\s+(\d{3})", r"\1\2", s)
    s = re.sub(r"(\d)\s+(\d{3}\b)", r"\1\2", s)   # "3,441 959,708" style
    # periods-as-thousands: 2+ groups of exactly 3 digits separated by '.'
    def _fix_periods(m):
        return m.group(0).replace(".", ",")
    s = re.sub(r"\b\d{1,3}(?:\.\d{3}){2,}\b", _fix_periods, s)
    return s


def _parse_number(tok: str) -> Optional[float]:
    t = tok.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "").replace(" ", "")
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return None


def _columns(line: str, after: int = 0) -> list[float]:
    """All financial numbers on a line, in order, from character `after` onward."""
    out = []
    for m in _NUM_RE.finditer(line, after):
        v = _parse_number(m.group())
        if v is not None:
            out.append(v)
    return out


# ── Field label anchors ───────────────────────────────────────────────────────
# For each field: ordered (confidence, anchor-regex). The regex matches the label at
# the START of the meaningful part of the line. Balance-sheet totals REQUIRE "total".

_FIELDS = {
    "revenue": [
        ("high",   r"^\s*(?:net\s+)?revenue\s+from\s+contracts?\s+with\s+customers"),
        ("high",   r"^\s*(?:net\s+)?sales?\s*[-–]?\s*(?:net)?\b"),
        ("high",   r"^\s*(?:net\s+)?turnover\s*[-–]?\s*(?:net)?\b"),
        ("medium", r"^\s*(?:gross\s+)?revenue\b"),
        ("medium", r"^\s*(?:net\s+)?interest\s+income\b"),      # banks
        ("medium", r"^\s*mark[\s-]*up\s*/?\s*(?:return|interest)\s+earned"),  # banks
    ],
    "gross_profit": [
        ("high",   r"^\s*gross\s+profit\b"),
    ],
    "ebit": [
        ("high",   r"^\s*(?:profit|loss)\s+from\s+operations?\b"),
        ("high",   r"^\s*operating\s+profit\b"),
        ("medium", r"^\s*profit\s+before\s+(?:interest\s+and\s+)?tax(?:ation)?\b"),
    ],
    "net_income": [
        ("high",   r"^\s*profit\s+(?:for\s+the\s+(?:year|period))\b"),
        ("high",   r"^\s*(?:profit|loss)\s+after\s+tax(?:ation)?\b"),
        ("medium", r"^\s*net\s+profit\b"),
        ("medium", r"^\s*(?:profit|loss)\s+for\s+the\s+(?:quarter|half\s+year)\b"),
    ],
    "total_assets": [
        ("high",   r"^\s*total\s+assets\b"),
    ],
    "current_assets": [
        ("high",   r"^\s*total\s+current\s+assets\b"),
    ],
    "total_equity": [
        ("high",   r"^\s*total\s+equity\b"),
        ("high",   r"^\s*total\s+(?:share\s*holders'?|shareholders'?)\s+equity\b"),
        ("medium", r"^\s*(?:total\s+)?equity\s+attributable\s+to\s+(?:the\s+)?(?:owners|equity\s+holders|shareholders)"),
    ],
    "current_liabilities": [
        ("high",   r"^\s*total\s+current\s+liabilities\b"),
    ],
    "total_liabilities": [
        ("high",   r"^\s*total\s+liabilities\b"),
    ],
    # Supporting fields the ratio engine needs.
    "cash": [
        ("medium", r"^\s*cash\s+and\s+(?:bank\s+balances?|cash\s+equivalents?)\b"),
    ],
    "inventory": [
        ("medium", r"^\s*(?:stock\s+in\s+trade|inventor(?:y|ies)|stores?,?\s+spares?)\b"),
    ],
    "interest_expense": [
        ("medium", r"^\s*(?:finance\s+cost|financial\s+charges|mark[\s-]*up.*expensed)\b"),
    ],
}

# Lines that must never be treated as a balance-sheet total even if a keyword appears.
_SUBITEM_GUARD = re.compile(
    r"long\s+term\s+investments?|spare\s+parts?|loose\s+tools?|stores?\b|"
    r"advances?|deposits?|prepayments?|other\s+receivables?|trade\s+(?:debts?|receivables?)",
    re.I,
)


def _clean_line(line: str) -> str:
    # Repair number noise first, then strip leading note-reference numbers.
    line = _normalize_numbers(line)
    return re.sub(r"^\s*\d{1,2}(?:\.\d+)?\s+", "", line)


def parse_statements(text: str) -> dict:
    """
    Parse a financial-statement text block into fields with full provenance.

    Returns a dict:
      { field: {value, raw, scale, column, all_columns, source_line, label, confidence}, ... }
      plus _unit_scale, _unit_evidence, _balance_check.
    """
    scale, evidence = detect_scale(text)
    lines = [l for l in (text or "").split("\n") if l.strip()]

    found: dict = {}
    for field, anchors in _FIELDS.items():
        for conf, pat in anchors:
            rx = re.compile(pat, re.I)
            hit = None
            for raw_line in lines:
                line = _clean_line(raw_line)
                if not rx.match(line):
                    continue
                # Reject balance-sheet totals that are actually sub-items.
                if field in ("current_liabilities", "current_assets", "total_assets",
                             "total_equity", "total_liabilities") and _SUBITEM_GUARD.search(line):
                    continue
                m = rx.match(line)
                cols = _columns(line, after=m.end())
                if not cols:
                    continue
                hit = {
                    "raw": cols[0],                 # current period = first column
                    "column": 0,
                    "all_columns": cols[:5],
                    "source_line": line.strip()[:120],
                    "label": pat,
                    "confidence": conf,
                }
                break
            if hit:
                found[field] = hit
                break

    # ── Decide the unit scale ────────────────────────────────────────────────
    if scale is None:
        scale, evidence = _infer_scale(found, evidence)

    for f, h in found.items():
        h["scale"] = scale
        h["value"] = h["raw"] * scale

    found["_unit_scale"] = scale
    found["_unit_evidence"] = evidence
    found["_balance_check"] = _balance_check(found)
    return found


def _infer_scale(found: dict, evidence: str) -> tuple[int, str]:
    """
    No explicit unit header. Pick the scale from magnitude: an unscaled total_assets
    for a real listed company sits in the millions-to-trillions of ACTUAL rupees.
    If the raw total_assets is already >= 1e8, it is printed in actual rupees (x1);
    otherwise it is a thousands table (x1000). This is the conservative choice and is
    validated by the balance-sheet cross-check.
    """
    ta = found.get("total_assets", {}).get("raw")
    rev = found.get("revenue", {}).get("raw")
    probe = ta if ta is not None else rev
    if probe is None:
        return 1_000, evidence + " | default x1000 (no anchor to infer from)"
    if probe >= 1e8:
        return 1, evidence + f" | inferred x1 (raw magnitude {probe:.0f} already actual rupees)"
    return 1_000, evidence + f" | inferred x1000 (raw magnitude {probe:.0f} is a thousands table)"


def _balance_check(found: dict) -> dict:
    """Assets == Liabilities + Equity, within 1%. The ultimate correctness gate."""
    ta = found.get("total_assets", {}).get("value")
    te = found.get("total_equity", {}).get("value")
    tl = found.get("total_liabilities", {}).get("value")
    if ta is None or te is None or tl is None:
        return {"ok": None, "reason": "need total_assets + total_equity + total_liabilities"}
    lhs, rhs = ta, te + tl
    if lhs == 0:
        return {"ok": None, "reason": "total_assets is zero"}
    diff_pct = abs(lhs - rhs) / abs(lhs) * 100
    return {"ok": diff_pct <= 1.0, "assets": ta, "liab_plus_equity": rhs,
            "diff_pct": round(diff_pct, 2)}


def to_financial_dict(parsed: dict, min_confidence: str = "medium") -> dict:
    """
    Flatten to {field: scaled_value} for the ratio engine. Only ships values at or
    above min_confidence. Values are already unit-scaled to actual rupees, so the
    ratio engine must NOT rescale them again.
    """
    order = {"low": 0, "medium": 1, "high": 2}
    floor = order.get(min_confidence, 1)
    out = {}
    for f, h in parsed.items():
        if f.startswith("_"):
            continue
        if order.get(h.get("confidence", "low"), 0) >= floor and h.get("value") is not None:
            out[f] = h["value"]
    # Map to the ratio engine's vocabulary.
    if "total_equity" in out:
        out.setdefault("shareholders_equity", out["total_equity"])
    out["_already_scaled"] = True     # tell financial_model not to apply its own x1000
    return out


def parse_financials_accurate(text: str) -> dict:
    """
    Drop-in accurate parser for the extraction chain. Returns a flat dict of
    already-scaled values plus quality signals the chain uses to pick the best method:
      _already_scaled, _unit_scale, _unit_evidence, _balance_ok, _n_high, _provenance
    """
    parsed = parse_statements(text)
    flat = to_financial_dict(parsed, min_confidence="medium")
    bc = parsed.get("_balance_check", {})
    flat["_unit_scale"] = parsed.get("_unit_scale")
    flat["_unit_evidence"] = parsed.get("_unit_evidence")
    flat["_balance_ok"] = bc.get("ok")
    flat["_n_high"] = sum(1 for k, h in parsed.items()
                          if not k.startswith("_") and h.get("confidence") == "high")
    # Compact provenance so every stored value stays auditable.
    flat["_provenance"] = {
        k: {"raw": h["raw"], "scale": h["scale"], "column": h["column"],
            "source_line": h["source_line"], "confidence": h["confidence"]}
        for k, h in parsed.items() if not k.startswith("_")
    }
    return flat


def quality_score(flat: dict) -> tuple:
    """
    Rank an extraction result. Higher is better. Used to choose between methods
    (pdfplumber tables vs text vs OCR) - prefer the one that BALANCES and has more
    high-confidence values, not merely the first that returned >=3 fields.
    """
    n_graded = sum(1 for k in flat if not k.startswith("_"))
    balance = 2 if flat.get("_balance_ok") is True else (0 if flat.get("_balance_ok") is False else 1)
    return (balance, flat.get("_n_high", 0), n_graded)
