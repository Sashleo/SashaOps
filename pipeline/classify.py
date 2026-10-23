"""pipeline/classify.py — S3 Classify: detect document type and text-layer coverage.

Inputs:  a Bronze PDF (file path from the filings table)
Outputs: doc_class, text_pages written back to the filings row

Classes
-------
annual_report   : full set (BS + P&L + CF) with an annual title or long page count
interim_report  : full set with an interim/quarterly title
results_sheet   : short filing with income statement only (no BS, no CF)
cover_letter    : very short, no statement headings at all
scanned         : probed pages returned no text layer
unknown         : does not fit any of the above

"Scanned" is decided by the text layer only, never by whether fields were found.
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog

log = structlog.get_logger()

CLASSIFIER_VERSION = "1.1"

# Pages to probe for statement headings (first pass; cheap because text layer is free)
_MAX_PROBE_PAGES = 80

# Minimum characters on a page to count it as "has a text layer"
_MIN_TEXT_CHARS = 40

_BALANCE_SHEET_RE = re.compile(
    r"balance\s+sheet|statement\s+of\s+financial\s+position",
    re.IGNORECASE,
)
_INCOME_RE = re.compile(
    r"(?:profit|income|loss)\s+(?:and|or|&)\s+(?:loss|income)|"
    r"statement\s+of\s+(?:comprehensive\s+)?income|"
    r"statement\s+of\s+(?:profit|earnings)",
    re.IGNORECASE,
)
_CASHFLOW_RE = re.compile(
    r"cash\s+flow|statement\s+of\s+cash",
    re.IGNORECASE,
)
_ANNUAL_TITLE_RE = re.compile(
    r"annual\s+report|annual\s+(?:financial\s+)?statements?\s+for\s+the\s+year|"
    r"transmission\s+of\s+annual|annual\s+(?:audited|un-?audited)",
    re.IGNORECASE,
)
_INTERIM_TITLE_RE = re.compile(
    r"(?:quarterly|half.?year|interim|six.month|nine.month)\s+report|"
    r"for\s+the\s+(?:quarter|period)\s+end",
    re.IGNORECASE,
)
_RESULTS_TITLE_RE = re.compile(
    r"(?:financial|audited|un-?audited)\s+results?\s+for\s+the\s+(?:year|period)",
    re.IGNORECASE,
)


def _scan_pdf(file_path: str) -> tuple[int, int, dict]:
    """
    Open a PDF with pdfplumber, count pages with a text layer, and probe for
    statement headings on text pages.

    Returns (total_pages, text_pages, found_types) where found_types maps
    statement type -> True for each heading found in the probed window.
    """
    try:
        import pdfplumber
    except ImportError:
        log.warning("S3 Classify pdfplumber not available")
        return 0, 0, {}

    total_pages = 0
    text_pages = 0
    found: dict = {}

    try:
        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            probe = min(total_pages, _MAX_PROBE_PAGES)
            for i in range(probe):
                try:
                    t = pdf.pages[i].extract_text() or ""
                except Exception:
                    t = ""
                if len(t.strip()) >= _MIN_TEXT_CHARS:
                    text_pages += 1
                    if "balance_sheet" not in found and _BALANCE_SHEET_RE.search(t):
                        found["balance_sheet"] = True
                    if "income" not in found and _INCOME_RE.search(t):
                        found["income"] = True
                    if "cashflow" not in found and _CASHFLOW_RE.search(t):
                        found["cashflow"] = True
                try:
                    pdf.pages[i].flush_cache()
                except Exception:
                    pass
            # For large PDFs the probe may have all text pages — scale up the
            # count proportionally rather than leaving it capped at probe size.
            if probe < total_pages and text_pages == probe:
                # Every probed page had text: assume the rest do too.
                text_pages = total_pages
    except Exception as e:
        log.info("S3 Classify pdf open failed", path=file_path, error=str(e)[:80])

    return total_pages, text_pages, found


def _infer_class(
    total_pages: int,
    text_pages: int,
    found: dict,
    ann_title: str,
) -> str:
    if text_pages == 0:
        return "scanned"

    has_bs  = "balance_sheet" in found
    has_inc = "income"        in found
    has_cf  = "cashflow"      in found

    if not has_inc and total_pages <= 5:
        return "cover_letter"

    # Short filing with P&L only — annual results / condensed notice
    if has_inc and not has_bs and total_pages <= 30:
        return "results_sheet"

    # A title that explicitly says "annual results" and is short → results_sheet
    if _RESULTS_TITLE_RE.search(ann_title or "") and total_pages <= 30:
        return "results_sheet"

    if has_bs and has_inc and has_cf:
        if _INTERIM_TITLE_RE.search(ann_title or ""):
            return "interim_report"
        return "annual_report"

    # Partial set — income or cashflow headings may lie beyond the probe window.
    # Trust the announcement title when it is unambiguous.
    if _ANNUAL_TITLE_RE.search(ann_title or ""):
        # Any statement heading found (or substantial text) is enough to confirm
        # this is a full report whose later sections were not yet reached.
        if has_bs or has_inc or has_cf or text_pages >= 30:
            return "annual_report"
    if _INTERIM_TITLE_RE.search(ann_title or ""):
        if has_bs or has_inc or has_cf or text_pages >= 10:
            return "interim_report"

    if has_inc and _RESULTS_TITLE_RE.search(ann_title or ""):
        return "results_sheet"

    # Short with just income → results_sheet
    if has_inc and total_pages <= 30:
        return "results_sheet"

    return "unknown"


def classify_one(db, doc_sha256: str) -> dict:
    """
    Classify one filing.  Reads the PDF from Bronze, updates the filings row
    with doc_class, text_pages and classifier_version, and returns a summary dict.

    Outcomes: classified | already_classified | not_found | failed:<reason>
    """
    row = db.execute(
        "SELECT file_path, pages, ann_title, doc_class, classifier_version "
        "FROM filings WHERE doc_sha256=?",
        (doc_sha256,),
    ).fetchone()

    if row is None:
        log.warning("S3 Classify filing not found", doc_sha256=doc_sha256[:12])
        return {
            "doc_sha256": doc_sha256,
            "outcome": "not_found",
            "doc_class": None,
            "text_pages": None,
        }

    file_path, pages, ann_title, existing_class, existing_cv = row

    if existing_class and existing_cv == CLASSIFIER_VERSION:
        return {
            "doc_sha256": doc_sha256,
            "outcome": "already_classified",
            "doc_class": existing_class,
            "text_pages": None,
        }

    if not file_path or not Path(file_path).exists():
        log.warning("S3 Classify file missing", doc_sha256=doc_sha256[:12],
                    path=file_path)
        return {
            "doc_sha256": doc_sha256,
            "outcome": "failed:file_missing",
            "doc_class": None,
            "text_pages": None,
        }

    total_pages, text_pages, found = _scan_pdf(file_path)
    doc_class = _infer_class(total_pages, text_pages, found, ann_title or "")

    from pipeline.store import update_filing_classification
    update_filing_classification(
        db,
        doc_sha256=doc_sha256,
        doc_class=doc_class,
        text_pages=text_pages,
        classifier_version=CLASSIFIER_VERSION,
    )
    db.commit()

    log.info(
        "S3 Classify classified",
        doc_sha256=doc_sha256[:12],
        doc_class=doc_class,
        text_pages=text_pages,
        total_pages=total_pages,
        headings=sorted(found.keys()),
    )

    return {
        "doc_sha256": doc_sha256,
        "outcome": "classified",
        "doc_class": doc_class,
        "text_pages": text_pages,
    }
