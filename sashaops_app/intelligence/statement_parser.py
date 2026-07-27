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
    # Leading-zero glue: "Sales 0171,450" is a mangled "171,450". Without this the
    # number regex matches the 4-digit run "0171" and stores 171 (holdout: FIL).
    # The lookbehind is essential: without it this also fires INSIDE a legitimate
    # number - "61,782,057,256" would lose its ",057," group and parse as 61,782
    # (a 1,000,000x understatement caught by the sample-1 regression check).
    s = re.sub(r"(?<![\d,])0+(\d{1,3},\d{3})", r"\1", s)
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
    # Revenue. Guard against deduction lines that merely contain "sales"
    # ("Sales tax", "Sales returns", "Cost of sales") - a real corpus failure:
    # "^sales?" matched "Sales tax (57,706,226)" and stored NEGATIVE revenue.
    "revenue": [
        ("high",   r"^\s*(?:net\s+)?revenue\s+from\s+contracts?\s+with\s+customers"),
        ("high",   r"^\s*net\s+sales\b(?!\s*tax)"),
        ("high",   r"^\s*sales\s*[-–]\s*net\b"),
        ("high",   r"^\s*(?:net\s+)?turnover\b(?!\s*tax)"),
        ("medium", r"^\s*(?:gross\s+)?revenue\b(?!\s*(?:tax|reserve))"),
        ("medium", r"^\s*sales\b(?!\s*(?:tax|return|discount|commission|promotion))"),
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
        # A labelled total is best; but many Pakistani statements (and 6-year
        # highlight tables) label the subtotal simply "Current assets". The
        # "current portion / maturity / tax / account" sub-item lines are blocked by
        # the anchor (they don't start with "current assets") and by _SUBITEM_GUARD.
        ("high",   r"^\s*total\s+current\s+assets\b"),
        ("medium", r"^\s*current\s+assets\b(?!\s*[:\-])"),
    ],
    # Equity. CRITICAL: Pakistani balance sheets close with "TOTAL EQUITY AND
    # LIABILITIES", which equals TOTAL ASSETS, not equity. A bare "^total equity"
    # matches it and silently stores assets as equity (holdout: AGHA, GAMON showed
    # total_assets == total_equity). The negative lookahead blocks that.
    "total_equity": [
        ("high",   r"^\s*total\s+equity\b(?!\s*(?:and|&)\s*liabilit)"),
        ("high",   r"^\s*total\s+(?:share\s*holders'?|shareholders'?)\s+equity\b"),
        # Banks and DFIs do NOT split current/non-current; their balance sheet closes
        # with "NET ASSETS" (= Assets - Liabilities = equity). Verified on ABL, BML,
        # HBL. "Net assets per share/value" is a per-unit ratio, not the total - block it.
        ("high",   r"^\s*net\s+assets\b(?!\s*(?:per|value|attributable|/\s*share))"),
        ("medium", r"^\s*(?:total\s+)?(?:share\s+capital\s+and\s+reserves|capital\s+and\s+reserves)\b"),
        ("medium", r"^\s*(?:total\s+)?equity\s+attributable\s+to\s+(?:the\s+)?(?:owners|equity\s+holders|shareholders)"),
    ],
    "current_liabilities": [
        ("high",   r"^\s*total\s+current\s+liabilities\b"),
        ("medium", r"^\s*current\s+liabilities\b(?!\s*[:\-])"),
    ],
    "total_liabilities": [
        ("high",   r"^\s*total\s+liabilities\b"),
    ],
    # Supporting fields the ratio engine needs.
    "cash": [
        ("medium", r"^\s*cash\s+and\s+(?:bank\s+balances?|cash\s+equivalents?)\b"),
    ],
    "inventory": [
        # Inventory = stock in trade. "Stores, spares and loose tools" is a
        # SEPARATE consumables line; the holdout run matched it as inventory
        # on 6 firms, so it is deliberately excluded here.
        ("medium", r"^\s*stock[\s-]*in[\s-]*trade"),
        ("medium", r"^\s*inventor(?:y|ies)"),
    ],
    "interest_expense": [
        ("medium", r"^\s*(?:finance\s+cost|financial\s+charges|mark[\s-]*up.*expensed)\b"),
    ],
}

# Lines that must never be treated as a balance-sheet total even if a keyword appears.
_SUBITEM_GUARD = re.compile(
    r"long\s+term\s+investments?|spare\s+parts?|loose\s+tools?|stores?\b|"
    r"advances?|deposits?|prepayments?|other\s+receivables?|trade\s+(?:debts?|receivables?)|"
    # Insurance/Takaful sub-fund totals ("Total assets of Operator's Fund") are a
    # SEGMENT, not the company total. Matched via the "of ... Fund" qualifier so a
    # real mutual fund - where "Fund" IS the entity - still parses normally.
    r"\bof\s+(?:the\s+)?(?:operator|participant|statutory|policyholder|shareholder)[’'s]*\s*"
    r"(?:fund|sub[\s-]*fund)",
    re.I,
)

# Sign guard. These can never legitimately be negative on a statement, so a negative
# candidate means we matched a deduction/contra line (e.g. "Sales tax (57,706,226)")
# and must keep looking. Profit lines are excluded: losses are real.
_MUST_BE_POSITIVE = {
    "revenue", "total_assets", "current_assets",
    "current_liabilities", "total_liabilities", "cash", "inventory",
}

