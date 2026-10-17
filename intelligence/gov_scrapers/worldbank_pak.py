"""
gov_scrapers/worldbank_pak.py — World Bank Pakistan macro indicators.

Reliable structured JSON backbone for the macro layer. The official Pakistani sites
are best-effort; this guarantees core macro figures (GDP growth, inflation,
unemployment, current account, etc.) are always available.
Source: https://api.worldbank.org/v2 (no key required).
"""

from __future__ import annotations

from typing import Optional

from intelligence.gov_scrapers.base import BasePakGovScraper
from database.macro_data import upsert_macro, upsert_sbp_indicator

# World Bank indicator code → (our indicator name, unit)
_INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": ("gdp_growth", "%"),
    "FP.CPI.TOTL.ZG":    ("inflation", "%"),
    "SL.UEM.TOTL.ZS":    ("unemployment", "%"),
    "BN.CAB.XOKA.GD.ZS": ("current_account_pct_gdp", "% of GDP"),
    "GC.DOD.TOTL.GD.ZS": ("govt_debt_pct_gdp", "% of GDP"),
    "NE.EXP.GNFS.ZS":    ("exports_pct_gdp", "% of GDP"),
    "NE.IMP.GNFS.ZS":    ("imports_pct_gdp", "% of GDP"),
    "NY.GDP.PCAP.CD":    ("gdp_per_capita_usd", "USD"),
    # Remittances - one of the plan's Priority 11 targets (GDP growth, current
    # account balance, remittances), previously missing entirely. Verified
    # 2026-07-28: 2025 = $40.478bn against the live API.
    "BX.TRF.PWKR.CD.DT": ("remittances_usd", "USD"),
}

_BASE = "https://api.worldbank.org/v2/country/PAK/indicator/{code}?format=json&per_page=5"
_CODE_OF = {name: code for code, (name, _unit) in _INDICATORS.items()}


class WorldBankPakScraper(BasePakGovScraper):
    scraper_id = "worldbank_pak"
    label = "World Bank Pakistan"
    category = "stats"
    default_freshness_hours = 8760
    # Verified 2026-07-28: GDP growth (3.70%, 2025) and current account (-0.11% of
    # GDP, 2025) matched the live API exactly by hand. govt_debt_pct_gdp has been
    # null for Pakistan in every recent year on the source itself (checked directly,
    # not a scraper bug) - 7 of 9 indicators returning data per run is the realistic
    # bar, not 9 of 9.
    target_success_rate = 0.75

    def scrape(self) -> Optional[dict]:
        out: dict = {}
        for code, (name, unit) in _INDICATORS.items():
            data = self.get_json(_BASE.format(code=code))
            if not data or len(data) < 2 or not data[1]:
                continue
            for row in data[1]:
                if row.get("value") is not None:
                    out[name] = {"period": row["date"], "value": row["value"], "unit": unit}
                    break
        return out or None

    def persist(self, data: dict) -> int:
        n = 0
        for name, rec in data.items():
            upsert_macro("worldbank", name, rec["period"], rec["value"], rec["unit"])
            n += 1
            # Mirror inflation into sbp_indicators.cpi so the macro summary can read it
            if name == "inflation":
                upsert_sbp_indicator("cpi", rec["period"], rec["value"], "%", source="worldbank")

            # The plan's three named targets (GDP growth, current account, remittances)
            # get full provenance so they are independently verifiable, matching every
            # other Phase 2 scraper.
            if name in ("gdp_growth", "current_account_pct_gdp", "remittances_usd"):
                from database.provenance import record_observation
                record_observation(
                    indicator=name,
                    value=rec["value"],
                    period=rec["period"],
                    unit=rec["unit"],
                    source="World Bank",
                    source_url=_BASE.format(code=_CODE_OF[name]),
                    source_snippet=f"{name} {rec['period']}: {rec['value']}",
                    method_used="json_api",
                    scraper_id=self.scraper_id,
                )
        return n
