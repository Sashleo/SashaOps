"""
intelligence/psx_analyser.py - Phase 3 Task 3.2: the single analysis entry point.

Every one of the twelve PSX Analyser sections reads from the PSXAnalysis object
this module builds. No section template touches the database.

Contract (from the Phase 3 plan):
  - Reads ONLY the local database. Never scrapes on request; a stale ticker shows
    a staleness banner and a refresh button instead.
  - Applies PKR unit scaling ONCE, here. Downstream code never rescales.
  - Every metric goes through validate_metric() and is stored as a ValidatedMetric
    ({value, status, note}), never a bare float, so an implausible figure can
    never be rendered as a number.
  - Populates missing_requirements per section, so a template never has to work
    out what to say when data is absent - it prints "Required: X".
  - Returns in under 500ms for a fully populated ticker (index-served reads; no
    caching, which would reintroduce staleness bugs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import structlog

from intelligence.validation import validate_metric

log = structlog.get_logger()

# PSX publishes statement figures in PKR thousands. Scaling happens exactly once,
# here, so no downstream code has to know or care about the unit convention.
PKR_THOUSANDS = 1_000.0

_MONEY_FIELDS = (
    "revenue", "gross_profit", "ebitda", "ebit", "net_income", "total_assets",
    "total_equity", "total_debt", "cash", "current_assets", "current_liabilities",
    "retained_earnings", "inventory", "receivables", "payables",
    "interest_expense", "depreciation", "ocf", "capex",
)

# Minimum peers before a sector benchmark is treated as meaningful (plan: below 4
# show "insufficient peers" rather than a misleading percentile).
MIN_PEERS = 4


def _vm(name: str, value, is_bank: bool = False, raw: Optional[dict] = None) -> dict:
    """Shorthand: run a value through validation and return the ValidatedMetric."""
    return validate_metric(name, value, is_bank=is_bank, raw_inputs=raw)


def _div(a, b) -> Optional[float]:
    try:
        if a is None or b in (None, 0):
            return None
        return float(a) / float(b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


@dataclass
class PSXAnalysis:
    # identity
    ticker: str
    company_name: str = ""
    sector: str = ""
    sub_sector: Optional[str] = None
    is_bank: bool = False

    # market
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    shares_outstanding: Optional[float] = None
    price_change_pct: Optional[float] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None

    # periods available (newest first)
    annual_periods: list = field(default_factory=list)
    quarterly_periods: list = field(default_factory=list)

    # statements: period -> {field: value}, unit-scaled to actual PKR
    balance_sheet: dict = field(default_factory=dict)
    income_statement: dict = field(default_factory=dict)
    cash_flow: dict = field(default_factory=dict)

    # computed
    ratios: dict = field(default_factory=dict)          # period -> {name: ValidatedMetric}
    dupont: dict = field(default_factory=dict)
    altman: dict = field(default_factory=dict)
    valuation: dict = field(default_factory=dict)
    sector_medians: dict = field(default_factory=dict)
    sector_percentiles: dict = field(default_factory=dict)
    peers: list = field(default_factory=list)

    # context
    compliance: dict = field(default_factory=dict)
    announcements: list = field(default_factory=list)
    statement_quality: dict = field(default_factory=dict)

    # meta
    health_score: dict = field(default_factory=dict)
    data_sources: dict = field(default_factory=dict)
    missing_requirements: dict = field(default_factory=dict)
    staleness: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    build_ms: int = 0
    found: bool = False

    @property
    def latest_period(self) -> Optional[str]:
        return self.annual_periods[0] if self.annual_periods else None

    def require(self, section: str, what: str) -> None:
        """Record a specific, actionable gap for a section."""
        self.missing_requirements.setdefault(section, [])
        line = f"Required: {what}"
        if line not in self.missing_requirements[section]:
            self.missing_requirements[section].append(line)

    def source(self, section: str, source: str, method: str, as_at: Optional[str]) -> None:
        self.data_sources[section] = {"source": source, "method": method, "as_at": as_at}


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_identity(db, ticker: str, a: PSXAnalysis) -> bool:
    row = db.execute(
        "SELECT * FROM psx_companies WHERE symbol = ? LIMIT 1", (ticker,)
    ).fetchone()
    if not row:
        return False
    d = dict(row)
    from intelligence.financial_model import _is_bank

    a.company_name = d.get("company_name") or ticker
    a.sector = d.get("sector_name") or ""
    a.is_bank = _is_bank(a.sector)
    a.market_cap = d.get("market_cap")
    a.shares_outstanding = d.get("shares_outstanding")
    a.week52_high = d.get("week52_high")
    a.week52_low = d.get("week52_low")
    a.profile = d
    a.source("profile", "PSX company profile", "html_bs4", d.get("profile_fetched_at"))

    for label, key in (("chief executive", "ceo"), ("chairman", "chairman"),
                       ("registered office", "address"), ("auditor", "registrar")):
        if not d.get(key):
            a.require("profile", f"{label} - run a company profile refresh for {ticker}")
    return True


def _load_price(db, ticker: str, a: PSXAnalysis) -> None:
    row = db.execute(
        "SELECT trade_date, current, change_pct, sanity_flag FROM psx_prices "
        "WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1", (ticker,)
    ).fetchone()
    if not row:
        a.require("market", f"price data for {ticker} - run the PSX price scraper")
        return
    d = dict(row)
    # A price the hourly sanity sweep flagged must not be shown as a number.
    if d.get("sanity_flag") == "implausible":
        a.require("market", f"a plausible price for {ticker} - the stored value "
                            f"({d.get('current')}) failed the sanity check")
    else:
        a.current_price = d.get("current")
        a.price_change_pct = d.get("change_pct")
    a.source("market", "PSX daily quote", "html_bs4", d.get("trade_date"))


def _load_statements(db, ticker: str, a: PSXAnalysis) -> None:
    """Load every stored period, scaled to actual PKR exactly once."""
    rows = db.execute(
        "SELECT * FROM psx_financials WHERE symbol = ? AND period_type = 'annual' "
        "AND period != 'latest' ORDER BY period DESC", (ticker,)
    ).fetchall()
    if not rows:
        for s in ("balance_sheet", "income_statement", "cash_flow"):
            a.require(s, f"annual financials for {ticker} - none stored; "
                         f"run a refresh for this ticker")
        return

    latest_fetch = None
    for row in rows:
        d = dict(row)
        period = str(d.get("period"))
        scaled = {}
        for k in _MONEY_FIELDS:
            v = d.get(k)
            scaled[k] = float(v) * PKR_THOUSANDS if isinstance(v, (int, float)) else None
        scaled["eps"] = d.get("eps")   # per-share values are already absolute PKR
        a.annual_periods.append(period)
        a.income_statement[period] = {
            k: scaled.get(k) for k in
            ("revenue", "gross_profit", "ebitda", "ebit", "net_income",
             "interest_expense", "depreciation")
        } | {"eps": scaled["eps"]}
        a.balance_sheet[period] = {
            k: scaled.get(k) for k in
            ("total_assets", "total_equity", "total_debt", "cash", "current_assets",
             "current_liabilities", "retained_earnings", "inventory", "receivables",
             "payables")
        }
        a.cash_flow[period] = {k: scaled.get(k) for k in ("ocf", "capex")}
        latest_fetch = latest_fetch or d.get("fetched_at")

    a.source("income_statement", "PSX financial statements", "html_bs4", latest_fetch)
    a.source("balance_sheet", "PSX financial statements + annual report", "html_bs4/pdf",
             latest_fetch)
    a.source("cash_flow", "PSX annual report", "pdf", latest_fetch)

    latest = a.latest_period
    bs, cf = a.balance_sheet.get(latest, {}), a.cash_flow.get(latest, {})
    if bs.get("current_assets") is None or bs.get("current_liabilities") is None:
        a.require("balance_sheet",
                  "current assets and current liabilities - not present in the PSX "
                  "summary; needs an annual report parse that clears the accuracy gate")
    if bs.get("total_assets") is None:
        a.require("balance_sheet", "total assets - needed for leverage and Altman Z")
    if cf.get("ocf") is None:
        a.require("cash_flow",
                  "cash flow statement - operating cash flow is not published in the "
                  "PSX summary and must come from the annual report")


def _load_quarterly(db, ticker: str, a: PSXAnalysis) -> None:
    rows = db.execute(
        "SELECT period FROM psx_financials WHERE symbol = ? AND period_type = 'quarterly' "
        "ORDER BY period DESC LIMIT 8", (ticker,)
    ).fetchall()
    a.quarterly_periods = [str(dict(r)["period"]) for r in rows]


def _load_announcements(db, ticker: str, a: PSXAnalysis) -> None:
    rows = db.execute(
        "SELECT ann_type, ann_date, title, document_url FROM psx_announcements "
        "WHERE symbol = ? ORDER BY ann_date DESC LIMIT 25", (ticker,)
    ).fetchall()
    a.announcements = [dict(r) for r in rows]
    if not a.announcements:
        a.require("announcements", f"announcements for {ticker} - run the PSX scraper")
    a.source("announcements", "PSX announcements", "html_bs4",
             a.announcements[0]["ann_date"] if a.announcements else None)


# ── Computation ───────────────────────────────────────────────────────────────

def _compute_ratios(a: PSXAnalysis) -> None:
    """Per-period ratios, each stored as a ValidatedMetric."""
    for period in a.annual_periods:
        inc = a.income_statement.get(period, {})
        bs = a.balance_sheet.get(period, {})
        cf = a.cash_flow.get(period, {})
        rev, ni = inc.get("revenue"), inc.get("net_income")
        out: dict = {}

        # Profitability
        out["gross_margin"] = _vm("gross_margin", _div(inc.get("gross_profit"), rev), a.is_bank)
        out["operating_margin"] = _vm("operating_margin", _div(inc.get("ebit"), rev), a.is_bank)
        out["ebitda_margin"] = _vm("ebitda_margin", _div(inc.get("ebitda"), rev), a.is_bank)
        out["net_margin"] = _vm("net_margin", _div(ni, rev), a.is_bank)
        out["roa"] = _vm("roa", _div(ni, bs.get("total_assets")), a.is_bank)
        out["roe"] = _vm("roe", _div(ni, bs.get("total_equity")), a.is_bank)

        # Liquidity
        ca, cl = bs.get("current_assets"), bs.get("current_liabilities")
        out["current_ratio"] = _vm("current_ratio", _div(ca, cl), a.is_bank)
        quick = _div((ca - bs["inventory"]), cl) if (
            ca is not None and cl and bs.get("inventory") is not None) else None
        out["quick_ratio"] = _vm("quick_ratio", quick, a.is_bank)
        out["cash_ratio"] = _vm("cash_ratio", _div(bs.get("cash"), cl), a.is_bank)
        out["working_capital"] = _vm(
            "working_capital", (ca - cl) if (ca is not None and cl is not None) else None,
            a.is_bank)

        # Leverage
        out["debt_to_equity"] = _vm(
            "debt_to_equity", _div(bs.get("total_debt"), bs.get("total_equity")), a.is_bank)
        out["debt_to_assets"] = _vm(
            "debt_to_assets", _div(bs.get("total_debt"), bs.get("total_assets")), a.is_bank)
        out["equity_multiplier"] = _vm(
            "equity_multiplier", _div(bs.get("total_assets"), bs.get("total_equity")), a.is_bank)
        # Interest coverage is only meaningful against a real interest COST. BFBIO
        # returned -12.38 alongside a positive operating margin, which is
        # self-contradictory: it means the parsed interest_expense was negative (net
        # finance income, or a sign/column artefact). Coverage against a negative
        # denominator is not a weak-coverage signal, it is an undefined one, so it is
        # withheld rather than shown as an alarming negative number.
        ie = inc.get("interest_expense")
        coverage = _div(inc.get("ebit"), ie) if (ie is not None and ie > 0) else None
        out["interest_coverage"] = _vm("interest_coverage", coverage, a.is_bank)
        if ie is not None and ie <= 0:
            a.require("ratios", "a positive interest expense to compute interest "
                                f"coverage (stored value: {ie:,.0f})")

        # Efficiency
        out["asset_turnover"] = _vm(
            "asset_turnover", _div(rev, bs.get("total_assets")), a.is_bank)
        cogs = (rev - inc["gross_profit"]) if (
            rev is not None and inc.get("gross_profit") is not None) else None
        out["days_inventory"] = _vm(
            "days_inventory", (_div(bs.get("inventory"), cogs) or 0) * 365
            if (bs.get("inventory") is not None and cogs) else None, a.is_bank)
        out["days_sales_outstanding"] = _vm(
            "days_sales_outstanding", (_div(bs.get("receivables"), rev) or 0) * 365
            if (bs.get("receivables") is not None and rev) else None, a.is_bank)
        out["days_payables"] = _vm(
            "days_payables", (_div(bs.get("payables"), cogs) or 0) * 365
            if (bs.get("payables") is not None and cogs) else None, a.is_bank)

        # Cash flow quality
        out["fcf"] = _vm("fcf", (cf["ocf"] - abs(cf["capex"])) if (
            cf.get("ocf") is not None and cf.get("capex") is not None) else None, a.is_bank)
        out["ocf_to_net_income"] = _vm(
            "ocf_to_net_income", _div(cf.get("ocf"), ni), a.is_bank)

        # Per share / market
        out["eps"] = _vm("eps", inc.get("eps"), a.is_bank)
        if a.shares_outstanding:
            out["book_value_per_share"] = _vm(
                "book_value_per_share", _div(bs.get("total_equity"), a.shares_outstanding),
                a.is_bank)
        if a.current_price and inc.get("eps"):
            out["pe"] = _vm("pe", _div(a.current_price, inc["eps"]), a.is_bank)

        a.ratios[period] = out

    # Growth needs two consecutive periods.
    if len(a.annual_periods) >= 2:
        new, old = a.annual_periods[0], a.annual_periods[1]
        for name, key in (("revenue_growth", "revenue"),
                          ("net_income_growth", "net_income"),
                          ("gross_profit_growth", "gross_profit")):
            cur = a.income_statement.get(new, {}).get(key)
            prv = a.income_statement.get(old, {}).get(key)
            growth = _div(cur - prv, abs(prv)) if (cur is not None and prv) else None
            a.ratios[new][name] = _vm(name, growth, a.is_bank)
    else:
        a.require("ratios", "a second annual period - growth metrics need 2+ periods "
                            f"(available: {len(a.annual_periods)})")


def _compute_sector_context(a: PSXAnalysis) -> None:
    """Sector medians, the subject's percentile, and the peer table."""
    from intelligence.sector_medians import get_sector_median
    from database.connection import get_db

    latest = a.latest_period
    subject = a.ratios.get(latest, {}) if latest else {}

    for metric in ("gross_margin", "net_margin", "roe", "roa", "current_ratio",
                   "debt_to_equity", "asset_turnover", "pe"):
        med = get_sector_median(a.sector, metric)
        if med:
            a.sector_medians[metric] = med
    if not a.sector_medians:
        a.require("sector", f"sector medians for {a.sector} - run the sector medians job")

    # Peers: same sector, with their latest stored income statement.
    try:
        rows = get_db().execute(
            """SELECT c.symbol, c.company_name, c.market_cap, f.revenue, f.net_income,
                      f.total_assets, f.total_equity, f.eps
               FROM psx_companies c
               JOIN psx_financials f ON f.symbol = c.symbol AND f.period_type='annual'
               WHERE c.sector_name = ? AND COALESCE(c.is_debt,0)=0
                 AND f.revenue IS NOT NULL
               GROUP BY c.symbol
               ORDER BY c.market_cap DESC NULLS LAST LIMIT 40""",
            (a.sector,),
        ).fetchall()
    except Exception:
        rows = []

    peers = []
    for r in rows:
        d = dict(r)
        rev = (d.get("revenue") or 0) * PKR_THOUSANDS
        ni = (d.get("net_income") or 0) * PKR_THOUSANDS
        eq = (d.get("total_equity") or 0) * PKR_THOUSANDS
        peers.append({
            "symbol": d["symbol"],
            "company_name": d.get("company_name"),
            "market_cap": d.get("market_cap"),
            "net_margin": _div(ni, rev),
            "roe": _div(ni, eq),
            "eps": d.get("eps"),
            "is_subject": d["symbol"] == a.ticker,
        })
    a.peers = peers

    n_valid = sum(1 for p in peers if p["net_margin"] is not None)
    if n_valid < MIN_PEERS:
        a.require("sector", f"more sector peers - only {n_valid} company(ies) in "
                            f"{a.sector} have valid data (need {MIN_PEERS}+ for a "
                            f"reliable benchmark)")
    else:
        # Percentile: share of valid peers the subject sits above.
        for metric in ("net_margin", "roe"):
            mine = subject.get(metric, {}).get("value")
            vals = [p[metric] for p in peers if p.get(metric) is not None]
            if mine is not None and len(vals) >= MIN_PEERS:
                below = sum(1 for v in vals if v < mine)
                a.sector_percentiles[metric] = round(below / len(vals) * 100)


