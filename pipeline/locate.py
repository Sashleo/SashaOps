"""pipeline/locate.py — S4 Locate: find statement pages, basis, period ends and scale.

Inputs:  a text-layer Bronze PDF (classified as annual_report or interim_report)
Outputs: doc_statements rows (one per statement_type × basis)

Approach
--------
1. Use statement_locator.locate_statement_pages() — already proven on PSX PDFs —
   to find the page index of each statement type's heading.
2. For each found page, read its text with pdfplumber.
3. Detect basis (consolidated / unconsolidated) from the title area.
4. Extract period ends from column headers ("As at June 30, 2026").
5. Detect scale with statement_parser.detect_scale().
6. Write one doc_statements row per (statement_type, basis) pair.

A document without a text layer is skipped here (M12 will handle scanned PDFs).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()

LOCATOR_VERSION = "1.3"

# Classes that have a text layer and are worth locating
_LOCATABLE_CLASSES = {"annual_report", "interim_report"}

# How many months each class corresponds to
_CLASS_MONTHS = {
    "annual_report":  12,
    "interim_report": None,  # must be read from the header; 3, 6 or 9
}

# Period-end date patterns found in Pakistani statement headers.
# "As at June 30, 2026" / "For the year ended June 30, 2026" /
# "for the period ended March 31, 2026"
_PERIOD_DATE_RE = re.compile(
    r"(?:as\s+at|for\s+the\s+(?:year|period|quarter)\s+ended?(?:\s*[-–]\s*|\s+))"
    r"(\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE,
)
# Also catch bare "30 June 2026" or "June 30, 2026" in the first 25 lines
_BARE_DATE_RE = re.compile(
    r"\b(\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}\s+\w+\s+\d{4})\b",
)
_DATE_FMTS = (
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%d %B %Y",  "%d %b %Y",
)

_CONSOL_RE = re.compile(
    r"\bconsolidated\b", re.IGNORECASE,
)
_UNCONSOL_RE = re.compile(
    r"\bunconsolidated\b|\bseparate\b|\bstandalone\b", re.IGNORECASE,
)

# Months patterns: "nine months", "six months", "three months", "half year"
_MONTHS_RE = re.compile(
    r"\b(nine|9)\s+month|\b(six|6)\s+month|\b(three|3)\s+month|"
    r"\bhalf.?year",
    re.IGNORECASE,
)


def _parse_date(raw: str) -> Optional[str]:
    """Parse a date string into ISO format, or None."""
    raw = re.sub(r"\s+", " ", raw or "").strip().rstrip(",")
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_period_ends_from_text(text: str) -> list[str]:
    """
    Extract ISO period-end dates from a statement's text (first 30 lines).

    Returns a list of ISO date strings, newest first.  A two-column statement
    normally yields two dates ("2026-06-30", "2025-06-30").
    """
    dates: list[str] = []
    lines = (text or "").split("\n")[:30]
    header = "\n".join(lines)

    # 1. Look for explicit "As at …" or "for the year ended …" phrases
    for m in _PERIOD_DATE_RE.finditer(header):
        iso = _parse_date(m.group(1))
        if iso and iso not in dates:
            dates.append(iso)

    # 2. Fallback: scan first 25 lines for plausible standalone dates
    if not dates:
        for line in lines[:25]:
            for m in _BARE_DATE_RE.finditer(line):
                iso = _parse_date(m.group(1))
                if iso and iso not in dates:
                    dates.append(iso)

    # Sort descending (newest period first)
    dates.sort(reverse=True)
    return dates


def _detect_basis(text: str) -> str:
    """
    Detect 'consolidated' or 'unconsolidated' from the first 10 lines.
    Defaults to 'unconsolidated' when ambiguous.
    """
    header = "\n".join((text or "").split("\n")[:10])
    if _UNCONSOL_RE.search(header):
        return "unconsolidated"
    if _CONSOL_RE.search(header):
        return "consolidated"
    return "unconsolidated"


def _detect_months(text: str, doc_class: str) -> Optional[int]:
    """Detect the period length in months from the statement header."""
    if doc_class == "annual_report":
        return 12
    m = _MONTHS_RE.search((text or "")[:500])
    if m:
        raw = m.group(0).lower()
        if "nine" in raw or "9" in raw:
            return 9
        if "six" in raw or "6" in raw:
            return 6
        if "three" in raw or "3" in raw or "quarter" in (text or "")[:200].lower():
            return 3
        if "half" in raw:
            return 6
    return None


def _get_page_text(pdf_path: str, page_idx: int) -> str:
    """Extract text from a single PDF page with pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if page_idx >= len(pdf.pages):
                return ""
            return pdf.pages[page_idx].extract_text() or ""
    except Exception as e:
        log.info("locate: page text extraction failed",
                 path=pdf_path, page=page_idx, error=str(e)[:80])
        return ""


