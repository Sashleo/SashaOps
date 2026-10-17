"""
gov_scrapers/fbr_revenue.py — FBR tax revenue collection.

Verified 2026-07-28 (docs/scraper_verification.md Priority 6): the previous version
scraped fbr.gov.pk/Newsroom/ for a number near a "revenue collection" keyword — that
page is a news/nav homepage with no collection figures on it at all, so it could
never have worked. The real source is a PDF table published on the "Revenue
Collections" page: "MONTH WISE / TAX WISE NET COLLECTION DURING FY <year>", one PDF
per fiscal year, listed newest-first. This scraper downloads the latest one and reads
the last reported MONTH row and the FY Total row directly out of its table.
"""

from __future__ import annotations

import re
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper, HEADERS
from database.macro_data import upsert_fbr_revenue, upsert_macro

_COLLECTIONS_PAGE = "https://www.fbr.gov.pk/revenue-collections/142253/131355"

_MONTH_NAMES = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
               "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")
_MONTH_ROW = re.compile(
    r"^(" + "|".join(_MONTH_NAMES) + r")\s+([\d,]+\.?\d*(?:\s+[\d,]+\.?\d*){3,4})\s*$",
    re.I,
)
_TOTAL_ROW = re.compile(r"^Total\s+([\d,]+\.?\d*(?:\s+[\d,]+\.?\d*){3,4})\s*$", re.I)
_FY_TITLE = re.compile(r"NET\s+COLLECTION\s+DURING\s+FY:?\s*(\d{4}-\d{2,4})", re.I)


def _last_number(numbers_str: str) -> Optional[float]:
    """The rightmost column on a table row is the TOTAL across tax types."""
    nums = re.findall(r"[\d,]+\.?\d*", numbers_str)
    if not nums:
        return None
    try:
        return float(nums[-1].replace(",", ""))
    except ValueError:
        return None


class FBRRevenueScraper(BasePakGovScraper):
    scraper_id = "fbr_revenue"
    label = "FBR revenue"
    category = "regulator"
    default_freshness_hours = 720
    # Verified 2026-07-28: FY2024-25 PDF matched hand-read ground truth exactly
    # (June 1,510.4bn; FY total 11,744.3bn). The source only publishes a new PDF a
    # few times a year (FBR updates it, not us), so a modest target reflects normal
    # "no new PDF yet" gaps, not scraper breakage.
    target_success_rate = 0.9
    source_url = _COLLECTIONS_PAGE

    def scrape(self) -> Optional[dict]:
        soup = self.get_soup(_COLLECTIONS_PAGE)
        if not soup:
            return None

        # PDF links are listed newest fiscal-year first (verified against the live
        # page); take the first "MONTH WISE ... COLLECTION DURING FY ..." link.
        pdf_url = None
        for a in soup.find_all("a", href=True):
            title = (a.get_text() or "").strip()
            if re.search(r"month\s*wise.*collection\s*during\s*fy", title, re.I) \
                    and a["href"].lower().endswith(".pdf"):
                pdf_url = a["href"]
                break
        if not pdf_url:
            return None

        pdf_bytes = self._download_pdf(pdf_url)
        if not pdf_bytes:
            return None

        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception:
            return None

        fy_match = _FY_TITLE.search(text)
        fy = fy_match.group(1) if fy_match else None

        latest_month, latest_value, latest_line = None, None, None
        fy_total = None
        for line in text.split("\n"):
            ls = line.strip()
            m = _MONTH_ROW.match(ls)
            if m:
                latest_month = m.group(1).upper()
                latest_value = _last_number(m.group(2))
                latest_line = ls
                continue
            t = _TOTAL_ROW.match(ls)
            if t:
                fy_total = _last_number(t.group(1))

        if latest_value is None and fy_total is None:
            return None

        return {
            "fy": fy,
            "latest_month": latest_month,
            "latest_value_bn": latest_value,
            "latest_line": latest_line,
            "fy_total_bn": fy_total,
            "_source_url": pdf_url,
        }

    def _download_pdf(self, url: str) -> Optional[bytes]:
        try:
            from intelligence.gov_scrapers.base import safe_get
            r = safe_get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                return r.content
        except Exception:
            pass
        return None

    def persist(self, data: dict) -> int:
        n = 0
        fy = data.get("fy") or "unknown-fy"

        if data.get("latest_value_bn") is not None and data.get("latest_month"):
            period = f"FY{fy}-{data['latest_month'].title()}"
            upsert_fbr_revenue("total", period, data["latest_value_bn"], None)
            upsert_macro("fbr", "tax_collection_monthly", period,
                         data["latest_value_bn"], "PKR_bn")
            n += 1
            from database.provenance import record_observation
            record_observation(
                indicator="fbr_tax_collection_monthly_bn",
                value=data["latest_value_bn"],
                period=period,
                unit="PKR_bn",
                source="FBR",
                source_url=data.get("_source_url", ""),
                source_snippet=data.get("latest_line", ""),
                method_used="pdf",
                scraper_id=self.scraper_id,
            )

        if data.get("fy_total_bn") is not None:
            period = f"FY{fy}"
            upsert_fbr_revenue("total", period, data["fy_total_bn"], None)
            upsert_macro("fbr", "tax_collection_fy_total", period,
                         data["fy_total_bn"], "PKR_bn")
            n += 1

        return n
