"""
intelligence/pdf_extractor.py — Financial-statement extraction from PDF bytes.

Chain-of-responsibility: each extractor exposes the same interface and is tried in
order until one yields at least 3 financial line items.

    extract_financials(pdf_bytes) -> {"financials": {...}, "method": str, "confidence": 0-1}
                                     (empty financials on failure)

All parsing calls parse_financials_accurate() from statement_parser. Scanned PDFs
(no text layer) return an empty result; OCR is a separate offline batch step.
"""

from __future__ import annotations

import io
import re

import structlog

from intelligence.psx_report_parser import MAX_PAGES
from intelligence.statement_parser import parse_financials_accurate, quality_score

log = structlog.get_logger()

_MIN_FIELDS = 3


def _matched(fin: dict) -> int:
    return sum(1 for k, v in (fin or {}).items()
               if not k.startswith("_") and v is not None)


class PdfplumberTablesExtractor:
    """Digital PDFs: parse pdfplumber tables (fastest, highest confidence)."""
    method = "pdfplumber_tables"
    confidence = 0.9

    def extract(self, pdf_bytes: bytes) -> dict:
        import pdfplumber
        lines: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                for table in (page.extract_tables() or []):
                    for row in table:
                        cells = [str(c).strip() for c in row if c is not None]
                        if cells:
                            lines.append(" ".join(cells))
        fin = parse_financials_accurate("\n".join(lines))
        return {"financials": fin, "method": self.method, "confidence": self.confidence}


# The PRIMARY statement of financial position in a long annual report (200-400 pages
# for banks) sits far past a blind page cap. Reading only the first MAX_PAGES reaches
# the "Financial Highlights" 6-year table instead - whose FIRST column is the OLDEST
# year, so HBL's total assets parsed as the 2020 figure. These markers let the text
# extractor SEEK the real audited statements before giving up on the page budget.
_STATEMENT_MARKERS = re.compile(
    r"statement\s+of\s+financial\s+position|balance\s+sheet|"
    r"statement\s+of\s+profit\s+(?:or\s+loss|and\s+loss)|profit\s+and\s+loss\s+account",
    re.I,
)
# Highlights / six-year-review pages carry multi-year tables that mislead column
# choice; do not let them stand in for the audited statement.
_HIGHLIGHTS_MARKERS = re.compile(
    r"financial\s+highlights|(?:six|ten|eleven)[\s-]*year|horizontal\s+analysis|"
    r"vertical\s+analysis|key\s+operating|financial\s+summary", re.I,
)


class PdfplumberTextExtractor:
    """Digital PDFs without clean tables: parse the text layer.

    Reads pages lazily and STOPS once it has captured the audited statement pages
    (statement of financial position + profit or loss), so a 400-page bank report is
    not scanned in full yet its deep balance sheet is still reached. Highlights pages
    are captured only as a fallback when no audited statement page is found.
    """
    method = "pdfplumber_text"
    confidence = 0.7
    # Hard ceiling so a pathological PDF cannot make us read hundreds of pages.
    # Replaced by contents-page lookup in P4.
    _SEEK_CAP = 320

    def extract(self, pdf_bytes: bytes) -> dict:
        import pdfplumber
        statement_chunks: list[str] = []
        highlights_chunks: list[str] = []
        head_chunks: list[str] = []
        seen_statement = False
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for i, page in enumerate(pdf.pages[:self._SEEK_CAP]):
                t = page.extract_text() or ""
                if not t:
                    continue
                if i < MAX_PAGES:
                    head_chunks.append(t)
                if _STATEMENT_MARKERS.search(t) and not _HIGHLIGHTS_MARKERS.search(t):
                    statement_chunks.append(t)
                    seen_statement = True
                elif _HIGHLIGHTS_MARKERS.search(t):
                    highlights_chunks.append(t)
                # Once we have the audited statements and scanned the cheap head,
                # stop - no need to read the rest of a long report.
                if seen_statement and i >= MAX_PAGES:
                    break
        # Prefer the audited statement text; fall back to the head (short reports put
        # the statement in the first pages) plus highlights only if nothing better.
        if statement_chunks:
            text = "\n".join(statement_chunks + head_chunks[:5])
        else:
            text = "\n".join(head_chunks + highlights_chunks)
        fin = parse_financials_accurate(text)
        return {"financials": fin, "method": self.method, "confidence": self.confidence}


def extract_financials(pdf_bytes: bytes) -> dict:
    """
    Try each extractor in order; return the result with the best quality score
    and >= _MIN_FIELDS matched line items. Returns an empty result if both fail
    (e.g. a scanned PDF with no text layer).

    Escalating router, derived from a 131-document, 5-method study (tools/pdf_methods.py):

      TIER 1 (cheap, always): pdfplumber_text then pdfplumber_tables.
        - text beat tables 43-0 head to head, so text leads; tables is a tie-break.

    Scanned PDFs (no text layer) return {"financials": {}, "method": "none", ...}.
    OCR is an offline batch step not run in the request path.
    """
    best = None
    best_score = None

    def _consider(result) -> bool:
        """Fold a result into the running best. Returns True if it is authoritative
        (a reconciled balance sheet), meaning we can stop escalating."""
        nonlocal best, best_score
        if not (result and result.get("financials")):
            return False
        fin = result["financials"]
        n = _matched(fin)
        score = quality_score(fin)
        log.debug("Extractor result", method=result.get("method"), matched_fields=n,
                  balance_ok=fin.get("_balance_ok"), n_high=fin.get("_n_high"))
        if n >= _MIN_FIELDS and (best_score is None or score > best_score):
            best, best_score = result, score
        return bool(fin.get("_balance_ok") is True and n >= _MIN_FIELDS)

    def _run(extractor) -> bool:
        try:
            return _consider(extractor.extract(pdf_bytes))
        except Exception as e:
            log.warning("Extractor failed", method=extractor.__class__.__name__, error=str(e)[:120])
            return False

    # Text leads; tables tie-breaks (text beat tables 43-0 head-to-head).
    if not _run(PdfplumberTextExtractor()):
        _run(PdfplumberTablesExtractor())

    if best is not None:
        log.info("Extraction succeeded", method=best["method"],
                 matched_fields=_matched(best["financials"]),
                 balance_ok=best["financials"].get("_balance_ok"))
        return best

    log.debug("No digital text layer found — document may be scanned")
    return {"financials": {}, "method": "none", "confidence": 0.0}
