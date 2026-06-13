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
- Listed companies: yfinance (real filings from exchanges)
- Private companies: structured data extracted from uploaded documents

Output: A structured FinancialAnalysis object that feeds directly
into the Financials and Valuation report sections.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import Optional
import structlog

log = structlog.get_logger()

# ── Try importing financial libraries ─────────────────────────────────────────
try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    log.warning("yfinance/pandas not available — install for live financial data")


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

def analyse_listed_company(
    ticker: str,
    region_id: str = "pakistan",
    peer_tickers: Optional[list] = None,
) -> FinancialAnalysis:
    """
    Full financial analysis of a publicly listed company using yfinance.

    Args:
        ticker: Stock ticker symbol (e.g. ENGRO, LUCK, HBL — PSX symbols)
        region_id: Region for WACC parameters and currency context
        peer_tickers: Optional list of peer tickers for comparable analysis

    Returns:
        Complete FinancialAnalysis object
    """
    result = FinancialAnalysis(ticker=ticker, data_source="yfinance")

    if not YFINANCE_AVAILABLE:
        result.errors.append("yfinance not installed. Run: pip install yfinance pandas numpy")
        return result

    try:
        stock = yf.Ticker(ticker)
        info  = stock.info or {}

        result.company_name = info.get("longName", ticker)
        result.currency     = info.get("financialCurrency", "USD")

        # ── Pull financial statements ─────────────────────────────────────────
        income_stmt   = stock.income_stmt
        balance_sheet = stock.balance_sheet
        cash_flow     = stock.cashflow

        if income_stmt is None or income_stmt.empty:
            result.errors.append(f"No financial data available for {ticker} on yfinance")
            return result

        # Extract latest year figures (first column)
        def _get(df, *keys):
            """Try multiple key names — yfinance uses different names across regions."""
            if df is None or df.empty:
                return None
            for key in keys:
                if key in df.index:
                    val = df[df.columns[0]].get(key)
                    if val is not None and not (isinstance(val, float) and pd.isna(val)):
                        return float(val)
            return None

        # ── Income statement ──────────────────────────────────────────────────
        result.revenue      = _get(income_stmt, "Total Revenue", "Revenue")
        result.gross_profit = _get(income_stmt, "Gross Profit")
        result.ebit         = _get(income_stmt, "EBIT", "Operating Income")
        result.net_income   = _get(income_stmt, "Net Income", "Net Income Common Stockholders")

        # EBITDA = EBIT + D&A
        da = _get(cash_flow, "Depreciation And Amortization", "Depreciation & Amortization")
        if result.ebit is not None and da is not None:
            result.ebitda = result.ebit + da
        else:
            result.ebitda = _get(income_stmt, "EBITDA", "Normalized EBITDA")

        # ── Balance sheet ─────────────────────────────────────────────────────
        result.total_assets        = _get(balance_sheet, "Total Assets")
        result.total_debt          = _get(balance_sheet, "Total Debt", "Long Term Debt")
        result.cash                = _get(balance_sheet, "Cash And Cash Equivalents", "Cash")
        result.shareholders_equity = _get(balance_sheet, "Stockholders Equity", "Common Stock Equity")

        current_assets      = _get(balance_sheet, "Current Assets")
        current_liabilities = _get(balance_sheet, "Current Liabilities")
        inventory           = _get(balance_sheet, "Inventory")
        receivables         = _get(balance_sheet, "Accounts Receivable", "Net Receivables")
        payables            = _get(balance_sheet, "Accounts Payable")

        # ── Cash flow ─────────────────────────────────────────────────────────
        ocf         = _get(cash_flow, "Operating Cash Flow", "Cash From Operating Activities")
        capex       = _get(cash_flow, "Capital Expenditure", "Purchases Of Property Plant And Equipment")
        fcf_actual  = _get(cash_flow, "Free Cash Flow")
        if fcf_actual is None and ocf is not None and capex is not None:
            fcf_actual = ocf + capex  # capex is typically negative in yfinance

        result.fiscal_year = str(income_stmt.columns[0].year) if hasattr(income_stmt.columns[0], 'year') else "Latest"

        # ── Historical data for growth ────────────────────────────────────────
        revenue_history = []
        if len(income_stmt.columns) >= 2:
            for col in income_stmt.columns[:4]:
                rev = income_stmt[col].get("Total Revenue") or income_stmt[col].get("Revenue")
                if rev is not None and not pd.isna(rev):
                    revenue_history.append(float(rev))

        # ── Compute all ratios ────────────────────────────────────────────────
        result.liquidity    = _compute_liquidity(current_assets, current_liabilities, inventory, ocf, result.cash)
        result.profitability = _compute_profitability(result.revenue, result.gross_profit, result.ebitda, result.ebit, result.net_income, result.total_assets, result.shareholders_equity)
        result.leverage     = _compute_leverage(result.total_debt, result.shareholders_equity, result.ebitda, result.ebit, result.cash, income_stmt)
        result.efficiency   = _compute_efficiency(result.revenue, result.total_assets, receivables, payables, inventory, result.gross_profit)
        result.growth       = _compute_growth(revenue_history, result.gross_profit, result.ebitda, income_stmt)
        result.altman_z     = _compute_altman_z(current_assets, current_liabilities, result.total_assets, result.total_debt, result.shareholders_equity, result.revenue, result.ebit, result.cash)
        result.dupont       = _compute_dupont(result.net_income, result.revenue, result.total_assets, result.shareholders_equity)
        result.working_capital = _compute_working_capital(current_assets, current_liabilities, result.revenue, revenue_history)

        # ── Comparable analysis ───────────────────────────────────────────────
        if peer_tickers:
            result.comps = _compute_comparables(peer_tickers, result.revenue, result.ebitda)
        else:
            # Use yfinance's built-in peer data if available
            auto_peers = _find_peers_from_info(info, ticker)
            if auto_peers:
                result.comps = _compute_comparables(auto_peers, result.revenue, result.ebitda)

        # ── DCF Valuation ─────────────────────────────────────────────────────
        result.dcf = _compute_dcf(
            revenue=result.revenue,
            ebitda=result.ebitda,
            fcf=fcf_actual,
            revenue_growth=result.growth.revenue_growth_yoy,
            total_debt=result.total_debt,
            cash=result.cash,
            region_id=region_id,
        )

        # ── Three-statement projection ────────────────────────────────────────
        result.three_statement = _build_three_statement_model(
            revenue=result.revenue,
            gross_margin=result.profitability.gross_margin,
            ebitda_margin=result.profitability.ebitda_margin,
            net_margin=result.profitability.net_margin,
            growth_rate=result.growth.revenue_growth_yoy,
            capex=capex,
        )

        # ── Overall health score ──────────────────────────────────────────────
        result.financial_health_score = _compute_health_score(result)
        result.key_risks, result.key_strengths = _identify_risks_strengths(result)

        log.info("Financial analysis complete", ticker=ticker, health_score=result.financial_health_score)

    except Exception as e:
        result.errors.append(f"Analysis error: {str(e)}")
        log.error("Financial analysis failed", ticker=ticker, error=str(e))

    return result


