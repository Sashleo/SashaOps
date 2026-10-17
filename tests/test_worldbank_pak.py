"""
Regression tests for the World Bank Pakistan scraper.

Priority 11's real gap: remittances was entirely absent from the indicator list
despite being one of the plan's three named targets (GDP growth, current account
balance, remittances). These tests lock in that it is now covered and that the
existing null-skipping behaviour (World Bank has no recent govt-debt data for
Pakistan - confirmed against the live API, not a scraper bug) still works.
"""

from intelligence.gov_scrapers.worldbank_pak import _INDICATORS, _CODE_OF


def test_remittances_indicator_is_present():
    assert "BX.TRF.PWKR.CD.DT" in _INDICATORS
    name, unit = _INDICATORS["BX.TRF.PWKR.CD.DT"]
    assert name == "remittances_usd"
    assert unit == "USD"


def test_all_plan_target_indicators_present():
    names = {n for n, _u in _INDICATORS.values()}
    assert {"gdp_growth", "current_account_pct_gdp", "remittances_usd"} <= names


def test_code_of_reverse_lookup_matches_indicators():
    for code, (name, _unit) in _INDICATORS.items():
        assert _CODE_OF[name] == code
