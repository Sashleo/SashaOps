"""
intelligence/financial_model.py — Quantitative Financial Analysis Engine.

This module performs the work a trained financial analyst does:
- Full ratio analysis across 5 categories (liquidity, profitability,
  leverage, efficiency, growth)
- Three-statement financial model with 3-year projections
- DCF valuation with region-appropriate WACC
- Comparable company analysis with multiples table
- Altman Z-Score for bankruptcy risk
- DuPont decomposition of ROE
- Working capital analysis
- Sensitivity analysis on key assumptions

Data sources:
- Listed companies: PSX scrapers (psx_companies, psx_prices, psx_financials DB tables)
- Private companies: structured data extracted from uploaded documents

Output: A structured FinancialAnalysis object that feeds directly
into the Financials and Valuation report sections.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import Optional
import structlog

log = structlog.get_logger()

def _guess_ticker(business_name: str) -> str:
    """
    Attempt to match a business name to a PSX ticker from the database.
    Returns empty string if no match found.
    """
    try:
        from database.connection import get_db
        db = get_db()
        # Exact match
        row = db.execute(
            "SELECT ticker FROM psx_companies WHERE LOWER(company_name) = LOWER(?)",
            (business_name.strip(),)
        ).fetchone()
        if row:
            return row["ticker"]
        # Partial match
        row = db.execute(
            "SELECT ticker FROM psx_companies WHERE LOWER(company_name) LIKE LOWER(?)",
            (f"%{business_name.strip()}%",)
        ).fetchone()
        if row:
            return row["ticker"]
        return ""
    except Exception:
        return ""

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class LiquidityRatios:
    current_ratio: Optional[float] = None          # Current assets / current liabilities. >2 healthy, <1 danger
    quick_ratio: Optional[float] = None             # (Current assets - inventory) / current liabilities
    cash_ratio: Optional[float] = None              # Cash / current liabilities. Pure liquidity
    operating_cash_flow_ratio: Optional[float] = None  # OCF / current liabilities
    interpretation: str = ""


@dataclass
class ProfitabilityRatios:
    gross_margin: Optional[float] = None            # Gross profit / revenue
    ebitda_margin: Optional[float] = None           # EBITDA / revenue
    operating_margin: Optional[float] = None        # EBIT / revenue
    net_margin: Optional[float] = None              # Net income / revenue
    roe: Optional[float] = None                     # Net income / shareholders equity
    roa: Optional[float] = None                     # Net income / total assets
    roce: Optional[float] = None                    # EBIT / capital employed
    interpretation: str = ""


@dataclass
class LeverageRatios:
    debt_to_equity: Optional[float] = None          # Total debt / equity
    debt_to_ebitda: Optional[float] = None          # Net debt / EBITDA. >4x warning sign
    interest_coverage: Optional[float] = None       # EBIT / interest expense. <2x danger
    net_debt: Optional[float] = None                # Total debt - cash
    net_debt_to_equity: Optional[float] = None
    interpretation: str = ""


@dataclass
class EfficiencyRatios:
    asset_turnover: Optional[float] = None          # Revenue / total assets
    receivables_days: Optional[float] = None        # (Receivables / revenue) * 365
    payables_days: Optional[float] = None           # (Payables / COGS) * 365
    inventory_days: Optional[float] = None          # (Inventory / COGS) * 365
    cash_conversion_cycle: Optional[float] = None   # Receivables days + inventory days - payables days
    interpretation: str = ""


@dataclass
class GrowthMetrics:
    revenue_growth_yoy: Optional[float] = None      # Year-over-year revenue growth
    revenue_cagr_3y: Optional[float] = None         # 3-year compound annual growth rate
    gross_profit_growth: Optional[float] = None
    ebitda_growth: Optional[float] = None
    eps_growth: Optional[float] = None
    interpretation: str = ""


@dataclass
class AltmanZScore:
    z_score: Optional[float] = None
    # Z > 2.99: safe zone, 1.81-2.99: grey zone, < 1.81: distress zone
    zone: str = ""
    components: dict = field(default_factory=dict)
    interpretation: str = ""
    model: str = ""   # "Altman Z (standard)" | "Altman Z'' (bank/non-manufacturer)"


@dataclass
class DuPontAnalysis:
    """ROE decomposed into its three drivers."""
    net_margin: Optional[float] = None              # Profitability driver
    asset_turnover: Optional[float] = None          # Efficiency driver
    equity_multiplier: Optional[float] = None       # Leverage driver
    roe_computed: Optional[float] = None            # Should equal net_margin * asset_turnover * equity_multiplier
    interpretation: str = ""


@dataclass
class DCFValuation:
    revenue_base: Optional[float] = None
    ebitda_base: Optional[float] = None
    fcf_projections: list = field(default_factory=list)   # 5 years of FCF
    terminal_value: Optional[float] = None
    wacc: Optional[float] = None
    enterprise_value: Optional[float] = None
    equity_value: Optional[float] = None
    # Scenarios
    bear_value: Optional[float] = None
    base_value: Optional[float] = None
    bull_value: Optional[float] = None
    # Assumptions used
    revenue_growth_assumption: Optional[float] = None
    terminal_growth_rate: Optional[float] = None
    wacc_components: dict = field(default_factory=dict)
    sensitivity_table: dict = field(default_factory=dict)
    interpretation: str = ""


@dataclass
class ComparableCompany:
    name: str
    ticker: str
    market_cap: Optional[float] = None
    ev: Optional[float] = None
    revenue: Optional[float] = None
    ebitda: Optional[float] = None
    ev_revenue: Optional[float] = None
    ev_ebitda: Optional[float] = None
    pe_ratio: Optional[float] = None
    gross_margin: Optional[float] = None


@dataclass
class ComparableAnalysis:
    companies: list = field(default_factory=list)
    median_ev_revenue: Optional[float] = None
    median_ev_ebitda: Optional[float] = None
    median_pe: Optional[float] = None
    implied_value_ev_revenue: Optional[float] = None
    implied_value_ev_ebitda: Optional[float] = None
    interpretation: str = ""


@dataclass
class WorkingCapitalAnalysis:
    current_working_capital: Optional[float] = None
    working_capital_trend: str = ""       # improving | deteriorating | stable
    net_working_capital_to_revenue: Optional[float] = None
    funding_gap: Optional[float] = None   # Days of revenue needed to fund WC
    interpretation: str = ""


@dataclass
class ThreeStatementModel:
    """Simplified 3-year forward projection."""
    # P&L projections
    revenue_projections: list = field(default_factory=list)      # [Y1, Y2, Y3]
    ebitda_projections: list = field(default_factory=list)
    net_income_projections: list = field(default_factory=list)
    # Cash flow projections
    fcf_projections: list = field(default_factory=list)
    # Key assumptions
    revenue_growth_rates: list = field(default_factory=list)
    margin_assumptions: dict = field(default_factory=dict)
    capex_to_revenue: Optional[float] = None
    interpretation: str = ""


@dataclass
class FinancialAnalysis:
    """Complete financial analysis output."""
    ticker: Optional[str] = None
    company_name: str = ""
    currency: str = "USD"
    data_source: str = ""            # yfinance | uploaded_document | estimated
    fiscal_year: Optional[str] = None
    # Raw financials (latest year)
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    ebitda: Optional[float] = None
    ebit: Optional[float] = None
    net_income: Optional[float] = None
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    shareholders_equity: Optional[float] = None
    # Analysis components
    liquidity: LiquidityRatios = field(default_factory=LiquidityRatios)
    profitability: ProfitabilityRatios = field(default_factory=ProfitabilityRatios)
    leverage: LeverageRatios = field(default_factory=LeverageRatios)
    efficiency: EfficiencyRatios = field(default_factory=EfficiencyRatios)
    growth: GrowthMetrics = field(default_factory=GrowthMetrics)
    altman_z: AltmanZScore = field(default_factory=AltmanZScore)
    dupont: DuPontAnalysis = field(default_factory=DuPontAnalysis)
    dcf: DCFValuation = field(default_factory=DCFValuation)
    comps: ComparableAnalysis = field(default_factory=ComparableAnalysis)
    working_capital: WorkingCapitalAnalysis = field(default_factory=WorkingCapitalAnalysis)
    three_statement: ThreeStatementModel = field(default_factory=ThreeStatementModel)
    # Summary
    financial_health_score: Optional[float] = None   # 1-10
    health_confidence: float = 0.0                    # 0-1: fraction of metric weight available
    health_basis: list = field(default_factory=list)  # metric categories that contributed
    health_missing: list = field(default_factory=list) # categories with no usable metric
    key_risks: list = field(default_factory=list)
    key_strengths: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# ── WACC assumptions — Pakistan (with a global fallback for foreign peers) ─────
# Risk-free rates and equity risk premiums sourced from Damodaran data.
# The "global" entry is the fallback used only when benchmarking foreign sector peers.
REGIONAL_WACC_PARAMS = {
    "pakistan": {
        "risk_free_rate": 0.155,       # Pakistan 10Y govt bond
        "equity_risk_premium": 0.082,  # Pakistan ERP (Damodaran)
        "default_beta": 1.1,
        "default_tax_rate": 0.29,      # Pakistan corporate tax
        "currency": "PKR",
    },
    "global": {
        "risk_free_rate": 0.043,       # US 10Y Treasury ~4.3%
        "equity_risk_premium": 0.055,  # Damodaran US ERP
        "default_beta": 1.0,
        "default_tax_rate": 0.21,
        "currency": "USD",
    },
}


# ── Main entry points ─────────────────────────────────────────────────────────

def analyse_from_document(
    financials: dict,
    company_name: str,
    region_id: str = "pakistan",
    peer_tickers: Optional[list] = None,
    sector: str = "",
) -> FinancialAnalysis:
    """
    Full financial analysis from manually extracted financial data.
    Used when a client uploads their financial statements.

    Args:
        financials: Dict with keys matching FinancialAnalysis fields
                   (revenue, gross_profit, ebitda, ebit, net_income,
                    total_assets, total_debt, cash, shareholders_equity,
                    current_assets, current_liabilities, inventory,
                    receivables, payables, ocf, capex,
                    revenue_prior_year, revenue_2y_ago)
        company_name: Name of the company
        region_id: For WACC parameters
        peer_tickers: Optional listed peers for benchmarking
    """
    result = FinancialAnalysis(
        company_name=company_name,
        data_source="uploaded_document"
    )

    try:
        g = financials  # shorthand

        result.revenue             = g.get("revenue")
        result.gross_profit        = g.get("gross_profit")
        result.ebitda              = g.get("ebitda")
        result.ebit                = g.get("ebit")
        result.net_income          = g.get("net_income")
        result.total_assets        = g.get("total_assets")
        result.total_debt          = g.get("total_debt")
        result.cash                = g.get("cash")
        result.shareholders_equity = g.get("shareholders_equity")

        current_assets      = g.get("current_assets")
        current_liabilities = g.get("current_liabilities")
        inventory           = g.get("inventory")
        receivables         = g.get("receivables")
        payables            = g.get("payables")
        ocf                 = g.get("ocf")
        capex               = g.get("capex")

        revenue_history = [
            v for v in [
                g.get("revenue_2y_ago"),
                g.get("revenue_prior_year"),
                result.revenue
            ] if v is not None
        ]

        result.liquidity     = _compute_liquidity(current_assets, current_liabilities, inventory, ocf, result.cash)
        result.profitability = _compute_profitability(result.revenue, result.gross_profit, result.ebitda, result.ebit, result.net_income, result.total_assets, result.shareholders_equity)
        result.leverage      = _compute_leverage(result.total_debt, result.shareholders_equity, result.ebitda, result.ebit, result.cash, g.get("interest_expense"))
        result.efficiency    = _compute_efficiency(result.revenue, result.total_assets, receivables, payables, inventory, result.gross_profit)
        result.growth        = _compute_growth(revenue_history, result.gross_profit, result.ebitda, None)
        result.altman_z      = _compute_altman_z(current_assets, current_liabilities, result.total_assets, result.total_debt, result.shareholders_equity, result.revenue, result.ebit, result.cash, g.get("retained_earnings"), is_bank=_is_bank(sector), net_income=result.net_income)
        result.dupont        = _compute_dupont(result.net_income, result.revenue, result.total_assets, result.shareholders_equity)
        result.working_capital = _compute_working_capital(current_assets, current_liabilities, result.revenue, revenue_history)
        # M8 — enterprise FCFF DCF is not meaningful for financial institutions.
        # Banks are valued on multiples (P/B, P/E) instead (assembled downstream).
        # FIX 1 — never let a DCF error crash the whole analysis; leave dcf empty.
        if not _is_bank(sector):
            try:
                result.dcf   = _compute_dcf(result.revenue, result.ebitda, None, result.growth.revenue_growth_yoy, result.total_debt, result.cash, region_id)
            except Exception as _dcf_e:
                log.warning("DCF computation failed - skipping DCF", error=str(_dcf_e)[:120])
        result.three_statement = _build_three_statement_model(result.revenue, result.profitability.gross_margin, result.profitability.ebitda_margin, result.profitability.net_margin, result.growth.revenue_growth_yoy, capex)

        if peer_tickers:
            result.comps = _compute_comparables(peer_tickers, result.revenue, result.ebitda)

        _health = _compute_health_score(result, is_bank=_is_bank(sector), is_stub=bool(g.get("_stub")))
        result.financial_health_score = _health["score"]
        result.health_confidence      = _health["confidence"]
        result.health_basis           = _health["basis"]
        result.health_missing         = _health["missing"]
        result.key_risks, result.key_strengths = _identify_risks_strengths(result)

    except Exception as e:
        result.errors.append(f"Analysis error: {str(e)}")
        log.error("Document financial analysis failed", error=str(e))

    return result


# ── Ratio computation functions ───────────────────────────────────────────────

def _safe_div(a, b) -> Optional[float]:
    try:
        if a is None or b is None or b == 0:
            return None
        return round(float(a) / float(b), 4)
    except Exception:
        return None


def _compute_liquidity(current_assets, current_liabilities, inventory, ocf, cash) -> LiquidityRatios:
    r = LiquidityRatios()
    r.current_ratio = _safe_div(current_assets, current_liabilities)
    if current_assets is not None and inventory is not None:
        r.quick_ratio = _safe_div(current_assets - inventory, current_liabilities)
    r.cash_ratio = _safe_div(cash, current_liabilities)
    r.operating_cash_flow_ratio = _safe_div(ocf, current_liabilities)

    # Interpretation
    if r.current_ratio is not None:
        if r.current_ratio >= 2.0:
            r.interpretation = "Strong liquidity position. Company can comfortably cover short-term obligations."
        elif r.current_ratio >= 1.2:
            r.interpretation = "Adequate liquidity. Short-term obligations covered but limited buffer."
        elif r.current_ratio >= 1.0:
            r.interpretation = "Tight liquidity. Monitor cash closely - limited room for unexpected expenses."
        else:
            r.interpretation = "LIQUIDITY RISK: Current liabilities exceed current assets. Potential short-term solvency concern."

    return r


def _compute_profitability(revenue, gross_profit, ebitda, ebit, net_income, total_assets, equity) -> ProfitabilityRatios:
    r = ProfitabilityRatios()
    r.gross_margin    = _safe_div(gross_profit, revenue)
    r.ebitda_margin   = _safe_div(ebitda, revenue)
    r.operating_margin = _safe_div(ebit, revenue)
    r.net_margin      = _safe_div(net_income, revenue)
    r.roa             = _safe_div(net_income, total_assets)
    r.roe             = _safe_div(net_income, equity)

    # ROCE = EBIT / (Total Assets - Current Liabilities) — approximated as EBIT / Total Assets here
    r.roce            = _safe_div(ebit, total_assets)

    # Interpretation
    flags = []
    if r.net_margin is not None:
        if r.net_margin < 0:
            flags.append("Company is loss-making at the net income level")
        elif r.net_margin < 0.03:
            flags.append("Very thin net margins - vulnerable to cost increases")
    if r.ebitda_margin is not None and r.ebitda_margin < 0.10:
        flags.append("EBITDA margin below 10% - limited operating leverage")
    if r.roe is not None and r.roe > 0.20:
        flags.append("Strong ROE above 20% - efficient use of shareholder capital")

    r.interpretation = " | ".join(flags) if flags else "Profitability within normal range"
    return r


def _compute_leverage(total_debt, equity, ebitda, ebit, cash, interest_expense=None) -> LeverageRatios:
    r = LeverageRatios()
    r.debt_to_equity = _safe_div(total_debt, equity)

    if total_debt is not None and cash is not None:
        r.net_debt = total_debt - cash
        r.net_debt_to_equity = _safe_div(r.net_debt, equity)
        r.debt_to_ebitda     = _safe_div(r.net_debt, ebitda)

    # Interest coverage — from the explicit interest_expense (PSX statements / documents).
    try:
        ie = abs(float(interest_expense)) if interest_expense is not None else None
    except Exception:
        ie = None
    if ie and ie > 0:
        r.interest_coverage = _safe_div(ebit, ie)

    # Interpretation
    flags = []
    if r.debt_to_ebitda is not None:
        if r.debt_to_ebitda > 5:
            flags.append("HIGH LEVERAGE: Net debt/EBITDA above 5x - significant refinancing risk")
        elif r.debt_to_ebitda > 3:
            flags.append("Elevated leverage: Net debt/EBITDA 3-5x - manageable but monitor")
        elif r.debt_to_ebitda < 1:
            flags.append("Conservative balance sheet: Net debt/EBITDA below 1x")

    if r.interest_coverage is not None and r.interest_coverage < 2:
        flags.append("INTEREST COVERAGE RISK: EBIT less than 2x interest expense")

    r.interpretation = " | ".join(flags) if flags else "Leverage within acceptable range"
    return r


def _compute_efficiency(revenue, total_assets, receivables, payables, inventory, cogs) -> EfficiencyRatios:
    r = EfficiencyRatios()
    r.asset_turnover   = _safe_div(revenue, total_assets)
    r.receivables_days = None if revenue is None else _safe_div(receivables * 365, revenue) if receivables else None
    r.payables_days    = None if cogs is None else _safe_div(payables * 365, cogs) if payables else None
    r.inventory_days   = None if cogs is None else _safe_div(inventory * 365, cogs) if inventory else None

    if all(v is not None for v in [r.receivables_days, r.inventory_days, r.payables_days]):
        r.cash_conversion_cycle = round(r.receivables_days + r.inventory_days - r.payables_days, 1)

    flags = []
    if r.cash_conversion_cycle is not None:
        if r.cash_conversion_cycle > 90:
            flags.append(f"Long cash conversion cycle ({r.cash_conversion_cycle:.0f} days) - capital tied up in operations")
        elif r.cash_conversion_cycle < 0:
            flags.append(f"Negative CCC ({r.cash_conversion_cycle:.0f} days) - company funded by suppliers (e.g. retail model)")

    r.interpretation = " | ".join(flags) if flags else "Operating efficiency within normal range"
    return r


def _compute_growth(revenue_history: list, gross_profit, ebitda, income_stmt) -> GrowthMetrics:
    r = GrowthMetrics()

    if len(revenue_history) >= 2:
        r.revenue_growth_yoy = _safe_div(revenue_history[-1] - revenue_history[-2], abs(revenue_history[-2]))

    if len(revenue_history) >= 3:
        try:
            r.revenue_cagr_3y = round(
                (revenue_history[-1] / revenue_history[-3]) ** (1/2) - 1, 4
            )
        except Exception:
            pass

    if r.revenue_growth_yoy is not None:
        if r.revenue_growth_yoy > 0.20:
            r.interpretation = f"High growth: revenue growing at {r.revenue_growth_yoy:.1%} YoY"
        elif r.revenue_growth_yoy > 0.05:
            r.interpretation = f"Steady growth: {r.revenue_growth_yoy:.1%} YoY"
        elif r.revenue_growth_yoy > 0:
            r.interpretation = f"Slow growth: {r.revenue_growth_yoy:.1%} YoY - below inflation in most markets"
        else:
            r.interpretation = f"Revenue declining: {r.revenue_growth_yoy:.1%} YoY - investigate cause"

    return r


BANKING_SECTORS = {
    "banks", "banking", "commercial banks", "investment banks",
    "microfinance", "financial services", "modarabas", "leasing",
}


def _is_bank(sector: str) -> bool:
    return any(b in (sector or "").lower() for b in BANKING_SECTORS)


def _compute_altman_z(current_assets, current_liabilities, total_assets, total_debt, equity, revenue, ebit, cash, retained_earnings=None, is_bank=False, net_income=None) -> AltmanZScore:
    r = AltmanZScore()

    try:
        # Banks report no "current assets/liabilities" and carry very different
        # leverage, so the classic 5-factor Z is meaningless for them. Use the
        # four-factor Altman Z''-Score (emerging-market / non-manufacturer variant).
        if is_bank:
            if not all(v is not None and total_assets and total_assets != 0 for v in
                       [total_assets, ebit]):
                r.interpretation = ("Z-Score unavailable: balance sheet data incomplete. "
                                    "Upload annual report to enable.")
                return r
            wc = (current_assets - current_liabilities) if (
                current_assets is not None and current_liabilities is not None) else 0
            if retained_earnings is None:
                retained_earnings = net_income if net_income is not None else 0
            x1 = wc / total_assets
            x2 = retained_earnings / total_assets
            x3 = ebit / total_assets
            x4 = (equity or 0) / (total_debt if total_debt and total_debt > 0 else 1)
            r.z_score = round(6.56*x1 + 3.26*x2 + 6.72*x3 + 1.05*x4, 3)
            r.components = {"X1": round(x1,3), "X2": round(x2,3), "X3": round(x3,3), "X4": round(x4,3)}
            r.model = "Altman Z'' (bank/non-manufacturer)"
            if r.z_score > 2.6:
                r.zone = "SAFE"
                r.interpretation = f"Z''-Score {r.z_score:.2f} - Safe zone (bank model). Low distress risk."
            elif r.z_score > 1.1:
                r.zone = "GREY"
                r.interpretation = f"Z''-Score {r.z_score:.2f} - Grey zone (bank model). Monitor capital adequacy."
            else:
                r.zone = "DISTRESS"
                r.interpretation = f"Z''-Score {r.z_score:.2f} - Distress zone (bank model). Elevated risk."
            return r

        if not all(v is not None and total_assets and total_assets != 0 for v in
                   [current_assets, current_liabilities, total_assets, revenue, ebit]):
            r.interpretation = ("Z-Score unavailable: balance sheet data incomplete. "
                                "Upload annual report to enable.")
            return r

        working_capital     = current_assets - current_liabilities
        # Use reported retained earnings when available; else approximate from equity.
        if retained_earnings is None:
            retained_earnings = equity * 0.4 if equity else 0
        market_cap_proxy    = equity if equity and equity > 0 else 1

        x1 = working_capital / total_assets
        x2 = retained_earnings / total_assets
        x3 = ebit / total_assets
        x4 = market_cap_proxy / (total_debt if total_debt and total_debt > 0 else 1)
        x5 = revenue / total_assets

        r.z_score = round(1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5, 3)
        r.components = {"X1": round(x1,3), "X2": round(x2,3), "X3": round(x3,3), "X4": round(x4,3), "X5": round(x5,3)}
        r.model = "Altman Z (standard)"

        if r.z_score > 2.99:
            r.zone = "SAFE"
            r.interpretation = f"Z-Score {r.z_score:.2f} - Safe zone. Low bankruptcy risk."
        elif r.z_score > 1.81:
            r.zone = "GREY"
            r.interpretation = f"Z-Score {r.z_score:.2f} - Grey zone. Some financial stress. Monitor closely."
        else:
            r.zone = "DISTRESS"
            r.interpretation = f"Z-Score {r.z_score:.2f} - Distress zone. Elevated bankruptcy risk. Investigate immediately."

    except Exception as e:
        r.interpretation = f"Z-Score computation error: {str(e)}"

    return r


def _compute_dupont(net_income, revenue, total_assets, equity) -> DuPontAnalysis:
    r = DuPontAnalysis()
    r.net_margin      = _safe_div(net_income, revenue)
    r.asset_turnover  = _safe_div(revenue, total_assets)
    r.equity_multiplier = _safe_div(total_assets, equity)

    if all(v is not None for v in [r.net_margin, r.asset_turnover, r.equity_multiplier]):
        r.roe_computed = round(r.net_margin * r.asset_turnover * r.equity_multiplier, 4)

        # Identify the primary ROE driver
        drivers = {
            "profitability (net margin)":   r.net_margin,
            "efficiency (asset turnover)":  r.asset_turnover,
            "leverage (equity multiplier)": r.equity_multiplier,
        }

        # Normalise to identify dominant driver
        if r.roe_computed and r.roe_computed > 0:
            if r.equity_multiplier > 3:
                r.interpretation = f"ROE of {r.roe_computed:.1%} is primarily driven by financial leverage - strip out debt and underlying business returns are lower"
            elif r.net_margin and r.net_margin > 0.15:
                r.interpretation = f"ROE of {r.roe_computed:.1%} is driven by strong profitability - high-quality earnings"
            else:
                r.interpretation = f"ROE of {r.roe_computed:.1%} - balanced contribution from margin, efficiency, and leverage"
        else:
            r.interpretation = "Negative or zero ROE - equity is being eroded"

    return r


def _compute_dcf(revenue, ebitda, fcf, revenue_growth, total_debt, cash, region_id: str = "pakistan") -> DCFValuation:
    r = DCFValuation()

    if revenue is None:
        r.interpretation = "Insufficient data for DCF valuation"
        return r

    params = REGIONAL_WACC_PARAMS.get(region_id, REGIONAL_WACC_PARAMS["global"])

    # WACC computation
    # Simplified: WACC = Ke (assuming all-equity for private, adjust for listed)
    beta          = params["default_beta"]
    rfr           = params["risk_free_rate"]
    rfr_source    = "regional default (10Y govt bond)"

    # Prefer the live SBP policy rate (Pakistan monetary policy) as the risk-free
    # rate so DCF valuations reflect actual conditions. Stored as percentage points
    # (e.g. 22.0) → divide by 100 for a decimal fraction.
    if region_id == "pakistan":
        live_rate = None
        try:
            from database.macro_data import get_latest_macro
            macro     = get_latest_macro()
            live_rate = macro.get("sbp_policy_rate")
            risk_free = (live_rate or 11.0) / 100
        except Exception:
            risk_free = 0.11
        rfr        = risk_free
        rfr_source = "SBP policy rate (live)" if live_rate else "default (11%)"

    erp           = params["equity_risk_premium"]
    ke            = rfr + beta * erp      # Cost of equity (CAPM)
    r.wacc        = round(ke, 4)          # Simplified — use ke as WACC for private companies

    r.wacc_components = {
        "risk_free_rate":     f"{rfr:.1%}",
        "risk_free_source":   rfr_source,
        "equity_risk_premium": f"{erp:.1%}",
        "beta":               beta,
        "cost_of_equity":     f"{ke:.1%}",
        "note":               "Simplified CAPM. Adjust debt/equity weighting for capital structure.",
    }

    # Growth assumptions
    if revenue_growth is not None and revenue_growth > 0:
        base_growth    = min(revenue_growth, 0.25)   # Cap at 25%
        bear_growth    = base_growth * 0.4
        bull_growth    = base_growth * 1.5
    else:
        base_growth = 0.08
        bear_growth = 0.02
        bull_growth = 0.18

    terminal_growth = min(rfr * 0.5, 0.04)  # Conservative terminal growth
    r.terminal_growth_rate    = terminal_growth
    r.revenue_growth_assumption = base_growth

    # FCF margin assumption
    ebitda_margin = (ebitda / revenue) if (ebitda and revenue) else 0.12
    fcf_margin    = max(ebitda_margin * 0.55, 0.04)   # FCF ~55% of EBITDA after tax/capex

    # 5-year FCF projections (base case)
    r.revenue_base = revenue
    r.ebitda_base  = ebitda

    def _project_fcfs(growth_rate):
        projections = []
        rev = revenue
        for _ in range(5):
            rev = rev * (1 + growth_rate)
            projections.append(round(rev * fcf_margin, 0))
        return projections

    base_fcfs  = _project_fcfs(base_growth)
    bear_fcfs  = _project_fcfs(bear_growth)
    bull_fcfs  = _project_fcfs(bull_growth)

    r.fcf_projections = base_fcfs

    def _dcf_value(fcfs, wacc, tgr):
        try:
            pv_fcfs = sum(fcf / (1 + wacc)**i for i, fcf in enumerate(fcfs, 1))
            terminal = fcfs[-1] * (1 + tgr) / (wacc - tgr)
            pv_terminal = terminal / (1 + wacc)**5
            ev = pv_fcfs + pv_terminal
            net_debt_val = (total_debt or 0) - (cash or 0)
            equity_val = ev - net_debt_val
            return round(ev, 0), round(equity_val, 0)
        except Exception:
            return None, None

    base_ev,  base_eq  = _dcf_value(base_fcfs,  r.wacc, terminal_growth)
    bear_ev,  bear_eq  = _dcf_value(bear_fcfs,  r.wacc, terminal_growth * 0.5)
    bull_ev,  bull_eq  = _dcf_value(bull_fcfs,  r.wacc * 0.9, terminal_growth * 1.5)

    r.enterprise_value = base_ev
    r.equity_value     = base_eq
    r.base_value       = base_eq
    r.bear_value       = bear_eq
    r.bull_value       = bull_eq

    # Sensitivity table: equity value at different WACC and growth combinations
    if revenue:
        sensitivity = {}
        for g in [bear_growth, base_growth, bull_growth]:
            row = {}
            for w_adj in [-0.02, 0, 0.02]:
                _, eq = _dcf_value(_project_fcfs(g), r.wacc + w_adj, terminal_growth)
                row[f"WACC {(r.wacc + w_adj):.1%}"] = eq
            sensitivity[f"Growth {g:.1%}"] = row
        r.sensitivity_table = sensitivity

    if base_eq and revenue:
        ev_rev_multiple = round(base_ev / revenue, 1) if base_ev else None
        r.interpretation = (
            f"Base case equity value: {_fmt(base_eq)} | "
            f"Bear: {_fmt(bear_eq)} | Bull: {_fmt(bull_eq)} | "
            f"Implied EV/Revenue: {ev_rev_multiple}x | "
            f"WACC: {r.wacc:.1%} | Terminal growth: {terminal_growth:.1%}"
        )
    else:
        r.interpretation = "DCF computation incomplete - insufficient financial data"

    return r


def _compute_comparables(peer_tickers: list, target_revenue, target_ebitda) -> ComparableAnalysis:
    """PSX peer comparables — populated from psx_companies DB, not yfinance."""
    r = ComparableAnalysis()
    if not peer_tickers:
        return r
    try:
        from database.connection import get_db
        db = get_db()
        placeholders = ",".join("?" * len(peer_tickers[:8]))
        rows = db.execute(
            f"SELECT symbol, company_name, market_cap, sector_name "
            f"FROM psx_companies WHERE symbol IN ({placeholders})",
            peer_tickers[:8],
        ).fetchall()
        companies = []
        for row in rows:
            companies.append(ComparableCompany(
                name=row["company_name"] or row["symbol"],
                ticker=row["symbol"],
                market_cap=row["market_cap"],
            ))
        r.companies = companies
        if companies:
            r.interpretation = f"PSX peer group: {len(companies)} listed companies in same sector"
    except Exception as e:
        log.warning("PSX comparables lookup failed", error=str(e)[:80])
    return r


def _compute_working_capital(current_assets, current_liabilities, revenue, revenue_history) -> WorkingCapitalAnalysis:
    r = WorkingCapitalAnalysis()

    if current_assets is not None and current_liabilities is not None:
        r.current_working_capital = current_assets - current_liabilities
        r.net_working_capital_to_revenue = _safe_div(r.current_working_capital, revenue)

        if revenue:
            r.funding_gap = round((r.current_working_capital / revenue) * 365, 1)

    if len(revenue_history) >= 2 and revenue:
        wc_ratio_now = _safe_div(r.current_working_capital, revenue)
        r.working_capital_trend = "stable"
        if wc_ratio_now is not None:
            r.working_capital_trend = "improving" if wc_ratio_now > 0.15 else "deteriorating" if wc_ratio_now < 0.05 else "stable"

    r.interpretation = f"Working capital: {_fmt(r.current_working_capital)} | NWC/Revenue: {r.net_working_capital_to_revenue:.1%}" if r.current_working_capital and r.net_working_capital_to_revenue else "Insufficient data"
    return r


def _build_three_statement_model(revenue, gross_margin, ebitda_margin, net_margin, growth_rate, capex) -> ThreeStatementModel:
    r = ThreeStatementModel()

    if not revenue:
        return r

    g = growth_rate if growth_rate and growth_rate > 0 else 0.08
    gm = gross_margin or 0.35
    em = ebitda_margin or 0.12
    nm = net_margin or 0.06

    # Apply slight margin improvement assumption (management usually targets this)
    for yr in range(1, 4):
        proj_rev    = round(revenue * (1 + g) ** yr, 0)
        proj_ebitda = round(proj_rev * min(em * (1 + 0.01 * yr), em * 1.05), 0)
        proj_ni     = round(proj_rev * min(nm * (1 + 0.01 * yr), nm * 1.05), 0)
        capex_yr    = round(proj_rev * 0.04, 0)   # Default capex assumption: 4% of revenue
        proj_fcf    = round(proj_ebitda * 0.55 - capex_yr, 0)

        r.revenue_projections.append(proj_rev)
        r.ebitda_projections.append(proj_ebitda)
        r.net_income_projections.append(proj_ni)
        r.fcf_projections.append(proj_fcf)
        r.revenue_growth_rates.append(round(g, 4))

    r.margin_assumptions = {
        "gross_margin": gm,
        "ebitda_margin": em,
        "net_margin": nm,
    }
    r.capex_to_revenue = 0.04
    try:
        r.interpretation = (
            f"3-year projection at {g:.1%} CAGR | "
            f"Y3 Revenue: {_fmt(r.revenue_projections[-1] if r.revenue_projections else None)} | "
            f"Y3 EBITDA: {_fmt(r.ebitda_projections[-1] if r.ebitda_projections else None)} | "
            f"Y3 FCF: {_fmt(r.fcf_projections[-1] if r.fcf_projections else None)}"
        )
    except Exception:
        r.interpretation = "3-year projections computed - see table below"
    return r


def _compute_health_score(analysis: FinancialAnalysis, is_bank: bool = False,
                          is_stub: bool = False) -> dict:
    """
    Confidence-weighted health score. Only metrics passing plausibility validation
    (intelligence/validation.py) contribute. Category weights sum to 7.0 —
    liquidity 1.5, profitability 2.0, leverage 1.5, growth 1.0, altman 1.0.

    Returns {score, confidence, basis, missing}. confidence = contributing / 7.0.
    Stub-sourced data (approximated from market cap) caps confidence at 0.3.
    """
    from intelligence.validation import validate_metric

    score = 5.0
    contributing = 0.0
    basis: list = []
    missing: list = []

    # Liquidity (1.5) — banks don't report current assets/liabilities, so N/A there.
    cr = None if is_bank else validate_metric("current_ratio", analysis.liquidity.current_ratio, is_bank)["value"]
    if cr is not None:
        contributing += 1.5; basis.append("liquidity")
        if cr >= 2.0:   score += 1.5
        elif cr >= 1.5: score += 0.75
        elif cr < 1.0:  score -= 1.5
    else:
        missing.append("liquidity")

    # Profitability (2.0) — net margin.
    nm = validate_metric("net_margin", analysis.profitability.net_margin, is_bank)["value"]
    if nm is not None:
        contributing += 2.0; basis.append("profitability")
        if nm >= 0.15:   score += 2.0
        elif nm >= 0.08: score += 1.0
        elif nm < 0:     score -= 2.0
    else:
        missing.append("profitability")

    # Leverage (1.5) — net debt/EBITDA (skip for banks; not meaningful).
    de = None if is_bank else validate_metric("debt_to_equity", analysis.leverage.debt_to_equity, is_bank)["value"]
    dte = analysis.leverage.debt_to_ebitda
    if not is_bank and dte is not None:
        contributing += 1.5; basis.append("leverage")
        if dte <= 1.0:   score += 1.5
        elif dte <= 3.0: score += 0.5
        elif dte <= 5.0: score -= 0.5
        else:            score -= 1.5
    else:
        missing.append("leverage")

    # Growth (1.0).
    g = validate_metric("revenue_growth", analysis.growth.revenue_growth_yoy, is_bank)["value"]
    if g is not None:
        contributing += 1.0; basis.append("growth")
        if g >= 0.20:   score += 1.0
        elif g >= 0.05: score += 0.5
        elif g < 0:     score -= 1.0
    else:
        missing.append("growth")

    # Altman zone (1.0).
    if analysis.altman_z.zone in ("SAFE", "GREY", "DISTRESS"):
        contributing += 1.0; basis.append("altman")
        if analysis.altman_z.zone == "SAFE":       score += 1.0
        elif analysis.altman_z.zone == "DISTRESS": score -= 1.0
    else:
        missing.append("altman")

    confidence = round(contributing / 7.0, 2)
    if is_stub:
        confidence = min(confidence, 0.3)

    return {
        "score":      round(max(1.0, min(10.0, score)), 1),
        "confidence": confidence,
        "basis":      basis,
        "missing":    missing,
    }


def _identify_risks_strengths(analysis: FinancialAnalysis) -> tuple[list, list]:
    risks, strengths = [], []

    if analysis.liquidity.current_ratio:
        if analysis.liquidity.current_ratio < 1.0:
            risks.append(f"Current ratio {analysis.liquidity.current_ratio:.2f} - short-term liquidity risk")
        elif analysis.liquidity.current_ratio >= 2.0:
            strengths.append(f"Strong current ratio {analysis.liquidity.current_ratio:.2f}")

    if analysis.profitability.net_margin:
        if analysis.profitability.net_margin < 0:
            risks.append("Loss-making at net income level")
        elif analysis.profitability.net_margin > 0.15:
            strengths.append(f"High net margin {analysis.profitability.net_margin:.1%}")

    if analysis.leverage.debt_to_ebitda:
        if analysis.leverage.debt_to_ebitda > 5:
            risks.append(f"High leverage: net debt/EBITDA {analysis.leverage.debt_to_ebitda:.1f}x")

    if analysis.leverage.interest_coverage:
        if analysis.leverage.interest_coverage < 2:
            risks.append(f"Weak interest coverage {analysis.leverage.interest_coverage:.1f}x")
        elif analysis.leverage.interest_coverage > 5:
            strengths.append(f"Strong interest coverage {analysis.leverage.interest_coverage:.1f}x")

    if analysis.altman_z.zone == "DISTRESS":
        risks.append(f"Altman Z-Score in distress zone ({analysis.altman_z.z_score:.2f})")
    elif analysis.altman_z.zone == "SAFE":
        strengths.append(f"Altman Z-Score in safe zone ({analysis.altman_z.z_score:.2f})")

    if analysis.growth.revenue_growth_yoy:
        if analysis.growth.revenue_growth_yoy > 0.20:
            strengths.append(f"High revenue growth {analysis.growth.revenue_growth_yoy:.1%} YoY")
        elif analysis.growth.revenue_growth_yoy < 0:
            risks.append(f"Revenue declining {analysis.growth.revenue_growth_yoy:.1%} YoY")

    return risks[:5], strengths[:5]


def _fmt(value) -> str:
    """Format a large number to human-readable form."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        if abs(v) >= 1e12:  return f"{v/1e12:.2f}T"
        if abs(v) >= 1e9:   return f"{v/1e9:.2f}B"
        if abs(v) >= 1e6:   return f"{v/1e6:.2f}M"
        if abs(v) >= 1e3:   return f"{v/1e3:.2f}K"
        return f"{v:.2f}"
    except Exception:
        return str(value)