# Title patterns that check joined pairs of lines, for PDFs where titles wrap.
# Each entry: (statement_type, single-line regex, joined-line regex)
_MULTILINE_HEADING_RE = [
    ("balance_sheet",
     re.compile(r"statement\s+of\s+(?:financial\s+)?position", re.IGNORECASE),
     re.compile(r"statement\s+of\s*\n\s*financial\s+position", re.IGNORECASE)),
    ("income",
     re.compile(r"statement\s+of\s+(?:profit|income|comprehensive\s+income)", re.IGNORECASE),
     re.compile(r"statement\s+of\s*\n\s*(?:profit|income|comprehensive\s+income)", re.IGNORECASE)),
    ("cashflow",
     re.compile(r"statement\s+of\s+cash\s+flows?|cash\s+flow\s+statement", re.IGNORECASE),
     re.compile(r"statement\s+of\s*\n\s*cash\s+flows?", re.IGNORECASE)),
]

_MIN_NUMERIC_ROWS_LOCATOR = 3


def _numeric_row_count(text: str) -> int:
    num_re = re.compile(r"\d{1,3}(?:,\d{3})+|\d{5,}")
    return sum(1 for line in (text or "").split("\n") if num_re.search(line))


def _scan_for_multiline_titles(pdf_path: str) -> dict:
    """
    Fallback page scanner for PDFs whose statement titles wrap across two lines.

    Collects ALL candidate pages per statement type and selects the one with the
    most numeric rows (highest density = most likely to be the real statement page,
    not a summary or analysis callout that happens to mention the title).

    Returns {statement_type: [page_idx, ...]} (same contract as locate_statement_pages).
    """
    # {stype: [(page_idx, numeric_row_count), ...]}
    candidates: dict = {stype: [] for stype, _, _ in _MULTILINE_HEADING_RE}
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            n_pages = len(pdf.pages)
            for i in range(n_pages):
                try:
                    t = pdf.pages[i].extract_text() or ""
                except Exception:
                    t = ""
                if not t.strip():
                    try:
                        pdf.pages[i].flush_cache()
                    except Exception:
                        pass
                    continue
                nr = _numeric_row_count(t)
                for stype, single_re, joined_re in _MULTILINE_HEADING_RE:
                    if single_re.search(t) or joined_re.search(t):
                        if nr >= _MIN_NUMERIC_ROWS_LOCATOR:
                            candidates[stype].append((i, nr))
                try:
                    pdf.pages[i].flush_cache()
                except Exception:
                    pass
    except Exception as e:
        log.info("locate: multiline scan failed", path=pdf_path, error=str(e)[:80])
        return {}

    found: dict = {}
    for stype, pages in candidates.items():
        if not pages:
            continue
        # Pick the page with the most numeric rows; on a tie, pick the latest page
        # (the real statement is usually near the other statements, not in the
        # narrative analysis section which appears earlier).
        best_page = max(pages, key=lambda x: (x[1], x[0]))[0]
        found[stype] = [best_page]
    return found