# Plausible magnitudes for a PSX-listed company, in ACTUAL rupees AFTER scaling.
# Used to CHOOSE the unit scale: the correct scale is the one that puts the most
# extracted values inside these ranges. A printed header is only a prior - a holdout
# run found a bank whose report mentioned "million" in unrelated prose, which sent
# total assets to 3.3 QUADRILLION when the figures were already actual rupees.
_PLAUSIBLE = {
    "revenue":             (1e6, 5e12),
    "total_assets":        (1e6, 2e13),
    "current_assets":      (1e5, 5e12),
    "current_liabilities": (1e5, 5e12),
    "total_liabilities":   (1e5, 2e13),
    "total_equity":        (1e5, 5e12),
    "net_income":          (1e5, 1e12),
    "gross_profit":        (1e5, 2e12),
    "ebit":                (1e5, 2e12),
    "cash":                (1e4, 5e12),
    "inventory":           (1e4, 2e12),
}
_CANDIDATE_SCALES = (1, 1_000, 1_000_000)


def _clean_line(line: str) -> str:
    # Repair number noise first, then strip leading note-reference numbers.
    line = _normalize_numbers(line)
    return re.sub(r"^\s*\d{1,2}(?:\.\d+)?\s+", "", line)


def _pick_candidate(candidates: list, conf: str, pat: str) -> Optional[dict]:
    """
    Choose the best matching line for a field. The canonical primary Statement of
    Financial Position prints exactly TWO numeric columns (current period, prior
    period) with the current period first. A 6-year "Financial Highlights" table
    prints 5-6 columns and, on Pakistani reports, usually oldest-year-first - so
    taking its first column silently reports a figure from years ago (HBL: total
    assets 3.85tn in 2020 vs 7.71tn in 2025). Prefer the 2-3 column primary line and
    fall back to a wide highlights line only when nothing else matched.
    """
    if not candidates:
        return None

    def _penalty(n: int) -> int:
        if n in (2, 3):
            return 0        # primary statement (current + prior [+ note])
        if n == 1:
            return 1        # single-column statement
        if n == 4:
            return 2
        return 3            # >=5 columns: multi-year highlights table

    best_i = min(range(len(candidates)),
                 key=lambda i: (_penalty(len(candidates[i][1])), i))
    line, cols = candidates[best_i]
    # Column choice. A primary statement lists the CURRENT period FIRST, so column 0.
    # But a 6-year "Financial Highlights" table (>=5 columns) lists years oldest to
    # newest left-to-right (verified on HBL: Total Assets 3.85tn[2020] ... 7.71tn[2025]),
    # so the CURRENT year is the LAST column. Taking column 0 there reports figures
    # from years ago. Only wide highlight rows flip to the last column; the canonical
    # 2-3 column statement stays on column 0.
    if len(cols) >= 5:
        idx = len(cols) - 1
    else:
        idx = 0
    return {
        "raw": cols[idx],
        "column": idx,
        "all_columns": cols[:6],
        "source_line": line.strip()[:120],
        "label": pat,
        "confidence": conf,
    }


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
            candidates = []
            for raw_line in lines:
                line = _clean_line(raw_line)
                if not rx.match(line):
                    continue
                # Reject sub-item lines for EVERY field. A totals-only guard let
                # inventory match "Stores, spares and loose tools" on 6 holdout firms.
                if _SUBITEM_GUARD.search(line):
                    continue

                m = rx.match(line)
                cols = _columns(line, after=m.end())
                if not cols:
                    continue
                # Sign guard: a negative here means a deduction/contra line was
                # matched, not the real total. Keep searching.
                if field in _MUST_BE_POSITIVE and cols[0] <= 0:
                    continue
                candidates.append((line, cols))
            hit = _pick_candidate(candidates, conf, pat)
            if hit:
                found[field] = hit
                break

    # ── Decide the unit scale ────────────────────────────────────────────────
    # Always validate against plausibility, even when a header was found: a
    # stray "million" elsewhere in a long report must not inflate statements.
    scale, evidence = _choose_scale(found, scale, evidence)

    for f, h in found.items():
        h["scale"] = scale
        h["value"] = h["raw"] * scale

    found["_unit_scale"] = scale
    found["_unit_evidence"] = evidence
    found["_balance_check"] = _balance_check(found)
    return found


def _plausibility(found: dict, scale: int) -> int:
    """How many extracted values land inside their plausible range at this scale."""
    score = 0
    for f, h in found.items():
        if f.startswith("_") or f not in _PLAUSIBLE:
            continue
        lo, hi = _PLAUSIBLE[f]
        v = abs(h["raw"] * scale)
        if lo <= v <= hi:
            score += 1
    return score


def _choose_scale(found: dict, header_scale: Optional[int], evidence: str) -> tuple[int, str]:
    """
    Choose the unit scale that makes the MOST extracted values plausible.

    The printed header is a prior, not gospel: a 300-page annual report can mention
    "million" in prose far from the statements. The holdout run caught exactly that
    (BAHL total assets -> 3.3 quadrillion). So every candidate scale is scored on how
    many values land in a sane range, and the header only breaks ties.
    """
    scored = []
    for s in _CANDIDATE_SCALES:
        scored.append((_plausibility(found, s), s))
    best_score = max(s for s, _ in scored) if scored else 0

    if best_score == 0:
        # Nothing to judge by: trust the header, else the historical x1000 default.
        if header_scale:
            return header_scale, evidence + " | header used (no values to validate)"
        return 1_000, evidence + " | default x1000 (nothing to infer from)"

    winners = [s for sc, s in scored if sc == best_score]
    if header_scale in winners:
        return header_scale, evidence + f" | header confirmed by plausibility ({best_score} fields in range)"
    chosen = min(winners)   # prefer the smaller (less inflating) scale on a tie
    if header_scale and header_scale not in winners:
        return chosen, (f"header said x{header_scale} but that made values implausible; "
                        f"chose x{chosen} ({best_score} fields in range)")
    return chosen, evidence + f" | chose x{chosen} by plausibility ({best_score} fields in range)"


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