def to_report_dict(analysis: FinancialAnalysis) -> dict:
    """
    Convert FinancialAnalysis to a clean dict for report rendering.
    Formats numbers for human readability.
    """
    return {
        "company":         analysis.company_name,
        "ticker":          analysis.ticker,
        "data_source":     analysis.data_source,
        "fiscal_year":     analysis.fiscal_year,
        "health_score":    analysis.financial_health_score,
        "currency":        analysis.currency,
        # Raw financials
        "revenue":         _fmt(analysis.revenue),
        "gross_profit":    _fmt(analysis.gross_profit),
        "ebitda":          _fmt(analysis.ebitda),
        "net_income":      _fmt(analysis.net_income),
        "total_debt":      _fmt(analysis.total_debt),
        "cash":            _fmt(analysis.cash),
        "net_debt":        _fmt(analysis.leverage.net_debt),
        # Ratios
        "current_ratio":   analysis.liquidity.current_ratio,
        "quick_ratio":     analysis.liquidity.quick_ratio,
        "gross_margin":    f"{analysis.profitability.gross_margin:.1%}" if analysis.profitability.gross_margin else "N/A",
        "ebitda_margin":   f"{analysis.profitability.ebitda_margin:.1%}" if analysis.profitability.ebitda_margin else "N/A",
        "net_margin":      f"{analysis.profitability.net_margin:.1%}" if analysis.profitability.net_margin else "N/A",
        "roe":             f"{analysis.profitability.roe:.1%}" if analysis.profitability.roe else "N/A",
        "roa":             f"{analysis.profitability.roa:.1%}" if analysis.profitability.roa else "N/A",
        "debt_to_ebitda":  analysis.leverage.debt_to_ebitda,
        "interest_coverage": analysis.leverage.interest_coverage,
        "ccc_days":        analysis.efficiency.cash_conversion_cycle,
        "revenue_growth":  f"{analysis.growth.revenue_growth_yoy:.1%}" if analysis.growth.revenue_growth_yoy else "N/A",
        "revenue_cagr_3y": f"{analysis.growth.revenue_cagr_3y:.1%}" if analysis.growth.revenue_cagr_3y else "N/A",
        # Altman Z
        "altman_z":        analysis.altman_z.z_score,
        "altman_zone":     analysis.altman_z.zone,
        # DCF
        "dcf_base":        _fmt(analysis.dcf.base_value),
        "dcf_bear":        _fmt(analysis.dcf.bear_value),
        "dcf_bull":        _fmt(analysis.dcf.bull_value),
        "wacc":            f"{analysis.dcf.wacc:.1%}" if analysis.dcf.wacc else "N/A",
        # Projections
        "revenue_y1":      _fmt(analysis.three_statement.revenue_projections[0]) if analysis.three_statement.revenue_projections else "N/A",
        "revenue_y2":      _fmt(analysis.three_statement.revenue_projections[1]) if len(analysis.three_statement.revenue_projections) > 1 else "N/A",
        "revenue_y3":      _fmt(analysis.three_statement.revenue_projections[2]) if len(analysis.three_statement.revenue_projections) > 2 else "N/A",
        # Interpretations
        "liquidity_note":     analysis.liquidity.interpretation,
        "profitability_note": analysis.profitability.interpretation,
        "leverage_note":      analysis.leverage.interpretation,
        "growth_note":        analysis.growth.interpretation,
        "dupont_note":        analysis.dupont.interpretation,
        "dcf_note":           analysis.dcf.interpretation,
        "comps_note":         analysis.comps.interpretation,
        "key_risks":          analysis.key_risks,
        "key_strengths":      analysis.key_strengths,
        "errors":             analysis.errors,
    }


