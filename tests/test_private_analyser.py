"""
C5: Private Analyser must not return a 500 for any combination of form values.
Zero, negative, huge, and empty values must give results or field messages, never a 500.
"""

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from unittest.mock import MagicMock, patch


def _make_app():
    from fastapi import FastAPI
    import routers.private as rp

    app = FastAPI()
    app.include_router(rp.router, prefix="/private")
    return app, rp


def _session_middleware(app):
    """Inject a fake session into request.state."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class FakeSession(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.session = {"user_id": "test", "client_id": "c1"}
            request.state.client_id = "c1"
            return await call_next(request)

    app.add_middleware(FakeSession)
    return app


@pytest.fixture
def client(monkeypatch):
    app, rp = _make_app()
    _session_middleware(app)

    monkeypatch.setattr(rp, "require_session", lambda r: None)
    monkeypatch.setattr(rp, "_sidebar", lambda r: {
        "clients": [], "active_client": {"id": "c1", "name": "Test"},
    })
    monkeypatch.setattr(rp, "_sectors", lambda: ["Cement", "Banking"])

    from database.private_entries import list_entries
    monkeypatch.setattr("database.private_entries.list_entries", lambda *a: [])

    return TestClient(app, raise_server_exceptions=False)


_BASE_FORM = {
    "company_name": "Test Co",
    "sector": "Cement",
    "unit": "thousands",
    "revenue": "100000",
    "gross_profit": "40000",
    "ebitda": "30000",
    "ebit": "25000",
    "net_income": "20000",
    "interest_expense": "5000",
    "depreciation": "5000",
    "eps": "10",
    "total_assets": "500000",
    "total_equity": "200000",
    "total_debt": "100000",
    "cash": "50000",
    "current_assets": "150000",
    "current_liabilities": "80000",
    "retained_earnings": "50000",
    "inventory": "20000",
    "receivables": "30000",
    "payables": "25000",
    "ocf": "35000",
    "capex": "15000",
    "risk_free_rate": "11.5",
    "equity_risk_premium": "5.0",
    "beta": "1.0",
    "tax_rate": "29.0",
    "terminal_growth": "8.0",
    "shares_outstanding": "100",
    "current_price": "120",
}


def test_normal_form_returns_200(client):
    resp = client.post("/private/analyse", data=_BASE_FORM)
    assert resp.status_code == 200, f"Normal form got {resp.status_code}: {resp.text[:200]}"


def test_all_zeros_no_500(client):
    form = {k: "0" for k in _BASE_FORM}
    form["company_name"] = "Zero Co"
    form["unit"] = "thousands"
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"All-zeros form got 500: {resp.text[:400]}"


def test_empty_form_no_500(client):
    """Completely empty form: must not 500, must show missing-field messages."""
    form = {"company_name": "Empty Co", "sector": "Cement", "unit": "thousands"}
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"Empty form got 500: {resp.text[:400]}"


def test_negative_values_no_500(client):
    form = dict(_BASE_FORM)
    form["net_income"] = "-50000"
    form["ocf"] = "-20000"
    form["capex"] = "-30000"
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"Negative values got 500: {resp.text[:400]}"


def test_huge_values_no_500(client):
    form = dict(_BASE_FORM)
    form["revenue"] = "999999999999"
    form["total_assets"] = "9999999999999"
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"Huge values got 500: {resp.text[:400]}"


def test_non_numeric_field_no_500(client):
    """A non-numeric field value must be treated as missing, not crash."""
    form = dict(_BASE_FORM)
    form["revenue"] = "not-a-number"
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"Non-numeric field got 500: {resp.text[:400]}"


def test_missing_dcf_assumptions_no_500(client):
    """When DCF assumptions are absent, DCF must be skipped, not 500."""
    form = dict(_BASE_FORM)
    del form["risk_free_rate"]
    del form["beta"]
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"Missing DCF fields got 500: {resp.text[:400]}"


def test_zero_revenue_no_500(client):
    """Zero revenue must not cause division-by-zero crash."""
    form = dict(_BASE_FORM)
    form["revenue"] = "0"
    resp = client.post("/private/analyse", data=form)
    assert resp.status_code == 200, f"Zero revenue got 500: {resp.text[:400]}"


def test_dcf_tv_share_none_no_500(client, monkeypatch):
    """DCFResult.tv_share_of_ev = None must not crash the template (line 168 of private.html)."""
    from intelligence.metrics import DCFResult

    def _mock_dcf(**kwargs):
        r = DCFResult()
        r.computable = True
        r.enterprise_value = 1_000_000_000
        r.equity_value = 800_000_000
        r.per_share = 8.0
        r.wacc = 0.12
        r.tv_share_of_ev = None  # triggers the un-guarded format() call
        return r

    import intelligence.metrics as im
    monkeypatch.setattr(im, "compute_dcf", _mock_dcf)

    resp = client.post("/private/analyse", data=_BASE_FORM)
    assert resp.status_code == 200, f"None tv_share_of_ev got 500: {resp.text[:400]}"
