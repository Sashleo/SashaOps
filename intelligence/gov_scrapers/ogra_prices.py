"""
gov_scrapers/ogra_prices.py — OGRA petroleum prices.

Verified 2026-07-28 (docs/scraper_verification.md Priority 7): OGRA's price pages
render their content client-side via JavaScript — a raw-byte inspection of
requests-fetched HTML found no price data anywhere (confirmed, not assumed), so no
requests+BeautifulSoup approach could ever have worked. This scraper renders the
page with Playwright (already a project dependency) to read the real download links,
then reads price figures out of OGRA's own PDF notifications.

OGRA's "Notified Petroleum Prices" feed mixes document types by day — some entries
are Petrol(MS)/Diesel(HSD) ex-depot price computations (digital, real text layer),
others are single-product SRO notifications (e.g. Kerosene-only) or SCANNED images
with no text layer at all (verified: 4 of the 5 most recent entries checked were
scanned). Rather than trust whichever is newest, this walks the list newest-first
and uses the first entry that is BOTH digital and actually contains a Petrol/Diesel
computation — skipping scanned or wrong-product entries rather than guessing at
their content via OCR. The period stored is the PRICE'S OWN effective date (read
from inside the PDF, e.g. "Price Effective 23 July 2026"), not today's date, so a
stale-but-verified figure is never mislabeled as current.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_ogra_price

_LISTING_PAGE = "https://www.ogra.org.pk/notified-petroleum-prices"
_MAX_CANDIDATES = 8       # how far back to look for a usable digital entry
_MIN_TEXT_CHARS = 500     # below this, treat the PDF as scanned (no usable text layer)

_TOTAL_LINE = re.compile(r"Total\s+(Petrol|Diesel)\s+Price", re.I)
_TOTAL_VALUE = re.compile(r"T=\{P\+Q\+R\+S\}\s*([\d.]+)")
_EFFECTIVE_DATE = re.compile(
    r"(Petrol|Diesel)\s*\([A-Z]+\)\s*Price\s*Effective\s+(\d{1,2}\s+\w+\s+\d{4})", re.I,
)


def _list_candidates() -> list[tuple[str, str]]:
    """(title, pdf_url) pairs from the rendered listing, newest first."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(_LISTING_PAGE, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            items = page.eval_on_selector_all(
                "li.dd-item",
                """els => els.map(e => {
                    const p = e.querySelector('p');
                    const a = e.querySelector('a.download-pdf');
                    return [p ? p.innerText.trim() : '', a ? a.href : ''];
                })""",
            )
            browser.close()
        return [(t, h) for t, h in items if h]
    except Exception:
        return []


def _download(url: str) -> Optional[bytes]:
    try:
        from intelligence.gov_scrapers.base import HEADERS, safe_get
        r = safe_get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return r.content
    except Exception:
        pass
    return None


def _to_iso(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%d %B %Y").date().isoformat()
    except ValueError:
        return date_str


def _extract_prices(pdf_bytes: bytes) -> Optional[dict]:
    """Parse Total Petrol/Diesel price + each product's own effective date. None if
    the PDF has no usable text layer or does not contain a Petrol/Diesel computation."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = [p.extract_text() or "" for p in pdf.pages]
    except Exception:
        return None

    full_text = "\n".join(pages_text)
    if len(full_text) < _MIN_TEXT_CHARS:
        return None   # scanned / no text layer
    if "petrol" not in full_text.lower() and "diesel" not in full_text.lower():
        return None   # wrong document type (e.g. a Kerosene-only SRO)

    out: dict = {}
    for page_text in pages_text:
        date_m = _EFFECTIVE_DATE.search(page_text)
        lines = page_text.split("\n")
        for i, line in enumerate(lines):
            if _TOTAL_LINE.search(line) and i + 1 < len(lines):
                val_m = _TOTAL_VALUE.search(lines[i + 1])
                if not val_m:
                    continue
                product = "petrol" if "petrol" in line.lower() else "hsd"
                out[product] = float(val_m.group(1))
                if date_m:
                    out[f"{product}_date"] = _to_iso(date_m.group(2))
    return out or None


class OGRAPricesScraper(BasePakGovScraper):
    scraper_id = "ogra_prices"
    label = "OGRA fuel prices"
    category = "regulator"
    default_freshness_hours = 336
    # Verified 2026-07-28: 1 of the 5 most recent notifications was a usable digital
    # Petrol/Diesel computation (the rest were scanned or single-product notices), so
    # a moderate target reflects the source's real day-to-day mix rather than every
    # run failing to find a fresh one.
    target_success_rate = 0.6
    source_url = _LISTING_PAGE

    def scrape(self) -> Optional[dict]:
        candidates = _list_candidates()
        for title, pdf_url in candidates[:_MAX_CANDIDATES]:
            pdf_bytes = _download(pdf_url)
            if not pdf_bytes:
                continue
            prices = _extract_prices(pdf_bytes)
            if prices:
                prices["_source_url"] = pdf_url
                prices["_title"] = title
                return prices
        return None

    def persist(self, data: dict) -> int:
        n = 0
        for product, unit in (("petrol", "PKR/litre"), ("hsd", "PKR/litre")):
            if product in data:
                period = data.get(f"{product}_date") or data.get("_title", "")
                upsert_ogra_price(product, period, data[product], unit)
                n += 1
                from database.provenance import record_observation
                record_observation(
                    indicator=f"ogra_{product}_price_pkr_litre",
                    value=data[product],
                    period=period,
                    unit=unit,
                    source="OGRA",
                    source_url=data.get("_source_url", ""),
                    source_snippet=data.get("_title", ""),
                    method_used="pdf",
                    scraper_id=self.scraper_id,
                )
        return n