# ── Local-first entry points (no AI call) ────────────────────────────────────

def run_local_analysis_psx(ticker: str) -> dict:
    """
    Run the FULL local financial analysis on PSX-sourced statements (not yfinance).
    Pakistani listed companies get the complete ratio/Altman/DuPont/DCF engine with
    PSX as the data source. No AI call — pure math. Returns the same flat dict shape
    as run_local_analysis(), with data_source="psx".

    Raises ValueError if PSX has no usable financial data for the ticker.
    """
    from intelligence.psx_financials import fetch_psx_doc_financials

    fetched = fetch_psx_doc_financials(ticker)
    if not fetched:
        raise ValueError(f"No PSX financial data for {ticker}")
    doc, company_name = fetched
    src = doc.get("_source", "psx_summary")
    ocr = bool(doc.get("_ocr"))

    # PSX statements are reported in PKR thousands — scale monetary fields to actual
    # PKR so absolute outputs (DCF, revenue) are in the correct magnitude (billions,
    # not millions). Ratios are unit-independent so this doesn't change them. The
    # market-cap STUB is already in PKR millions, so it is not rescaled.
    if not doc.get("_stub"):
        _SCALE = 1_000
        _MONEY = ("revenue", "gross_profit", "ebitda", "ebit", "net_income",
                  "total_assets", "total_debt", "cash", "shareholders_equity",
                  "current_assets", "current_liabilities", "inventory", "receivables",
                  "payables", "ocf", "capex", "interest_expense", "depreciation",
                  "retained_earnings", "revenue_prior_year", "revenue_2y_ago")
        doc = {**doc, **{k: doc[k] * _SCALE for k in _MONEY
                         if isinstance(doc.get(k), (int, float))}}

    # Resolve the PSX sector so banks use the correct (four-factor) Altman model.
    sector = ""
    try:
        from database.connection import get_db
        row = get_db().execute(
            "SELECT sector_name FROM psx_companies WHERE symbol=? LIMIT 1",
            (ticker.strip().upper(),),
        ).fetchone()
        if row:
            sector = row["sector_name"] or ""
    except Exception:
        pass

    analysis = analyse_from_document(doc, company_name, region_id="pakistan", sector=sector)
    analysis.ticker      = ticker.strip().upper()
    analysis.currency    = "PKR"
    analysis.data_source = src
    if doc.get("_fiscal_year"):
        analysis.fiscal_year = doc["_fiscal_year"]

    if analysis.errors and not analysis.revenue:
        raise ValueError(analysis.errors[0])

    result = _assemble_report_dict(analysis, "pakistan", is_bank=_is_bank(sector))
    result["source"]      = src
    result["ocr_derived"] = ocr

    # M8 — bank valuation via multiples (P/B, P/E) from market cap + equity + net income.
    if _is_bank(sector):
        try:
            from database.connection import get_db
            row = get_db().execute(
                "SELECT market_cap FROM psx_companies WHERE symbol=? LIMIT 1",
                (ticker.strip().upper(),),
            ).fetchone()
            mcap = float(row["market_cap"]) if (row and row["market_cap"]) else None  # PKR mn
            eq   = analysis.shareholders_equity   # PKR (scaled)
            ni   = analysis.net_income
            eq_mn = (eq / 1_000_000) if eq else None
            ni_mn = (ni / 1_000_000) if ni else None
            pb = (mcap / eq_mn) if (mcap and eq_mn) else None
            pe = (mcap / ni_mn) if (mcap and ni_mn and ni_mn > 0) else None
            result["bank_multiples"] = {"market_cap_mn": mcap, "pb": pb, "pe": pe}
        except Exception:
            result["bank_multiples"] = {}
    return result