def _compute_models(a: PSXAnalysis) -> None:
    """DuPont, Altman and valuation, each honest about missing inputs."""
    from intelligence.financial_model import _compute_altman_z, _compute_dupont

    latest = a.latest_period
    if not latest:
        return
    inc = a.income_statement.get(latest, {})
    bs = a.balance_sheet.get(latest, {})

    # DuPont
    dp = _compute_dupont(inc.get("net_income"), inc.get("revenue"),
                        bs.get("total_assets"), bs.get("total_equity"))
    a.dupont = {
        "net_margin": _vm("net_margin", dp.net_margin, a.is_bank),
        "asset_turnover": _vm("asset_turnover", dp.asset_turnover, a.is_bank),
        "equity_multiplier": _vm("equity_multiplier", dp.equity_multiplier, a.is_bank),
        "roe": _vm("roe", dp.roe_computed, a.is_bank),
    }
    missing_dp = [k for k, v in (("net income", inc.get("net_income")),
                                 ("revenue", inc.get("revenue")),
                                 ("total assets", bs.get("total_assets")),
                                 ("total equity", bs.get("total_equity"))) if v is None]
    if missing_dp:
        a.require("dupont", f"{', '.join(missing_dp)} - ROE cannot be decomposed "
                            f"without all three drivers")

    # Altman: never compute a partial score.
    need = {"total assets": bs.get("total_assets"),
            "current assets": bs.get("current_assets"),
            "current liabilities": bs.get("current_liabilities"),
            "total equity": bs.get("total_equity"),
            "revenue": inc.get("revenue")}
    missing_z = [k for k, v in need.items() if v is None]
    if missing_z:
        a.altman = {"computable": False, "missing": missing_z}
        a.require("distress", f"{', '.join(missing_z)} - Z-Score cannot be computed "
                              f"without complete balance sheet data")
    else:
        z = _compute_altman_z(
            bs.get("current_assets"), bs.get("current_liabilities"),
            bs.get("total_assets"), bs.get("total_debt"), bs.get("total_equity"),
            inc.get("revenue"), inc.get("ebit"), bs.get("cash"),
            bs.get("retained_earnings"), is_bank=a.is_bank,
            net_income=inc.get("net_income"))
        a.altman = {
            "computable": z.z_score is not None,
            "score": _vm("altman_z", z.z_score, a.is_bank),
            "zone": z.zone,
            "components": z.components,
            "formula_used": z.model,
        }

    # Valuation: banks take multiples, non-banks need cash flow for a DCF.
    if a.is_bank:
        bvps = a.ratios.get(latest, {}).get("book_value_per_share", {}).get("value")
        a.valuation = {
            "approach": "multiples",
            "reason": ("Enterprise DCF is not applied to banks, as debt is an "
                      "operating input rather than a financing choice for financial "
                      "institutions. Valuation uses book value and earnings multiples "
                      "benchmarked against listed banking peers."),
            "pb": _vm("pb", _div(a.current_price, bvps), a.is_bank),
            "pe": a.ratios.get(latest, {}).get("pe", {"value": None, "status": "missing"}),
            "sector_pe": a.sector_medians.get("pe"),
        }
        if bvps is None:
            a.require("valuation", "book value per share - needs total equity and "
                                   "shares outstanding")
    else:
        cf = a.cash_flow.get(latest, {})
        if cf.get("ocf") is None:
            a.valuation = {"approach": "dcf", "computable": False}
            a.require("valuation", "operating cash flow and capital expenditure - a "
                                   "DCF cannot be run without free cash flow")
        else:
            a.valuation = {"approach": "dcf", "computable": True,
                          "note": "DCF inputs available; scenarios computed in Section 9"}


