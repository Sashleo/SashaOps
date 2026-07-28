"""
gov_scrapers/pta_stats.py — PTA telecom subscriber count.

Verified 2026-07-28 (docs/scraper_verification.md Priority 10): the previous version
searched PTA page text for a subscriber number near keywords - the real figures are
never plain text, they are embedded as Highcharts JS chart configs (JSON literals
inside inline <script> blocks), which is why the scraper's own comment called the
page "JavaScript-rendered" (accurate, but the fix is JSON extraction, not a browser -
the data is already in the raw HTML, just as JS object literals rather than text or
a <table>).

https://www.pta.gov.pk/category/telecom-indicators/164 embeds a "Monthly Mobile
Cellular Subscribers" Highcharts chart with per-operator series (Jazz, Ufone, Zong,
Telenor, SCO) plus a "Total" series, and an xAxis "categories" array of month labels
(e.g. "Jun-26"). The chart block is bounded by its title string and the next
`Highcharts.chart(` call (the following chart, Annual Cellular Subscribers) so the
right "Total" series is read even though the page embeds ~20 charts total.
"""

from __future__ import annotations

import re
from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_pta

_INDICATORS_PAGE = "https://www.pta.gov.pk/category/telecom-indicators/164"
_CHART_TITLE = '"Monthly Mobile Cellular Subscribers"'

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _period_from_label(label: str) -> Optional[str]:
    """'Jun-26' -> '2026-06'. None if unparseable."""
    m = re.match(r"([A-Za-z]{3})-(\d{2})$", label.strip())
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    year = 2000 + int(m.group(2))
    return f"{year:04d}-{mon:02d}"


def _extract_latest_total(html: str) -> Optional[dict]:
    """
    Scope to the "Monthly Mobile Cellular Subscribers" chart block specifically
    (bounded by the NEXT Highcharts.chart() call, which starts a different chart),
    so a same-named "Total" series in another chart is never picked up instead.
    """
    title_idx = html.find(_CHART_TITLE)
    if title_idx < 0:
        return None
    next_chart_idx = html.find("Highcharts.chart(", title_idx)
    block = html[title_idx: next_chart_idx if next_chart_idx > 0 else len(html)]

    cat_m = re.search(r'"categories":\s*(\[[^\]]+\])', block)
    total_m = re.search(r'"name":"Total".*?"data":(\[[^\]]+\])', block)
    if not cat_m or not total_m:
        return None

    import json
    try:
        categories = json.loads(cat_m.group(1))
        totals = json.loads(total_m.group(1))
    except Exception:
        return None
    if not categories or not totals or len(categories) != len(totals):
        return None

    return {
        "subscribers": totals[-1],
        "period_label": categories[-1],
        "period": _period_from_label(categories[-1]),
    }


class PTAStatsScraper(BasePakGovScraper):
    scraper_id = "pta_stats"
    label = "PTA mobile subscribers"
    category = "stats"
    default_freshness_hours = 720   # PTA updates this chart roughly monthly
    # Verified 2026-07-28: June 2026 total (208,057,255) matched the chart's own
    # embedded data exactly. A single well-defined chart on a stable government
    # page, so a high target is reasonable.
    target_success_rate = 0.9
    source_url = _INDICATORS_PAGE

    def scrape(self) -> Optional[dict]:
        try:
            import requests
            from intelligence.gov_scrapers.base import HEADERS
            r = requests.get(_INDICATORS_PAGE, headers=HEADERS, timeout=25, verify=False)
            if r.status_code != 200:
                return None
            html = r.text
        except Exception:
            return None

        result = _extract_latest_total(html)
        if not result or not result.get("period"):
            return None
        result["_source_url"] = _INDICATORS_PAGE
        return result

    def persist(self, data: dict) -> int:
        if data.get("subscribers") is None or not data.get("period"):
            return 0
        subs_millions = round(data["subscribers"] / 1_000_000, 2)
        upsert_pta("mobile_subs_total", data["period"], subs_millions, "million")
        from database.provenance import record_observation
        record_observation(
            indicator="pta_mobile_subscribers_million",
            value=subs_millions,
            period=data["period"],
            unit="million",
            source="PTA",
            source_url=data.get("_source_url", ""),
            source_snippet=f"Total (chart) {data['period_label']}: {data['subscribers']}",
            method_used="html_bs4",
            scraper_id=self.scraper_id,
        )
        return 1