def _assemble_report_dict(analysis: "FinancialAnalysis", region_id: str, is_bank: bool = False) -> dict:
    """Build the flat, template-/JSON-friendly dict from a FinancialAnalysis.

    Every ratio is passed through the plausibility validator (M7): an implausible
    value (parse/unit error) is nulled here so it never displays as a number and
    never feeds verdicts. The set of excluded metrics is returned in `_excluded`.
    """
    from intelligence.validation import validate_metric

    result = to_report_dict(analysis)

    _raw_metrics = {
        "gross_margin":       analysis.profitability.gross_margin,
        "ebitda_margin":      analysis.profitability.ebitda_margin,
        "net_margin":         analysis.profitability.net_margin,
        "roe":                analysis.profitability.roe,
        "roa":                analysis.profitability.roa,
        "current_ratio":      analysis.liquidity.current_ratio,
        "quick_ratio":        analysis.liquidity.quick_ratio,
        "debt_to_equity":     analysis.leverage.debt_to_equity,
        "interest_coverage":  analysis.leverage.interest_coverage,
        "revenue_growth":     analysis.growth.revenue_growth_yoy,
        "altman_z":           analysis.altman_z.z_score,
        "asset_turnover":     analysis.efficiency.asset_turnover,
        "wacc":               analysis.dcf.wacc,
    }
    excluded: list = []
    validated: dict = {}
    for name, val in _raw_metrics.items():
        vr = validate_metric(name, val, is_bank)
        validated[name] = vr["value"]
        if vr["status"] == "implausible":
            excluded.append(name)

    # Add raw float versions for colour-coding in templates (validated: implausible → None)
    result["_raw"] = {
        "gross_margin":       validated["gross_margin"],
        "ebitda_margin":      _raw_metrics["ebitda_margin"],
        "net_margin":         validated["net_margin"],
        "roe":                validated["roe"],
        "roa":                validated["roa"],
        "current_ratio":      validated["current_ratio"],
        "quick_ratio":        validated["quick_ratio"],
        "debt_to_equity":     validated["debt_to_equity"],
        "debt_to_ebitda":     analysis.leverage.debt_to_ebitda,
        "interest_coverage":  validated["interest_coverage"],
        "revenue_growth_yoy": validated["revenue_growth"],
        "altman_z":           validated["altman_z"],
        "altman_components":  analysis.altman_z.components or {},
        "health_score":       analysis.financial_health_score,
        "dcf_base":           analysis.dcf.base_value,
        "dcf_bear":           analysis.dcf.bear_value,
        "dcf_bull":           analysis.dcf.bull_value,
        "wacc":               validated["wacc"],
        "asset_turnover":     validated["asset_turnover"],
        "ccc_days":           analysis.efficiency.cash_conversion_cycle,
        "receivables_days":   analysis.efficiency.receivables_days,
        "payables_days":      analysis.efficiency.payables_days,
    }
    result["_excluded"] = excluded
    # Also overwrite the human-readable display fields for excluded metrics so an
    # implausible number never prints (verification: HBL shows no implausible ratio).
    _EXCLUDE_MARK = "data error suspected - excluded"
    for name in excluded:
        if name in result:                       # gross_margin, net_margin, roe, ...
            result[name] = _EXCLUDE_MARK
    # Health confidence (M6) — surfaced so displays can qualify the score.
    result["health_confidence"] = analysis.health_confidence
    result["health_basis"]      = analysis.health_basis
    result["health_missing"]    = analysis.health_missing
    result["is_bank"]           = is_bank
    result["region_id"]   = region_id
    result["altman_zone"]  = analysis.altman_z.zone
    result["altman_model"] = analysis.altman_z.model
    result["dupont"] = {
        "net_margin":      analysis.dupont.net_margin,
        "asset_turnover":  analysis.dupont.asset_turnover,
        "equity_mult":     analysis.dupont.equity_multiplier,
        "roe_computed":    analysis.dupont.roe_computed,
    }
    result["dcf_sensitivity"] = analysis.dcf.sensitivity_table
    result["fcf_projections"] = analysis.dcf.fcf_projections
    result["comps"] = [
        {
            "name":       c.name if hasattr(c, "name") else str(c),
            "ticker":     getattr(c, "ticker", ""),
            "ev_ebitda":  getattr(c, "ev_ebitda", None),
            "pe_ratio":   getattr(c, "pe_ratio", None),
        }
        for c in analysis.comps.companies
    ] if analysis.comps.companies else []

    return result


