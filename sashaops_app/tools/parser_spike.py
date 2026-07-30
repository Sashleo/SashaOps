"""
tools/parser_spike.py - Phase 3 STEP 2: what is ACTUALLY extractable?

Before extending statement_parser.py to ~25 fields, measure which of them can be
found across genuinely different report formats. Candidate anchors here are
DIAGNOSTIC ONLY - they are not merged into the parser until this matrix shows a
field is reliably present. Prints a field x company matrix.

Run: PYTHONPATH=/app python -u tools/parser_spike.py
"""
import re
import sys
from pathlib import Path

from intelligence.statement_parser import _clean_line, _columns

# Companies spanning bank/cement/fertilizer/pharma/scanned.
TARGETS = {
    "HBL":   "/app/.tmp/psx_reports/HBL_2026-07-29.pdf",
    "LUCK":  "/app/.tmp/psx_reports/LUCK_2026-07-29.pdf",
    "ENGRO": "/app/.tmp/pdf_validate/pdfs/ENGRO.pdf",
    "BFBIO": "/app/.tmp/pdf_corpus/pdfs/BFBIO.pdf",
    "IMAGE": "/app/.tmp/pdf_corpus/pdfs/IMAGE.pdf",
}

# The full Phase 3 target set. Existing parser fields are marked (*).
CANDIDATES = {
    # ── Balance sheet: non-current assets ────────────────────────────────────
    "ppe":                 r"^\s*(?:property,?\s+plant\s+and\s+equipment|operating\s+fixed\s+assets)",
    "intangibles":         r"^\s*intangible\s+assets?",
    "right_of_use":        r"^\s*right[\s-]of[\s-]use\s+assets?",
    "long_term_invest":    r"^\s*long[\s-]term\s+investments?",
    "total_noncurrent_assets": r"^\s*total\s+non[\s-]?current\s+assets",
    # ── Balance sheet: current assets ────────────────────────────────────────
    "inventory*":          r"^\s*(?:stock[\s-]in[\s-]trade|inventor(?:y|ies))",
    "trade_receivables":   r"^\s*trade\s+(?:debts?|receivables?)",
    "advances_prepay":     r"^\s*(?:advances,?\s+deposits|prepayments?)",
    "cash*":               r"^\s*cash\s+and\s+(?:bank\s+balances?|cash\s+equivalents?)",
    "current_assets*":     r"^\s*total\s+current\s+assets",
    "total_assets*":       r"^\s*total\s+assets",
    # ── Balance sheet: equity + liabilities ─────────────────────────────────
    "issued_capital":      r"^\s*(?:issued,?\s+subscribed|share\s+capital)",
    "reserves":            r"^\s*(?:revenue\s+)?reserves?\b",
    "total_equity*":       r"^\s*total\s+equity\b(?!\s*(?:and|&)\s*liabilit)",
    "total_noncurrent_liab": r"^\s*total\s+non[\s-]?current\s+liabilit",
    "current_liabilities*": r"^\s*total\s+current\s+liabilit",
    "total_liabilities*":  r"^\s*total\s+liabilit",
    # ── Income statement ────────────────────────────────────────────────────
    "revenue*":            r"^\s*(?:net\s+)?(?:revenue|turnover|sales)\b(?!\s*tax)",
    "cost_of_sales":       r"^\s*cost\s+of\s+(?:sales|revenue|goods)",
    "gross_profit*":       r"^\s*gross\s+profit",
    "distribution_exp":    r"^\s*(?:distribution|selling)\s+(?:and\s+selling\s+)?(?:cost|expense)",
    "admin_exp":          r"^\s*administrat(?:ive|ion)\s+(?:cost|expense)",
    "other_income":        r"^\s*other\s+(?:income|operating\s+income)",
    "ebit*":               r"^\s*(?:profit\s+from\s+operations?|operating\s+profit)",
    "finance_cost*":       r"^\s*(?:finance\s+cost|financial\s+charges)",
    "pbt":                 r"^\s*profit\s+before\s+tax",
    "taxation":            r"^\s*(?:taxation|income\s+tax\s+expense)\b",
    "net_income*":         r"^\s*(?:profit\s+after\s+tax|profit\s+for\s+the\s+(?:year|period))",
    "eps":                 r"^\s*earnings?\s+per\s+share",
    # ── Cash flow ───────────────────────────────────────────────────────────
    "cash_from_ops":       r"^\s*cash\s+(?:generated\s+)?from\s+operations",
    "net_cash_operating":  r"^\s*net\s+cash\s+(?:generated\s+)?(?:from|used\s+in)\s+operating",
    "net_cash_investing":  r"^\s*net\s+cash\s+(?:generated\s+)?(?:from|used\s+in)\s+investing",
    "net_cash_financing":  r"^\s*net\s+cash\s+(?:generated\s+)?(?:from|used\s+in)\s+financing",
    "capex":               r"^\s*(?:additions?\s+to|purchase\s+of)\s+(?:property|operating\s+fixed)",
    "cash_end":            r"^\s*cash\s+and\s+cash\s+equivalents\s+at\s+(?:the\s+)?end",
    # ── Bank-specific ───────────────────────────────────────────────────────
    "markup_earned":       r"^\s*mark[\s-]*up.*earned",
    "markup_expensed":     r"^\s*mark[\s-]*up.*expensed",
    "advances_bank":       r"^\s*advances\b",
    "deposits_bank":       r"^\s*deposits\s+and\s+other\s+accounts",
    # ── Audit report (Section 6) ────────────────────────────────────────────
    "auditor_opinion":     r"(?:unqualified|qualified|adverse)\s+opinion|true\s+and\s+fair",
    "going_concern":       r"going\s+concern",
}


