"""pipeline/contract.py — Single source of truth for all pipeline field definitions."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

BASES = ("consolidated", "unconsolidated")
STATEMENT_TYPES = ("PL", "BS", "CF")

@dataclass(frozen=True)
class Field:
    key: str
    statement: Literal["PL", "BS", "CF", "derived"]
    definition: str
    sign: Literal["pos", "neg"] = "pos"   # pos = stored as a positive number
    applies_to: Literal["non_bank", "bank", "both"] = "non_bank"
    is_sum: bool = False                   # True = defined sum, not a printed line

FIELDS: dict[str, Field] = {f.key: f for f in [
    # ── P&L ─────────────────────────────────────────────────────────────────────
    Field("revenue",                      "PL", "Revenue from contracts with customers, net of sales tax and discounts"),
    Field("cost_of_sales",                "PL", "Cost of sales",                                          sign="neg"),
    Field("gross_profit",                 "PL", "Printed gross profit (revenue − cost_of_sales)",          is_sum=True),
    Field("operating_profit",             "PL", "Printed operating profit / profit before finance cost"),
    Field("finance_cost",                 "PL", "Finance cost",                                           sign="neg"),
    Field("profit_before_tax",            "PL", "Printed profit before tax"),
    Field("taxation",                     "PL", "Printed total tax charge",                               sign="neg"),
    Field("profit_after_tax",             "PL", "Profit for the year",                                    is_sum=True),
    Field("eps_basic",                    "PL", "Basic EPS in rupees — not scaled"),
    # ── BS ──────────────────────────────────────────────────────────────────────
    Field("total_non_current_assets",     "BS", "Printed total non-current assets"),
    Field("total_current_assets",         "BS", "Printed total current assets"),
    Field("total_assets",                 "BS", "Printed total assets",                                    is_sum=True),
    Field("total_equity",                 "BS", "Printed total equity including NCI"),
    Field("retained_earnings",            "BS", "Unappropriated profit / accumulated profit"),
    Field("total_non_current_liabilities","BS", "Printed total non-current liabilities"),
    Field("total_current_liabilities",    "BS", "Printed total current liabilities"),
    Field("total_liabilities",            "BS", "Printed total liabilities",                               is_sum=True),
    Field("cash_and_equivalents",         "BS", "Cash and bank balances"),
    Field("inventory",                    "BS", "Stock-in-trade"),
    Field("trade_receivables",            "BS", "Trade debts"),
    Field("trade_payables",               "BS", "Trade and other payables",                               sign="neg"),
    Field("borrowings_long_term",         "BS", "Long-term borrowings"),
    Field("borrowings_current_portion",   "BS", "Current portion of long-term borrowings"),
    Field("borrowings_short_term",        "BS", "Short-term borrowings"),
    Field("lease_liabilities",            "BS", "Lease liabilities"),
    Field("total_debt",           "derived",    "Sum of all four borrowing line items",                    is_sum=True),
    # ── CF ──────────────────────────────────────────────────────────────────────
    Field("cfo",                          "CF", "Net cash from (used in) operating activities"),
    Field("capex",                        "CF", "Purchase of PPE plus additions to CWIP",                  sign="neg"),
    Field("depreciation",                 "CF", "Depreciation in operating cash flow adjustments",         sign="neg"),
    Field("cfi",                          "CF", "Net cash from (used in) investing activities"),
    Field("cff",                          "CF", "Net cash from (used in) financing activities"),
    Field("net_change_in_cash",           "CF", "Net change in cash and cash equivalents"),
    Field("cash_opening",                 "CF", "Cash at beginning of period"),
    Field("cash_closing",                 "CF", "Cash at end of period / reconciling total",               is_sum=True),
]}
