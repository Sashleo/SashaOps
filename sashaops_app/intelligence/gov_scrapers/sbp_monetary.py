"""
gov_scrapers/sbp_monetary.py — State Bank of Pakistan monetary indicators.

Latest policy rate, tracked against the SBP Monetary Policy Committee (MPC) calendar:
the scraper knows the MPC meeting schedule, so after a decision date it force-refreshes
(bypassing the daily cache) to capture the newly-announced rate, records the trend of
the change (cut / hold / hike, in bps), and exposes the "as-of" date and the next
scheduled MPC meeting. Robust numeric parsing with a Pakistan-realistic plausibility
band so a stray number near a policy-rate keyword can't be stored as the rate.
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Optional

import structlog

from intelligence.gov_scrapers.base import BasePakGovScraper
from intelligence.scraper_base import ScrapeResult
from database.macro_data import upsert_sbp_indicator, get_sbp_indicator

log = structlog.get_logger()

_PAGES = [
    # Primary — contains the policy rate clearly labelled
    "https://www.sbp.org.pk/ecodata/index2.asp",
    "https://www.sbp.org.pk/ecodata/",
    "https://www.sbp.org.pk/m_policy/index.asp",
    "https://www.sbp.org.pk/monetary/",
    "https://www.sbp.org.pk/",
]

_PRESS_PAGES = [
    "https://www.sbp.org.pk/m_policy/index.asp",
    "https://www.sbp.org.pk/press/index.asp",
    "https://www.sbp.org.pk/press/",
]

_KEYWORDS = [
    "policy rate", "policy (target) rate", "target rate", "monetary policy",
    "kept the policy rate", "maintained at", "reduce the policy rate",
    "raised the policy rate", "revised to", "rate to", "kept at", "per cent", "percent",
]

_DOMAIN_KEYWORDS = [
    "policy rate", "monetary policy", "inflation", "interest rate",
    "discount rate", "basis points", "exchange rate", "reserves",
    "current account", "fiscal deficit", "remittances",
]

# Pakistan's SBP policy rate has ranged ~7%–22% in recent years. Reject readings
# outside a realistic band so a stray "3" (e.g. "3 members voted") can't be stored.
_PLAUSIBLE_MIN = 5.0
_PLAUSIBLE_MAX = 30.0

# SBP MPC advance calendar. SBP publishes scheduled meeting dates ~6 times/year
# (roughly every 6–7 weeks). Known/anticipated dates below; beyond the last entry
# the schedule is projected forward on the same cadence so the scraper keeps working
# without a code change as time advances.
_MPC_MEETINGS = [
    "2024-12-16", "2025-01-27", "2025-03-10", "2025-05-05", "2025-06-16",
    "2025-07-30", "2025-09-15", "2025-10-27", "2025-12-15",
    "2026-01-26", "2026-03-09", "2026-05-04", "2026-06-16", "2026-07-30",
    "2026-09-14", "2026-10-26", "2026-12-14",
]
_MPC_CADENCE_DAYS = 45   # fallback projection interval beyond the known calendar


def _mpc_dates(today: date) -> list[date]:
    """Known MPC dates, extended by cadence projection to just past `today`."""
    dates = [datetime.fromisoformat(d).date() for d in _MPC_MEETINGS]
    d = dates[-1]
    while d < today + timedelta(days=_MPC_CADENCE_DAYS):
        d = d + timedelta(days=_MPC_CADENCE_DAYS)
        dates.append(d)
    return dates


def most_recent_mpc(today: Optional[date] = None) -> Optional[date]:
    today = today or date.today()
    past = [d for d in _mpc_dates(today) if d <= today]
    return max(past) if past else None


def next_mpc(today: Optional[date] = None) -> Optional[date]:
    today = today or date.today()
    fut = [d for d in _mpc_dates(today) if d > today]
    return min(fut) if fut else None


def _to_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(s[:7], "%Y-%m").date()
        except Exception:
            return None


class SBPMonetaryScraper(BasePakGovScraper):
    scraper_id = "sbp_monetary"
    label = "SBP monetary policy"
    category = "macro"
    default_freshness_hours = 720
    target_success_rate = 0.0   # set from the live suite (Task 2.4)

    # ── MPC-calendar-aware refresh ────────────────────────────────────────────
    def is_refresh_due(self, today: Optional[date] = None) -> bool:
        """
        True if the stored policy rate is missing, older than the most recent MPC
        meeting (a decision has happened since), or stale beyond one cadence period.
        """
        today = today or date.today()
        row = get_sbp_indicator("policy_rate")
        if not row or row.get("value") is None:
            return True
        last = _to_date(row.get("fetched_at") or "") or _to_date(row.get("period") or "")
        if not last:
            return True
        recent = most_recent_mpc(today)
        if recent and last < recent:
            return True   # an MPC decision was announced after our last update
        return (today - last).days > _MPC_CADENCE_DAYS

    def fetch(self) -> "ScrapeResult":
        """Force a re-scrape (bypass the daily cache) when an MPC refresh is due."""
        if self.is_refresh_due():
            p = self._cache_path()
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        return super().fetch()

    # ── Scrape ────────────────────────────────────────────────────────────────
    def scrape(self) -> Optional[dict]:
        out: dict = {}
        all_text = ""

        for url in _PAGES:
            soup = self.get_soup(url)
            if not soup:
                continue
            text = soup.get_text(" ", strip=True)
            all_text += " " + text
            rate, snippet = self.number_near_ctx(text, *_KEYWORDS, window=100)
            if rate is not None and _PLAUSIBLE_MIN <= rate <= _PLAUSIBLE_MAX:
                out["policy_rate"] = rate
                # Provenance: exactly where and from what text we read it.
                out["_source_url"] = url
                out["_snippet"] = snippet
                out["_method"] = "html_bs4"
                break

        if "policy_rate" not in out:
            for press_url in _PRESS_PAGES:
                press_text = self.get_press_release_text(press_url, max_releases=2)
                if press_text:
                    all_text += " " + press_text
                    rate, snippet = self.number_near_ctx(press_text, *_KEYWORDS, window=120)
                    if rate is not None and _PLAUSIBLE_MIN <= rate <= _PLAUSIBLE_MAX:
                        out["policy_rate"] = rate
                        out["_source_url"] = press_url
                        out["_snippet"] = snippet
                        out["_method"] = "html_bs4"
                        break

        if all_text.strip():
            nlp = self.nlp_extract(all_text, _DOMAIN_KEYWORDS)
            if nlp.get("regulatory_text"):
                out["regulatory_text"] = nlp["regulatory_text"]
            if nlp.get("nlp_entities"):
                out["nlp_entities"] = nlp["nlp_entities"]

        return out if out else None

    # ── Persist (+ trend tracking) ────────────────────────────────────────────
    def persist(self, data: dict) -> int:
        n = 0
        if "policy_rate" in data:
            new_rate = float(data["policy_rate"])
            # Capture the previous reading BEFORE we overwrite, to compute the trend.
            prev = get_sbp_indicator("policy_rate")
            prev_rate = float(prev["value"]) if (prev and prev.get("value") is not None) else None

            period = date.today().strftime("%Y-%m")
            upsert_sbp_indicator("policy_rate", period, new_rate, "%", source="SBP")
            n += 1

            # Verifiable pipeline: store the value WITH its provenance so any future
            # discrepancy can be traced to the exact page + text it was read from.
            from database.provenance import record_observation
            record_observation(
                indicator="sbp_policy_rate",
                value=new_rate,
                period=period,
                unit="%",
                source="SBP",
                source_url=data.get("_source_url", ""),
                source_snippet=data.get("_snippet", ""),
                method_used=data.get("_method", "html_bs4"),
                scraper_id=self.scraper_id,
            )

            # Trend: signed change in basis points vs the previous distinct reading.
            if prev_rate is not None and abs(new_rate - prev_rate) > 1e-9:
                bps = round((new_rate - prev_rate) * 100)
                upsert_sbp_indicator("policy_rate_change_bps", period, float(bps), "bps", source="SBP")
                n += 1
                direction = "hike" if bps > 0 else "cut"
                log.info("SBP policy rate updated", rate=new_rate, prev=prev_rate,
                         change_bps=bps, direction=direction)
        return n


def get_rate_status() -> dict:
    """
    Compact policy-rate status for display (Economy module / macro summary):
      {rate, as_of, trend_bps, trend, next_mpc, refresh_due}
    """
    row = get_sbp_indicator("policy_rate")
    change = get_sbp_indicator("policy_rate_change_bps")
    today = date.today()
    rate = float(row["value"]) if (row and row.get("value") is not None) else None
    bps = int(change["value"]) if (change and change.get("value") is not None) else None
    trend = "hold"
    if bps:
        trend = f"{'hike' if bps > 0 else 'cut'} {abs(bps)}bps"
    nxt = next_mpc(today)
    return {
        "rate": rate,
        "as_of": (row.get("period") if row else None),
        "trend_bps": bps,
        "trend": trend,
        "next_mpc": nxt.isoformat() if nxt else None,
        "refresh_due": SBPMonetaryScraper().is_refresh_due(today),
    }
