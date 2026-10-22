"""
routers/private.py — Private Analyser: manual entry of financials for
non-listed companies. Uses the same metrics engine as the PSX Analyser.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from database.connection import get_db

log = structlog.get_logger()
router = APIRouter()
templates = Jinja2Templates(directory="templates")

_UNIT_SCALE = {"thousands": 1_000.0, "millions": 1_000_000.0, "absolute": 1.0}

_MONEY_FIELDS = (
    "revenue", "gross_profit", "ebitda", "ebit", "net_income",
    "interest_expense", "depreciation", "total_assets", "total_equity",
    "total_debt", "cash", "current_assets", "current_liabilities",
    "retained_earnings", "inventory", "receivables", "payables", "ocf", "capex",
    "prev_revenue", "prev_net_income", "prev_gross_profit",
)


def _fill_private_dcf_defaults(prefill: dict) -> None:
    """Pre-fill DCF assumption fields from the resolved rate store (override→live→fallback)."""
    sector = prefill.get("sector", "")
    try:
        from config.sectors import is_bank as _is_bank
        from intelligence.dcf_rates import get_dcf_rates
        rates = get_dcf_rates(is_bank=_is_bank(sector))
    except Exception:
        rates = {"risk_free_rate": 11.5, "equity_risk_premium": 5.0,
                 "tax_rate": 29.0, "terminal_growth": 8.0}
    if not prefill.get("risk_free_rate"):
        prefill["risk_free_rate"] = round(rates["risk_free_rate"], 2)
    if not prefill.get("equity_risk_premium"):
        prefill["equity_risk_premium"] = round(rates["equity_risk_premium"], 2)
    if not prefill.get("tax_rate"):
        prefill["tax_rate"] = round(rates["tax_rate"], 2)
    if not prefill.get("terminal_growth"):
        prefill["terminal_growth"] = round(rates["terminal_growth"], 2)


def _sectors() -> list:
    try:
        rows = get_db().execute(
            "SELECT DISTINCT sector_name FROM psx_companies "
            "WHERE sector_name IS NOT NULL AND COALESCE(is_debt,0)=0 "
            "ORDER BY sector_name"
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def _sidebar(request: Request) -> dict:
    from database.clients import list_clients
    session = getattr(request.state, "session", {}) or {}
    clients = list_clients()
    cid = session.get("client_id")
    return {"clients": clients,
            "active_client": next((c for c in clients if c["id"] == cid), None)}


def _float(form: dict, key: str) -> float | None:
    v = form.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def page(request: Request):
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    saved_flash = request.query_params.get("saved") == "1"
    no_client_flash = request.query_params.get("no_client") == "1"

    prefill: dict = {}
    entries: list = []
    loaded_entry_id: str = ""
    if active_client:
        from database.private_entries import list_entries, load_entry
        entries = list_entries(active_client["id"])
        load_id = request.query_params.get("load") or (entries[0]["id"] if entries else None)
        if load_id:
            prefill = load_entry(load_id)
            loaded_entry_id = load_id

    # Suggest Pakistan-calibrated DCF defaults when the form is blank (fresh or unset fields)
    _fill_private_dcf_defaults(prefill)

    return templates.TemplateResponse(request, "private.html", {
        "active_page":     "private",
        "mode":            "private",
        "prefill":         prefill,
        "sectors":         _sectors(),
        "ticker":          "",
        "result":          None,
        "saved_flash":     saved_flash,
        "no_client_flash": no_client_flash,
        "entries":         entries,
        "loaded_entry_id": loaded_entry_id,
        **sidebar,
    })


@router.post("/save")
async def save(request: Request):
    """Save current form as a new named entry."""
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    if not active_client:
        return RedirectResponse("/private?no_client=1", status_code=303)
    form = dict(await request.form())
    entry_name = form.pop("_entry_name", "").strip()
    from database.private_entries import save_entry
    eid = save_entry(active_client["id"], form, entry_name=entry_name)
    return RedirectResponse(f"/private?saved=1&eid={eid}", status_code=303)


@router.get("/entries", response_class=HTMLResponse)
async def entries_list(request: Request):
    """HTMX partial: list of saved entries for the active client."""
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    entries = []
    if active_client:
        from database.private_entries import list_entries
        entries = list_entries(active_client["id"])
    return templates.TemplateResponse(request, "private_entries.html", {
        "entries": entries,
        "active_client": active_client,
    })


@router.get("/load/{entry_id}", response_class=HTMLResponse)
async def load(request: Request, entry_id: str):
    """Load a saved entry into the form (full page reload with prefill)."""
    require_session(request)
    from database.private_entries import load_entry
    prefill = load_entry(entry_id)
    if not prefill:
        return RedirectResponse("/private", status_code=303)
    sidebar = _sidebar(request)
    entries = []
    if sidebar.get("active_client"):
        from database.private_entries import list_entries
        entries = list_entries(sidebar["active_client"]["id"])
    return templates.TemplateResponse(request, "private.html", {
        "active_page":     "private",
        "mode":            "private",
        "prefill":         prefill,
        "sectors":         _sectors(),
        "ticker":          "",
        "result":          None,
        "saved_flash":     False,
        "no_client_flash": False,
        "entries":         entries,
        "loaded_entry_id": entry_id,
        **sidebar,
    })


@router.post("/entries/{entry_id}/delete", response_class=HTMLResponse)
async def delete_entry_route(request: Request, entry_id: str):
    """Delete one entry; return updated entries partial."""
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    from database.private_entries import delete_entry, list_entries
    delete_entry(entry_id)
    entries = list_entries(active_client["id"]) if active_client else []
    return templates.TemplateResponse(request, "private_entries.html", {
        "entries": entries,
        "active_client": active_client,
    })


@router.post("/entries/{entry_id}/rename", response_class=HTMLResponse)
async def rename_entry_route(request: Request, entry_id: str):
    """Rename an entry; return updated entries partial."""
    require_session(request)
    sidebar = _sidebar(request)
    active_client = sidebar["active_client"]
    form = dict(await request.form())
    new_name = form.get("name", "").strip()
    if new_name:
        from database.private_entries import rename_entry
        rename_entry(entry_id, new_name)
    from database.private_entries import list_entries
    entries = list_entries(active_client["id"]) if active_client else []
    return templates.TemplateResponse(request, "private_entries.html", {
        "entries": entries,
        "active_client": active_client,
    })


@router.post("/analyse", response_class=HTMLResponse)
async def analyse(request: Request):
    require_session(request)
    form = dict(await request.form())

    unit = form.get("unit", "thousands")
    scale = _UNIT_SCALE.get(unit, 1_000.0)
    company_name = form.get("company_name", "").strip()
    sector = form.get("sector", "")

    def scaled(key: str) -> float | None:
        v = _float(form, key)
        if v is None:
            return None
        if key in ("eps", "current_price", "risk_free_rate", "equity_risk_premium",
                    "beta", "tax_rate", "terminal_growth", "shares_outstanding"):
            return v
        return v * scale

    inc = {k: scaled(k) for k in
           ("revenue", "gross_profit", "ebitda", "ebit", "net_income",
            "interest_expense", "depreciation")}
    inc["eps"] = scaled("eps")

    bs = {k: scaled(k) for k in
          ("total_assets", "total_equity", "total_debt", "cash", "current_assets",
           "current_liabilities", "retained_earnings", "inventory", "receivables",
           "payables")}

    cf = {"ocf": scaled("ocf"), "capex": scaled("capex")}

    from intelligence.metrics import (
        compute_all, compute_growth, compute_dcf, compute_health_score,
    )

    result = compute_all(inc, bs, cf, sector=sector)
    result["company_name"] = company_name

    prev = {}
    for cur_key, prev_key in (("revenue", "prev_revenue"),
                               ("net_income", "prev_net_income"),
                               ("gross_profit", "prev_gross_profit")):
        v = scaled(prev_key)
        if v is not None:
            prev[cur_key] = v
    if prev:
        result["growth"] = compute_growth(inc, prev)
    else:
        result["growth"] = {}

    result["health"] = compute_health_score(result.get("ratios", {}))

    rfr = _float(form, "risk_free_rate")
    erp = _float(form, "equity_risk_premium")
    beta_v = _float(form, "beta")
    tax_r = _float(form, "tax_rate")
    tg = _float(form, "terminal_growth")
    shares = _float(form, "shares_outstanding")
    price = _float(form, "current_price")

    dcf_inputs_present = all(v is not None for v in (rfr, erp, beta_v, tax_r, tg))
    fcf_present = cf.get("ocf") is not None and cf.get("capex") is not None

    if dcf_inputs_present and fcf_present and inc.get("revenue") and inc.get("ebit"):
        tax_expense = (inc.get("ebit", 0) or 0) * (tax_r / 100.0) if tax_r else None
        pbt = (inc.get("ebit", 0) or 0) - (inc.get("interest_expense", 0) or 0)
        short_term_borrowings = 0.0

        from config.sectors import is_bank as _is_bank
        dcf = compute_dcf(
            revenue=inc["revenue"],
            ebit=inc["ebit"],
            depreciation=inc.get("depreciation") or 0,
            capex=abs(cf.get("capex") or 0),
            ocf=cf["ocf"],
            current_assets=bs.get("current_assets") or 0,
            current_liabilities=bs.get("current_liabilities") or 0,
            cash=bs.get("cash") or 0,
            short_term_borrowings=short_term_borrowings,
            total_debt=bs.get("total_debt") or 0,
            interest_expense=inc.get("interest_expense") or 0,
            tax_expense=tax_expense or 0,
            pbt=pbt,
            shares_outstanding=(shares or 1) * 1_000_000,
            risk_free_rate=rfr / 100.0,
            equity_risk_premium=erp / 100.0,
            beta=beta_v,
            tax_rate=tax_r / 100.0,
            terminal_growth=tg / 100.0,
            current_price=price,
            is_bank=_is_bank(sector),
        )
        result["dcf"] = dcf
    else:
        missing = []
        if not dcf_inputs_present:
            missing.append("valuation assumptions (risk-free rate, ERP, beta, tax rate, terminal growth)")
        if not fcf_present:
            missing.append("cash flow data (OCF and capex)")
        if not inc.get("revenue"):
            missing.append("revenue")
        if not inc.get("ebit"):
            missing.append("EBIT")
        result["dcf"] = type("DCF", (), {"computable": False, "missing": missing})()

    sb = _sidebar(request)
    entries = []
    if sb.get("active_client"):
        from database.private_entries import list_entries
        entries = list_entries(sb["active_client"]["id"])
    return templates.TemplateResponse(request, "private.html", {
        "active_page":     "private",
        "mode":            "private",
        "prefill":         form,
        "sectors":         _sectors(),
        "ticker":          "",
        "result":          result,
        "saved_flash":     False,
        "no_client_flash": False,
        "entries":         entries,
        "loaded_entry_id": "",
        **sb,
    })
