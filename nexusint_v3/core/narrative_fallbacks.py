"""
core/narrative_fallbacks.py — Local module renderer for LOCAL_ONLY mode + Gemini fallbacks.

Two entry points:
  render_local_module(mod_id, local_result, data_summary, business_name, ...) -> str
      Produces complete structured markdown for a module using only local data.
      Called in LOCAL_ONLY mode (no Gemini) or when Gemini fails.

  render_fallback(module_name, token_sheet, extra_context) -> str
      Thin token-sheet-based fallback for use after Gemini OutputValidator rejects
      a response twice. Preserved for compatibility with the existing validator path.

All functions always return a non-empty string. Never raise.
"""

from __future__ import annotations

import html as _html
from typing import Optional, Any

from jinja2 import Environment

from core.sanitiser import TokenSheet

_env = Environment(autoescape=False)

# Per-module banner intentionally blank — the "computed locally" note is stated once
# in the report footer/methodology instead of repeated on every section.
_BANNER_LOCAL = ""
_BANNER_AI    = "_AI-enhanced_"


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL MODULE RENDERER — entry point for LOCAL_ONLY mode
# ══════════════════════════════════════════════════════════════════════════════

def render_local_module(
    mod_id:        str,
    local_result:  Optional[dict],
    data_summary:  Any,           # DataCollectionSummary or None
    business_name: str,
    sector:        str = "",
    industry_tag:  str = "",
    peers_text:    Optional[str] = None,
    sentiment_scores: Optional[dict] = None,
    extra_context: str = "",
) -> str:
    """
    Produce complete structured markdown for a module using only local data.

    Returns markdown string suitable for display in the analysis panel.
    Includes a 'Computed locally' banner so the user knows which path ran.
    """
    try:
        fn = _LOCAL_RENDERERS.get(mod_id, _render_generic)
        result = fn(
            local_result=local_result,
            data_summary=data_summary,
            business_name=business_name,
            sector=sector,
            industry_tag=industry_tag,
            peers_text=peers_text,
            sentiment_scores=sentiment_scores,
            extra_context=extra_context,
        )
        return result or _render_generic(
            local_result=local_result, data_summary=data_summary,
            business_name=business_name, sector=sector,
        )
    except Exception as _e:
        return (
            f"## {mod_id.title()} - Local Analysis\n\n"
            f"{_BANNER_LOCAL}\n\n"
            f"Analysis compiled from available local data sources.\n\n"
            f"_Note: {str(_e)[:120]}_"
        )


# ── Helper utilities ─────────────────────────────────────────────────────────

def _v(d: Optional[dict], key: str, default: Any = None) -> Any:
    return (d or {}).get(key, default)