def load_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    import pdfplumber
    try:
        with pdfplumber.open(str(p)) as pdf:
            txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages[:60])
    except Exception:
        return ""
    if len(txt) > 500:
        return txt
    # Scanned: OCR a bounded page range so the spike stays quick.
    try:
        from pdf2image import convert_from_path
        import pytesseract
        imgs = convert_from_path(str(p), dpi=200, first_page=1, last_page=12)
        return "\n".join(pytesseract.image_to_string(i, timeout=90) or "" for i in imgs)
    except Exception:
        return ""


def probe(text: str) -> dict:
    """For each candidate field: did an anchored line with a number appear?"""
    lines = [_clean_line(l) for l in text.split("\n") if l.strip()]
    hits = {}
    for field, pat in CANDIDATES.items():
        rx = re.compile(pat, re.I)
        found = False
        for line in lines:
            m = rx.match(line) if pat.startswith("^") else rx.search(line)
            if not m:
                continue
            if field in ("auditor_opinion", "going_concern"):
                found = True
                break
            if _columns(line, after=m.end()):
                found = True
                break
        hits[field] = found
    return hits


def main() -> None:
    results, meta = {}, {}
    for tk, path in TARGETS.items():
        text = load_text(path)
        meta[tk] = len(text)
        print(f"[{tk}] text chars={len(text)}", flush=True)
        results[tk] = probe(text) if text else {f: False for f in CANDIDATES}

    names = list(TARGETS)
    print("\n=== FIELD x COMPANY EXTRACTION MATRIX ===")
    print(f"{'field':26} " + " ".join(f"{n:>6}" for n in names) + "   n/5")
    print("-" * (26 + 7 * len(names) + 7))
    reliable, partial, absent = [], [], []
    for field in CANDIDATES:
        cells = [results[n].get(field, False) for n in names]
        n_ok = sum(cells)
        print(f"{field:26} " + " ".join(f"{'  YES' if c else '   . ':>6}" for c in cells)
              + f"   {n_ok}/5")
        (reliable if n_ok >= 4 else partial if n_ok >= 2 else absent).append(field)

    print(f"\nRELIABLE (4-5 of 5): {len(reliable)}")
    print("  " + ", ".join(reliable))
    print(f"\nPARTIAL (2-3 of 5): {len(partial)}")
    print("  " + ", ".join(partial))
    print(f"\nABSENT (0-1 of 5): {len(absent)}")
    print("  " + ", ".join(absent))
    print("\ntext chars per company:", meta)


if __name__ == "__main__":
    main()
