"""Tests for the plausibility validation layer (M7) and health score (M6)."""

from intelligence.validation import validate_metric


def test_ok_metric_within_bounds():
    assert validate_metric("net_margin", 0.15)["status"] == "ok"
    assert validate_metric("current_ratio", 1.4)["status"] == "ok"


def test_implausible_metric_excluded_and_nulled():
    r = validate_metric("net_margin", 40.0)      # 4000% — parse error
    assert r["status"] == "implausible"
    assert r["value"] is None                    # never displayed as a number


def test_missing_metric():
    assert validate_metric("roe", None)["status"] == "missing"
    assert validate_metric("roe", "N/A")["status"] == "missing"


def test_bank_override_widens_leverage():
    # 12x D/E is implausible for a normal company's own metric bounds? bounds are 0-15,
    # so 12 is ok for both; test a value only a bank can have.
    assert validate_metric("debt_to_equity", 20.0, is_bank=True)["status"] == "ok"
    assert validate_metric("debt_to_equity", 20.0, is_bank=False)["status"] == "implausible"


def test_bank_roe_bounds():
    # 1527% ROE (HBL unit error) is implausible under the bank override.
    assert validate_metric("roe", 15.27, is_bank=True)["status"] == "implausible"
