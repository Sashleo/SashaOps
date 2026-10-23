"""
intelligence/psx_report_parser.py — Parse PSX-linked annual/financial report PDFs.

PSX's company page only publishes a summary (Sales, Profit after Tax, EPS). The full
balance sheet — needed for liquidity, leverage, Altman Z and ROE — lives in the annual
report PDF that PSX links under company announcements. This module:

  1. Finds the most recent annual/financial-results PDF for a ticker (from PSX announcements)
  2. Downloads it (cached to .tmp/psx_reports/)
  3. Extracts balance-sheet + P&L line items via pdfplumber text parsing
  4. Returns a dict keyed for the analysis engine

Pure Python, no AI. Scanned PDFs (no text layer) are recorded as such and deferred to
the offline OCR batch step. Never raises — returns {} / None on any failure.
"""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()

CACHE_DIR = Path(".tmp/psx_reports")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
}
MAX_PAGES = 80          # annual reports are long; cap pages scanned
DOWNLOAD_TIMEOUT = 20   # hard cap on the annual-report PDF download


def fetch_psx_report_financials(ticker: str) -> Optional[dict]:
    """
    Return a dict of balance-sheet + P&L items parsed from the latest PSX-linked
    annual/financial report PDF, keyed for analyse_from_document(). None on failure.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    cached = _load_cache(ticker)
    if cached is not None:
        return cached or None   # cached {} means "tried, found nothing"

    selected = select_report_document(ticker)
    if not selected:
        _save_cache(ticker, {})
        return None
    url = selected["url"]
    doc_meta = {
        "source_document_type": selected.get("doc_type"),
        "source_document_pages": selected.get("pages"),
        "source_document_url": url,
        "source_document_date": selected.get("date"),
    }

    pdf_bytes = _download_by_url(url)
    if not pdf_bytes:
        _save_cache(ticker, {})
        return None

    from intelligence.pdf_extractor import extract_financials
    res = extract_financials(pdf_bytes)
    doc = res.get("financials") or {}
    if doc:
        doc["_method"] = res.get("method", "none")
        doc.update(doc_meta)
        _save_cache(ticker, doc)
        log.info("PSX report parsed", ticker=ticker, fields=sorted(doc.keys()),
                 method=doc["_method"], doc_type=selected.get("doc_type"),
                 pages=selected.get("pages"), url=url)
        return doc

    log.info("No text layer found — document may be scanned", ticker=ticker,
             pages=selected.get("pages"), doc_type=selected.get("doc_type"))
    _save_cache(ticker, {})
    return None


ANNUAL_REPORT_PATTERNS = [
    r"transmission of annual report",
    r"annual report for the year ended",
    r"annual audited (financial )?statements",
    r"annual report \d{4}",
]
ANNUAL_RESULTS_PATTERNS = [
    r"announcement of financial results for the year ended",
    r"audited financial results for the year",
    r"financial results for the year ended",
]
INTERIM_PATTERNS = [
    r"quarterly report",
    r"half year",
    r"interim",
    r"period ended (march|june|september)\s*3?0?1?",
]

# An annual report book is typically 80+ pages. Below this a document claiming to
# be one is a mislabelled extract, so selection falls through to annual results.
_ANNUAL_MIN_PAGES = 40


def _matches_any(title: str, patterns: list) -> bool:
    t = (title or "").lower()
    return any(re.search(p, t) for p in patterns)


def _ann_date_key(raw: str):
    """
    Sortable key for a PSX announcement date.

    PSX prints dates as "Apr 28, 2026". Sorting those as raw strings is
    alphabetical, so "Apr 28, 2026" outranks "Mar 6, 2026" and the newest filing
    within a tier is not actually the newest. Parse to a real date; unparseable
    values sort last rather than silently winning.
    """
    from datetime import datetime
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    from datetime import date as _date
    return _date.min


def _download_by_url(url: str) -> Optional[bytes]:
    """
    Fetch a PDF cached by URL, not by ticker.

    _download() keys its cache on ticker+date, so probing several candidate
    documents for one ticker would return the FIRST download for every later
    candidate - silently making page-count verification compare a document against
    itself. Selection needs per-URL caching.
    """
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = CACHE_DIR / f"sel_{key}.pdf"
    if path.exists():
        try:
            return path.read_bytes()
        except Exception:
            pass
    try:
        import time, random, requests
        time.sleep(2 + random.uniform(0, 1))   # don't hammer PSX PDF server
        r = requests.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT, verify=True)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return r.content
    except Exception as e:
        log.info("candidate download failed", url=url[:80], error=str(e)[:80])
    return None


def _pdf_page_count(pdf_bytes: bytes) -> Optional[int]:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def select_report_document(ticker: str) -> Optional[dict]:
    """
    Choose the PSX announcement PDF most likely to carry a full audited Statement
    of Financial Position, and say WHICH KIND of document was chosen.

    Selection (Phase 3 Track B):
      1. ANNUAL_REPORT_PATTERNS, most recent first. Verified by page count - an
         annual report under _ANNUAL_MIN_PAGES pages is a mislabelled extract, so
         it falls through rather than being trusted.
      2. ANNUAL_RESULTS_PATTERNS. Shorter, but these carry the audited statement
         of financial position and often the auditor's report.
      3. Most recent interim, tagged so callers can state the limitation instead
         of reporting a generic gap.

    Returns {url, doc_type, pages, date, title} or None. doc_type is one of
    'annual_report' | 'annual_results' | 'interim'.
    """
    try:
        from intelligence.exchange_crawlers.psx import fetch_psx_company
        data = fetch_psx_company(ticker, fetch_types=["announcements"])
    except Exception:
        return None
    anns = [a for a in ((data or {}).get("financial_announcements") or []) if a.get("pdf_url")]
    if not anns:
        return None

    def _sorted(subset):
        return sorted(subset, key=lambda a: _ann_date_key(a.get("date", "")), reverse=True)

    def _result(a, doc_type, pages):
        return {"url": a["pdf_url"], "doc_type": doc_type, "pages": pages,
                "date": a.get("date"), "title": (a.get("title") or "")[:200]}

    # 1. A real annual report, newest first, page-count verified.
    for a in _sorted([x for x in anns if _matches_any(x.get("title"), ANNUAL_REPORT_PATTERNS)]):
        pdf = _download_by_url(a["pdf_url"])
        pages = _pdf_page_count(pdf) if pdf else None
        if pages and pages >= _ANNUAL_MIN_PAGES:
            return _result(a, "annual_report", pages)
        log.info("Annual-report candidate too short - treating as an extract",
                 ticker=ticker, pages=pages, title=(a.get("title") or "")[:60])

    # 2. Annual results: audited, carries the balance sheet.
    for a in _sorted([x for x in anns if _matches_any(x.get("title"), ANNUAL_RESULTS_PATTERNS)]):
        pdf = _download_by_url(a["pdf_url"])
        return _result(a, "annual_results", _pdf_page_count(pdf) if pdf else None)

    # 3. Interim only. Tagged, so the UI can name the limitation precisely.
    for a in _sorted([x for x in anns if _matches_any(x.get("title"), INTERIM_PATTERNS)]):
        pdf = _download_by_url(a["pdf_url"])
        return _result(a, "interim", _pdf_page_count(pdf) if pdf else None)

    a = _sorted(anns)[0]
    pdf = _download_by_url(a["pdf_url"])
    return _result(a, "interim", _pdf_page_count(pdf) if pdf else None)


def _cache_json(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}_{date.today().isoformat()}.json"


def _load_cache(ticker: str) -> Optional[dict]:
    p = _cache_json(ticker)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cache(ticker: str, doc: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_json(ticker).write_text(json.dumps(doc), encoding="utf-8")
    except Exception:
        pass