def _raw_float(v: Any) -> Optional[float]:
    """Convert a value that may be a float, int, or formatted string to float or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace("%", "").replace(",", "").replace("M", "").replace("B", "").strip()
        if s in ("N/A", "-", "", "None"):
            return None
        try:
            f = float(s)
            # If the original had a % suffix, the value was already a percentage (e.g. "35.5%")
            # convert to decimal ratio for comparisons: 35.5 → 0.355
            if "%" in str(v):
                f = f / 100.0
            return f
        except (ValueError, TypeError):
            return None
    return None


def _pct(v: Any, decimals: int = 1) -> str:
    """Format value as percentage. Accepts float (ratio), formatted string, or None."""
    if v is None:
        return "N/A"
    if isinstance(v, str):
        return v if v not in ("None", "") else "N/A"
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _num(v: Any, decimals: int = 1, suffix: str = "") -> str:
    """Format numeric value. Accepts float, int, formatted string, or None."""
    if v is None:
        return "N/A"
    if isinstance(v, str):
        return v if v not in ("None", "") else "N/A"
    try:
        return f"{float(v):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _signal(v: Any, good_above: float, warn_above: float) -> str:
    """Return a plain-text traffic-light signal. Handles float or pre-formatted string."""
    f = _raw_float(v)
    if f is None:
        return "N/A"
    if f >= good_above:
        return "OK"
    if f >= warn_above:
        return "WATCH"
    return "RISK"


def _lines_from_text(text: str, max_lines: int = 6) -> list[str]:
    """Extract up to max_lines non-empty lines from a text block."""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    return lines[:max_lines]


def _news_bullets(data_summary: Any, max_lines: int = 8) -> list[str]:
    """
    Extract only the *headline* bullets from the (relevance-filtered) news brief,
    skipping the meta/header lines. The brief is built by
    intelligence.news_analyser.build_local_brief, so its bullets are already
    filtered to company/sector-relevant items. Returns [] when there are none.
    """
    brief = getattr(data_summary, "news_brief", "") or ""
    if not brief:
        return []
    out: list[str] = []
    for raw in brief.split("\n"):
        s = raw.strip()
        if s.startswith("•") or s.startswith("- ") or s.startswith("[THREAT") or s.startswith("[OPPORTUNITY"):
            cleaned = s.lstrip("•- ").strip()
            if cleaned:
                out.append(cleaned)
        if len(out) >= max_lines:
            break
    return out


def _no_relevant_news_line(data_summary: Any) -> str:
    """A single honest 'no relevant news' statement, reused across modules."""
    note = getattr(data_summary, "analyst_note", "") or ""
    if "No company- or sector-specific news" in note:
        return "_No company- or sector-specific news in this window._"
    return "_No relevant news collected for this module in this window._"


# ── financial ────────────────────────────────────────────────────────────────

def _render_financial(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    peers_text: Optional[str] = None,
    sentiment_scores: Optional[dict] = None,
    extra_context: str = "",
    **_kw,
) -> str:
    if not local_result:
        return (
            f"## Financial Analysis - {business_name}\n\n"
            f"{_source_line('PSX financial statements')}\n\n"
            + _needs("Financial Analysis",
                     "balance sheet + income statement - upload the annual report PDF "
                     "or ensure PSX financials are scraped for this ticker")
        )
    try:
        from intelligence.financial_model import generate_local_financial_report
    except Exception:
        return f"## Financial Analysis\n\nFinancial model unavailable."

    report = generate_local_financial_report(
        local_result,
        sentiment_scores=sentiment_scores,
        peers_text=peers_text,
    )

    # Sector-specific signal context below the ratio table (static, no AI).
    try:
        from intelligence.compliance_rules import get_financial_signals_context
        _prof = getattr(data_summary, "psx_profile", {}) if data_summary else {}
        _sec = (_prof.get("sector") if isinstance(_prof, dict) else "") or industry_tag or sector
        signals = get_financial_signals_context(_sec)
        if signals:
            report += "\n\n### Sector Context - what to watch\n"
            report += "\n".join(f"- {s}" for s in signals)
    except Exception:
        pass

    return report


# ── valuation ────────────────────────────────────────────────────────────────

def _render_valuation(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    peers_text: Optional[str] = None,
    **_kw,
) -> str:
    if not local_result:
        return (
            f"## Valuation - {business_name}\n\n"
            f"{_BANNER_LOCAL}\n\n"
            f"No financial data available. Cannot compute DCF or fair value estimates."
        )

    r = local_result
    company = r.get("company") or business_name
    ticker  = r.get("ticker") or ""
    dcf     = r.get("dcf") or {}
    price   = r.get("current_price")
    health  = r.get("health_score")
    z       = r.get("altman_z")

    bear_val  = _v(dcf, "bear")
    base_val  = _v(dcf, "base")
    bull_val  = _v(dcf, "bull")
    wacc      = _v(dcf, "wacc")
    bear_f    = _raw_float(bear_val)
    base_f    = _raw_float(base_val)
    bull_f    = _raw_float(bull_val)
    wacc_f    = _raw_float(wacc)
    price_f   = _raw_float(price)

    lines = [
        f"## Valuation - {company}" + (f" ({ticker})" if ticker else ""),
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # DCF scenarios
    if base_f:
        lines += [
            "### DCF Fair Value Scenarios",
            "",
            "| Scenario | Fair Value (PKR mn) | WACC |",
            "|----------|---------------------|------|",
            f"| Bear  | {_num(bear_val, 0)}  | {_pct(wacc_f) if wacc_f is not None else 'N/A'} |",
            f"| Base  | {_num(base_val, 0)}  | {_pct(wacc_f) if wacc_f is not None else 'N/A'} |",
            f"| Bull  | {_num(bull_val, 0)}  | {_pct(wacc_f) if wacc_f is not None else 'N/A'} |",
            "",
        ]
        if price_f and base_f:
            premium = (price_f - base_f) / base_f * 100
            if premium > 15:
                lines.append(f"**Current price {_num(price, 2)} PKR trades {abs(premium):.0f}% above base fair value** - potentially stretched.")
            elif premium < -15:
                lines.append(f"**Current price {_num(price, 2)} PKR trades {abs(premium):.0f}% below base fair value** - potential upside.")
            else:
                lines.append(f"Current price {_num(price, 2)} PKR is near base-case fair value ({premium:+.1f}%).")
            lines.append("")

    # Health/Z summary
    lines += [
        "### Valuation Context",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Health Score | {_num(health, 1)}/10 |",
        f"| Altman Z-Score | {_num(z, 2) if z else 'N/A (banks use different model)'} |",
    ]

    # Revenue projections
    projs = r.get("revenue_projections") or []
    if projs:
        lines += ["", "### 3-Year Revenue Projections (PKR mn)", ""]
        lines.append("| Year | Conservative | Base | Optimistic |")
        lines.append("|------|-------------|------|------------|")
        for proj in projs:
            lines.append(
                f"| {proj.get('year','-')} "
                f"| {_num(proj.get('conservative'), 0)} "
                f"| {_num(proj.get('base'), 0)} "
                f"| {_num(proj.get('optimistic'), 0)} |"
            )

    if peers_text:
        lines += ["", "### PSX Sector Peers", "", "```", peers_text, "```"]

    return "\n".join(lines)


# ── sentiment ────────────────────────────────────────────────────────────────

def _render_sentiment(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    sentiment_scores: Optional[dict] = None,
    extra_context: str = "",
    **_kw,
) -> str:
    lines = [
        f"## Market Sentiment - {business_name}",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # M10 — PSX announcements shown as a TYPE breakdown (dividend/buyback vs
    # procedural vs show-cause), never a FinBERT "100% neutral".
    if sentiment_scores and sentiment_scores.get("kind") == "announcement_types":
        n = sentiment_scores.get("n", 0)
        lines += [
            f"### PSX Announcements - type breakdown ({n} filings)", "",
            f"**{sentiment_scores.get('summary', 'no announcements')}**", "",
        ]
        top = sentiment_scores.get("top", [])
        if top:
            lines.append("**Most recent:**")
            for h in top[:5]:
                lines.append(f"- {h}")
            lines.append("")

    # Feed-based sentiment — relevance-filtered headlines with tailwind/headwind tags.
    sent_bullets = _news_bullets(data_summary, 6)
    news_brief = getattr(data_summary, "news_brief", "") or ""
    if sent_bullets:
        lines += ["### Relevant News Signals (tailwind ＋ / headwind －)", ""]
        for ln in sent_bullets:
            lines.append(f"- {ln}")
        lines.append("")

    search_snippets = getattr(data_summary, "search_snippets", "") or ""
    if search_snippets:
        lines += ["### Search Intelligence", ""]
        for ln in _lines_from_text(search_snippets, 6):
            lines.append(ln)
        lines.append("")

    if not sentiment_scores and not news_brief:
        lines.append(
            "No sentiment data available. Run with PSX ticker to enable FinBERT scoring "
            "on corporate announcements."
        )

    return "\n".join(lines)


# ── economy ──────────────────────────────────────────────────────────────────

def _render_economy(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    extra_context: str = "",
    **_kw,
) -> str:
    lines = [
        f"## Economy & Market Environment",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # Forex
    forex_text = getattr(data_summary, "forex_text", "") or ""
    if forex_text:
        lines += ["### Exchange Rates", ""]
        for ln in _lines_from_text(forex_text, 6):
            lines.append(f"- {ln}" if not ln.startswith("-") else ln)
        lines.append("")

    # KSE index
    index_text = getattr(data_summary, "index_text", "") or ""
    if index_text:
        lines += ["### KSE Index", ""]
        for ln in _lines_from_text(index_text, 4):
            lines.append(f"- {ln}" if not ln.startswith("-") else ln)
        lines.append("")

    # Macro data
    try:
        from database.macro_data import get_macro_summary_text
        macro = get_macro_summary_text(industry_tag or "general")
        if macro:
            lines += ["### Pakistan Macro Indicators", ""]
            for ln in _lines_from_text(macro, 10):
                lines.append(ln)
            lines.append("")
    except Exception:
        pass

    # Macro/economic headlines — relevance-filtered (economy is a valid news lens).
    econ_bullets = _news_bullets(data_summary, 6)
    if econ_bullets:
        lines += ["### Macro-Relevant Headlines", ""]
        for ln in econ_bullets:
            lines.append(f"- {ln}")
        lines.append("")

    # Impact on subject company if financial data available
    if local_result:
        debt_eq   = local_result.get("debt_equity")
        ic        = local_result.get("interest_coverage")
        debt_eq_f = _raw_float(debt_eq)
        ic_f      = _raw_float(ic)
        if debt_eq_f is not None or ic_f is not None:
            lines += ["### Rate Sensitivity", ""]
            if debt_eq_f is not None:
                if debt_eq_f > 2.0:
                    lines.append(f"- **High leverage (D/E {debt_eq_f:.2f})** - rising SBP policy rate materially increases financing cost.")
                elif debt_eq_f > 1.0:
                    lines.append(f"- Moderate leverage (D/E {debt_eq_f:.2f}) - interest rate changes have material but manageable impact.")
                else:
                    lines.append(f"- Low leverage (D/E {debt_eq_f:.2f}) - limited sensitivity to interest rate changes.")
            if ic_f is not None:
                if ic_f < 1.5:
                    lines.append(f"- **Weak interest coverage ({ic_f:.1f}x)** - debt service at risk if rates rise further.")
                elif ic_f > 5:
                    lines.append(f"- Strong interest coverage ({ic_f:.1f}×) - comfortable debt service margin.")
            lines.append("")

    if not forex_text and not index_text and not news_brief:
        lines.append("Economic context data not yet collected. Run a full Stage 1 scan to populate.")

    return "\n".join(lines)


# ── competitors ───────────────────────────────────────────────────────────────

def _render_competitors(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    peers_text: Optional[str] = None,
    **_kw,
) -> str:
    lines = [
        f"## Competitive Landscape - {business_name}",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # Resolve the subject's ticker + PSX sector, then build a REAL side-by-side
    # comparison: default peers = top-5 by market share in the sector, each peer's
    # ratios computed and benchmarked against the subject.
    ticker      = (local_result or {}).get("ticker") or ""
    psx_sector  = getattr(data_summary, "psx_profile", {}) if data_summary else {}
    psx_sector  = (psx_sector.get("sector") if isinstance(psx_sector, dict) else "") or sector

    comparison = ""
    try:
        from intelligence.competitors import build_competitor_comparison
        # subject raw ratios (for the unlisted case where there's no ticker)
        raw = (local_result or {}).get("_raw", {}) if local_result else {}
        target_metrics = {
            "net_margin":   raw.get("net_margin"),
            "gross_margin": raw.get("gross_margin"),
            "roe":          raw.get("roe"),
            "de":           raw.get("debt_to_equity"),
            "current_ratio": raw.get("current_ratio"),
        }
        comparison = build_competitor_comparison(
            ticker, psx_sector, target_name=business_name, target_metrics=target_metrics,
        )
    except Exception as _e:
        comparison = ""

    if comparison:
        lines += [comparison, ""]
    elif not psx_sector:
        lines.append(
            "No sector peer comparison available: this company is not PSX-listed and no "
            "sector could be inferred. Provide a PSX ticker or industry to benchmark peers."
        )
    else:
        lines.append(
            f"No PSX peers with usable financials were found in the {psx_sector} sector "
            "to build a comparison."
        )

    # Subject's own position summary (health + Altman) as a quick read
    if local_result:
        health = local_result.get("health_score")
        z      = local_result.get("altman_z")
        lines += [
            "",
            f"### {business_name} - Position Snapshot",
            "",
            f"| Metric | Value | Signal |",
            f"|--------|-------|--------|",
            f"| Health Score | {_num(health, 1)}/10 | {_signal(health, 7.0, 5.0)} |",
            f"| Altman Z-Score | {_num(z, 2) if z else 'N/A'} | {'OK' if z and z >= 2.99 else 'WATCH' if z and z >= 1.81 else 'RISK' if z else 'N/A'} |",
            "",
        ]

    return "\n".join(lines)


# ── overview ──────────────────────────────────────────────────────────────────

def _leadership_governance_section(local_result, data_summary, business_name) -> list:
    """
    Leadership & Governance — merged in from the former Management Quality module.
    Uses financial health as a proxy for management effectiveness and PSX
    announcement frequency as a disclosure-quality signal. No AI.
    """
    r = local_result or {}
    ticker = (r.get("ticker") or "").strip()
    lines = ["### Leadership & Governance", ""]

    # Disclosure quality from PSX announcement cadence.
    ann_count = 0
    if ticker:
        try:
            from database.connection import get_db
            row = get_db().execute(
                "SELECT COUNT(*) c FROM psx_announcements WHERE symbol=?", (ticker.upper(),)
            ).fetchone()
            ann_count = int(row["c"]) if row else 0
        except Exception:
            ann_count = 0

    pts = []
    roe = _raw_float(r.get("roe"))
    if roe is not None and roe >= 0.20:
        pts.append(f"ROE of {roe:.1%} suggests effective capital deployment by management.")
    gm = _raw_float(r.get("gross_margin"))
    if gm is not None and gm >= 0.35:
        pts.append(f"Sustained gross margin ({gm:.1%}) indicates disciplined cost and pricing execution.")
    cr = _raw_float(r.get("current_ratio"))
    if cr is not None and cr >= 1.5:
        pts.append(f"Healthy current ratio ({cr:.2f}×) reflects prudent working-capital management.")
    de = _raw_float(r.get("debt_to_equity"))
    if de is not None and de > 2:
        pts.append(f"Elevated leverage ({de:.2f}× D/E) warrants scrutiny of capital-structure decisions.")
    health = _raw_float(r.get("health_score"))
    if health is not None and health < 5:
        pts.append(f"Financial-health score of {health:.1f}/10 raises questions about strategic execution.")

    if pts:
        lines += [f"- {p}" for p in pts]
    else:
        lines.append("- Insufficient computed ratios to infer management effectiveness.")

    if ticker:
        cadence = ("active" if ann_count >= 20 else "standard" if ann_count >= 5 else "limited")
        lines.append(f"- Disclosure cadence: {ann_count} PSX announcement(s) on record - {cadence} investor communication.")
    else:
        lines.append("- Board/executive detail requires uploaded filings - not available for unlisted companies without documents.")
    lines.append("")
    return lines


def _render_overview(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    **_kw,
) -> str:
    lines = [
        f"## Business Overview - {business_name}",
        "",
        _source_line("PSX profile + financial statements + company disclosures"),
        "",
    ]

    # Profile from website scrape
    website_snippet = getattr(data_summary, "website_snippet", "") or ""
    if website_snippet:
        lines += ["### Digital Presence", ""]
        for ln in _lines_from_text(website_snippet, 6):
            lines.append(ln)
        lines.append("")

    # ── Leadership & Governance (merged from the former Management Quality module) ──
    lines += _leadership_governance_section(local_result, data_summary, business_name)

    # Health summary from financials
    if local_result:
        health  = local_result.get("health_score")
        company = local_result.get("company") or business_name
        ticker  = local_result.get("ticker") or ""
        rev     = local_result.get("revenue")
        ta      = local_result.get("total_assets")
        ni      = local_result.get("net_income")
        roe     = local_result.get("roe")
        gm      = local_result.get("gross_margin")
        de      = local_result.get("debt_equity")
        z       = local_result.get("altman_z")

        lines += [
            f"### Financial Health - {company}" + (f" ({ticker})" if ticker else ""),
            "",
            f"| Indicator | Value |",
            f"|-----------|-------|",
            f"| Health Score | **{_num(health, 1)}/10** |",
            f"| Revenue | {_num(rev, 0)} PKR mn |",
            f"| Total Assets | {_num(ta, 0)} PKR mn |",
            f"| Net Income | {_num(ni, 0)} PKR mn |",
            f"| ROE | {_pct(roe)} |",
            f"| Gross Margin | {_pct(gm)} |",
            f"| Debt/Equity | {_num(de, 2)} |",
            "",
        ]

        # SWOT from computed signals — use _raw_float() to handle pre-formatted strings
        strengths, weaknesses, opportunities, threats = [], [], [], []

        health_f = _raw_float(health)
        if health_f is not None:
            if health_f >= 7.5:
                strengths.append(f"Strong overall financial health (score {health_f:.1f}/10)")
            elif health_f >= 5.0:
                opportunities.append("Financial health improvement potential")
            else:
                weaknesses.append(f"Below-average financial health (score {health_f:.1f}/10)")

        gm_f = _raw_float(gm)
        if gm_f is not None:
            if gm_f >= 0.30:
                strengths.append(f"Healthy gross margins ({gm_f:.1%}) suggest pricing power")
            elif gm_f < 0.10:
                weaknesses.append(f"Thin gross margins ({gm_f:.1%}) - limited buffer for cost increases")

        roe_f = _raw_float(roe)
        if roe_f is not None:
            if roe_f >= 0.20:
                strengths.append(f"Strong ROE ({roe_f:.1%}) - efficient use of shareholder capital")
            elif roe_f < 0.08:
                weaknesses.append(f"Low ROE ({roe_f:.1%}) - suboptimal capital utilisation")

        de_f = _raw_float(de)
        if de_f is not None:
            if de_f <= 0.5:
                strengths.append(f"Conservative leverage (D/E {de_f:.2f}) - financial flexibility")
            elif de_f >= 2.0:
                threats.append(f"High leverage (D/E {de_f:.2f}) - elevated financial risk")

        z_f = _raw_float(z)
        if z_f is not None:
            if z_f >= 2.99:
                strengths.append(f"Altman Z-Score {z_f:.2f} in safe zone")
            elif z_f < 1.81:
                threats.append(f"Altman Z-Score {z_f:.2f} in distress zone - monitor closely")

        # Revenue growth
        rev_growth = local_result.get("revenue_growth")
        rev_growth_f = _raw_float(rev_growth)
        if rev_growth_f is not None:
            if rev_growth_f >= 0.15:
                strengths.append(f"Strong revenue growth ({rev_growth_f:+.1%} YoY)")
                opportunities.append("Growth momentum supports market expansion")
            elif rev_growth_f < -0.05:
                weaknesses.append(f"Revenue contraction ({rev_growth_f:.1%} YoY)")
                threats.append("Declining top-line may compress margins further")

        if sector:
            opportunities.append(f"Sector context: {sector}")

        lines.append("### SWOT Analysis _(derived from computed financial signals)_")
        lines.append("")
        if strengths:
            lines.append("**Strengths**")
            for s in strengths:
                lines.append(f"- {s}")
        if weaknesses:
            lines.append("**Weaknesses**")
            for w in weaknesses:
                lines.append(f"- {w}")
        if opportunities:
            lines.append("**Opportunities**")
            for o in opportunities:
                lines.append(f"- {o}")
        if threats:
            lines.append("**Threats**")
            for t in threats:
                lines.append(f"- {t}")
        lines.append("")

    return "\n".join(lines)


# ── marketing ─────────────────────────────────────────────────────────────────

def _render_marketing(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    **_kw,
) -> str:
    lines = [
        f"## Digital Presence - {business_name}",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    website_snippet = getattr(data_summary, "website_snippet", "") or ""
    if website_snippet:
        lines += ["### Website Intelligence", ""]
        for ln in _lines_from_text(website_snippet, 10):
            lines.append(ln)
        lines.append("")
    else:
        lines.append("No website content was scraped during Stage 1 data collection.")
        lines.append("")

    search_snippets = getattr(data_summary, "search_snippets", "") or ""
    if search_snippets:
        lines += ["### Online Presence Signals (Search)", ""]
        for ln in _lines_from_text(search_snippets, 6):
            lines.append(ln)
        lines.append("")

    lines += [
        "### Marketing Data Limitations",
        "",
        "- Social media follower counts: not available from local data sources",
        "- Ad spend estimates: require third-party data subscription",
        "- Brand mention volume: requires extended web crawl",
        "- SEO domain authority: requires external API (Moz/Ahrefs)",
        "",
        "_Upload digital marketing reports or social analytics exports "
        "to enable deeper analysis._",
    ]

    return "\n".join(lines)


# ── country ───────────────────────────────────────────────────────────────────

def _render_country(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    extra_context: str = "",
    **_kw,
) -> str:
    lines = [
        "## Pakistan Country & Regulatory Environment",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # Macro data
    try:
        from database.macro_data import get_macro_summary_text
        macro = get_macro_summary_text("general")
        if macro:
            lines += ["### Macro Indicators", ""]
            for ln in _lines_from_text(macro, 12):
                lines.append(ln)
            lines.append("")
    except Exception:
        pass

    # Sector-specific macro
    if industry_tag and industry_tag != "general":
        try:
            from database.macro_data import get_macro_summary_text
            sector_macro = get_macro_summary_text(industry_tag)
            if sector_macro:
                lines += [f"### Sector-Specific ({industry_tag.title()}) Context", ""]
                for ln in _lines_from_text(sector_macro, 8):
                    lines.append(ln)
                lines.append("")
        except Exception:
            pass

    # (Policy/regulatory headlines are consolidated in the World News module —
    #  not repeated here — to avoid the same digest appearing in every section.)
    lines += [
        "### Pakistan Regulatory Landscape",
        "",
        "- **SECP** - Securities and Exchange Commission regulates listed companies, "
        "non-banking finance, insurance",
        "- **SBP** - State Bank of Pakistan regulates banking sector and monetary policy",
        "- **FBR** - Federal Board of Revenue administers tax collection (income, sales, customs)",
        "- **OGRA / NEPRA** - Sector-specific regulators for oil/gas and power",
        "- **PSX** - Listed companies subject to disclosure and corporate governance requirements",
        "",
        "_Regulatory risk details require document uploads or government scraper data._",
    ]

    return "\n".join(lines)


# ── supplychain ───────────────────────────────────────────────────────────────

def _render_supplychain(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    **_kw,
) -> str:
    lines = [
        f"## Supply Chain Analysis - {business_name}",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # If documents were uploaded, surface what was found
    doc_text = getattr(data_summary, "document_text", "") or ""
    if doc_text:
        lines += [
            "### Document-Extracted Supply Chain Data",
            "_Financial statements and uploaded documents were analysed_",
            "",
        ]
        for ln in _lines_from_text(doc_text, 6):
            lines.append(ln)
        lines.append("")

    # Working capital from financial model
    if local_result:
        inv    = local_result.get("inventory")
        rec    = local_result.get("receivables")
        pay    = local_result.get("payables")
        rev    = local_result.get("revenue")

        if any(v is not None for v in [inv, rec, pay]):
            lines += ["### Working Capital Cycle", ""]
            lines.append("| Component | Value (PKR mn) | Days (~) |")
            lines.append("|-----------|----------------|----------|")
            if inv is not None:
                inv_days = (inv / rev * 365) if rev else None
                lines.append(f"| Inventory | {_num(inv, 0)} | {_num(inv_days, 0)} days |")
            if rec is not None:
                rec_days = (rec / rev * 365) if rev else None
                lines.append(f"| Receivables | {_num(rec, 0)} | {_num(rec_days, 0)} days |")
            if pay is not None:
                pay_days = (pay / rev * 365) if rev else None
                lines.append(f"| Payables | {_num(pay, 0)} | {_num(pay_days, 0)} days |")
            lines.append("")

    lines += [
        "### Supply Chain Data Limitations",
        "",
        "- Supplier concentration, geographic distribution, and logistics data require",
        "  uploaded procurement reports or supply chain documents.",
        "- Raw material cost trends sourced from government commodity data when available.",
        "",
        "_Upload supply chain documents or annual reports for deeper analysis._",
    ]

    return "\n".join(lines)


# ── scaling ───────────────────────────────────────────────────────────────────

def _render_scaling(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    peers_text: Optional[str] = None,
    **_kw,
) -> str:
    lines = [
        f"## Scaling & Growth Potential - {business_name}",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    if local_result:
        rev        = local_result.get("revenue")
        rev_growth = local_result.get("revenue_growth")
        ni         = local_result.get("net_income")
        health     = local_result.get("health_score")
        projs      = local_result.get("revenue_projections") or []

        lines += [
            "### Current Scale",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Revenue | {_num(rev, 0)} PKR mn |",
            f"| Net Income | {_num(ni, 0)} PKR mn |",
            f"| YoY Revenue Growth | {_pct(rev_growth)} |",
            f"| Health Score | {_num(health, 1)}/10 |",
            "",
        ]

        rev_growth_f = _raw_float(rev_growth)
        if rev_growth_f is not None:
            if rev_growth_f >= 0.20:
                lines.append("**High growth trajectory** - above 20% YoY growth signals strong scaling momentum.")
            elif rev_growth_f >= 0.10:
                lines.append("**Healthy growth** - double-digit expansion, sustainable scaling underway.")
            elif rev_growth_f >= 0:
                lines.append("**Modest growth** - single-digit expansion; scaling requires efficiency improvements.")
            else:
                lines.append("**Revenue contraction** - scaling strategy should focus on retention before expansion.")
            lines.append("")

        if projs:
            lines += ["### 3-Year Revenue Projections (PKR mn)", ""]
            lines.append("| Year | Conservative | Base | Optimistic |")
            lines.append("|------|-------------|------|------------|")
            for p in projs:
                lines.append(
                    f"| {p.get('year','-')} "
                    f"| {_num(p.get('conservative'), 0)} "
                    f"| {_num(p.get('base'), 0)} "
                    f"| {_num(p.get('optimistic'), 0)} |"
                )
            lines.append("")

    if peers_text:
        lines += ["### Sector Scaling Benchmarks", ""]
        lines += ["```", peers_text, "```", ""]

    lines += [
        "### Scaling Factors to Assess",
        "",
        "- **Capital requirements**: Determine capex intensity from asset base vs revenue",
        "- **Market headroom**: Sector size and growth rate vs current revenue share",
        "- **Operational leverage**: Fixed cost base and how margins improve with volume",
        "- **Management bandwidth**: Leadership capacity for managing scaled operations",
        "",
        "_For detailed market sizing, upload industry reports or enable government data scrapers._",
    ]

    return "\n".join(lines)


# ── worldnews ─────────────────────────────────────────────────────────────────

def _render_worldnews(
    local_result: Optional[dict],
    data_summary: Any,
    business_name: str,
    sector: str = "",
    industry_tag: str = "",
    **_kw,
) -> str:
    lines = [
        f"## Global News & Sector Intelligence",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # This is the news home — show the full relevance-ranked digest here (only here).
    bullets = _news_bullets(data_summary, 15)
    analyst = getattr(data_summary, "analyst_note", "") or ""
    if bullets:
        lines += ["### Curated News Digest (relevance-ranked)",
                  "_Filtered to the company and its sector's drivers._", ""]
        for ln in bullets:
            lines.append(f"- {ln}")
        lines.append("")
    else:
        lines += ["### Curated News Digest", "", _no_relevant_news_line(data_summary), ""]
    if analyst:
        lines += [f"**Analyst note:** {analyst}", ""]

    search_snippets = getattr(data_summary, "search_snippets", "") or ""
    if search_snippets:
        lines += ["### Search Intelligence", ""]
        for ln in _lines_from_text(search_snippets, 10):
            lines.append(ln)
        lines.append("")

    return "\n".join(lines)


# ── generic qualitative fallback ─────────────────────────────────────────────

def _render_generic(
    local_result: Optional[dict] = None,
    data_summary: Any = None,
    business_name: str = "",
    sector: str = "",
    **_kw,
) -> str:
    lines = [
        f"## Analysis - {business_name}",
        "",
        _source_line("PSX market, financial statements and macro data"),
        "",
    ]

    # NOTE: framework/qualitative extended modules do NOT re-dump the news digest —
    # that lives in the World News module only (avoids the same brief appearing in
    # every section). They use company-specific research + financial signals instead.
    search_snippets = getattr(data_summary, "search_snippets", "") or ""
    website_snippet = getattr(data_summary, "website_snippet", "") or ""

    if search_snippets:
        lines += ["### Research Findings", ""]
        for ln in _lines_from_text(search_snippets, 6):
            lines.append(ln)
        lines.append("")

    if website_snippet:
        lines += ["### Company Digital Presence", ""]
        for ln in _lines_from_text(website_snippet, 4):
            lines.append(ln)
        lines.append("")

    if local_result:
        lines += [
            "### Financial Signals",
            "",
            f"Health Score: {_num(local_result.get('health_score'), 1)}/10",
            f"Net Margin: {_pct(local_result.get('net_margin'))}  ·  ROE: {_pct(local_result.get('roe'))}",
        ]

    if not search_snippets and not website_snippet and not local_result:
        lines.append(
            "No company-specific data was collected for this module in Stage 1. "
            "To enable it: run a full web-search scan, upload documents, or provide a PSX ticker."
        )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# RICH QUALITATIVE RENDERERS — real analytical prose from local data, zero AI
# ══════════════════════════════════════════════════════════════════════════════

SECTOR_REGULATORS = {
    "banking":    ("SBP", "CAR compliance, NPL thresholds, AML requirements, digital-banking licensing"),
    "pharma":     ("DRAP", "drug registration, pricing controls, GMP standards, API import regulation"),
    "cement":     ("SECP / Competition Commission", "price-coordination scrutiny, environmental (SEPA) standards"),
    "oil_gas":    ("OGRA", "gas/petroleum pricing, exploration licensing, circular-debt exposure"),
    "energy":     ("NEPRA", "tariff determinations, capacity-payment obligations, transmission access"),
    "textile":    ("SECP / FBR", "export-rebate (DLTL) compliance, sales tax on energy, GSP+ conformity"),
    "tech":       ("SECP / PTA", "data protection, IT-export tax regime, telecom regulation"),
    "fertilizer": ("SECP / MNFSR", "gas-feedstock allocation, urea price notifications, GIDC"),
    "auto":       ("EDB / FBR", "localisation policy, import duty on CKD kits, auto financing rules"),
    "fmcg":       ("PSQCA / FBR", "product standards, FED/sales tax, food-safety regulation"),
    "sugar":      ("Competition Commission / provincial cane boards", "support-price setting, export permission"),
    "steel":      ("NTC / FBR", "anti-dumping duties, energy tariff, import regulation"),
}

DIGITAL_MATURITY_BY_SECTOR = {
    "tech":       "Digital maturity is the core business model - revenue per employee, contract pipeline and cloud/SaaS mix are the key metrics.",
    "banking":    "Digital banking adoption is the key competitive differentiator - mobile banking, digital payments and API/open banking are sector priorities.",
    "pharma":     "Digital maturity in pharma centres on supply-chain traceability, regulatory e-submissions (DRAP) and digital detailing to prescribers.",
    "cement":     "Cement digital maturity typically centres on plant automation, logistics/fleet optimisation and dealer-management systems.",
    "textile":    "Export textile firms increasingly need digital traceability for EU/US sustainability compliance, plus ERP-driven order management.",
    "oil_gas":    "E&P uses digital for seismic-data processing, predictive maintenance and reservoir modelling; OMCs for retail network telemetry.",
    "energy":     "Power-sector digital maturity centres on SCADA/plant telemetry, smart metering and outage analytics.",
    "fertilizer": "Digital focus is on dealer/farmer engagement platforms, plant reliability analytics and demand forecasting.",
    "fmcg":       "FMCG digital maturity centres on route-to-market/distributor apps, demand sensing and D2C/e-commerce channels.",
}


def _macro_ctx():
    try:
        from database.macro_data import get_latest_macro
        m = get_latest_macro() or {}
        return m.get("sbp_policy_rate"), m.get("cpi"), m.get("gdp_growth")
    except Exception:
        return None, None, None


def _sector_peer_count(sector: str) -> int:
    if not sector:
        return 0
    try:
        from database.connection import get_db
        r = get_db().execute(
            "SELECT COUNT(*) c FROM psx_companies WHERE sector_name=? AND is_debt=0", (sector,)
        ).fetchone()
        return int(r["c"]) if r else 0
    except Exception:
        return 0


def _news_signals(data_summary):
    """(tailwind_count, headwind_count, top_headline) parsed from the local brief."""
    brief = getattr(data_summary, "news_brief", "") or ""
    tw = brief.count("(tailwind)")
    hw = brief.count("(headwind)")
    top = ""
    _skip = ("news intelligence", "region", "market sentiment", "key themes",
             "analyst", "recent headlines", "critical developments")
    for line in brief.split("\n"):
        s = line.strip().lstrip("- ").strip()
        if s and not any(s.lower().startswith(p) for p in _skip):
            top = s.split("  (")[0].strip()
            break
    return tw, hw, top


def _pct_of(local_result: Optional[dict], key: str):
    """Return a ratio field as a PERCENT number (15.5 for 15.5%), or None."""
    f = _raw_float((local_result or {}).get(key))
    return round(f * 100, 1) if f is not None else None


def _source_line(source: str) -> str:
    """Rule 2 — every module opens with a data-source line."""
    from datetime import date
    return f"Source: {source} · Computed locally · {date.today():%B %Y}"


def _needs(module_name: str, required: str) -> str:
    """Rule 3 — explicit 'what is needed' message instead of empty/generic text."""
    return (f"**{module_name}:** Insufficient data to compute this section.\n\n"
            f"_Required: {required}_")


def _module_ctx(local_result, data_summary, business_name, sector, industry_tag) -> dict:
    r = local_result or {}
    sbp, cpi, gdp = _macro_ctx()
    tw, hw, top = _news_signals(data_summary)
    _prof = getattr(data_summary, "psx_profile", {}) if data_summary else {}
    psx_sector = (_prof.get("sector") if isinstance(_prof, dict) else "") or ""
    sec = (psx_sector or sector or industry_tag or "").strip().lower()
    ticker = (r.get("ticker") or "").strip()
    return {
        "business_name": business_name,
        "sector":        sec,
        "psx_sector":    psx_sector,
        "ticker":        ticker,
        "is_listed":     bool(ticker),
        "health":        _raw_float(r.get("health_score")),
        "net_margin":    _pct_of(r, "net_margin"),
        "gross_margin":  _pct_of(r, "gross_margin"),
        "roe":           _pct_of(r, "roe"),
        "roa":           _pct_of(r, "roa"),
        "current_ratio": _raw_float(r.get("current_ratio")),
        "de":            _raw_float(r.get("debt_to_equity")),
        "altman_z":      _raw_float(r.get("altman_z")),
        "z_zone":        r.get("altman_zone", ""),
        "revenue":       r.get("revenue"),
        "sbp":           sbp, "cpi": cpi, "gdp": gdp,
        "tailwind": tw, "headwind": hw, "top_headline": top,
        "peer_count":    _sector_peer_count(psx_sector),
    }


def _news_signal_line(c: dict) -> str:
    if c["tailwind"] or c["headwind"] or c["top_headline"]:
        line = (f"_News signals (relevance-filtered): {c['tailwind']} tailwind, "
                f"{c['headwind']} headwind.")
        if c["top_headline"]:
            line += f" Top signal: {c['top_headline']}_"
        else:
            line += "_"
        return line
    return "_No company- or sector-specific news signals in this window._"


def _render_pestle(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    reg, risk = SECTOR_REGULATORS.get(c["sector"], ("SECP", "standard corporate governance"))
    L = [f"## PESTLE Analysis - {business_name}", "",
         _source_line("SBP macro data + PBS statistics + static compliance rules"), ""]

    # Political
    sbp = c["sbp"]
    if sbp is None:
        pol = "Monetary-policy data unavailable for this window."
    elif sbp > 15:
        pol = f"Highly restrictive monetary environment (SBP rate {sbp:g}%) limits access to affordable credit; floating-rate borrowers face a heavy interest burden."
    elif sbp > 10:
        pol = f"Elevated policy rate ({sbp:g}%) constrains borrowing costs - any easing cycle would give meaningful relief to leveraged firms."
    elif sbp > 6:
        pol = f"Moderately accommodative policy ({sbp:g}%); credit conditions support operational financing."
    else:
        pol = f"Low policy-rate environment ({sbp:g}%) supports business investment and expansion financing."
    L += ["### Political", pol, f"Sector oversight sits with **{reg}** ({risk}).", ""]

    # Economic
    cpi, gdp = c["cpi"], c["gdp"]
    if cpi is None:
        eco = "Inflation data unavailable for this window."
    elif cpi > 20:
        eco = f"Severe inflation (CPI {cpi:.1f}%) erodes purchasing power and compresses real margins for cost-pass-through-challenged businesses."
    elif cpi > 10:
        eco = f"Elevated inflation ({cpi:.1f}%) demands tight cost management; pricing power is a key differentiator."
    elif cpi > 5:
        eco = f"Moderate inflation ({cpi:.1f}%) within a manageable range" + (f"; GDP growth of {gdp:.1f}% suggests stable demand." if gdp else ".")
    else:
        eco = f"Stable price environment (CPI {cpi:.1f}%) supports consumer spending and margin predictability."
    L += ["### Economic", eco, ""]

    # Legal (L) — populated automatically from the static compliance knowledge base.
    L += ["### Legal"]
    try:
        from intelligence.compliance_rules import get_compliance_profile
        profile = get_compliance_profile(c["psx_sector"] or c["sector"], is_listed=c["is_listed"])
        sp = profile.get("sector_specific", {}) or {}
        L.append(f"- **Primary regulator:** {profile.get('regulator')}")
        for law in (sp.get("governing_laws", []) or profile.get("governing_laws", []))[:4]:
            L.append(f"- **Governing law:** {law}")
        for rk in (sp.get("key_risks", []))[:3]:
            L.append(f"- **Legal/regulatory risk:** {rk}")
    except Exception:
        L.append(f"- {reg} governs {risk}.")
    L.append("")

    # Social / Technological / Environmental — sector-tailored
    L += ["### Social, Technological & Environmental",
          f"- **Technological:** {DIGITAL_MATURITY_BY_SECTOR.get(c['sector'], 'Digital adoption is an emerging differentiator; upload company materials to assess.')}",
          f"- **Environmental:** {_esg_env_line(c['sector'])}",
          "- **Social:** demand is shaped by Pakistan's consumption patterns and the inflation backdrop noted above.",
          "", _news_signal_line(c)]
    return "\n".join(L)


def _esg_env_line(sector: str) -> str:
    return {
        "cement":     "CO₂ intensity and coal usage are the dominant environmental exposures; carbon/coal-price policy is material.",
        "energy":     "fuel mix (coal vs gas vs renewables) and emissions profile drive environmental risk.",
        "textile":    "water usage and effluent treatment (ZLD) are key; EU/US buyers increasingly require compliance.",
        "pharma":     "pharmaceutical-waste handling and effluent management are the main environmental factors.",
        "oil_gas":    "spill/emissions management and flaring are the principal environmental exposures.",
        "banking":    "direct footprint is low; green-financing exposure and portfolio emissions are the emerging lens.",
        "fertilizer": "gas-based emissions and effluent are material; feedstock efficiency is the key lever.",
    }.get(sector, "environmental exposure is moderate and sector-typical; disclosure quality varies.")


def _render_porters(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    reg, _risk = SECTOR_REGULATORS.get(c["sector"], ("SECP", ""))
    L = [f"## Porter's Five Forces - {business_name}", ""]

    gm = c["gross_margin"]
    if gm is None:      sup = "Gross-margin data unavailable - supplier power cannot be inferred from financials."
    elif gm >= 40:      sup = f"Gross margin of {gm:.1f}% indicates manageable input-cost exposure and intact pricing power over suppliers."
    elif gm >= 25:      sup = f"Moderate gross margin ({gm:.1f}%) suggests balanced supplier dynamics; input-cost movements warrant monitoring."
    elif gm >= 10:      sup = f"Compressed gross margin ({gm:.1f}%) signals meaningful supplier pricing power or raw-material vulnerability."
    else:               sup = f"Very thin gross margin ({gm:.1f}%) points to severe supplier leverage or a structural cost disadvantage."
    L += ["### Supplier Power", sup, ""]

    nm = c["net_margin"]
    if nm is None:      buy = "Net-margin data unavailable - buyer power cannot be inferred."
    elif nm >= 15:      buy = f"Net margin of {nm:.1f}% suggests retained pricing authority with customers; assess revenue concentration via the annual report."
    elif nm >= 8:       buy = f"Adequate net margin ({nm:.1f}%) indicates moderate buyer influence - competitive pricing pressure exists but is manageable."
    else:               buy = f"Low net margin ({nm:.1f}%) may reflect significant buyer power or an inability to pass cost increases downstream."
    L += ["### Buyer Power", buy, ""]

    pc = c["peer_count"]
    if pc >= 10:        riv = f"The sector has {pc} listed peers - high competitive intensity; differentiation and cost efficiency are critical."
    elif pc >= 5:       riv = f"Moderate competitive landscape with {pc} listed peers."
    elif pc > 0:        riv = f"Limited listed competition ({pc} peers) may indicate entry barriers or niche positioning."
    else:               riv = "Peer set could not be resolved from PSX data."
    L += ["### Competitive Rivalry", riv, ""]

    L += ["### Threat of New Entrants & Substitutes",
          f"Entry is gated by **{reg}** licensing/approvals and by "
          + {"cement": "the heavy capital intensity of clinker capacity",
             "banking": "SBP capital and licensing requirements",
             "pharma": "DRAP registration and GMP build-out",
             "energy": "NEPRA generation licensing and long gestation",
             "oil_gas": "exploration-licence access and capital scale",
            }.get(c["sector"], "capital requirements and regulatory approval") + ".",
          "", _news_signal_line(c)]
    return "\n".join(L)


def _render_regulatory(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    """
    Rule 4 — this module ALWAYS runs from intelligence/compliance_rules.py (static,
    deterministic, no AI, no uploaded documents required). Red-flag signals from PSX
    announcements (listed) and/or uploaded documents (listed + unlisted) are surfaced
    at the very top before the compliance profile.
    """
    from intelligence.compliance_rules import (
        get_compliance_checklist_text,
        check_compliance_signals_from_announcements,
        check_compliance_signals_from_documents,
    )
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    sector_str = c["psx_sector"] or c["sector"]
    is_listed = c["is_listed"]

    signals: list = []
    # Listed → scan the company's PSX announcements from the DB.
    if is_listed and c["ticker"]:
        try:
            from database.connection import get_db
            rows = get_db().execute(
                "SELECT title FROM psx_announcements WHERE symbol=? ORDER BY ann_date DESC LIMIT 40",
                (c["ticker"].upper(),),
            ).fetchall()
            ann = [r["title"] for r in rows if r["title"]]
            signals += check_compliance_signals_from_announcements(ann, sector_str)
        except Exception:
            pass
    # Listed + unlisted → scan uploaded document text.
    doc_text = getattr(data_summary, "document_text", "") or ""
    if doc_text:
        signals += check_compliance_signals_from_documents(doc_text, sector_str, is_listed)

    out: list[str] = []
    if signals:
        crit = [s for s in signals if s.get("severity") == "critical"]
        high = [s for s in signals if s.get("severity") == "high"]
        med  = [s for s in signals if s.get("severity") == "medium"]
        out.append("### Compliance Signals Detected")
        for s in crit:
            out.append(f"- **CRITICAL - {s['signal']}**: {(s.get('context') or s.get('text','') )[:150]}")
        for s in high:
            out.append(f"- **HIGH - {s['signal']}**: {(s.get('context') or s.get('text',''))[:120]}")
        for s in med:
            out.append(f"- Medium - {s['signal']}")
        out.append("")

    out.append(get_compliance_checklist_text(sector_str, business_name, is_listed=is_listed))
    return "\n".join(out)


def _render_management(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    L = [f"## Management Quality - {business_name}", "",
         "_Financial signals used as proxies for execution quality._", ""]
    pts = []
    if c["roe"] is not None and c["roe"] > 20:
        pts.append(f"ROE of {c['roe']:.1f}% suggests effective capital deployment by management.")
    if c["gross_margin"] is not None and c["gross_margin"] > 35:
        pts.append(f"Sustained gross margin above 35% ({c['gross_margin']:.1f}%) indicates disciplined cost management and pricing execution.")
    if c["current_ratio"] is not None and c["current_ratio"] > 1.5:
        pts.append(f"Healthy current ratio ({c['current_ratio']:.2f}×) suggests prudent working-capital management.")
    if c["de"] is not None and c["de"] > 2:
        pts.append(f"Elevated leverage ({c['de']:.2f}× D/E) warrants scrutiny of capital-structure decisions.")
    if c["health"] is not None and c["health"] < 5:
        pts.append(f"Overall financial-health score of {c['health']:.1f}/10 raises questions about strategic execution.")
    if not pts:
        pts.append("Insufficient computed ratios to infer management quality - upload the annual report to enable a fuller read.")
    L += [f"- {p}" for p in pts]
    L += ["", _news_signal_line(c)]
    return "\n".join(L)


def _render_digital(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    ctx = DIGITAL_MATURITY_BY_SECTOR.get(
        c["sector"], "Digital-maturity assessment requires website/operational data - upload company materials to enable.")
    L = [f"## Digital Maturity - {business_name}", "", ctx, ""]
    website = getattr(data_summary, "website_snippet", "") or ""
    if website:
        L += ["### Observed digital presence", ""]
        for ln in _lines_from_text(website, 4):
            L.append(f"- {ln}")
    L += ["", _news_signal_line(c)]
    return "\n".join(L)


def _render_esg(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    L = [f"## ESG Assessment - {business_name}", "",
         "### Environmental", _esg_env_line(c["sector"]), ""]
    if c["health"] is not None and c["health"] >= 7:
        soc = f"Financial-strength score of {c['health']:.1f}/10 suggests capacity to invest in workforce and community programmes."
    elif c["health"] is not None:
        soc = f"Financial constraints (score {c['health']:.1f}/10) may limit discretionary social investment."
    else:
        soc = "Social-investment capacity depends on financial strength, which could not be computed here."
    L += ["### Social", soc, "",
          "### Governance",
          "Governance quality is proxied by disclosure cadence and the leverage/return profile above; review board composition and related-party disclosures in the annual report.",
          "", _news_signal_line(c)]
    return "\n".join(L)


def _render_bmc(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    gm = c["gross_margin"]
    if gm is None:      rev_model = "Revenue model unclear without margin data."
    elif gm > 50:       rev_model = "High-margin product/service mix."
    elif gm > 30:       rev_model = "Mixed product and service revenue."
    else:               rev_model = "Volume-driven, lower-margin revenue model."
    L = [f"## Business Model Canvas - {business_name}", "",
         "### Value Proposition",
         f"As a {c['sector'] or 'listed'} company, core value delivery centres on "
         + {"cement": "reliable bulk supply, distribution reach and cost leadership",
            "banking": "deposit mobilisation, credit intermediation and transaction services",
            "pharma": "quality-assured formulations, regulatory approvals and prescriber trust",
            "textile": "cost-competitive, compliant export manufacturing",
            "oil_gas": "reserve replacement and reliable hydrocarbon supply",
           }.get(c["sector"], "its core sector offering") + ".",
         "",
         "### Revenue Streams",
         (f"Revenue of {_num(c['revenue'], 0)} (latest FY). {rev_model}" if c["revenue"] else rev_model),
         "",
         "### Cost Structure",
         (f"Gross margin {gm:.1f}% implies " + ("an input-cost-heavy model." if gm < 30 else "a balanced cost base." if gm < 50 else "a services/brand-led, lighter cost base.")) if gm is not None else "Cost structure requires income-statement detail.",
         "", _news_signal_line(c)]
    return "\n".join(L)


def _render_scenario(local_result, data_summary, business_name, sector="", industry_tag="", **_kw):
    c = _module_ctx(local_result, data_summary, business_name, sector, industry_tag)
    sbp = c["sbp"]; cpi = c["cpi"]; gdp = c["gdp"]
    rev = _num(c["revenue"], 0) if c["revenue"] else "the latest reported level"
    base = (f"Revenue near {rev} holds. SBP rate {sbp:g}% and CPI {cpi:.1f}% maintained; "
            f"demand tracks GDP growth of {gdp:.1f}%." ) if (sbp and cpi and gdp) else f"Revenue near {rev} holds under current macro conditions."
    sector_bull = {"cement": " Higher PSDP allocations lift dispatches.",
                   "pharma": " DRAP price relief and stable API costs aid margins.",
                   "banking": " Credit growth accelerates as rates ease."}.get(c["sector"], "")
    sector_bear = {"cement": " PSDP cuts and higher coal prices squeeze margins.",
                   "pharma": " Rupee weakness raises imported-API costs.",
                   "banking": " Credit growth decelerates and NPLs tick up."}.get(c["sector"], "")
    L = [f"## Scenario Planning - {business_name}", "",
         "### Base Case", base, "",
         "### Bull Case",
         "A 200–300bps SBP easing cycle boosts margins; GDP growth accelerates and sector demand expands - revenue growth runs ~15–20% above base." + sector_bull, "",
         "### Bear Case",
         "The policy rate is held or raised, currency depreciation adds import-cost pressure and energy/circular-debt costs rise - growth stalls and margins compress ~200–400bps." + sector_bear,
         "", _news_signal_line(c)]
    return "\n".join(L)


# Module renderer dispatch table
# The active 12-module set (marketing, supplychain, porters, bmc, scenario,
# digital_maturity and management_quality were removed; management folded into overview).
_LOCAL_RENDERERS = {
    "overview":    _render_overview,
    "competitors": _render_competitors,
    "financial":   _render_financial,
    "valuation":   _render_valuation,
    "sentiment":   _render_sentiment,
    "economy":     _render_economy,
    "country":     _render_country,
    "worldnews":   _render_worldnews,
    "pestle":              _render_pestle,
    "regulatory_risk":     _render_regulatory,
    "esg":                 _render_esg,
    "scaling":     _render_scaling,
}


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI FALLBACK — called when OutputValidator rejects a response twice
# ══════════════════════════════════════════════════════════════════════════════

_FALLBACK_TEMPLATES: dict[str, str] = {
    "financial": (
        "{{ company }} operates in the {{ sector }} sector. "
        "The current ratio stands at {{ current_ratio }}, indicating "
        "{% if current_ratio_raw and current_ratio_raw|float >= 1.5 %}adequate{% else %}tight{% endif %} "
        "short-term liquidity. "
        "Debt-to-equity is {{ debt_equity }}, and the Altman Z-Score of {{ altman_z }} "
        "{% if altman_z_raw and altman_z_raw|float >= 2.99 %}suggests low distress risk"
        "{% elif altman_z_raw and altman_z_raw|float >= 1.81 %}suggests moderate distress risk"
        "{% else %}warrants close monitoring{% endif %}. "
        "Return on equity is {{ roe }}, reflecting the business's capital efficiency."
    ),
    "valuation": (
        "{{ company }} trades at a price-to-earnings ratio of {{ pe_ratio }} "
        "against a sector median of {{ sector_pe }}. "
        "The discounted cash flow model yields an intrinsic value of {{ dcf_value }}, "
        "compared to the current market price of {{ market_price }}."
    ),
    "sentiment": (
        "Recent market sentiment around {{ company }} is {{ sentiment_label }} "
        "with {{ sentiment_confidence }} confidence based on {{ headline_count }} "
        "relevant news items tracked over the analysis window."
    ),
    "economy": (
        "The macroeconomic environment shows a {{ trend_direction }} trend for the {{ sector }} sector. "
        "The PKR/USD rate is {{ fx_rate }}, and the PSX KSE-100 index stands at {{ kse100 }}. "
        "SBP policy rate is {{ policy_rate }}, influencing borrowing costs."
    ),
    "default": (
        "Analysis for {{ company }} in the {{ sector }} sector has been compiled "
        "based on available data. Key metrics: current ratio {{ current_ratio }}, "
        "Altman Z-Score {{ altman_z }}."
    ),
}


def render_fallback(
    module_name: str,
    token_sheet: TokenSheet,
    extra_context: dict | None = None,
) -> str:
    """
    Render a token-sheet-based fallback template for the given module.
    Used after Gemini OutputValidator rejects a response twice.
    Falls back to 'default' template if module not found.
    """
    template_str = _FALLBACK_TEMPLATES.get(module_name, _FALLBACK_TEMPLATES["default"])
    tmpl = _env.from_string(template_str)

    ctx: dict[str, str] = {}
    with token_sheet._lock:
        for token, value in token_sheet._reverse.items():
            raw = token.strip("{}").lower().replace("token_", "")
            ctx[raw] = value
            ctx[raw + "_raw"] = value

    if extra_context:
        ctx.update(extra_context)

    try:
        rendered = tmpl.render(**ctx)
    except Exception:
        rendered = (
            f"Analysis for {ctx.get('company', '[company]')} "
            f"is available in the data sources panel."
        )

    return rendered