def format_ratios_for_prompt(analysis_dict: dict) -> str:
    """
    Compact text representation of computed ratios for injection into AI prompts.
    The AI reads numbers directly rather than re-deriving them — reduces token waste.
    """
    raw = analysis_dict.get("_raw", {})

    def pct(v):
        if v is None: return "N/A"
        return f"{v:.1%}"

    def x(v):
        if v is None: return "N/A"
        return f"{v:.2f}x"

    def n(v):
        if v is None: return "N/A"
        return f"{v:.1f}"

    lines = [
        f"PRE-COMPUTED FINANCIAL RATIOS - {analysis_dict.get('company', '')} "
        f"({analysis_dict.get('ticker', '')}, {analysis_dict.get('fiscal_year', 'latest')})",
        "",
        f"Revenue: {analysis_dict.get('revenue', 'N/A')}  EBITDA: {analysis_dict.get('ebitda', 'N/A')}  "
        f"Net Income: {analysis_dict.get('net_income', 'N/A')}",
        "",
        "PROFITABILITY",
        f"  Gross Margin: {pct(raw.get('gross_margin'))}  EBITDA Margin: {pct(raw.get('ebitda_margin'))}  "
        f"Net Margin: {pct(raw.get('net_margin'))}  ROE: {pct(raw.get('roe'))}  ROA: {pct(raw.get('roa'))}",
        "",
        "LIQUIDITY",
        f"  Current: {x(raw.get('current_ratio'))}  Quick: {x(raw.get('quick_ratio'))}",
        "",
        "LEVERAGE",
        f"  D/E: {x(raw.get('debt_to_equity'))}  Net Debt/EBITDA: {x(raw.get('debt_to_ebitda'))}  "
        f"Interest Coverage: {x(raw.get('interest_coverage'))}",
        "",
        "GROWTH",
        f"  Revenue YoY: {pct(raw.get('revenue_growth_yoy'))}  "
        f"3Y CAGR: {analysis_dict.get('revenue_cagr_3y', 'N/A')}",
        "",
        "RISK",
        f"  Altman Z-Score: {n(raw.get('altman_z'))} ({analysis_dict.get('altman_zone', 'N/A')})",
        f"  Financial Health: {n(raw.get('health_score'))}/10",
        "",
        "DCF VALUATION",
        f"  WACC: {pct(raw.get('wacc'))}  Bear: {analysis_dict.get('dcf_bear', 'N/A')}  "
        f"Base: {analysis_dict.get('dcf_base', 'N/A')}  Bull: {analysis_dict.get('dcf_bull', 'N/A')}",
        "",
        "KEY RISKS: " + "; ".join(analysis_dict.get("key_risks", [])),
        "KEY STRENGTHS: " + "; ".join(analysis_dict.get("key_strengths", [])),
        "",
        "Use these pre-computed numbers directly. Do not re-derive ratios. Write qualitative commentary only.",
    ]
    return "\n".join(lines)