def _compute_staleness(a: PSXAnalysis, db) -> None:
    """
    Compare each source's age against its expected refresh window.

    Ages come from THIS TICKER'S stored data timestamps, not only from scrape_log.
    A scrape_log lookup alone reported "Prices has never been refreshed" for HBL
    while psx_prices held that day's quote: the scheduler job id (psx_prices_close)
    is not the scraper id that writes the log row (psx_market), and a bulk job may
    populate a ticker without a per-ticker log entry either way. Judging freshness
    by the data actually on hand is both accurate and what the analyst cares about.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    def _age_hours(raw) -> Optional[float]:
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).total_seconds() / 3600.0
        except Exception:
            return None

    def _fmt(age_h: float) -> str:
        return f"{age_h / 24:.0f} days" if age_h >= 48 else f"{age_h:.0f} hours"

    checks = (
        ("Prices", _age_hours((db.execute(
            "SELECT MAX(trade_date) m FROM psx_prices WHERE symbol=?",
            (a.ticker,)).fetchone() or {"m": None})["m"]), 27.0, "expected daily"),
        ("PSX financials", _age_hours((db.execute(
            "SELECT MAX(fetched_at) m FROM psx_financials WHERE symbol=?",
            (a.ticker,)).fetchone() or {"m": None})["m"]), 192.0, "expected weekly"),
        ("Company profile", _age_hours(a.profile.get("profile_fetched_at")),
         30.0, "expected daily"),
    )

    stale = []
    for label, age_h, window, expectation in checks:
        if age_h is None:
            stale.append(f"{label} has never been refreshed for {a.ticker}")
        elif age_h > window:
            stale.append(f"{label} last refreshed {_fmt(age_h)} ago ({expectation})")
    a.staleness = {"is_stale": bool(stale), "messages": stale}


# ── Entry point ───────────────────────────────────────────────────────────────

def build_analysis(ticker: str) -> PSXAnalysis:
    """
    Assemble everything the twelve sections need, from the local database only.

    Never raises for a missing ticker: `found` is False and the caller renders a
    not-found state. Never scrapes; staleness is reported, not repaired.
    """
    import time
    t0 = time.time()
    ticker = (ticker or "").strip().upper()
    a = PSXAnalysis(ticker=ticker)

    from database.connection import get_db
    db = get_db()

    if not _load_identity(db, ticker, a):
        a.build_ms = int((time.time() - t0) * 1000)
        return a
    a.found = True

    _load_price(db, ticker, a)
    _load_statements(db, ticker, a)
    _load_quarterly(db, ticker, a)
    _load_announcements(db, ticker, a)

    if a.annual_periods:
        _compute_ratios(a)
        _compute_models(a)
    _compute_sector_context(a)
    _compute_staleness(a, db)

    # Regulatory context is static rules, always available.
    try:
        from intelligence.compliance_rules import get_compliance_profile
        a.compliance = get_compliance_profile(a.sector, is_listed=True,
                                             company_type="listed_public")
        a.source("regulatory", "Static compliance rules, Pakistan", "static", None)
    except Exception as e:
        log.info("compliance profile unavailable", ticker=ticker, error=str(e)[:80])
        a.require("regulatory", f"compliance rules for sector {a.sector}")

    # Statement quality needs audit-report text, which is not stored yet.
    a.statement_quality = {"available": False}
    a.require("statement_quality",
              "audit report text - the annual report PDF for this ticker has not "
              "yielded a readable auditor's report")

    a.health_score = _health(a)
    a.build_ms = int((time.time() - t0) * 1000)
    log.debug("build_analysis complete", ticker=ticker, ms=a.build_ms,
             periods=len(a.annual_periods))
    return a


def _health(a: PSXAnalysis) -> dict:
    """
    Score only what is actually measurable, and say so.

    Confidence is driven by how much of the intended input set was available, so a
    thin-data ticker can never present a confident-looking score.
    """
    latest = a.latest_period
    r = a.ratios.get(latest, {}) if latest else {}
    checks, passed = 0, 0
    for name, good in (("net_margin", lambda v: v > 0.05),
                       ("roe", lambda v: v > 0.10),
                       ("current_ratio", lambda v: v >= 1.2),
                       ("interest_coverage", lambda v: v >= 2.0),
                       ("revenue_growth", lambda v: v > 0.0)):
        m = r.get(name) or {}
        if m.get("status") == "ok" and m.get("value") is not None:
            checks += 1
            if good(m["value"]):
                passed += 1
    if not checks:
        return {"score": None, "confidence": "none", "basis": 0,
                "note": "No validated metric was available to score."}
    score = round(passed / checks * 10, 1)
    # Thin coverage must never read as a confident verdict. Scoring 10/10 on two of
    # five checks is arithmetically right but presentationally misleading, so the
    # label and the note both carry the basis: 4+ checks is the only "high".
    confidence = "high" if checks >= 4 else "moderate" if checks == 3 else "low"
    return {"score": score, "confidence": confidence, "basis": checks,
            "note": f"Scored on {checks} of 5 intended checks; "
                   f"{passed} passed. Treat as indicative only when the basis is "
                   f"below 4." if checks < 4 else
                   f"Scored on {checks} of 5 intended checks; {passed} passed."}
