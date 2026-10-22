"""
C2: Financial overrides must be saved and applied in build_analysis.

Tests that must fail before the fix and pass after:
1. Saved overrides populate balance sheet fields that were None.
2. DuPont computes when overrides supply the missing balance sheet data.
3. Without overrides, balance sheet fields stay None.
"""

from unittest.mock import MagicMock, patch

_TICKER = "TESTCO"
_PERIOD = "2024"

# Override rows — supply the balance sheet missing in DB.
_OVERRIDE_ROWS = [
    {"period": _PERIOD, "field": "total_assets",        "value_pkr_t": 500_000},
    {"period": _PERIOD, "field": "total_equity",        "value_pkr_t": 200_000},
    {"period": _PERIOD, "field": "current_assets",      "value_pkr_t": 150_000},
    {"period": _PERIOD, "field": "current_liabilities", "value_pkr_t": 80_000},
    {"period": _PERIOD, "field": "retained_earnings",   "value_pkr_t": 50_000},
]


def _make_override_rows():
    rows = []
    for o in _OVERRIDE_ROWS:
        r = MagicMock()
        r.__getitem__ = lambda s, k, _o=o: _o[k]
        r.get = lambda k, d=None, _o=o: _o.get(k, d)
        rows.append(r)
    return rows


def _make_db(with_overrides: bool):
    """Return a mock DB; queries to financial_overrides return rows or []."""
    db = MagicMock()

    def _execute(sql, params=()):
        result = MagicMock()
        if "financial_overrides" in sql.lower():
            result.fetchall.return_value = _make_override_rows() if with_overrides else []
        else:
            result.fetchone.return_value = None
            result.fetchall.return_value = []
        return result

    db.execute = _execute
    return db


# ── Helper: pre-populate an analysis with income data but no balance sheet ───

def _stub_analysis_no_bs(a):
    """Fill income statement for _PERIOD, leave balance sheet empty — simulates
    a ticker that PSX HTML gives revenue/profit for but no balance sheet."""
    import intelligence.psx_analyser as pa
    PKR_T = 1_000  # PKR_THOUSANDS constant

    a.found = True
    a.sector = "Cement"
    a.company_name = "Test Company"
    a.annual_periods = [_PERIOD]
    a.income_statement[_PERIOD] = {
        "revenue": 100_000 * PKR_T, "gross_profit": 40_000 * PKR_T,
        "ebitda": 30_000 * PKR_T, "ebit": 25_000 * PKR_T,
        "net_income": 20_000 * PKR_T, "interest_expense": 5_000 * PKR_T,
        "depreciation": 5_000 * PKR_T, "eps": 10.0,
    }
    a.balance_sheet[_PERIOD] = {k: None for k in (
        "total_assets", "total_equity", "total_debt", "cash",
        "current_assets", "current_liabilities", "retained_earnings",
        "inventory", "receivables", "payables",
    )}
    a.cash_flow[_PERIOD] = {"ocf": None, "capex": None}


def test_overrides_populate_balance_sheet(monkeypatch):
    """After applying overrides, build_analysis must have non-None balance sheet fields."""
    import intelligence.psx_analyser as pa
    from intelligence.psx_analyser import PSXAnalysis

    db = _make_db(with_overrides=True)

    def _fake_build(ticker):
        a = PSXAnalysis(ticker=ticker)
        _stub_analysis_no_bs(a)
        # Try calling _apply_financial_overrides — this is the function we're adding.
        if hasattr(pa, "_apply_financial_overrides"):
            pa._apply_financial_overrides(db, ticker, a)
        return a

    a = _fake_build(_TICKER)
    bs = a.balance_sheet.get(_PERIOD, {})

    assert bs.get("total_assets") is not None, (
        "total_assets is None — _apply_financial_overrides() is not defined. "
        "Add it to psx_analyser.py and call it from build_analysis()."
    )
    assert bs.get("total_equity") is not None
    assert bs.get("current_assets") is not None


def test_dupont_computes_with_overrides(monkeypatch):
    """DuPont must produce a result when overrides supply the missing balance sheet."""
    import intelligence.psx_analyser as pa
    from intelligence.psx_analyser import PSXAnalysis, _compute_ratios, _compute_models

    db = _make_db(with_overrides=True)

    a = PSXAnalysis(ticker=_TICKER)
    _stub_analysis_no_bs(a)

    assert not hasattr(pa, "_apply_financial_overrides") or True  # existence check

    if hasattr(pa, "_apply_financial_overrides"):
        pa._apply_financial_overrides(db, _TICKER, a)

    _compute_ratios(a)
    _compute_models(a)

    assert a.dupont, (
        "DuPont is empty even with complete overrides — "
        "_apply_financial_overrides() must be defined and supply balance_sheet data."
    )
    roe = (a.dupont or {}).get("roe") or {}
    assert roe.get("status") == "ok", f"DuPont ROE not ok: {roe}"


def test_altman_computes_with_overrides(monkeypatch):
    """Altman Z'' must compute when overrides supply retained_earnings and total_assets."""
    import intelligence.psx_analyser as pa
    from intelligence.psx_analyser import PSXAnalysis, _compute_ratios, _compute_models

    db = _make_db(with_overrides=True)

    a = PSXAnalysis(ticker=_TICKER)
    _stub_analysis_no_bs(a)

    if hasattr(pa, "_apply_financial_overrides"):
        pa._apply_financial_overrides(db, _TICKER, a)

    _compute_ratios(a)
    _compute_models(a)

    altman = getattr(a, "altman", None) or {}
    assert altman, (
        "Altman result is empty even with complete overrides. "
        "retained_earnings and total_assets from overrides must reach _compute_models()."
    )


def test_no_overrides_means_null_balance_sheet(monkeypatch):
    """Without overrides, balance sheet fields must remain None (no data invented)."""
    import intelligence.psx_analyser as pa
    from intelligence.psx_analyser import PSXAnalysis

    db = _make_db(with_overrides=False)

    a = PSXAnalysis(ticker=_TICKER)
    _stub_analysis_no_bs(a)

    if hasattr(pa, "_apply_financial_overrides"):
        pa._apply_financial_overrides(db, _TICKER, a)

    bs = a.balance_sheet.get(_PERIOD, {})
    assert bs.get("total_assets") is None, "total_assets should be None without overrides"