# ── Local report generator (no AI call) ──────────────────────────────────────

def score_announcements_sentiment(ticker: str, n: int = 10) -> Optional[dict]:
    """
    Score the most recent PSX announcements for a ticker via FinBERT.
    Returns a dict with positive/neutral/negative fractions and top headlines,
    or None if ML is disabled, the model is unavailable, or no data exists.
    """
    if not ticker:
        return None
    try:
        from config.settings import settings as _s
        if not getattr(_s, "LOCAL_ML", True):
            return None
    except Exception:
        pass
    try:
        from database.connection import get_db
        db   = get_db()
        rows = db.execute(
            """SELECT title FROM psx_announcements
               WHERE symbol = ? AND title IS NOT NULL
               ORDER BY ann_date DESC, id DESC LIMIT ?""",
            (ticker.upper(), n),
        ).fetchall()
        if not rows:
            return None
        headlines = [r["title"] for r in rows if r["title"]]
        if not headlines:
            return None
        # M10 — classify announcements by TYPE (keyword), not FinBERT. Procedural
        # filings dominate PSX announcements and FinBERT scores them a meaningless
        # 100% neutral; a type breakdown is far more informative.
        from intelligence.announcement_classifier import classify_announcements
        breakdown = classify_announcements(headlines)
        breakdown["top"] = headlines[:3]
        breakdown["kind"] = "announcement_types"
        return breakdown
    except Exception as _e:
        log.debug("score_announcements_sentiment failed", error=str(_e))
        return None


