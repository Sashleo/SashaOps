"""
gov_scrapers/pbs_lsmi.py — PBS Large Scale Manufacturing Index (LSM/QIM) + CPI.

Verified 2026-07-28 (docs/scraper_verification.md Priority 9): the previous version
searched pbs.gov.pk pages for a growth number near "LSM"/"QIM" keywords in free text
- the real figures live in downloadable monthly Excel/PDF releases, not on-page text,
so it could not reliably find them. It also had NO CPI extraction at all, despite
downstream code (database/macro_data.py get_macro_summary_text) already expecting a
"cpi" indicator - a real, previously invisible gap.

Real sources, found by following the actual site structure:
  - QIM (LSM index): https://www.pbs.gov.pk/industry-2/ links to a monthly
    "Table 1 <Month>-<Year>.xlsx" - the QIM summary row and %Change columns are the
    headline LSM index level and YoY growth.
  - CPI: https://www.pbs.gov.pk/?page_id=11078 links to a monthly
    "Press-Release-<Month>-<Year>.pdf" whose headline table states the YoY inflation
    rate directly.
Both listings carry several months of past releases un-ordered by date in the DOM in
some cases, so the latest is found by parsing month/year out of each candidate link
and taking the maximum - not by assuming page order.
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_lsmi, upsert_macro, upsert_sbp_indicator

_INDUSTRY_PAGE = "https://www.pbs.gov.pk/industry-2/"
_PRESS_PAGE = "https://www.pbs.gov.pk/?page_id=11078"

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
# PBS's uploads folder has a known typo, "Febuary" (missing the first "r") -
# matched explicitly rather than silently failing to parse that month's release.
_MONTH_PATTERN = "|".join(list(_MONTHS.keys()) + ["febuary"])
_MONTH_YEAR_RE = re.compile(rf"({_MONTH_PATTERN})[\s\-_]*'?(\d{{4}})", re.I)


def _month_num(name: str) -> int:
    return _MONTHS.get(name.lower(), _MONTHS.get(name.lower().replace("febuary", "february"), 0))


def _latest_link(links: list[tuple[str, str]], text_pat: re.Pattern,
                 href_suffix: str) -> Optional[tuple[str, str, int, int]]:
    """
    From (title, href) pairs, keep those matching text_pat and ending href_suffix,
    parse a month/year out of title or href, and return the one with the latest
    (year, month) - not simply the first one in DOM order, since these listings are
    not reliably date-sorted.
    """
    best = None
    for title, href in links:
        if not href.lower().endswith(href_suffix):
            continue
        if not text_pat.search(title):
            continue
        m = _MONTH_YEAR_RE.search(title) or _MONTH_YEAR_RE.search(href)
        if not m:
            continue
        month, year = _month_num(m.group(1)), int(m.group(2))
        if best is None or (year, month) > (best[2], best[3]):
            best = (title, href, year, month)
    return best


def _download(url: str) -> Optional[bytes]:
    try:
        from intelligence.gov_scrapers.base import HEADERS, safe_get
        r = safe_get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


class PBSLsmiScraper(BasePakGovScraper):
    scraper_id = "pbs_lsmi"
    label = "PBS statistics (LSM + CPI)"
    category = "stats"
    default_freshness_hours = 720
    # Verified 2026-07-28: QIM May-2026 (116.10, YoY -0.98%) and CPI June-2026
    # (11.07% YoY) both matched hand-read ground truth exactly. Two independent
    # monthly releases in one run, so a real drop likely means one series' page
    # structure changed, not both.
    target_success_rate = 0.85
    source_url = _INDUSTRY_PAGE

    def scrape(self) -> Optional[dict]:
        out: dict = {}

        lsm = self._scrape_lsm()
        if lsm:
            out.update(lsm)

        cpi = self._scrape_cpi()
        if cpi:
            out.update(cpi)

        return out or None

    def _scrape_lsm(self) -> Optional[dict]:
        soup = self.get_soup(_INDUSTRY_PAGE)
        if not soup:
            return None
        links = [((a.get_text() or "").strip(), a["href"])
                for a in soup.find_all("a", href=True)]
        best = _latest_link(links, re.compile(r"^Table\s*1\b", re.I), ".xlsx")
        if not best:
            return None
        title, href, year, month = best

        raw = _download(href)
        if not raw:
            return None
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
            ws = wb.worksheets[0]
            for row in ws.iter_rows(values_only=True):
                if row and str(row[0]).strip().upper() == "QIM":
                    # Columns per PBS's own header: ... index level (col 4),
                    # ... %Change of <month> over prior month (col 9).
                    index_level = row[4]
                    pct_mom = row[9]
                    pct_yoy = row[10] if len(row) > 10 else None
                    if isinstance(index_level, (int, float)):
                        return {
                            "lsm_index": round(float(index_level), 2),
                            "lsm_growth_mom": round(float(pct_mom), 2) if isinstance(pct_mom, (int, float)) else None,
                            "lsm_growth": round(float(pct_yoy), 2) if isinstance(pct_yoy, (int, float)) else None,
                            "lsm_period": f"{year:04d}-{month:02d}",
                            "_lsm_source_url": href,
                            "_lsm_title": title,
                        }
        except Exception:
            return None
        return None

    def _scrape_cpi(self) -> Optional[dict]:
        soup = self.get_soup(_PRESS_PAGE)
        if not soup:
            return None
        links = [((a.get_text() or "").strip(), a["href"])
                for a in soup.find_all("a", href=True)]
        best = _latest_link(
            links, re.compile(r"consumer price index.*inflation", re.I), ".pdf",
        )
        if not best:
            return None
        title, href, year, month = best

        raw = _download(href)
        if not raw:
            return None
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = pdf.pages[0].extract_text() or ""
        except Exception:
            return None

        m = re.search(r"Year on Year\)\s*([\d.]+)", text)
        if not m:
            return None
        return {
            "cpi_yoy": round(float(m.group(1)), 2),
            "cpi_period": f"{year:04d}-{month:02d}",
            "_cpi_source_url": href,
            "_cpi_title": title,
            "_cpi_snippet": next((l for l in text.split("\n") if "Year on Year" in l), ""),
        }

    def persist(self, data: dict) -> int:
        n = 0
        if data.get("lsm_growth") is not None:
            period = data.get("lsm_period") or date.today().strftime("%Y-%m")
            upsert_lsmi("overall", period, data.get("lsm_index"), data["lsm_growth"])
            upsert_macro("pbs", "lsm_growth", period, data["lsm_growth"], "%")
            n += 1

        if data.get("cpi_yoy") is not None:
            period = data.get("cpi_period") or date.today().strftime("%Y-%m")
            # Written to sbp_indicators(indicator='cpi') because that is what
            # get_macro_summary_text() already reads via get_sbp_indicator("cpi") -
            # closing a gap where nothing ever populated it.
            upsert_sbp_indicator("cpi", period, data["cpi_yoy"], "%", source="PBS")
            from database.provenance import record_observation
            record_observation(
                indicator="cpi_yoy",
                value=data["cpi_yoy"],
                period=period,
                unit="%",
                source="PBS",
                source_url=data.get("_cpi_source_url", ""),
                source_snippet=data.get("_cpi_snippet", ""),
                method_used="pdf",
                scraper_id=self.scraper_id,
            )
            n += 1

        return n
