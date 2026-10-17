"""
Tests for the Phase 3 assembly layer (Task 3.2) and the upsert depth-preservation
fix found while building it.

Each case is anchored to a real defect observed against live tickers, so the
honesty guarantees (no bare floats, no confident-looking thin scores, no
undefined ratios rendered as numbers) cannot silently regress.
"""

from intelligence.psx_analyser import PSXAnalysis, _health, _div, MIN_PEERS


def test_missing_requirements_are_specific_and_deduplicated():
    a = PSXAnalysis(ticker="TEST")
    a.require("balance_sheet", "total assets - needed for leverage")
    a.require("balance_sheet", "total assets - needed for leverage")
    reqs = a.missing_requirements["balance_sheet"]
    assert len(reqs) == 1
    # Rule 4: never a blank or a vague "data unavailable".
    assert reqs[0].startswith("Required: ")
    assert "total assets" in reqs[0]


def test_latest_period_is_newest_first():
    a = PSXAnalysis(ticker="TEST")
    a.annual_periods = ["2025", "2024", "2023"]
    assert a.latest_period == "2025"


def test_latest_period_is_none_without_data():
    assert PSXAnalysis(ticker="TEST").latest_period is None


def test_div_is_safe_on_none_and_zero():
    assert _div(None, 2) is None
    assert _div(2, None) is None
    assert _div(2, 0) is None
    assert _div(1, 4) == 0.25


# ── Health score honesty ──────────────────────────────────────────────────────

def _with_ratios(ratios: dict) -> PSXAnalysis:
    a = PSXAnalysis(ticker="TEST")
    a.annual_periods = ["2025"]
    a.ratios = {"2025": ratios}
    return a


def test_health_is_none_when_nothing_is_validated():
    a = _with_ratios({"net_margin": {"value": None, "status": "missing"}})
    h = _health(a)
    assert h["score"] is None
    assert h["confidence"] == "none"


def test_thin_basis_is_labelled_low_confidence_even_when_all_checks_pass():
    # Observed live: HBL and LUCK both scored 10.0/10 on a basis of 2 while
    # labelled "moderate". A perfect score on two of five checks must not read as
    # a confident verdict.
    a = _with_ratios({
        "net_margin": {"value": 0.20, "status": "ok"},
        "roe": {"value": 0.25, "status": "ok"},
    })
    h = _health(a)
    assert h["score"] == 10.0
    assert h["basis"] == 2
    assert h["confidence"] == "low"
    assert "indicative only" in h["note"]


def test_full_basis_earns_high_confidence():
    a = _with_ratios({
        "net_margin": {"value": 0.20, "status": "ok"},
        "roe": {"value": 0.25, "status": "ok"},
        "current_ratio": {"value": 1.8, "status": "ok"},
        "interest_coverage": {"value": 5.0, "status": "ok"},
    })
    h = _health(a)
    assert h["basis"] == 4
    assert h["confidence"] == "high"


def test_implausible_metrics_are_not_counted_in_the_basis():
    a = _with_ratios({
        "net_margin": {"value": 0.20, "status": "ok"},
        "roe": {"value": None, "status": "implausible"},
    })
    assert _health(a)["basis"] == 1


def test_min_peers_threshold_matches_the_plan():
    # Below 4 valid peers the plan requires "insufficient peers", not a percentile.
    assert MIN_PEERS == 4


# ── Upsert must not destroy previously-extracted depth ───────────────────────

def test_thin_refresh_preserves_richer_stored_fields(monkeypatch):
    """
    The bulk sweep writes summary-only rows (no annual-report merge). A plain
    excluded.x overwrite NULLed out balance-sheet fields an earlier per-ticker run
    had extracted, dropping BFBIO's validated ratio count from 9 to 4. The upsert
    now COALESCEs, so an incoming NULL means "this source lacks the field" rather
    than "the field is empty".
    """
    captured = {}

    class _FakeDB:
        def execute(self, sql, vals=None):
            captured["sql"] = sql
            return self

        def commit(self):
            pass

    import database.psx_data as pd
    monkeypatch.setattr(pd, "get_db", lambda: _FakeDB())
    pd.upsert_psx_financials("TEST", "2025", "annual", {"revenue": 1.0})

    sql = captured["sql"]
    assert "COALESCE(excluded.revenue, revenue)" in sql
    assert "COALESCE(excluded.total_assets, total_assets)" in sql
    # fetched_at must still advance, so staleness stays truthful.
    assert "fetched_at=excluded.fetched_at" in sql
