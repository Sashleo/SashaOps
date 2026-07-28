"""
intelligence/annual_report_finder.py - Locate a DIGITAL full annual-report PDF from a
company's own website when the PSX announcement feed only offers a scanned interim
report (most non-bank tickers). Deliberately small and isolated:

  - one public function, find_annual_report_financials(ticker, company_name)
  - bounded work (a few web-search candidates, each capped)
  - never raises; returns None on any failure so the caller falls through to OCR

It reuses the existing web search (DuckDuckGo, no API key) and the accurate extraction
chain. It does NOT crawl arbitrary sites with a browser - that is where this kind of
fallback turns into a tangled mess. It only follows direct .pdf results and validates
each by requiring a sane statement (revenue + total assets) before accepting it.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

import structlog

log = structlog.get_logger()

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
_MAX_CANDIDATES = 4          # cap PDFs downloaded per ticker
_DOWNLOAD_TIMEOUT = 25


def _web_pdf_links(query: str, cap: int = 6) -> list[str]:
    """Direct .pdf result links for a query, via DuckDuckGo's HTML results endpoint.

    search.multi_search uses DDG's Instant-Answer API, which returns encyclopaedic
    blurbs, not web results, so it never surfaces report PDFs. The HTML endpoint does.
    Kept here (not in search.py) so this fallback stays self-contained.
    """
    try:
        import requests
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                          headers=_HEADERS, timeout=20)
        if r.status_code != 200:
            return []
    except Exception as e:
        log.info("web search failed", error=str(e)[:80])
        return []
    out: list[str] = []
    # DDG wraps targets as /l/?uddg=<url-encoded-target>
    for enc in re.findall(r"uddg=([^\"&]+)", r.text):
        u = urllib.parse.unquote(enc)
        if u.split("?")[0].lower().endswith(".pdf") and u not in out:
            out.append(u)
        if len(out) >= cap:
            break
    return out


def _pdf_candidate_urls(ticker: str, company_name: Optional[str]) -> list[str]:
    """Web-search for the company's own annual-report PDF; return direct .pdf links."""
    name = (company_name or ticker).strip()
    queries = [
        f"{name} annual report pdf",
        f"{name} annual report financial statements pdf",
        f"{ticker} PSX annual report pdf",
    ]
    urls: list[str] = []
    for q in queries:
        for u in _web_pdf_links(q):
            if u not in urls:
                urls.append(u)
        if len(urls) >= _MAX_CANDIDATES:
            break
    return urls[:_MAX_CANDIDATES]


def _fetch_pdf(url: str) -> Optional[bytes]:
    try:
        import requests
        r = requests.get(url, headers=_HEADERS, timeout=_DOWNLOAD_TIMEOUT, verify=False)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return r.content
    except Exception as e:
        log.info("IR annual-report download failed", url=url[:80], error=str(e)[:80])
    return None


def find_annual_report_financials(ticker: str,
                                  company_name: Optional[str] = None) -> Optional[dict]:
    """
    Return an accurate financials dict from the company's own digital annual report,
    or None. A result is accepted only if it parses to a sane statement (revenue and
    total assets both present), so a stray brochure PDF cannot be mistaken for one.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None
    from intelligence.pdf_extractor import extract_financials
    for url in _pdf_candidate_urls(ticker, company_name):
        pdf = _fetch_pdf(url)
        if not pdf:
            continue
        fin = extract_financials(pdf, ocr=False).get("financials") or {}
        if fin.get("revenue") and fin.get("total_assets"):
            fin["_method"] = "ir_annual_report"
            fin["_source_url"] = url
            log.info("IR annual report used", ticker=ticker, url=url[:90])
            return fin
    return None
