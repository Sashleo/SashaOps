"""
intelligence/psx_charts.py - chart payload builder (Phase 3 Task 3.8, web side).

Templates never assemble chart data. Each builder returns a dict carrying either a
renderable spec or the exact reason it cannot render, so the plan's universal chart
rules are enforced in one place:

  - axis labels always carry units ("PKR millions", "percent", "times", "days")
  - the title states what and over which periods
  - a source line sits beneath every chart
  - a period whose value failed validation becomes a GAP (null), never an
    interpolated or zeroed point, and earns a footnote
  - nothing renders below 2 valid points; the caller shows the reason instead

The same specs feed reports/chart_render.py for static export, so on-screen and
in-PDF charts cannot drift apart.
"""

from __future__ import annotations

import json
from typing import Optional

SOURCE_LINE = "Source: PSX annual reports. Computed locally."
MIN_POINTS = 2

# Subject accent, neutral grey for peers/sector, amber WATCH, red RISK.
COLOR_SUBJECT = "#00ff87"
COLOR_NEUTRAL = "#6b7280"
COLOR_WATCH = "#f59e0b"
COLOR_RISK = "#f87171"


def _blocked(reason: str) -> dict:
    return {"renderable": False, "reason": reason}


def _spec(kind: str, title: str, y_label: str, labels: list,
          series: list, footnotes: Optional[list] = None,
          x_label: str = "", extra: Optional[dict] = None) -> dict:
    spec = {"kind": kind, "title": title, "y_label": y_label, "x_label": x_label,
            "labels": labels, "series": series}
    if extra:
        spec.update(extra)
    return {"renderable": True, "title": title, "source": SOURCE_LINE,
            "footnotes": footnotes or [], "spec": spec,
            "spec_json": json.dumps(spec)}


def _count_valid(values: list) -> int:
    return sum(1 for v in values if v is not None)


def chart2_revenue_profit(a) -> dict:
    """Grouped bars: revenue and profit after tax per period, oldest left."""
    periods = list(reversed(a.annual_periods[:4]))
    if not periods:
        return _blocked("Chart requires at least 2 periods of valid data. Available: 0.")

    def _mn(period, key):
        v = a.income_statement.get(period, {}).get(key)
        return round(v / 1_000_000, 2) if isinstance(v, (int, float)) else None

    revenue = [_mn(p, "revenue") for p in periods]
    profit = [_mn(p, "net_income") for p in periods]
    if _count_valid(revenue) < MIN_POINTS:
        return _blocked(f"Chart requires at least 2 periods of valid data. "
                       f"Available: {_count_valid(revenue)}.")

    # Period-over-period growth annotations, computed here so the label text and
    # the plotted values can never disagree.
    growth = [None]
    for i in range(1, len(revenue)):
        cur, prv = revenue[i], revenue[i - 1]
        growth.append(round((cur - prv) / abs(prv) * 100, 1)
                     if (cur is not None and prv) else None)

    notes = [f"{p} excluded: value failed plausibility check."
             for p, v in zip(periods, revenue) if v is None]
    return _spec(
        "grouped_bar",
        f"Revenue and profit after tax, {periods[0]} to {periods[-1]}",
        "PKR millions", periods,
        [{"name": "Revenue", "data": revenue, "color": COLOR_SUBJECT},
         {"name": "Profit after tax", "data": profit, "color": COLOR_NEUTRAL}],
        footnotes=notes, x_label="Fiscal period",
        extra={"annotations": growth, "annotation_unit": "percent"},
    )


# Chart 1 covers the ratios that are computable from income-statement data today.
CHART1_RATIOS = [
    ("gross_margin", "Gross margin", "percent"),
    ("operating_margin", "Operating margin", "percent"),
    ("net_margin", "Net margin", "percent"),
    ("roa", "Return on assets", "percent"),
    ("roe", "Return on equity", "percent"),
    ("revenue_growth", "Revenue growth", "percent"),
]


