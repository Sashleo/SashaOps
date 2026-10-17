"""
intelligence/psx_analyser.py — Single analysis entry point.

Builds a PSXAnalysis object from the local database. Every section template
reads from this object; no template touches the database directly.

Reads only. Never scrapes on request. Stale data shows a staleness banner.
PKR unit scaling happens once here. Every metric goes through validate_metric.
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


def _vm(name: str, value, is_bank: bool = False, raw: Optional[dict] = None,
        sector: str = "") -> dict:
    """Shorthand: run a value through validation and return the ValidatedMetric."""
    return validate_metric(name, value, is_bank=is_bank, raw_inputs=raw, sector=sector)


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
    announcements: list = field(default_factory=list)
    statement_quality: dict = field(default_factory=dict)

    # meta
    health_score: dict = field(default_factory=dict)
    data_sources: dict = field(default_factory=dict)
    missing_requirements: dict = field(default_factory=dict)
    staleness: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    charts: dict = field(default_factory=dict)
    # Track B document provenance for the latest period: lets a section name the
    # actual reason a balance sheet is absent (an interim filing) instead of
    # reporting a generic gap.
    source_document_type: Optional[str] = None
    source_document_pages: Optional[int] = None
    source_document_url: Optional[str] = None
    source_document_date: Optional[str] = None
    observations: dict = field(default_factory=dict)
    dupont_driver: dict = field(default_factory=dict)
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
    from config.sectors import is_bank

    a.company_name = d.get("company_name") or ticker
    a.sector = d.get("sector_name") or ""
    a.is_bank = is_bank(a.sector)
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
        # Document provenance belongs to the newest period (loaded first).
        if a.source_document_type is None:
            a.source_document_type = d.get("source_document_type")
            a.source_document_pages = d.get("source_document_pages")
            a.source_document_url = d.get("source_document_url")
            a.source_document_date = d.get("source_document_date")

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
    """Per-period ratios via metrics.compute_ratios, each validated."""
    from intelligence.metrics import compute_ratios, compute_growth

    for period in a.annual_periods:
        inc = a.income_statement.get(period, {})
        bs = a.balance_sheet.get(period, {})
        cf = a.cash_flow.get(period, {})

        raw = compute_ratios(inc, bs, cf, a.is_bank)

        # Market ratios
        if a.shares_outstanding and bs.get("total_equity"):
            raw["book_value_per_share"] = _div(bs["total_equity"], a.shares_outstanding)
        if a.current_price and inc.get("eps"):
            raw["pe"] = _div(a.current_price, inc["eps"])

        out = {name: _vm(name, val, a.is_bank, sector=a.sector or "") for name, val in raw.items()}
        a.ratios[period] = out

    if len(a.annual_periods) >= 2:
        new, old = a.annual_periods[0], a.annual_periods[1]
        growth = compute_growth(
            a.income_statement.get(new, {}),
            a.income_statement.get(old, {}),
        )
        for name, val in growth.items():
            a.ratios[new][name] = _vm(name, val, a.is_bank, sector=a.sector or "")
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
        # Latest annual row per peer, plus the prior period so peer revenue growth
        # (Chart 8's x axis while balance-sheet data is unavailable) is computable.
        rows = get_db().execute(
            """SELECT c.symbol, c.company_name, c.market_cap,
                      f.period, f.revenue, f.net_income, f.total_equity, f.eps
               FROM psx_companies c
               JOIN psx_financials f ON f.symbol = c.symbol AND f.period_type='annual'
               WHERE c.sector_name = ? AND COALESCE(c.is_debt,0)=0
                 AND f.revenue IS NOT NULL AND f.period != 'latest'
               ORDER BY c.market_cap DESC NULLS LAST, c.symbol, f.period DESC""",
            (a.sector,),
        ).fetchall()
    except Exception:
        rows = []

    by_symbol: dict = {}
    for r in rows:
        by_symbol.setdefault(dict(r)["symbol"], []).append(dict(r))

    from intelligence.validation import validate_metric as _vmet
    peers = []
    for sym, recs in list(by_symbol.items())[:40]:
        latest_rec = recs[0]
        prior_rec = recs[1] if len(recs) > 1 else {}
        rev = (latest_rec.get("revenue") or 0) * PKR_THOUSANDS
        ni = (latest_rec.get("net_income") or 0) * PKR_THOUSANDS
        eq = (latest_rec.get("total_equity") or 0) * PKR_THOUSANDS
        prev_rev = (prior_rec.get("revenue") or 0) * PKR_THOUSANDS
        # Validate before display — parse errors in peer data must not show as real numbers
        _sec = a.sector or ""
        nm = _vmet("net_margin", _div(ni, rev), a.is_bank, sector=_sec)["value"]
        roe_v = _vmet("roe", _div(ni, eq), a.is_bank, sector=_sec)["value"]
        rg = _vmet("revenue_growth",
                   _div(rev - prev_rev, prev_rev) if prev_rev else None,
                   a.is_bank, sector=_sec)["value"]
        peers.append({
            "symbol": sym,
            "company_name": latest_rec.get("company_name"),
            "market_cap": latest_rec.get("market_cap"),
            "period": latest_rec.get("period"),
            "net_margin": nm,
            "roe": roe_v,
            "revenue_growth": rg,
            "eps": latest_rec.get("eps"),
            "is_subject": sym == a.ticker,
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
    from intelligence.metrics import compute_dupont, compute_altman_z

    latest = a.latest_period
    if not latest:
        return
    inc = a.income_statement.get(latest, {})
    bs = a.balance_sheet.get(latest, {})

    # DuPont
    dp = compute_dupont(inc.get("net_income"), inc.get("revenue"),
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

    # Altman Z'' (banks excluded per Altman)
    z = compute_altman_z(
        bs.get("current_assets"), bs.get("current_liabilities"),
        bs.get("total_assets"), bs.get("total_equity"),
        inc.get("revenue"), inc.get("ebit"),
        bs.get("retained_earnings"), sector=a.sector,
    )
    if z.computable:
        a.altman = {
            "computable": True,
            "score": _vm("altman_z", z.z_score, a.is_bank),
            "zone": z.zone,
            "components": z.components,
            "formula_used": z.model,
        }
    else:
        a.altman = {"computable": False, "missing": z.missing}
        if z.missing and z.missing != ["Altman Z excluded financial institutions"]:
            a.require("distress", f"{', '.join(z.missing)} - Z-Score cannot be computed")

    # Valuation
    if a.is_bank:
        bvps = a.ratios.get(latest, {}).get("book_value_per_share", {}).get("value")
        a.valuation = {
            "approach": "multiples",
            "reason": ("Enterprise DCF is not applied to banks. Valuation uses "
                      "book value and earnings multiples against banking peers."),
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
                          "note": "DCF inputs captured. Use the entry form to set "
                                  "assumptions and run scenarios."}


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

    # Statement quality needs audit-report text, which is not stored yet.
    a.statement_quality = {"available": False}
    a.require("statement_quality",
              "audit report text - the annual report PDF for this ticker has not "
              "yielded a readable auditor's report")

    a.dupont_driver = _dupont_driver(a)
    a.observations = _observations(a)
    a.health_score = _health(a)
    try:
        from intelligence.psx_charts import build_charts
        a.charts = build_charts(a)
    except Exception as e:
        log.info("chart build skipped", ticker=ticker, error=str(e)[:80])
    a.build_ms = int((time.time() - t0) * 1000)
    log.debug("build_analysis complete", ticker=ticker, ms=a.build_ms,
             periods=len(a.annual_periods))
    return a


_PCT_LABELS = {"net_margin": "Net margin", "gross_margin": "Gross margin",
               "roe": "Return on equity", "roa": "Return on assets",
               "operating_margin": "Operating margin"}


_DUPONT_INTERPRETATION = {
    "equity_multiplier": "Returns depend materially on balance sheet leverage "
                        "rather than operating performance.",
    "net_margin": "Returns are driven by operating profitability.",
    "asset_turnover": "Returns are driven by asset efficiency.",
}


def _dupont_driver(a: PSXAnalysis) -> dict:
    """
    Which component explains the ROE, measured as the largest relative deviation
    from its sector median. Returns {} when medians or components are missing,
    rather than naming a driver that was not actually compared to anything.
    """
    best = None
    for key in ("net_margin", "asset_turnover", "equity_multiplier"):
        mm = (a.dupont or {}).get(key) or {}
        med = (a.sector_medians.get(key) or {}).get("median")
        if mm.get("status") != "ok" or mm.get("value") is None or not med:
            continue
        deviation = abs(mm["value"] - med) / abs(med)
        if best is None or deviation > best[0]:
            best = (deviation, key, mm["value"])
    if not best:
        return {}
    _, key, value = best
    label = {"net_margin": "net margin", "asset_turnover": "asset turnover",
             "equity_multiplier": "the equity multiplier"}[key]
    shown = f"{value * 100:,.1f}%" if key == "net_margin" else f"{value:,.2f}x"
    return {"key": key, "name": label, "value": shown,
            "interpretation": _DUPONT_INTERPRETATION[key]}


def _observations(a: PSXAnalysis) -> dict:
    """
    Section 12: structured observations, not recommendations.

    Every entry is produced by a rule over already-validated values, so nothing
    here is generated prose. Rules that need data we do not have simply do not
    fire; the limitations list carries those gaps explicitly.
    """
    latest = a.latest_period
    R = a.ratios.get(latest, {}) if latest else {}
    strengths, weaknesses, risks = [], [], []

    def _trend(key: str) -> Optional[str]:
        """'improved' | 'held' | 'deteriorated' across available validated periods."""
        vals = []
        for p in reversed(a.annual_periods):
            mm = (a.ratios.get(p) or {}).get(key) or {}
            if mm.get("status") == "ok" and mm.get("value") is not None:
                vals.append(mm["value"])
        if len(vals) < 2:
            return None
        if vals[-1] > vals[0] * 1.02:
            return "improved"
        if vals[-1] < vals[0] * 0.98:
            return "deteriorated"
        return "held"

    for key, label in _PCT_LABELS.items():
        mm = R.get(key) or {}
        if mm.get("status") != "ok" or mm.get("value") is None:
            continue
        med = (a.sector_medians.get(key) or {}).get("median")
        if med is None:
            continue
        trend = _trend(key)
        n_periods = len([p for p in a.annual_periods
                        if ((a.ratios.get(p) or {}).get(key) or {}).get("status") == "ok"])
        if mm["value"] > med and trend in ("improved", "held", None):
            strengths.append(
                f"{label} of {mm['value'] * 100:,.1f}% is above the {a.sector} median "
                f"of {med * 100:,.1f}%"
                + (f" and has {trend} over {n_periods} periods." if trend else "."))
        elif mm["value"] < med and trend in ("deteriorated", "held", None):
            weaknesses.append(
                f"{label} of {mm['value'] * 100:,.1f}% is below the {a.sector} median "
                f"of {med * 100:,.1f}%"
                + (f" and has {trend} over {n_periods} periods." if trend else "."))

    # Risk rules that the available data can actually evaluate.
    ic = R.get("interest_coverage") or {}
    if ic.get("status") == "ok" and ic.get("value") is not None and ic["value"] < 1.5:
        risks.append(f"Interest coverage of {ic['value']:,.2f}x is below 1.5x; "
                    f"earnings provide a thin buffer over financing costs.")
    cr = R.get("current_ratio") or {}
    if cr.get("status") == "ok" and cr.get("value") is not None and cr["value"] < 1.0:
        risks.append(f"Current ratio of {cr['value']:,.2f} is below 1.0; current "
                    f"liabilities exceed current assets.")
    if a.altman.get("computable") and (a.altman.get("zone") or "").lower().startswith("distress"):
        risks.append("Altman Z-Score places the company in the distress zone.")

    # Revenue declining across two or more consecutive periods.
    revs = [a.income_statement.get(p, {}).get("revenue") for p in a.annual_periods]
    revs = [r for r in revs if r is not None]
    if len(revs) >= 3 and revs[0] < revs[1] < revs[2]:
        risks.append("Revenue has declined for two consecutive periods.")

    limitations = []
    for section, reqs in a.missing_requirements.items():
        for r in reqs:
            limitations.append(f"{section}: {r}")

    return {"strengths": strengths, "weaknesses": weaknesses, "risks": risks,
            "limitations": limitations}


def _health(a: PSXAnalysis) -> dict:
    """
    Score only what is actually measurable, and say so.

    Thresholds are calibrated for Pakistan:
      - ROE > 15%  (risk-free ~11.5% + ERP ~5%; returning less destroys value)
      - Net margin > 5%
      - Current ratio >= 1.2
      - Interest coverage >= 2.0
      - Revenue growth > 10%  (nominal; <10% likely means real contraction at Pakistan inflation)
      - Altman zone: SAFE passes, DISTRESS fails, GREY neutral, banks excluded
    """
    latest = a.latest_period
    r = a.ratios.get(latest, {}) if latest else {}
    checks, passed = 0, 0

    thresholds = [
        ("net_margin",        lambda v: v > 0.05),
        ("roe",               lambda v: v > 0.15),   # was 0.10 — below Pakistan risk-free
        ("current_ratio",     lambda v: v >= 1.2),
        ("interest_coverage", lambda v: v >= 2.0),
        ("revenue_growth",    lambda v: v > 0.10),   # was 0.0 — too lenient for 20%+ inflation
    ]
    for name, good in thresholds:
        m = r.get(name) or {}
        if m.get("status") == "ok" and m.get("value") is not None:
            checks += 1
            if good(m["value"]):
                passed += 1

    # Altman zone counts as a sixth check (banks excluded — no zone computed)
    altman_zone = (a.altman.get("zone") or "") if isinstance(a.altman, dict) else ""
    if altman_zone in ("SAFE", "DISTRESS"):
        checks += 1
        if altman_zone == "SAFE":
            passed += 1

    if not checks:
        return {"score": None, "confidence": "none", "basis": 0,
                "note": "No validated metric was available to score."}

    score = round(passed / checks * 10, 1)
    confidence = "high" if checks >= 4 else "moderate" if checks == 3 else "low"
    max_checks = 6
    note = (f"Scored on {checks} of {max_checks} checks; {passed} passed. "
            f"Treat as indicative only when the basis is below 4."
            if checks < 4 else
            f"Scored on {checks} of {max_checks} checks; {passed} passed.")
    return {"score": score, "confidence": confidence, "basis": checks, "note": note}
