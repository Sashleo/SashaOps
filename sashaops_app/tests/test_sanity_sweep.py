"""
Regression tests for the sanity-sweep pipeline (Task 2.7).

Locks in the PSX-price rule (psx_prices.current > 0 and < 10,000,000 - the plan's
own explicit bound, living outside the pk_macro/SANITY_BOUNDS system since it's a
different table) and the Data Health surfacing layer that turns flagged rows into
a distinct, readable violation with a manual-check prompt.
"""

from database.provenance import _check_psx_price


def test_zero_price_is_implausible():
    assert _check_psx_price(0.0) == "implausible"


def test_negative_price_is_implausible():
    assert _check_psx_price(-5.0) == "implausible"


def test_absurdly_large_price_is_implausible():
    assert _check_psx_price(99_999_999.0) == "implausible"


def test_normal_price_is_not_flagged():
    assert _check_psx_price(327.12) is None


def test_none_price_is_not_flagged():
    # Missing data reduces confidence, never gets treated as a violation of a bound.
    assert _check_psx_price(None) is None


def test_boundary_is_exclusive():
    assert _check_psx_price(10_000_000.0) == "implausible"


def test_get_sanity_violations_formats_macro_and_price_rows(monkeypatch):
    import core.data_health as dh

    fake_macro = [{
        "indicator": "sbp_policy_rate", "value": 999.0, "period": "2026-07",
        "source": "SBP", "source_url": "https://sbp.example", "scraper_id": "sbp_monetary",
    }]
    fake_prices = [{"symbol": "TESTX", "current": 0.0}]

    monkeypatch.setattr("database.provenance.get_flagged", lambda: fake_macro)
    monkeypatch.setattr("database.provenance.get_flagged_prices", lambda: fake_prices)

    violations = dh.get_sanity_violations()
    assert len(violations) == 2
    assert "sbp_policy_rate" in violations[0]["label"]
    assert "5.0-30.0" in violations[0]["message"]
    assert "sbp.example" in violations[0]["prompt"]
    assert "TESTX" in violations[1]["label"]
    assert "10,000,000" in violations[1]["message"]