def locate_one(db, doc_sha256: str) -> dict:
    """
    Locate statement pages for one filing.  Writes doc_statements rows and
    returns a summary dict.

    Outcomes: located | already_located | skipped:<reason> | failed:<reason>
    """
    row = db.execute(
        "SELECT file_path, doc_class, text_pages, ann_title "
        "FROM filings WHERE doc_sha256=?",
        (doc_sha256,),
    ).fetchone()

    if row is None:
        return {"doc_sha256": doc_sha256, "outcome": "not_found", "statements": []}

    file_path, doc_class, text_pages, ann_title = row

    if doc_class not in _LOCATABLE_CLASSES:
        return {
            "doc_sha256": doc_sha256,
            "outcome": f"skipped:{doc_class or 'unclassified'}",
            "statements": [],
        }

    if text_pages == 0:
        return {
            "doc_sha256": doc_sha256,
            "outcome": "skipped:scanned",
            "statements": [],
        }

    # Already located at this version — skip
    existing = db.execute(
        "SELECT count(*) FROM doc_statements "
        "WHERE doc_sha256=? AND locator_version=?",
        (doc_sha256, LOCATOR_VERSION),
    ).fetchone()[0]
    if existing:
        return {
            "doc_sha256": doc_sha256,
            "outcome": "already_located",
            "statements": [],
        }

    if not file_path or not Path(file_path).exists():
        return {
            "doc_sha256": doc_sha256,
            "outcome": "failed:file_missing",
            "statements": [],
        }

    # ── Run the locator ───────────────────────────────────────────────────────
    try:
        pdf_bytes = Path(file_path).read_bytes()
    except Exception as e:
        return {
            "doc_sha256": doc_sha256,
            "outcome": f"failed:read_error:{str(e)[:60]}",
            "statements": [],
        }

    try:
        from intelligence.statement_locator import locate_statement_pages
        found = locate_statement_pages(pdf_bytes)
    except Exception as e:
        log.warning("S4 Locate locator failed", doc_sha256=doc_sha256[:12],
                    error=str(e)[:80])
        return {
            "doc_sha256": doc_sha256,
            "outcome": f"failed:locator:{str(e)[:60]}",
            "statements": [],
        }

    del pdf_bytes  # free memory before per-page reads

    # Remove any stale rows from a previous locator version before writing new ones.
    # INSERT OR REPLACE only updates rows it re-finds; old (statement_type, basis)
    # combinations not found this run would otherwise remain.
    db.execute("DELETE FROM doc_statements WHERE doc_sha256=?", (doc_sha256,))

    # Primary locator matched nothing: try the multi-line title fallback (handles
    # PDFs like LUCK where "Unconsolidated Statement of\nFinancial Position"
    # spans two lines and defeats single-line pattern matching).
    primary_keys = {k for k in found if not k.startswith("_")}
    if not primary_keys:
        log.info("S4 Locate primary found nothing, trying multiline fallback",
                 doc_sha256=doc_sha256[:12])
        found = _scan_for_multiline_titles(file_path)

    statement_types = ("balance_sheet", "income", "cashflow")
    written: list[dict] = []

    for stype in statement_types:
        pages = found.get(stype) or []
        if not pages:
            continue

        # Each page that has the heading — could be unconsolidated vs consolidated.
        # Group by basis, keeping the first page for each.
        by_basis: dict = {}
        for pg_idx in pages:
            text = _get_page_text(file_path, pg_idx)
            basis = _detect_basis(text)
            if basis not in by_basis:
                by_basis[basis] = {"page_start": pg_idx, "text": text}

        for basis, info in by_basis.items():
            text = info["text"]
            period_ends = _extract_period_ends_from_text(text)
            months = _detect_months(text, doc_class or "annual_report")

            from intelligence.statement_parser import detect_scale
            scale_factor, scale_evidence = detect_scale(text)

            scale_str = (
                f"{scale_factor}x:{scale_evidence}" if scale_factor else None
            )
            from pipeline.store import upsert_doc_statement
            upsert_doc_statement(
                db,
                doc_sha256=doc_sha256,
                statement_type=stype,
                basis=basis,
                pdf_page_start=info["page_start"],
                pdf_page_end=pages[-1] if pages else info["page_start"],
                located_by="classifier",
                period_ends_json=json.dumps(period_ends),
                scale=scale_str,
                locator_version=LOCATOR_VERSION,
            )

            written.append({
                "statement_type": stype,
                "basis":          basis,
                "page_start":     info["page_start"],
                "period_ends":    period_ends,
                "scale":          scale_str,
                "months":         months,
            })

    db.commit()

    if not written:
        log.info("S4 Locate no_statements_found", doc_sha256=doc_sha256[:12],
                 doc_class=doc_class)
        return {
            "doc_sha256": doc_sha256,
            "outcome": "no_statements_found",
            "statements": [],
        }

    log.info("S4 Locate located", doc_sha256=doc_sha256[:12],
             doc_class=doc_class, statements=len(written),
             types=sorted({s["statement_type"] for s in written}))
    return {
        "doc_sha256": doc_sha256,
        "outcome": "located",
        "statements": written,
    }