def analyse_from_document(
    financials: dict,
    company_name: str,
    region_id: str = "pakistan",
    peer_tickers: Optional[list] = None,
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
        result.leverage      = _compute_leverage(result.total_debt, result.shareholders_equity, result.ebitda, result.ebit, result.cash, None)
        result.efficiency    = _compute_efficiency(result.revenue, result.total_assets, receivables, payables, inventory, result.gross_profit)
        result.growth        = _compute_growth(revenue_history, result.gross_profit, result.ebitda, None)
        result.altman_z      = _compute_altman_z(current_assets, current_liabilities, result.total_assets, result.total_debt, result.shareholders_equity, result.revenue, result.ebit, result.cash)
        result.dupont        = _compute_dupont(result.net_income, result.revenue, result.total_assets, result.shareholders_equity)
        result.working_capital = _compute_working_capital(current_assets, current_liabilities, result.revenue, revenue_history)
        result.dcf           = _compute_dcf(result.revenue, result.ebitda, None, result.growth.revenue_growth_yoy, result.total_debt, result.cash, region_id)
        result.three_statement = _build_three_statement_model(result.revenue, result.profitability.gross_margin, result.profitability.ebitda_margin, result.profitability.net_margin, result.growth.revenue_growth_yoy, capex)

        if peer_tickers and YFINANCE_AVAILABLE:
            result.comps = _compute_comparables(peer_tickers, result.revenue, result.ebitda)

        result.financial_health_score = _compute_health_score(result)
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
            r.interpretation = "Tight liquidity. Monitor cash closely — limited room for unexpected expenses."
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
            flags.append("Very thin net margins — vulnerable to cost increases")
    if r.ebitda_margin is not None and r.ebitda_margin < 0.10:
        flags.append("EBITDA margin below 10% — limited operating leverage")
    if r.roe is not None and r.roe > 0.20:
        flags.append("Strong ROE above 20% — efficient use of shareholder capital")

    r.interpretation = " | ".join(flags) if flags else "Profitability within normal range"
    return r


def _compute_leverage(total_debt, equity, ebitda, ebit, cash, income_stmt) -> LeverageRatios:
    r = LeverageRatios()
    r.debt_to_equity = _safe_div(total_debt, equity)

    if total_debt is not None and cash is not None:
        r.net_debt = total_debt - cash
        r.net_debt_to_equity = _safe_div(r.net_debt, equity)
        r.debt_to_ebitda     = _safe_div(r.net_debt, ebitda)

    # Interest coverage — try to get interest expense from income statement
    interest_expense = None
    if income_stmt is not None:
        try:
            interest_expense = income_stmt[income_stmt.columns[0]].get("Interest Expense")
            if interest_expense is not None:
                interest_expense = abs(float(interest_expense))
        except Exception:
            pass

    if interest_expense and interest_expense > 0:
        r.interest_coverage = _safe_div(ebit, interest_expense)

    # Interpretation
    flags = []
    if r.debt_to_ebitda is not None:
        if r.debt_to_ebitda > 5:
            flags.append("HIGH LEVERAGE: Net debt/EBITDA above 5x — significant refinancing risk")
        elif r.debt_to_ebitda > 3:
            flags.append("Elevated leverage: Net debt/EBITDA 3-5x — manageable but monitor")
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
            flags.append(f"Long cash conversion cycle ({r.cash_conversion_cycle:.0f} days) — capital tied up in operations")
        elif r.cash_conversion_cycle < 0:
            flags.append(f"Negative CCC ({r.cash_conversion_cycle:.0f} days) — company funded by suppliers (e.g. retail model)")

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
            r.interpretation = f"Slow growth: {r.revenue_growth_yoy:.1%} YoY — below inflation in most markets"
        else:
            r.interpretation = f"Revenue declining: {r.revenue_growth_yoy:.1%} YoY — investigate cause"

    return r


def _compute_altman_z(current_assets, current_liabilities, total_assets, total_debt, equity, revenue, ebit, cash) -> AltmanZScore:
    r = AltmanZScore()

    try:
        if not all(v is not None and total_assets and total_assets != 0 for v in
                   [current_assets, current_liabilities, total_assets, revenue, ebit]):
            r.interpretation = "Insufficient data for Altman Z-Score"
            return r

        working_capital     = current_assets - current_liabilities
        retained_earnings   = equity * 0.4 if equity else 0    # Approximation
        market_cap_proxy    = equity if equity and equity > 0 else 1

        x1 = working_capital / total_assets
        x2 = retained_earnings / total_assets
        x3 = ebit / total_assets
        x4 = market_cap_proxy / (total_debt if total_debt and total_debt > 0 else 1)
        x5 = revenue / total_assets

        r.z_score = round(1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5, 3)
        r.components = {"X1": round(x1,3), "X2": round(x2,3), "X3": round(x3,3), "X4": round(x4,3), "X5": round(x5,3)}

        if r.z_score > 2.99:
            r.zone = "SAFE"
            r.interpretation = f"Z-Score {r.z_score:.2f} — Safe zone. Low bankruptcy risk."
        elif r.z_score > 1.81:
            r.zone = "GREY"
            r.interpretation = f"Z-Score {r.z_score:.2f} — Grey zone. Some financial stress. Monitor closely."
        else:
            r.zone = "DISTRESS"
            r.interpretation = f"Z-Score {r.z_score:.2f} — Distress zone. Elevated bankruptcy risk. Investigate immediately."

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
                r.interpretation = f"ROE of {r.roe_computed:.1%} is primarily driven by financial leverage — strip out debt and underlying business returns are lower"
            elif r.net_margin and r.net_margin > 0.15:
                r.interpretation = f"ROE of {r.roe_computed:.1%} is driven by strong profitability — high-quality earnings"
            else:
                r.interpretation = f"ROE of {r.roe_computed:.1%} — balanced contribution from margin, efficiency, and leverage"
        else:
            r.interpretation = "Negative or zero ROE — equity is being eroded"

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
    erp           = params["equity_risk_premium"]
    ke            = rfr + beta * erp      # Cost of equity (CAPM)
    r.wacc        = round(ke, 4)          # Simplified — use ke as WACC for private companies

    r.wacc_components = {
        "risk_free_rate":     f"{rfr:.1%}",
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

    terminal_growth = min(params["risk_free_rate"] * 0.5, 0.04)  # Conservative terminal growth
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
        r.interpretation = "DCF computation incomplete — insufficient financial data"

    return r


def _compute_comparables(peer_tickers: list, target_revenue, target_ebitda) -> ComparableAnalysis:
    r = ComparableAnalysis()

    if not YFINANCE_AVAILABLE or not peer_tickers:
        return r

    companies = []
    for ticker in peer_tickers[:8]:   # Cap at 8 peers
        try:
            stock = yf.Ticker(ticker)
            info  = stock.info or {}

            market_cap = info.get("marketCap")
            total_debt = info.get("totalDebt", 0) or 0
            cash       = info.get("totalCash", 0) or 0
            revenue    = info.get("totalRevenue")
            ebitda     = info.get("ebitda")
            ev         = (market_cap + total_debt - cash) if market_cap else None

            comp = ComparableCompany(
                name       = info.get("longName", ticker),
                ticker     = ticker,
                market_cap = market_cap,
                ev         = ev,
                revenue    = revenue,
                ebitda     = ebitda,
                ev_revenue = _safe_div(ev, revenue),
                ev_ebitda  = _safe_div(ev, ebitda),
                pe_ratio   = info.get("trailingPE"),
                gross_margin = info.get("grossMargins"),
            )
            companies.append(comp)
        except Exception:
            continue

    r.companies = companies

    if companies:
        ev_revs   = [c.ev_revenue for c in companies if c.ev_revenue and c.ev_revenue > 0]
        ev_ebs    = [c.ev_ebitda  for c in companies if c.ev_ebitda  and c.ev_ebitda  > 0 and c.ev_ebitda < 100]
        pes       = [c.pe_ratio   for c in companies if c.pe_ratio   and c.pe_ratio   > 0 and c.pe_ratio  < 100]

        if YFINANCE_AVAILABLE:
            import numpy as np
            r.median_ev_revenue = round(float(np.median(ev_revs)), 2) if ev_revs else None
            r.median_ev_ebitda  = round(float(np.median(ev_ebs)), 2)  if ev_ebs  else None
            r.median_pe         = round(float(np.median(pes)), 2)     if pes     else None

        r.implied_value_ev_revenue = round(r.median_ev_revenue * target_revenue, 0) if (r.median_ev_revenue and target_revenue) else None
        r.implied_value_ev_ebitda  = round(r.median_ev_ebitda  * target_ebitda,  0) if (r.median_ev_ebitda  and target_ebitda)  else None

        r.interpretation = (
            f"Peer group of {len(companies)} companies | "
            f"Median EV/Revenue: {r.median_ev_revenue}x | "
            f"Median EV/EBITDA: {r.median_ev_ebitda}x | "
            f"Implied equity value range: {_fmt(r.implied_value_ev_revenue)} — {_fmt(r.implied_value_ev_ebitda)}"
        )

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
    r.interpretation = (
        f"3-year projection at {g:.1%} CAGR | "
        f"Y3 Revenue: {_fmt(r.revenue_projections[-1])} | "
        f"Y3 EBITDA: {_fmt(r.ebitda_projections[-1])} | "
        f"Y3 FCF: {_fmt(r.fcf_projections[-1])}"
    )
    return r


def _compute_health_score(analysis: FinancialAnalysis) -> float:
    """Compute an overall financial health score 1-10."""
    score = 5.0  # Start neutral

    # Liquidity (+/- up to 1.5)
    cr = analysis.liquidity.current_ratio
    if cr:
        if cr >= 2.0:   score += 1.5
        elif cr >= 1.5: score += 0.75
        elif cr >= 1.0: score += 0.0
        elif cr < 1.0:  score -= 1.5

    # Profitability (+/- up to 2.0)
    nm = analysis.profitability.net_margin
    if nm:
        if nm >= 0.15:  score += 2.0
        elif nm >= 0.08: score += 1.0
        elif nm >= 0.03: score += 0.0
        elif nm < 0:    score -= 2.0

    # Leverage (+/- up to 1.5)
    de = analysis.leverage.debt_to_ebitda
    if de:
        if de <= 1.0:   score += 1.5
        elif de <= 3.0: score += 0.5
        elif de <= 5.0: score -= 0.5
        elif de > 5.0:  score -= 1.5

    # Growth (+/- up to 1.0)
    g = analysis.growth.revenue_growth_yoy
    if g:
        if g >= 0.20:   score += 1.0
        elif g >= 0.05: score += 0.5
        elif g < 0:     score -= 1.0

    # Altman Z (+/- up to 1.0)
    if analysis.altman_z.zone == "SAFE":      score += 1.0
    elif analysis.altman_z.zone == "GREY":    score += 0.0
    elif analysis.altman_z.zone == "DISTRESS": score -= 1.0

    return round(max(1.0, min(10.0, score)), 1)


def _identify_risks_strengths(analysis: FinancialAnalysis) -> tuple[list, list]:
    risks, strengths = [], []

    if analysis.liquidity.current_ratio:
        if analysis.liquidity.current_ratio < 1.0:
            risks.append(f"Current ratio {analysis.liquidity.current_ratio:.2f} — short-term liquidity risk")
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


def _find_peers_from_info(info: dict, ticker: str) -> list:
    """Extract peer tickers from yfinance company info where available."""
    # yfinance does not reliably provide peer lists — return empty
    # In production, integrate with a financial data API for proper peer sets
    return []


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

def run_local_analysis(ticker: str, region_id: str = "pakistan") -> dict:
    """
    Fetch yfinance data and run the full FinancialAnalyzer locally.
    Returns a flat dict safe for JSON serialisation and HTML rendering.
    No AI call — pure math.

    Raises ValueError if ticker not found or has no financial data.
    """
    analysis = analyse_listed_company(ticker, region_id=region_id)

    if analysis.errors and not analysis.revenue:
        raise ValueError(analysis.errors[0] if analysis.errors else f"No data for {ticker}")

    return _assemble_report_dict(analysis, region_id)


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

    analysis = analyse_from_document(doc, company_name, region_id="pakistan")
    analysis.ticker      = ticker.strip().upper()
    analysis.currency    = "PKR"
    analysis.data_source = src
    if doc.get("_fiscal_year"):
        analysis.fiscal_year = doc["_fiscal_year"]

    if analysis.errors and not analysis.revenue:
        raise ValueError(analysis.errors[0])

    result = _assemble_report_dict(analysis, "pakistan")
    result["source"]      = src
    result["ocr_derived"] = ocr
    return result


def _assemble_report_dict(analysis: "FinancialAnalysis", region_id: str) -> dict:
    """Build the flat, template-/JSON-friendly dict from a FinancialAnalysis.
    Shared by both the yfinance and PSX analysis entry points."""
    result = to_report_dict(analysis)

    # Add raw float versions for colour-coding in templates
    result["_raw"] = {
        "gross_margin":       analysis.profitability.gross_margin,
        "ebitda_margin":      analysis.profitability.ebitda_margin,
        "net_margin":         analysis.profitability.net_margin,
        "roe":                analysis.profitability.roe,
        "roa":                analysis.profitability.roa,
        "current_ratio":      analysis.liquidity.current_ratio,
        "quick_ratio":        analysis.liquidity.quick_ratio,
        "debt_to_equity":     analysis.leverage.debt_to_equity,
        "debt_to_ebitda":     analysis.leverage.debt_to_ebitda,
        "interest_coverage":  analysis.leverage.interest_coverage,
        "revenue_growth_yoy": analysis.growth.revenue_growth_yoy,
        "altman_z":           analysis.altman_z.z_score,
        "altman_components":  analysis.altman_z.components or {},
        "health_score":       analysis.financial_health_score,
        "dcf_base":           analysis.dcf.base_value,
        "dcf_bear":           analysis.dcf.bear_value,
        "dcf_bull":           analysis.dcf.bull_value,
        "wacc":               analysis.dcf.wacc,
        "asset_turnover":     analysis.efficiency.asset_turnover,
        "ccc_days":           analysis.efficiency.cash_conversion_cycle,
        "receivables_days":   analysis.efficiency.receivables_days,
        "payables_days":      analysis.efficiency.payables_days,
    }
    result["region_id"]   = region_id
    result["altman_zone"] = analysis.altman_z.zone
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
        f"PRE-COMPUTED FINANCIAL RATIOS — {analysis_dict.get('company', '')} "
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
        from ml.sentiment import score as _finbert
        scores = _finbert(headlines)
        if not scores:
            return None
        counts: dict[str, int] = {"Positive": 0, "Neutral": 0, "Negative": 0}
        for s in scores:
            counts[s.label.capitalize()] = counts.get(s.label.capitalize(), 0) + 1
        total = len(scores)
        return {
            "positive": counts["Positive"] / total,
            "neutral":  counts["Neutral"]  / total,
            "negative": counts["Negative"] / total,
            "top":      headlines[:3],
            "n":        total,
        }
    except Exception as _e:
        log.debug("score_announcements_sentiment failed", error=str(_e))
        return None


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
        if v is None:                               return "·"
        if good_above is not None and v >= good_above: return "✓"
        if warn_below is not None and v < warn_below:  return "⚠"
        return "·"

    lines = [
        f"## Financial Health Analysis — {company} ({ticker}, FY {fy})",
        f"_Source: PSX financial statements · Computed locally · Currency: {currency}_",
        "",
        f"**Health Score: {_n(health, 1)}/10 ({health_label})"
        f"  ·  Altman Z-Score: {_n(altman_z, 2)} ({altman_zone})**",
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
        "GREY":     "Some financial stress — monitor closely.",
        "DISTRESS": "Elevated bankruptcy risk — investigate immediately.",
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
            f"**Z = {_n(altman_z, 2)} ({altman_zone})** — {zone_explain}",
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

    # ── DCF scenarios ─────────────────────────────────────────────────────────
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
        lines += ["### Key Risks"] + [f"- ⚠ {r}" for r in risks] + [""]
    if strengths:
        lines += ["### Key Strengths"] + [f"- ✓ {s}" for s in strengths] + [""]

    # ── FinBERT Sentiment ─────────────────────────────────────────────────────
    if isinstance(sentiment_scores, dict):
        pos = sentiment_scores.get("positive", 0)
        neu = sentiment_scores.get("neutral",  0)
        neg = sentiment_scores.get("negative", 0)
        n   = sentiment_scores.get("n", 0)
        top = sentiment_scores.get("top", [])
        lines += [
            "### Market Sentiment (PSX Announcements — FinBERT)",
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
