"""
intelligence/metrics.py — Pure financial metric computation.

Every formula lives here as a pure function (no database, no I/O).
The metric registry declares each metric once: id, label, formula,
required inputs, applicability, and the function. psx_analyser.py,
the private analyser and the entry-form calculator all call these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config.sectors import is_bank as _is_bank


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _div(a, b) -> Optional[float]:
    try:
        if a is None or b is None or b == 0:
            return None
        return float(a) / float(b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _growth(current, prior) -> Optional[float]:
    if current is None or prior is None:
        return None
    if prior == 0 or prior < 0:
        return None
    return _div(current - prior, abs(prior))


def _fmt_large(value) -> str:
    if value is None:
        return "N/A"
    v = float(value)
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.2f}K"
    return f"{v:.2f}"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DuPontResult:
    net_margin: Optional[float] = None
    asset_turnover: Optional[float] = None
    equity_multiplier: Optional[float] = None
    roe_computed: Optional[float] = None


@dataclass
class AltmanResult:
    z_score: Optional[float] = None
    zone: str = ""
    components: dict = field(default_factory=dict)
    model: str = ""
    computable: bool = False
    missing: list = field(default_factory=list)


@dataclass
class DCFResult:
    enterprise_value: Optional[float] = None
    equity_value: Optional[float] = None
    per_share: Optional[float] = None
    wacc: Optional[float] = None
    terminal_growth: Optional[float] = None
    terminal_value: Optional[float] = None
    tv_share_of_ev: Optional[float] = None
    fcf_projections: list = field(default_factory=list)
    sensitivity_wacc_tg: dict = field(default_factory=dict)
    sensitivity_growth_margin: dict = field(default_factory=dict)
    scenarios: dict = field(default_factory=dict)
    assumptions: dict = field(default_factory=dict)
    computable: bool = False
    missing: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# DuPont decomposition
# ---------------------------------------------------------------------------

def compute_dupont(net_income, revenue, total_assets, total_equity) -> DuPontResult:
    r = DuPontResult()
    r.net_margin = _div(net_income, revenue)
    r.asset_turnover = _div(revenue, total_assets)
    r.equity_multiplier = _div(total_assets, total_equity)
    if all(v is not None for v in (r.net_margin, r.asset_turnover, r.equity_multiplier)):
        r.roe_computed = round(r.net_margin * r.asset_turnover * r.equity_multiplier, 6)
    return r


# ---------------------------------------------------------------------------
# Altman Z'' (emerging-market, book-value variant)
# Coefficients: Altman, Corporate Financial Distress (2000), Z'' model.
# Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4 + 3.25
# X1 = working capital / total assets
# X2 = retained earnings / total assets
# X3 = EBIT / total assets
# X4 = book equity / total liabilities
# Zones: > 2.60 safe, 1.10-2.60 grey, < 1.10 distress
# Banks are excluded (Altman excluded financial institutions).
# ---------------------------------------------------------------------------

def compute_altman_z(
    current_assets, current_liabilities, total_assets,
    total_equity, revenue, ebit, retained_earnings,
    sector: str = "",
) -> AltmanResult:
    # revenue is accepted for API compatibility but Z'' does not use it
    r = AltmanResult()

    if _is_bank(sector):
        r.model = "not applicable (bank)"
        r.missing = ["Altman Z excluded financial institutions"]
        return r

    need = {
        "current assets": current_assets,
        "current liabilities": current_liabilities,
        "total assets": total_assets,
        "total equity": total_equity,
        "EBIT": ebit,
        "retained earnings": retained_earnings,
    }
    missing = [k for k, v in need.items() if v is None]
    if missing:
        r.missing = missing
        return r

    if total_assets == 0:
        r.missing = ["total assets (zero)"]
        return r

    total_liabilities = total_assets - total_equity
    if total_liabilities <= 0:
        r.missing = ["total liabilities (derived as zero or negative)"]
        return r

    wc = current_assets - current_liabilities
    x1 = wc / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x4 = total_equity / total_liabilities

    z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4 + 3.25

    r.z_score = round(z, 3)
    r.components = {
        "X1_wc_ta": round(x1, 4),
        "X2_re_ta": round(x2, 4),
        "X3_ebit_ta": round(x3, 4),
        "X4_eq_tl": round(x4, 4),
    }
    r.model = "Altman Z'' (emerging-market, book-value)"
    r.computable = True

    if z > 2.60:
        r.zone = "SAFE"
    elif z >= 1.10:
        r.zone = "GREY"
    else:
        r.zone = "DISTRESS"

    return r


# ---------------------------------------------------------------------------
# Ratio computation (single period)
# ---------------------------------------------------------------------------

def compute_ratios(inc: dict, bs: dict, cf: dict, is_bank_sector: bool = False) -> dict:
    """Compute all ratios for one period. Returns {name: value} (raw floats)."""
    rev = inc.get("revenue")
    ni = inc.get("net_income")
    ta = bs.get("total_assets")
    te = bs.get("total_equity")
    ca = bs.get("current_assets")
    cl = bs.get("current_liabilities")
    inv = bs.get("inventory")
    gp = inc.get("gross_profit")
    ie = inc.get("interest_expense")

    cogs = (rev - gp) if (rev is not None and gp is not None) else None

    out = {}
    out["gross_margin"] = _div(gp, rev)
    out["operating_margin"] = _div(inc.get("ebit"), rev)
    out["ebitda_margin"] = _div(inc.get("ebitda"), rev)
    out["net_margin"] = _div(ni, rev)
    out["roa"] = _div(ni, ta)
    out["roe"] = _div(ni, te)
    out["current_ratio"] = _div(ca, cl)
    out["quick_ratio"] = (
        _div(ca - inv, cl) if (ca is not None and inv is not None and cl) else None
    )
    out["cash_ratio"] = _div(bs.get("cash"), cl)
    out["working_capital"] = (ca - cl) if (ca is not None and cl is not None) else None
    out["debt_to_equity"] = _div(bs.get("total_debt"), te)
    out["debt_to_assets"] = _div(bs.get("total_debt"), ta)
    out["equity_multiplier"] = _div(ta, te)

    # Interest coverage: only meaningful against a positive interest cost.
    coverage = _div(inc.get("ebit"), ie) if (ie is not None and ie > 0) else None
    out["interest_coverage"] = coverage

    out["asset_turnover"] = _div(rev, ta)
    out["days_inventory"] = (
        _div(inv, cogs) * 365 if (inv is not None and cogs and cogs > 0) else None
    )
    out["days_receivables"] = (
        _div(bs.get("receivables"), rev) * 365
        if (bs.get("receivables") is not None and rev) else None
    )
    out["days_payables"] = (
        _div(bs.get("payables"), cogs) * 365
        if (bs.get("payables") is not None and cogs and cogs > 0) else None
    )

    ocf = cf.get("ocf")
    capex = cf.get("capex")
    out["fcf"] = (
        (ocf - abs(capex)) if (ocf is not None and capex is not None) else None
    )
    out["ocf_to_net_income"] = _div(ocf, ni)
    out["eps"] = inc.get("eps")

    return out


def compute_growth(current_inc: dict, prior_inc: dict) -> dict:
    """YoY growth rates between two periods."""
    out = {}
    for name, key in (
        ("revenue_growth", "revenue"),
        ("net_income_growth", "net_income"),
        ("gross_profit_growth", "gross_profit"),
    ):
        out[name] = _growth(current_inc.get(key), prior_inc.get(key))
    return out


def compute_cagr(values: list, years: int) -> Optional[float]:
    """CAGR from a list of values (oldest to newest) over the given year span."""
    if len(values) < 2 or years <= 0:
        return None
    start, end = values[0], values[-1]
    if start is None or end is None or start <= 0:
        return None
    try:
        return round((end / start) ** (1.0 / years) - 1, 4)
    except (ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Bank-specific metrics
# ---------------------------------------------------------------------------

BANK_METRIC_FIELDS = [
    "net_markup_income", "non_markup_income", "cost_to_income",
    "advances_to_deposits", "npl_ratio", "provision_coverage",
    "capital_adequacy_ratio",
]


def compute_bank_metrics(data: dict) -> dict:
    """Compute bank-specific ratios from available data."""
    out = {}
    ni = data.get("net_income")
    te = data.get("total_equity")
    ta = data.get("total_assets")
    out["roe"] = _div(ni, te)
    out["roa"] = _div(ni, ta)
    out["equity_multiplier"] = _div(ta, te)
    for f in BANK_METRIC_FIELDS:
        if data.get(f) is not None:
            out[f] = data[f]
    return out


# ---------------------------------------------------------------------------
# DCF valuation (non-banks only)
# ---------------------------------------------------------------------------

def compute_dcf(
    revenue: float,
    ebit: float,
    depreciation: float,
    capex: float,
    ocf: float,
    current_assets: float,
    current_liabilities: float,
    cash: float,
    short_term_borrowings: float,
    total_debt: float,
    interest_expense: float,
    tax_expense: float,
    pbt: float,
    shares_outstanding: float,
    risk_free_rate: float,
    equity_risk_premium: float,
    beta: float,
    tax_rate: float,
    terminal_growth: float,
    cost_of_debt_override: Optional[float] = None,
    scenario_assumptions: Optional[dict] = None,
    current_price: Optional[float] = None,
    is_bank: bool = False,
) -> DCFResult:
    """
    Free cash flow to the firm DCF over a 5-year horizon with Gordon-growth
    terminal value. Reports a bear-base-bull range with sensitivity grids.
    All inputs in PKR. No guesses for missing inputs.
    """
    r = DCFResult()
    if is_bank:
        r.warnings.append(
            "FCFF DCF is not designed for banks: customer deposits count as debt in "
            "WACC weights, overstating leverage. Terminal value will dominate at narrow "
            "WACC–TG spreads. Treat this valuation as indicative only."
        )

    # Validate required inputs
    need = {
        "revenue": revenue, "EBIT": ebit, "depreciation": depreciation,
        "capex": capex, "operating cash flow": ocf,
        "current assets": current_assets, "current liabilities": current_liabilities,
        "cash": cash, "total debt": total_debt,
        "interest expense": interest_expense, "shares outstanding": shares_outstanding,
        "risk-free rate": risk_free_rate, "equity risk premium": equity_risk_premium,
        "beta": beta, "tax rate": tax_rate, "terminal growth": terminal_growth,
    }
    missing = [k for k, v in need.items() if v is None]
    if missing:
        r.missing = missing
        return r

    # Short-term borrowings defaults to 0 if explicitly entered as such
    stb = short_term_borrowings if short_term_borrowings is not None else 0

    # Cost of equity (CAPM)
    ke = risk_free_rate + beta * equity_risk_premium

    # Cost of debt
    avg_debt = total_debt if total_debt > 0 else 1
    kd = cost_of_debt_override if cost_of_debt_override is not None else _div(interest_expense, avg_debt)
    if kd is None:
        kd = risk_free_rate + 0.02

    # Capital structure weights (market cap for listed, book for private)
    eq_weight = 0.5
    debt_weight = 0.5
    if shares_outstanding and current_price:
        market_cap = shares_outstanding * current_price
        total_capital = market_cap + total_debt
        if total_capital > 0:
            eq_weight = market_cap / total_capital
            debt_weight = total_debt / total_capital

    wacc = eq_weight * ke + debt_weight * kd * (1 - tax_rate)
    r.wacc = round(wacc, 4)

    if wacc <= terminal_growth:
        r.warnings.append(
            f"WACC ({wacc:.2%}) must exceed terminal growth ({terminal_growth:.2%})")
        return r

    # Base-case assumptions from history
    sa = scenario_assumptions or {}
    base = sa.get("base", {})
    rev_growth = base.get("revenue_growth", 0.05)   # default 5% p.a.; caller overrides
    ebit_margin = base.get("ebit_margin", _div(ebit, revenue) or 0.10)
    capex_ratio = base.get("capex_to_revenue", abs(capex) / revenue if revenue else 0.05)
    da_ratio = base.get("da_to_revenue", depreciation / revenue if revenue else 0.03)

    # NWC change: use the actual NWC-to-revenue ratio from the latest period so the
    # projection scales with revenue growth instead of using a flat 1% guess.
    # Capped at ±50% to guard against balance-sheet parse artefacts.
    nwc_current = (current_assets - cash) - (current_liabilities - stb)
    nwc_ratio = (nwc_current / revenue) if revenue > 0 else 0.01
    nwc_ratio = max(-0.50, min(0.50, nwc_ratio))

    def _project(growth_rate, margin, capex_r, da_r, tg):
        fcfs = []
        rev = revenue
        for _ in range(5):
            prev_rev = rev
            rev = rev * (1 + growth_rate)
            proj_ebit = rev * margin
            proj_da = rev * da_r
            proj_capex = rev * capex_r
            nwc_change = (rev - prev_rev) * nwc_ratio   # ΔNWC = Δrevenue × NWC/revenue
            fcff = proj_ebit * (1 - tax_rate) + proj_da - proj_capex - nwc_change
            fcfs.append(round(fcff))
        pv_fcfs = sum(f / (1 + wacc) ** i for i, f in enumerate(fcfs, 1))
        tv = fcfs[-1] * (1 + tg) / (wacc - tg)
        pv_tv = tv / (1 + wacc) ** 5
        ev = pv_fcfs + pv_tv
        net_debt = total_debt - cash
        eq_val = ev - net_debt
        ps = eq_val / shares_outstanding if shares_outstanding else None
        return {
            "fcfs": fcfs, "ev": round(ev), "eq": round(eq_val),
            "per_share": round(ps, 2) if ps else None,
            "tv": round(tv), "tv_pct": round(pv_tv / ev * 100, 1) if ev else None,
        }

    # Three scenarios
    bear_g = rev_growth * 0.5
    bull_g = rev_growth * 1.5
    bear_m = ebit_margin * 0.85
    bull_m = ebit_margin * 1.15
    bear_tg = terminal_growth * 0.5
    bull_tg = min(terminal_growth * 1.5, wacc - 0.005)

    base_res = _project(rev_growth, ebit_margin, capex_ratio, da_ratio, terminal_growth)
    bear_res = _project(bear_g, bear_m, capex_ratio, da_ratio, bear_tg)
    bull_res = _project(bull_g, bull_m, capex_ratio, da_ratio, bull_tg)

    r.computable = True
    r.enterprise_value = base_res["ev"]
    r.equity_value = base_res["eq"]
    r.per_share = base_res["per_share"]
    r.terminal_value = base_res["tv"]
    r.tv_share_of_ev = base_res["tv_pct"]
    r.fcf_projections = base_res["fcfs"]
    r.terminal_growth = terminal_growth

    r.scenarios = {
        "bear": {"equity_value": bear_res["eq"], "per_share": bear_res["per_share"]},
        "base": {"equity_value": base_res["eq"], "per_share": base_res["per_share"]},
        "bull": {"equity_value": bull_res["eq"], "per_share": bull_res["per_share"]},
    }

    if r.tv_share_of_ev and r.tv_share_of_ev > 75:
        r.warnings.append(
            f"Terminal value is {r.tv_share_of_ev:.0f}% of enterprise value")

    # Sensitivity: WACC vs terminal growth
    sens_wt = {}
    for w_delta in [-0.02, -0.01, 0, 0.01, 0.02]:
        w = wacc + w_delta
        row = {}
        for tg_delta in [-0.01, 0, 0.01]:
            tg = terminal_growth + tg_delta
            if w > tg:
                res = _project(rev_growth, ebit_margin, capex_ratio, da_ratio, tg)
                row[f"TG {tg:.1%}"] = res["per_share"]
            else:
                row[f"TG {tg:.1%}"] = None
        sens_wt[f"WACC {w:.1%}"] = row
    r.sensitivity_wacc_tg = sens_wt

    r.assumptions = {
        "revenue_growth": rev_growth,
        "ebit_margin": ebit_margin,
        "capex_to_revenue": capex_ratio,
        "da_to_revenue": da_ratio,
        "terminal_growth": terminal_growth,
        "wacc": wacc,
        "cost_of_equity": ke,
        "cost_of_debt": kd,
        "equity_weight": eq_weight,
        "debt_weight": debt_weight,
        "beta": beta,
        "risk_free_rate": risk_free_rate,
        "equity_risk_premium": equity_risk_premium,
        "tax_rate": tax_rate,
    }

    return r


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------

def compute_health_score(ratios: dict, altman_zone: str = "") -> dict:
    """
    Heuristic health score from available ratios + Altman zone.
    Returns {score, confidence, basis, note}.
    Up to 6 checks: net_margin, roe, current_ratio, interest_coverage,
    revenue_growth, and Altman zone (SAFE passes, DISTRESS fails, GREY neutral).
    """
    checks = 0
    passed = 0
    # Pakistan-calibrated: risk-free ~11.5%, inflation ~20% nominal
    thresholds = [
        ("net_margin",        lambda v: v > 0.05),
        ("roe",               lambda v: v > 0.15),   # below risk-free rate destroys value
        ("current_ratio",     lambda v: v >= 1.2),
        ("interest_coverage", lambda v: v >= 2.0),
        ("revenue_growth",    lambda v: v > 0.10),   # <10% nominal likely = real contraction
    ]
    for name, test in thresholds:
        v = ratios.get(name)
        if v is not None:
            checks += 1
            if test(v):
                passed += 1

    # Altman zone counts as a sixth check (excluded for banks where zone is blank)
    if altman_zone in ("SAFE", "DISTRESS"):
        checks += 1
        if altman_zone == "SAFE":
            passed += 1

    if not checks:
        return {"score": None, "confidence": "none", "basis": 0,
                "note": "No validated metric was available to score."}

    score = round(passed / checks * 10, 1)
    confidence = "high" if checks >= 4 else "moderate" if checks == 3 else "low"
    return {
        "score": score,
        "confidence": confidence,
        "basis": checks,
        "note": f"Scored on {checks} of 6 checks; {passed} passed.",
    }


# ---------------------------------------------------------------------------
# Metric registry
# ---------------------------------------------------------------------------

@dataclass
class MetricDef:
    id: str
    label: str
    formula: str
    required_inputs: list
    applicability: str  # "both", "non_bank", "bank"
    unit: str  # "ratio", "pct", "days", "pkr", "x", "score"
    basis: str


METRIC_REGISTRY: list[MetricDef] = [
    MetricDef("gross_margin", "Gross Margin", "Gross Profit / Revenue",
              ["revenue", "gross_profit"], "non_bank", "pct",
              "Annual, closing balances"),
    MetricDef("operating_margin", "Operating Margin", "EBIT / Revenue",
              ["revenue", "ebit"], "both", "pct", "Annual"),
    MetricDef("ebitda_margin", "EBITDA Margin", "EBITDA / Revenue",
              ["revenue", "ebitda"], "both", "pct", "Annual"),
    MetricDef("net_margin", "Net Margin", "Net Income / Revenue",
              ["revenue", "net_income"], "both", "pct", "Annual"),
    MetricDef("roa", "Return on Assets", "Net Income / Total Assets",
              ["net_income", "total_assets"], "both", "pct",
              "Annual; closing total assets"),
    MetricDef("roe", "Return on Equity", "Net Income / Total Equity",
              ["net_income", "total_equity"], "both", "pct",
              "Annual; closing total equity"),
    MetricDef("current_ratio", "Current Ratio", "Current Assets / Current Liabilities",
              ["current_assets", "current_liabilities"], "non_bank", "x",
              "Year-end balances"),
    MetricDef("quick_ratio", "Quick Ratio", "(Current Assets - Inventory) / Current Liabilities",
              ["current_assets", "inventory", "current_liabilities"], "non_bank", "x",
              "Year-end balances"),
    MetricDef("debt_to_equity", "Debt to Equity", "Total Debt / Total Equity",
              ["total_debt", "total_equity"], "non_bank", "x", "Year-end balances"),
    MetricDef("interest_coverage", "Interest Coverage", "EBIT / Interest Expense",
              ["ebit", "interest_expense"], "both", "x",
              "Annual EBIT; positive interest expense only"),
    MetricDef("asset_turnover", "Asset Turnover", "Revenue / Total Assets",
              ["revenue", "total_assets"], "both", "x", "Annual"),
    MetricDef("days_inventory", "Inventory Days", "(Inventory / COGS) * 365",
              ["inventory", "revenue", "gross_profit"], "non_bank", "days",
              "Year-end inventory; annual COGS"),
    MetricDef("days_receivables", "Receivable Days", "(Receivables / Revenue) * 365",
              ["receivables", "revenue"], "non_bank", "days", "Year-end balances"),
    MetricDef("days_payables", "Payable Days", "(Payables / COGS) * 365",
              ["payables", "revenue", "gross_profit"], "non_bank", "days",
              "Year-end balances"),
    MetricDef("fcf", "Free Cash Flow", "Operating Cash Flow - |Capex|",
              ["ocf", "capex"], "non_bank", "pkr",
              "Annual; capex stored as outflow"),
    MetricDef("revenue_growth", "Revenue Growth", "(Current - Prior) / |Prior|",
              ["revenue (2 periods)"], "both", "pct", "Annual YoY"),
    MetricDef("altman_z", "Altman Z'' Score",
              "6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4 + 3.25",
              ["current_assets", "current_liabilities", "total_assets",
               "total_equity", "ebit", "retained_earnings"],
              "non_bank", "score",
              "Altman 2000, emerging-market book-value variant"),
]


def get_dependency_map() -> dict[str, list[str]]:
    """For each input field, which metrics does it unlock."""
    deps: dict[str, list[str]] = {}
    for m in METRIC_REGISTRY:
        for inp in m.required_inputs:
            deps.setdefault(inp, []).append(m.label)
    return deps


def compute_all(
    inc: dict, bs: dict, cf: dict,
    prior_inc: Optional[dict] = None,
    sector: str = "",
    shares_outstanding: Optional[float] = None,
    current_price: Optional[float] = None,
) -> dict:
    """
    Single entry point. Returns {
        "ratios": {name: value},
        "growth": {name: value},
        "dupont": DuPontResult,
        "altman": AltmanResult,
        "health": {score, confidence, basis, note},
        "missing_inputs": [str],
        "identity_checks": [str],
    }
    """
    is_bank_sector = _is_bank(sector)
    ratios = compute_ratios(inc, bs, cf, is_bank_sector)

    growth = {}
    if prior_inc:
        growth = compute_growth(inc, prior_inc)
        ratios.update(growth)

    # Market ratios
    if shares_outstanding and bs.get("total_equity"):
        ratios["book_value_per_share"] = _div(bs["total_equity"], shares_outstanding)
    if current_price and inc.get("eps"):
        ratios["pe"] = _div(current_price, inc["eps"])
    if current_price and ratios.get("book_value_per_share"):
        ratios["pb"] = _div(current_price, ratios["book_value_per_share"])

    dupont = compute_dupont(
        inc.get("net_income"), inc.get("revenue"),
        bs.get("total_assets"), bs.get("total_equity"),
    )

    altman = compute_altman_z(
        bs.get("current_assets"), bs.get("current_liabilities"),
        bs.get("total_assets"), bs.get("total_equity"),
        inc.get("revenue"), inc.get("ebit"),
        bs.get("retained_earnings"), sector=sector,
    )

    health = compute_health_score(ratios, altman.zone)

    # Identity checks
    checks = []
    ta = bs.get("total_assets")
    te = bs.get("total_equity")
    tl = bs.get("total_liabilities")
    if ta is not None and te is not None and tl is not None:
        diff = abs(ta - te - tl)
        if diff > ta * 0.01:
            checks.append(
                f"Assets ({_fmt_large(ta)}) != Equity ({_fmt_large(te)}) + "
                f"Liabilities ({_fmt_large(tl)}), diff={_fmt_large(diff)}")
    rev = inc.get("revenue")
    gp = inc.get("gross_profit")
    if rev is not None and gp is not None and gp > rev:
        checks.append("Gross profit exceeds revenue")

    # Missing inputs
    all_fields = set()
    for m in METRIC_REGISTRY:
        if m.applicability == "bank" and not is_bank_sector:
            continue
        if m.applicability == "non_bank" and is_bank_sector:
            continue
        all_fields.update(m.required_inputs)
    available = set()
    for d in (inc, bs, cf):
        available.update(k for k, v in d.items() if v is not None)
    missing_inputs = sorted(all_fields - available - {"revenue (2 periods)"})
    if prior_inc is None:
        missing_inputs.append("prior period (for growth metrics)")

    return {
        "ratios": ratios,
        "growth": growth,
        "dupont": dupont,
        "altman": altman,
        "health": health,
        "missing_inputs": missing_inputs,
        "identity_checks": checks,
    }