def chart1_ratio_trends(a) -> dict:
    """Small multiples: one line per ratio across periods, sector median overlaid."""
    periods = list(reversed(a.annual_periods[:4]))
    if len(periods) < MIN_POINTS:
        return _blocked(f"Chart requires at least 2 periods of valid data. "
                       f"Available: {len(periods)}.")

    panels, notes = [], []
    for key, label, unit in CHART1_RATIOS:
        data, gaps = [], []
        for p in periods:
            m = (a.ratios.get(p) or {}).get(key) or {}
            if m.get("status") == "ok" and m.get("value") is not None:
                data.append(round(m["value"] * 100, 2) if unit == "percent"
                           else round(m["value"], 3))
            else:
                data.append(None)
                if m.get("status") == "implausible":
                    gaps.append(p)
        if _count_valid(data) < MIN_POINTS:
            continue
        med = (a.sector_medians.get(key) or {}).get("median")
        panels.append({
            "name": label, "unit": unit, "labels": periods, "data": data,
            "color": COLOR_SUBJECT,
            "median": round(med * 100, 2) if (med is not None and unit == "percent")
                      else (round(med, 3) if med is not None else None),
        })
        notes += [f"{label}: {p} excluded, value failed plausibility check." for p in gaps]

    if not panels:
        return _blocked("Chart requires at least 2 periods of valid data for a ratio. "
                       "No ratio has two validated periods yet.")
    return _spec("small_multiples",
                f"Ratio trends, {periods[0]} to {periods[-1]}",
                "percent", periods, panels, footnotes=notes,
                x_label="Fiscal period",
                extra={"median_note": "Dashed line marks the sector median."})


def chart4_percentiles(a) -> dict:
    """Horizontal dot plot of the subject's percentile per ratio, best first."""
    rows = []
    labels = {"net_margin": "Net margin", "roe": "Return on equity",
              "gross_margin": "Gross margin", "asset_turnover": "Asset turnover",
              "debt_to_equity": "Debt to equity (inverted)"}
    for key, pct in (a.sector_percentiles or {}).items():
        rows.append({"name": labels.get(key, key), "percentile": pct,
                    "color": COLOR_SUBJECT})
    if not rows:
        n = sum(1 for p in a.peers if p.get("net_margin") is not None)
        return _blocked(f"Insufficient peers in {a.sector or 'this sector'} for a "
                       f"reliable benchmark ({n} companies with valid data).")
    rows.sort(key=lambda r: r["percentile"], reverse=True)
    return _spec("dot_plot",
                f"Sector percentile ranking within {a.sector or 'sector'}",
                "percentile", [r["name"] for r in rows], rows,
                x_label="Percentile (0 to 100)",
                extra={"median_marker": 50,
                       "note": "Leverage percentiles are inverted, lower leverage "
                              "ranks higher."})


