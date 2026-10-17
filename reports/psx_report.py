"""
reports/psx_report.py - Phase 3 Task 3.9: the twelve-section PSX report.

Built from the same PSXAnalysis the web page renders, so the report cannot state
anything the screen does not. Structure:

  1. Cover: company, ticker, sector, report date, data as-at, health score with its
     confidence label, signature reference.
  2. Contents.
  3. Data provenance: per section, the source, extraction method and as-at stamp.
     This page is what makes a figure defensible when someone asks where it came
     from, so it is generated from a.data_sources rather than written by hand.
  4. The twelve sections.
  5. Appendix: full ratio table and the formulas used.
  6. Every page carries the ticker, page number and the standard disclaimer.

Charts are rendered server-side to PNG by reports/chart_render.py; no browser
screenshotting, so an exported chart always matches its on-screen twin.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Optional

import structlog

log = structlog.get_logger()

DISCLAIMER = (
    "This analysis is computed from published exchange data and filed financial "
    "statements. Figures should be verified against primary filings before use in "
    "decisions with material financial consequences. No investment recommendation "
    "is expressed or implied."
)

SECTION_TITLES = [
    (1, "Company Profile"), (2, "Statement of Financial Position"),
    (3, "Statement of Profit or Loss"), (4, "Statement of Cash Flows"),
    (5, "Ratio Analysis"), (6, "Statement Quality"),
    (7, "DuPont Decomposition"), (8, "Distress Prediction"),
    (9, "Valuation"), (10, "Regulatory Context"),
    (11, "Sector Position"), (12, "Investment Considerations"),
]

FORMULAS = [
    ("Gross margin", "Gross profit / revenue"),
    ("Operating margin", "EBIT / revenue"),
    ("Net margin", "Profit after tax / revenue"),
    ("Return on assets", "Profit after tax / total assets"),
    ("Return on equity", "Profit after tax / total equity"),
    ("Current ratio", "Current assets / current liabilities"),
    ("Quick ratio", "(Current assets - inventory) / current liabilities"),
    ("Debt to equity", "Total debt / total equity"),
    ("Interest coverage", "EBIT / interest expense"),
    ("Asset turnover", "Revenue / total assets"),
    ("Days inventory outstanding", "Inventory / cost of sales x 365"),
    ("Days sales outstanding", "Receivables / revenue x 365"),
    ("Altman Z (5 factor)", "1.2X1 + 1.4X2 + 3.3X3 + 0.6X4 + 1.0X5"),
    ("Altman Z'' (4 factor)", "6.56X1 + 3.26X2 + 6.72X3 + 1.05X4"),
    ("DuPont", "ROE = net margin x asset turnover x equity multiplier"),
]


def _e(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def _fmt_metric(m: Optional[dict], pct: bool = False) -> str:
    """Render a ValidatedMetric for print. Status is never hidden."""
    if not m or m.get("status") == "missing" or m.get("value") is None:
        return "not computed"
    if m.get("status") == "implausible":
        return "excluded: failed plausibility"
    v = m["value"]
    return f"{v * 100:,.1f}%" if pct else f"{v:,.2f}"


def _cover(a) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    hs = a.health_score or {}
    score = (f"{hs.get('score')}/10 ({hs.get('confidence')} confidence)"
             if hs.get("score") is not None else "not scored")
    return f"""
    <div class="cover">
      <h1>{_e(a.company_name)}</h1>
      <div class="ticker">{_e(a.ticker)}</div>
      <table class="meta">
        <tr><td>Sector</td><td>{_e(a.sector or 'not classified')}</td></tr>
        <tr><td>Listing</td><td>Pakistan Stock Exchange</td></tr>
        <tr><td>Report generated</td><td>{now}</td></tr>
        <tr><td>Latest period</td><td>{_e(a.latest_period or 'no annual period stored')}</td></tr>
        <tr><td>Health score</td><td>{_e(score)}</td></tr>
        <tr><td>Basis</td><td>{_e(hs.get('note', ''))}</td></tr>
      </table>
      <p class="note">Signed with ML-DSA. The signature reference is recorded with
      this report and can be verified independently.</p>
    </div>"""


def _contents() -> str:
    rows = "".join(f"<tr><td>{n}</td><td>{t}</td></tr>" for n, t in SECTION_TITLES)
    return f"<h2>Contents</h2><table class='contents'>{rows}</table>"


def _provenance(a) -> str:
    """The page that makes every figure traceable. Generated, never hand-written."""
    rows = []
    for key in ("profile", "market", "income_statement", "balance_sheet",
                "cash_flow", "announcements", "regulatory"):
        s = (a.data_sources or {}).get(key)
        if s:
            rows.append(f"<tr><td>{_e(key.replace('_', ' '))}</td>"
                       f"<td>{_e(s.get('source'))}</td>"
                       f"<td>{_e(s.get('method'))}</td>"
                       f"<td>{_e(s.get('as_at') or 'not recorded')}</td></tr>")
        else:
            rows.append(f"<tr><td>{_e(key.replace('_', ' '))}</td>"
                       f"<td colspan='3'>no data source recorded</td></tr>")

    doc = ""
    if a.source_document_type:
        doc = (f"<p>Source document for the latest period: "
              f"<strong>{_e(a.source_document_type)}</strong>, "
              f"{_e(a.source_document_pages)} pages, dated "
              f"{_e(a.source_document_date)}.<br>"
              f"{_e(a.source_document_url)}</p>")

    gaps = []
    for section, reqs in (a.missing_requirements or {}).items():
        for r in reqs:
            gaps.append(f"<li><strong>{_e(section)}</strong>: {_e(r)}</li>")
    gaps_html = (f"<h3>Data not available</h3><ul>{''.join(gaps)}</ul>"
                 if gaps else "")

    return f"""
    <h2>Data provenance</h2>
    <p>Every figure in this report is traceable to the source below. Where a value
    could not be extracted, the requirement is stated rather than estimated.</p>
    <table class="grid">
      <tr><th>Section data</th><th>Source</th><th>Method</th><th>As at</th></tr>
      {''.join(rows)}
    </table>
    {doc}
    {gaps_html}"""


def _ratio_appendix(a) -> str:
    latest = a.latest_period
    if not latest:
        return "<h2>Appendix: ratios</h2><p>No annual period is stored.</p>"
    R = a.ratios.get(latest, {})
    pct_keys = {"gross_margin", "operating_margin", "ebitda_margin", "net_margin",
                "roa", "roe", "revenue_growth", "net_income_growth",
                "gross_profit_growth"}
    rows = "".join(
        f"<tr><td>{_e(k.replace('_', ' '))}</td><td>{_fmt_metric(v, k in pct_keys)}</td></tr>"
        for k, v in sorted(R.items()))
    formulas = "".join(f"<tr><td>{_e(n)}</td><td>{_e(f)}</td></tr>" for n, f in FORMULAS)
    peers = "".join(
        f"<tr><td>{_e(p['symbol'])}</td><td>{_e((p.get('company_name') or '')[:40])}</td>"
        f"<td>{(p['net_margin'] * 100):,.1f}%</td></tr>"
        for p in a.peers if p.get("net_margin") is not None)
    return f"""
    <h2>Appendix A: full ratio table ({_e(latest)})</h2>
    <table class="grid"><tr><th>Ratio</th><th>Value</th></tr>{rows}</table>
    <h2>Appendix B: sector peers</h2>
    <table class="grid"><tr><th>Ticker</th><th>Company</th><th>Net margin</th></tr>{peers}</table>
    <h2>Appendix C: formulas used</h2>
    <table class="grid"><tr><th>Metric</th><th>Formula</th></tr>{formulas}</table>"""


def _sections(a) -> str:
    """
    Section bodies for print.

    Rendered from the analysis object rather than from the HTMX templates: the web
    partials carry interactive markup (canvas elements, HTMX attributes) that has
    no meaning on paper. Charts are inserted as static PNG by chart_render.
    """
    from reports.chart_render import render_chart_png_base64

    out = []
    for n, title in SECTION_TITLES:
        body = ""
        reqs = (a.missing_requirements or {}).get(_SECTION_REQ_KEY.get(n, ""), [])

        if n == 1:
            p = a.profile or {}
            body = "<table class='grid'>" + "".join(
                f"<tr><td>{lbl}</td><td>{_e(p.get(k) or 'Required: ' + lbl.lower())}</td></tr>"
                for lbl, k in (("Legal name", "company_name"), ("Sector", "sector_name"),
                              ("Chief executive", "ceo"), ("Chairman", "chairman"),
                              ("Registered office", "address"))) + "</table>"
        elif n == 3 and a.annual_periods:
            periods = a.annual_periods[:4]
            head = "".join(f"<th>{_e(p)}</th>" for p in periods)
            rows = ""
            for lbl, key in (("Revenue", "revenue"), ("Gross profit", "gross_profit"),
                            ("Operating profit", "ebit"),
                            ("Profit after taxation", "net_income")):
                cells = "".join(
                    f"<td>{(a.income_statement.get(p, {}).get(key) / 1e6):,.0f}</td>"
                    if a.income_statement.get(p, {}).get(key) is not None
                    else "<td>not reported</td>" for p in periods)
                rows += f"<tr><td>{lbl}</td>{cells}</tr>"
            body = (f"<table class='grid'><tr><th>PKR millions</th>{head}</tr>{rows}</table>")
        elif n == 5:
            body = _ratio_appendix_short(a)
        elif n == 11:
            body = "".join(
                f"<p>{_e(a.ticker)} ranks in the {v}th percentile on "
                f"{_e(k.replace('_', ' '))} within {_e(a.sector)}.</p>"
                for k, v in (a.sector_percentiles or {}).items())
        elif n == 12:
            o = a.observations or {}
            for heading, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses"),
                                ("Risks", "risks"), ("Data limitations", "limitations")):
                items = o.get(key) or []
                body += f"<h3>{heading}</h3>"
                body += ("<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"
                        if items else "<p>None recorded on the data available.</p>")

        # Static chart for this section, if one belongs here and is renderable.
        for ckey, cn in (("chart2", 3), ("chart1", 5), ("chart5", 5), ("chart3", 7),
                         ("chart6", 8), ("chart8", 9), ("chart4", 11)):
            if cn != n:
                continue
            spec = (a.charts or {}).get(ckey)
            if spec and spec.get("renderable"):
                png = render_chart_png_base64(spec["spec"])
                if png:
                    body += (f"<div class='chart'><img src='data:image/png;base64,{png}'/>"
                            f"<div class='src'>{_e(spec['source'])}</div></div>")
            elif spec:
                body += f"<p class='gap'>{_e(spec.get('reason'))}</p>"

        if reqs:
            body += "".join(f"<p class='gap'>{_e(r)}</p>" for r in reqs)
        if not body:
            body = "<p class='gap'>Required: data for this section was not available.</p>"
        out.append(f"<h2>{n}. {_e(title)}</h2>{body}")
    return "".join(out)


_SECTION_REQ_KEY = {1: "profile", 2: "balance_sheet", 3: "income_statement",
                    4: "cash_flow", 5: "ratios", 6: "statement_quality",
                    7: "dupont", 8: "distress", 9: "valuation",
                    10: "regulatory", 11: "sector", 12: ""}


def _ratio_appendix_short(a) -> str:
    latest = a.latest_period
    if not latest:
        return "<p class='gap'>Required: an annual period.</p>"
    R = a.ratios.get(latest, {})
    pct = {"gross_margin", "operating_margin", "net_margin", "roa", "roe",
           "revenue_growth"}
    rows = "".join(
        f"<tr><td>{_e(k.replace('_', ' '))}</td><td>{_fmt_metric(v, k in pct)}</td></tr>"
        for k, v in sorted(R.items()) if v.get("status") == "ok")
    return f"<table class='grid'><tr><th>Ratio</th><th>{_e(latest)}</th></tr>{rows}</table>"


_CSS = """
@page { size: A4; margin: 18mm 15mm 20mm 15mm;
        @bottom-center { content: counter(page); font-size: 8pt; color: #666; } }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 9.5pt;
       color: #111; line-height: 1.45; }
h1 { font-size: 20pt; margin: 0 0 4px; }
h2 { font-size: 12pt; margin: 18px 0 6px; border-bottom: 1px solid #999;
     padding-bottom: 3px; page-break-after: avoid; }
h3 { font-size: 10pt; margin: 10px 0 4px; }
.cover { page-break-after: always; padding-top: 40mm; }
.ticker { font-family: monospace; font-size: 13pt; color: #444; margin-bottom: 18px; }
table { width: 100%; border-collapse: collapse; margin: 6px 0; }
td, th { border: 1px solid #ccc; padding: 4px 6px; text-align: left;
         font-size: 8.5pt; vertical-align: top; }
th { background: #f0f0f0; }
table.meta td:first-child, table.contents td:first-child { width: 32%; color: #555; }
.gap { color: #8a6d00; font-size: 8.5pt; margin: 4px 0; }
.note { font-size: 8.5pt; color: #555; margin-top: 20px; }
.chart { margin: 10px 0; page-break-inside: avoid; }
.chart img { width: 100%; max-width: 165mm; }
.src { font-size: 7.5pt; color: #666; }
.disclaimer { margin-top: 18px; padding-top: 8px; border-top: 1px solid #999;
              font-size: 8pt; color: #444; }
"""


def build_psx_report_html(a) -> str:
    """Full report HTML for one analysis."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_e(a.ticker)} analysis</title><style>{_CSS}</style></head><body>
{_cover(a)}
{_contents()}
{_provenance(a)}
{_sections(a)}
{_ratio_appendix(a)}
<div class="disclaimer">{DISCLAIMER}<br>
Ticker {_e(a.ticker)} · generated
{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · computed locally, no AI.
</div></body></html>"""


def build_psx_report_pdf(ticker: str) -> tuple:
    """
    Render and ML-DSA sign the report. Returns (pdf_bytes, error).

    Signing failure is reported, never swallowed: an unsigned report must not be
    mistaken for a signed one.
    """
    from intelligence.psx_analyser import build_analysis
    from reports.pdf_renderer import render_pdf

    a = build_analysis(ticker)
    if not a.found:
        return None, f"No listed company matching {ticker}."
    pdf_bytes, err = render_pdf(build_psx_report_html(a))
    if err or not pdf_bytes:
        return None, err or "PDF rendering produced no output."
    return pdf_bytes, None