def format_health_score(analysis_dict: dict) -> str:
    """
    M6 display rule — never render a bare score. Qualify by confidence:
      >= 0.7 : "Health Score: X/10 (high confidence)"
      0.4-0.7: "Provisional Score: X/10 — N of M metrics; missing: [...]"
      < 0.4  : "Score withheld — insufficient data. Required: [...]"
    """
    raw   = analysis_dict.get("_raw", {})
    score = raw.get("health_score")
    conf  = analysis_dict.get("health_confidence", 0.0) or 0.0
    basis = analysis_dict.get("health_basis", []) or []
    missing = analysis_dict.get("health_missing", []) or []
    total = len(basis) + len(missing)
    if score is None or conf < 0.4:
        req = ", ".join(missing) or "balance sheet + income statement data"
        return f"**Score withheld - insufficient data.** _Required: {req}._"
    if conf >= 0.7:
        return f"**Health Score: {score:.1f}/10 (high confidence)**"
    miss = (", missing: " + ", ".join(missing)) if missing else ""
    return (f"**Provisional Score: {score:.1f}/10** - {len(basis)} of {total} metric "
            f"groups{miss}.")


def generate_local_financial_report(
    analysis_dict: dict,
    sentiment_scores: Optional[dict] = None,
    peers_text: Optional[str] = None,
) -> str:
    """
    Convert a fully-computed analysis dict into a report-quality markdown section.
    No AI call — pure computation. Covers Altman Z breakdown, DuPont, DCF,
    working capital, FinBERT sentiment, and PSX sector peers.
    """
    company  = analysis_dict.get("company", "")
    ticker   = analysis_dict.get("ticker", "")
    fy       = analysis_dict.get("fiscal_year", "Latest")
    raw      = analysis_dict.get("_raw", {})
    currency = analysis_dict.get("currency", "PKR")

    health = raw.get("health_score")
    health_label = (
        "Strong"   if (health or 0) >= 7 else
        "Adequate" if (health or 0) >= 5 else
        "Weak"
    )
    altman_z    = raw.get("altman_z")
    altman_zone = analysis_dict.get("altman_zone", "")

    def _pct(v):
        return f"{v:.1%}" if v is not None else "N/A"

    def _x(v):
        return f"{v:.2f}x" if v is not None else "N/A"

    def _n(v, dec: int = 3):
        return f"{v:.{dec}f}" if v is not None else "N/A"

    def _sig(v, good_above=None, warn_below=None):
        if v is None:                               return "-"
        if good_above is not None and v >= good_above: return "OK"
        if warn_below is not None and v < warn_below:  return "WATCH"
        return "-"

    lines = [
        f"## Financial Health Analysis - {company} ({ticker}, FY {fy})",
        f"_Source: PSX financial statements · Computed locally · Currency: {currency}_",
        "",
        f"{format_health_score(analysis_dict)}"
        f"  ·  Altman Z-Score: {_n(altman_z, 2)} ({altman_zone})",
        "",
    ]

    # ── Profitability ─────────────────────────────────────────────────────────
    lines += [
        "### Profitability",
        "| Metric | Value | Signal |",
        "|--------|-------|--------|",
        f"| Gross Margin | {_pct(raw.get('gross_margin'))} | {_sig(raw.get('gross_margin'), good_above=0.35, warn_below=0.15)} |",
        f"| EBITDA Margin | {_pct(raw.get('ebitda_margin'))} | {_sig(raw.get('ebitda_margin'), good_above=0.25, warn_below=0.10)} |",
        f"| Net Margin | {_pct(raw.get('net_margin'))} | {_sig(raw.get('net_margin'), good_above=0.15, warn_below=0.05)} |",
        f"| ROE | {_pct(raw.get('roe'))} | {_sig(raw.get('roe'), good_above=0.20, warn_below=0.08)} |",
        f"| ROA | {_pct(raw.get('roa'))} | {_sig(raw.get('roa'), good_above=0.12, warn_below=0.04)} |",
        "",
    ]

    # ── Liquidity ─────────────────────────────────────────────────────────────
    lines += [
        "### Liquidity",
        "| Metric | Value | Signal |",
        "|--------|-------|--------|",
        f"| Current Ratio | {_x(raw.get('current_ratio'))} | {_sig(raw.get('current_ratio'), good_above=2.0, warn_below=1.0)} |",
        f"| Quick Ratio | {_x(raw.get('quick_ratio'))} | {_sig(raw.get('quick_ratio'), good_above=1.5, warn_below=0.8)} |",
        "",
    ]

    # ── Leverage ──────────────────────────────────────────────────────────────
    lines += [
        "### Leverage",
        "| Metric | Value | Signal |",
        "|--------|-------|--------|",
        f"| Debt / Equity | {_x(raw.get('debt_to_equity'))} | · |",
        f"| Net Debt / EBITDA | {_x(raw.get('debt_to_ebitda'))} | "
        f"{_sig(raw.get('debt_to_ebitda'), warn_below=None)} |",
        f"| Interest Coverage | {_x(raw.get('interest_coverage'))} | "
        f"{_sig(raw.get('interest_coverage'), good_above=5.0, warn_below=2.0)} |",
        f"| Net Debt | {analysis_dict.get('net_debt', 'N/A')} {currency} | · |",
        "",
    ]

    # ── Growth & Efficiency ───────────────────────────────────────────────────
    rec_days = raw.get("receivables_days")
    lines += [
        "### Growth & Efficiency",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Revenue YoY | {_pct(raw.get('revenue_growth_yoy'))} |",
        f"| 3Y Revenue CAGR | {analysis_dict.get('revenue_cagr_3y', 'N/A')} |",
        f"| Asset Turnover | {_x(raw.get('asset_turnover'))} |",
        f"| Cash Conversion Cycle | {_n(raw.get('ccc_days'), 0)} days |",
        f"| Receivables Days | {f'{rec_days:.0f}d' if rec_days is not None else 'N/A'} |",
        "",
    ]

    # ── Altman Z breakdown ────────────────────────────────────────────────────
    components = raw.get("altman_components") or {}
    zone_explain = {
        "SAFE":     "Low bankruptcy risk.",
        "GREY":     "Some financial stress - monitor closely.",
        "DISTRESS": "Elevated bankruptcy risk - investigate immediately.",
    }.get(altman_zone, "")

    if components and altman_z is not None:
        x1, x2, x3, x4, x5 = (
            components.get("X1"), components.get("X2"), components.get("X3"),
            components.get("X4"), components.get("X5"),
        )
        lines += [
            "### Altman Z-Score Breakdown",
            f"**Z = 1.2×X1 + 1.4×X2 + 3.3×X3 + 0.6×X4 + 1.0×X5 = {_n(altman_z, 2)} → {altman_zone}**",
            f"_{zone_explain}_",
            "",
            "| Component | Formula | Raw Value | Weight | Contribution |",
            "|-----------|---------|-----------|--------|-------------|",
            f"| X1 | Working Capital / Total Assets | {_n(x1)} | 1.2× | {_n((x1 or 0)*1.2)} |",
            f"| X2 | Retained Earnings / Total Assets | {_n(x2)} | 1.4× | {_n((x2 or 0)*1.4)} |",
            f"| X3 | EBIT / Total Assets | {_n(x3)} | 3.3× | {_n((x3 or 0)*3.3)} |",
            f"| X4 | Equity / Debt | {_n(x4)} | 0.6× | {_n((x4 or 0)*0.6)} |",
            f"| X5 | Revenue / Total Assets | {_n(x5)} | 1.0× | {_n((x5 or 0)*1.0)} |",
            "",
        ]
    else:
        lines += [
            "### Altman Z-Score",
            f"**Z = {_n(altman_z, 2)} ({altman_zone})** - {zone_explain}",
            "_Component breakdown unavailable (insufficient balance-sheet data)._",
            "",
        ]

    # ── DuPont decomposition ──────────────────────────────────────────────────
    dupont = analysis_dict.get("dupont", {})
    if dupont and dupont.get("roe_computed") is not None:
        nm  = dupont.get("net_margin")
        at  = dupont.get("asset_turnover")
        em  = dupont.get("equity_mult")
        roe = dupont.get("roe_computed")
        dominant = (
            "leverage (high equity multiplier)" if (em or 0) > 3 else
            "profitability (high net margin)"    if (nm or 0) > 0.15 else
            "balanced across margin, efficiency, and leverage"
        )
        lines += [
            "### DuPont Decomposition",
            f"**ROE {_pct(roe)} = Net Margin {_pct(nm)} × Asset Turnover {_x(at)} × Equity Multiplier {_x(em)}**",
            f"_Primary ROE driver: {dominant}_",
            "",
        ]

    # ── Valuation ─────────────────────────────────────────────────────────────
    if analysis_dict.get("is_bank"):
        # M8 — banks: multiples, never FCFF DCF / EV-Revenue.
        bm = analysis_dict.get("bank_multiples", {}) or {}
        pb = bm.get("pb"); pe = bm.get("pe")
        pe_med = None
        try:
            from intelligence.sector_medians import get_sector_median
            m = get_sector_median("banking", "pe")
            pe_med = m["median"] if m else None
        except Exception:
            pe_med = None
        lines += [
            "### Bank Valuation (multiples)",
            "_Enterprise DCF is not applied to financial institutions; banks are valued on "
            "book-value and earnings multiples._",
            "",
            "| Multiple | Value | Banking-sector median |",
            "|----------|-------|-----------------------|",
            f"| P/B | {_x(pb) if pb else 'N/A'} | - |",
            f"| P/E | {_x(pe) if pe else 'N/A'} | {_x(pe_med) if pe_med else 'insufficient peers'} |",
            "",
        ]
    else:
        dcf_note = analysis_dict.get("dcf_note", "")
        if any(analysis_dict.get(k) not in (None, "N/A") for k in ("dcf_base", "dcf_bear", "dcf_bull")):
            lines += [
                "### DCF Valuation Scenarios",
                f"**WACC: {_pct(raw.get('wacc'))}**",
                "",
                "| Scenario | Equity Value |",
                "|----------|-------------|",
                f"| Bear | {analysis_dict.get('dcf_bear', 'N/A')} {currency} |",
                f"| Base | {analysis_dict.get('dcf_base', 'N/A')} {currency} |",
                f"| Bull | {analysis_dict.get('dcf_bull', 'N/A')} {currency} |",
            ]
            if dcf_note:
                lines.append(f"_{dcf_note[:220]}_")
            lines.append("")

    # ── 3-year revenue projections ────────────────────────────────────────────
    rev_y1, rev_y2, rev_y3 = (
        analysis_dict.get("revenue_y1"),
        analysis_dict.get("revenue_y2"),
        analysis_dict.get("revenue_y3"),
    )
    if any(v and v != "N/A" for v in (rev_y1, rev_y2, rev_y3)):
        lines += [
            "### 3-Year Revenue Projections",
            f"Y+1: **{rev_y1}** · Y+2: **{rev_y2}** · Y+3: **{rev_y3}** ({currency})",
            "",
        ]

    # ── Key Risks & Strengths ─────────────────────────────────────────────────
    risks     = analysis_dict.get("key_risks", [])
    strengths = analysis_dict.get("key_strengths", [])
    if risks:
        lines += ["### Key Risks"] + [f"- {r}" for r in risks] + [""]
    if strengths:
        lines += ["### Key Strengths"] + [f"- {s}" for s in strengths] + [""]

    # ── FinBERT Sentiment ─────────────────────────────────────────────────────
    if isinstance(sentiment_scores, dict):
        pos = sentiment_scores.get("positive", 0)
        neu = sentiment_scores.get("neutral",  0)
        neg = sentiment_scores.get("negative", 0)
        n   = sentiment_scores.get("n", 0)
        top = sentiment_scores.get("top", [])
        lines += [
            "### Market Sentiment (PSX Announcements - FinBERT)",
            f"_Based on {n} recent announcements_",
            "",
            f"**Positive: {pos:.0%}  ·  Neutral: {neu:.0%}  ·  Negative: {neg:.0%}**",
        ]
        if top:
            lines.append(f"_Recent: {top[0][:120]}_")
        lines.append("")

    # ── PSX Sector Peers ──────────────────────────────────────────────────────
    if peers_text and peers_text.strip():
        lines += [
            "### PSX Sector Peers",
            "```",
            peers_text.strip(),
            "```",
            "",
        ]

    return "\n".join(lines)