def chart8_peer_scatter(a) -> dict:
    """
    Peer scatter. Default axes are net margin against revenue growth, because
    debt-to-equity needs balance-sheet data that is not available for most tickers
    yet; the axis choice is stated so the reader is not left guessing.
    """
    pts = []
    for p in a.peers:
        x, y = p.get("revenue_growth"), p.get("net_margin")
        if x is None or y is None:
            continue
        pts.append({"label": p["symbol"], "x": round(x * 100, 2),
                   "y": round(y * 100, 2),
                   "size": p.get("market_cap"),
                   "is_subject": bool(p.get("is_subject")),
                   "color": COLOR_SUBJECT if p.get("is_subject") else COLOR_NEUTRAL})
    if len(pts) < MIN_POINTS:
        return _blocked(f"Chart requires at least 2 peers with both axes valid. "
                       f"Available: {len(pts)}.")

    xs = sorted(p["x"] for p in pts)
    ys = sorted(p["y"] for p in pts)
    mid = lambda v: v[len(v) // 2] if len(v) % 2 else (v[len(v)//2 - 1] + v[len(v)//2]) / 2
    return _spec("scatter",
                f"Peer comparison within {a.sector or 'sector'}",
                "Net margin (percent)", [], pts,
                x_label="Revenue growth (percent)",
                extra={"x_median": mid(xs), "y_median": mid(ys),
                       "quadrants": ["High margin, low growth", "High margin, high growth",
                                    "Low margin, low growth", "Low margin, high growth"],
                       "axis_note": "Debt to equity is unavailable until balance-sheet "
                                   "data is extracted, so revenue growth is the "
                                   "default x axis."})


def chart3_dupont(a) -> dict:
    """
    DuPont as a horizontal bar of each component with the sector median marked.

    A true waterfall is avoided deliberately: ROE decomposes MULTIPLICATIVELY, and
    an additive visual would misrepresent how the components combine.
    """
    dp = a.dupont or {}
    rows, medians = [], []
    for key, label in (("net_margin", "Net margin (%)"),
                       ("asset_turnover", "Asset turnover (x)"),
                       ("equity_multiplier", "Equity multiplier (x)"),
                       ("roe", "Return on equity (%)")):
        mm = dp.get(key) or {}
        if mm.get("status") != "ok" or mm.get("value") is None:
            continue
        pct = key in ("net_margin", "roe")
        rows.append({"name": label,
                    "value": round(mm["value"] * 100, 2) if pct else round(mm["value"], 3)})
        med = (a.sector_medians.get(key) or {}).get("median")
        medians.append(round(med * 100, 2) if (med is not None and pct)
                      else (round(med, 3) if med is not None else None))
    if len(rows) < MIN_POINTS:
        missing = a.missing_requirements.get("dupont") or []
        return _blocked(missing[0] if missing else
                       "Required: net income, revenue, total assets and total equity "
                       "to decompose ROE.")
    return _spec("stacked_bar", "DuPont decomposition of return on equity",
                "value", [r["name"] for r in rows],
                [{"name": "Company", "data": [r["value"] for r in rows],
                  "color": COLOR_SUBJECT},
                 {"name": "Sector median", "data": medians, "color": COLOR_NEUTRAL}],
                x_label="Component value")


def chart5_cash_cycle(a) -> dict:
    """Cash conversion cycle composition. Not meaningful for banks."""
    if a.is_bank:
        return _blocked("Cash conversion cycle is not meaningful for financial "
                       "institutions.")
    latest = a.latest_period
    R = a.ratios.get(latest, {}) if latest else {}
    dio = (R.get("days_inventory") or {}).get("value")
    dso = (R.get("days_sales_outstanding") or {}).get("value")
    dpo = (R.get("days_payables") or {}).get("value")
    if dio is None or dso is None or dpo is None:
        return _blocked("Required: inventory, receivables and payables from the "
                       "balance sheet to compute the cash conversion cycle.")
    ccc = dio + dso - dpo
    return _spec("stacked_bar",
                f"Cash conversion cycle composition, {latest}", "days",
                ["Company"],
                [{"name": "Days inventory", "data": [round(dio)], "color": COLOR_SUBJECT},
                 {"name": "Days sales outstanding", "data": [round(dso)], "color": COLOR_NEUTRAL},
                 {"name": "Days payables (offset)", "data": [-round(dpo)], "color": COLOR_WATCH}],
                x_label="Days",
                extra={"net_label": f"Net cash conversion cycle: {ccc:,.0f} days"})


def chart6_altman_gauge(a) -> dict:
    """Banded Z-Score scale with the score marked; bands depend on the formula."""
    z = a.altman or {}
    if not z.get("computable"):
        missing = ", ".join(z.get("missing") or []) or "complete balance sheet data"
        return _blocked(f"Not computable: {missing}.")
    score = (z.get("score") or {}).get("value")
    if score is None:
        return _blocked("Not computable: the Z-Score failed its plausibility check.")
    model = (z.get("formula_used") or "").lower()
    if "5" in model or "original" in model:
        bands = [{"name": "Distress (<1.81)", "width": 1.81, "color": COLOR_RISK},
                 {"name": "Grey (1.81-2.99)", "width": 1.18, "color": COLOR_WATCH},
                 {"name": "Safe (>2.99)", "width": 3.01, "color": COLOR_SUBJECT}]
    else:
        bands = [{"name": "Distress (<1.1)", "width": 1.1, "color": COLOR_RISK},
                 {"name": "Grey (1.1-2.6)", "width": 1.5, "color": COLOR_WATCH},
                 {"name": "Safe (>2.6)", "width": 3.4, "color": COLOR_SUBJECT}]
    return _spec("gauge", f"Altman Z-Score, {a.latest_period}", "", ["Z-Score"], [],
                x_label="Score",
                extra={"bands": bands, "score": round(score, 2),
                       "formula": z.get("formula_used"), "zone": z.get("zone")})


def chart7_dcf_sensitivity(a) -> dict:
    """DCF sensitivity grid. Non-banks only, and only once a DCF is computable."""
    if a.is_bank:
        return _blocked("Enterprise DCF is not applied to banks, so a WACC and "
                       "terminal-growth sensitivity grid does not apply.")
    v = a.valuation or {}
    if not v.get("computable"):
        missing = a.missing_requirements.get("valuation") or []
        return _blocked(missing[0] if missing else
                       "Required: free cash flow inputs to run a DCF sensitivity grid.")
    grid = v.get("sensitivity")
    if not grid:
        return _blocked("Required: a computed DCF before sensitivities can be shown.")
    return _spec("heatmap", "Equity value per share by WACC and terminal growth",
                "PKR per share", grid.get("wacc_labels", []),
                grid.get("rows", []), x_label="WACC")


def build_charts(a) -> dict:
    """All web chart payloads for one analysis. Never raises."""
    out = {}
    for key, fn in (("chart1", chart1_ratio_trends),
                    ("chart2", chart2_revenue_profit),
                    ("chart3", chart3_dupont),
                    ("chart4", chart4_percentiles),
                    ("chart5", chart5_cash_cycle),
                    ("chart6", chart6_altman_gauge),
                    ("chart7", chart7_dcf_sensitivity),
                    ("chart8", chart8_peer_scatter)):
        try:
            out[key] = fn(a)
        except Exception as e:  # a chart must never break a section
            out[key] = _blocked(f"Chart could not be prepared: {str(e)[:80]}")
    return out
