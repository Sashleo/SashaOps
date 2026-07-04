"""
routers/markets.py — Market data routes.

All market data served from the local PSX database (psx_index, psx_companies, psx_prices).
Forex rates served from DB scrapers. Local financial analysis runs without AI API calls.
"""

import asyncio
import json
import html as _html

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session
from intelligence.stocks import fetch_region_market_data, fetch_stock_data
from database.exchange_data import (
    get_latest_forex_rates,
    get_latest_index,
    get_scraper_status,
    get_index_range,
    get_period_summary,
    get_forex_range,
    get_top_movers,
    get_exchange_companies,
    _date_from_period,
)
from database.watchlist import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    is_watched,
)
from database.clusters import (
    create_cluster,
    delete_cluster,
    add_cluster_member,
    remove_cluster_member,
    get_clusters_with_members,
    save_announcements,
)
from config.regions import get_region

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    from database.clients import list_clients
    clients       = list_clients()
    client_id     = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _active_region(request: Request) -> str:
    return "pakistan"


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def markets_page(request: Request):
    require_session(request)
    sidebar   = _sidebar(request)
    region_id = _active_region(request)
    region    = get_region(region_id)

    db_index    = get_latest_index(region_id)
    forex_rates = get_latest_forex_rates(region_id)
    watchlist   = get_watchlist()
    scraper_status = get_scraper_status()

    exchange_url  = getattr(region, "exchange_url", "")
    exchange_name = getattr(region, "exchange_name", "Exchange")

    # Load KSE index data from local PSX database
    indices = []
    loop = asyncio.get_running_loop()
    region_data, _ = await loop.run_in_executor(
        None, lambda: fetch_region_market_data(region_id)
    )
    if region_data:
        indices = region_data.indices

    # Period summary for the stat row (default 30d)
    from_date, to_date = _date_from_period("30d")
    period_summary = get_period_summary(region_id, from_date, to_date)

    return templates.TemplateResponse("markets.html", {
        "request":        request,
        "active_page":    "markets",
        "indices":        indices,
        "db_index":       db_index,
        "forex_rates":    forex_rates,
        "exchange_url":   exchange_url,
        "exchange_name":  exchange_name,
        "market_region":  region_id,
        "period":         "30d",
        "period_summary": period_summary,
        "scraper_status": scraper_status,
        "watchlist":      watchlist,
        **sidebar,
    })


# ── Chart data (JSON) ─────────────────────────────────────────────────────────

@router.get("/data/index")
async def index_chart_data(
    request: Request,
    region: str = Query("pakistan"),
    period: str = Query("30d"),
):
    """Return JSON for Chart.js line chart of index close prices."""
    require_session(request)
    from_date, to_date = _date_from_period(period)
    rows = get_index_range(region, from_date, to_date)
    summary = get_period_summary(region, from_date, to_date)
    return JSONResponse({
        "dates":       [r["trade_date"] for r in rows],
        "closes":      [r["close_price"] for r in rows],
        "pct_changes": [r.get("change_pct") for r in rows],
        "index_name":  rows[-1]["index_name"] if rows else "",
        "summary":     summary,
    })


@router.get("/data/forex")
async def forex_chart_data(
    request: Request,
    region: str = Query("pakistan"),
    currency: str = Query("USD"),
    period: str = Query("30d"),
):
    """Return JSON for Chart.js line chart of forex rates."""
    require_session(request)
    from_date, to_date = _date_from_period(period)
    rows = get_forex_range(region, currency, from_date, to_date)
    base_ccy = rows[0]["base_currency"] if rows else ""
    return JSONResponse({
        "dates":     [r["rate_date"] for r in rows],
        "rates":     [r["rate"] for r in rows],
        "pair":      f"{base_ccy}/{currency}" if base_ccy else f"?/{currency}",
        "source":    rows[0]["central_bank"] if rows else "",
    })


# ── HTMX fragments ────────────────────────────────────────────────────────────

@router.post("/fetch", response_class=HTMLResponse)
async def fetch_market_data(
    request: Request,
    market_region: str = Form("pakistan"),
    period: str = Form("30d"),
):
    """Main HTMX refresh — returns index + forex + movers HTML fragment."""
    require_session(request)

    region = get_region(market_region)
    exchange_url   = getattr(region, "exchange_url", "")
    exchange_name  = getattr(region, "exchange_name", "Exchange")
    exchange_id    = getattr(region, "exchange_id", "")

    loop = asyncio.get_running_loop()
    region_data, _ = await loop.run_in_executor(
        None, lambda: fetch_region_market_data(market_region)
    )

    db_index    = get_latest_index(market_region)
    forex_rates = get_latest_forex_rates(market_region)

    from_date, to_date = _date_from_period(period)
    period_summary = get_period_summary(market_region, from_date, to_date)

    indices_html = ""
    for idx in (region_data.indices if region_data else []):
        if idx.current_price:
            indices_html += _render_index_row(idx)
    if db_index and not indices_html:
        indices_html += _render_db_index_row(db_index)
    if not indices_html:
        fallback = (
            f'<a href="{exchange_url}" target="_blank" rel="noopener" style="color:var(--accent)">'
            f'View live on {exchange_name} ↗</a>'
        ) if exchange_url else ""
        indices_html = f'<div class="empty-state"><div class="empty-icon">📈</div><div class="empty-text">Index data not yet available. Scraper runs every 6 hours.</div>{fallback}</div>'

    forex_html   = _render_forex_section(forex_rates, market_region)
    movers_html  = _render_movers_section(exchange_id)
    stats_html   = _render_period_stats(period_summary, period)

    return HTMLResponse(
        f'<div style="margin-bottom:8px;font-size:0.8em;color:var(--text-muted);'
        f'text-transform:uppercase;letter-spacing:1px">Market Indices — '
        f'{market_region.replace("_"," ").title()}</div>'
        f'{stats_html}'
        f'{indices_html}'
        f'{forex_html}'
        f'{movers_html}'
    )


@router.post("/companies", response_class=HTMLResponse)
async def list_companies(
    request: Request,
    market_region: str = Form("pakistan"),
    search: str = Form(""),
    sector: str = Form(""),
):
    """HTMX: return a company table for the active region."""
    require_session(request)
    region     = get_region(market_region)
    exchange_id = getattr(region, "exchange_id", "")
    if not exchange_id:
        return HTMLResponse('<div class="empty-state"><div class="empty-text">No exchange data for Global region.</div></div>')

    companies = get_exchange_companies(exchange_id, sector=sector or None, limit=100)
    if search:
        s = search.lower()
        companies = [c for c in companies if s in c.get("company_name", "").lower() or s in c.get("ticker", "").lower()]

    if not companies:
        return HTMLResponse('<div class="empty-state"><div class="empty-icon">🏢</div><div class="empty-text">No companies found. Exchange data populates after first scraper cycle.</div></div>')

    rows = ""
    for c in companies:
        latest = __import__("database.exchange_data", fromlist=["get_latest_company_price"]).get_latest_company_price(exchange_id, c["ticker"])
        price_str  = f"{latest['close_price']:,.2f}" if latest and latest.get("close_price") else '<span class="text-dim" title="Run the PSX scraper to load prices">pending</span>'
        change_str = ""
        if latest and latest.get("change_pct") is not None:
            cls  = "ticker-change-pos" if latest["change_pct"] >= 0 else "ticker-change-neg"
            sign = "+" if latest["change_pct"] >= 0 else ""
            change_str = f'<span class="{cls}">{sign}{latest["change_pct"]:.2f}%</span>'
        mcap_str = _fmt_num(c.get("market_cap"))
        watched  = is_watched(exchange_id, c["ticker"])
        star_btn = (
            f'<button class="btn btn-xs" style="padding:2px 8px;font-size:0.75em" '
            f'hx-delete="/markets/watchlist/{exchange_id}/{c["ticker"]}" '
            f'hx-target="closest tr" hx-swap="outerHTML" '
            f'title="Remove from watchlist">★</button>'
        ) if watched else (
            f'<button class="btn btn-xs" style="padding:2px 8px;font-size:0.75em;opacity:0.4" '
            f'hx-post="/markets/watchlist/add" '
            f'hx-vals=\'{{"exchange_id":"{exchange_id}","ticker":"{c["ticker"]}","company_name":"{c.get("company_name","")}"}}\'  '
            f'hx-target="closest tr" hx-swap="none" '
            f'hx-on::after-request="this.style.opacity=\'1\';this.textContent=\'★\'" '
            f'title="Add to watchlist">☆</button>'
        )
        rows += (
            f'<tr><td class="text-mono" style="font-size:0.85em">{c["ticker"]}</td>'
            f'<td style="font-size:0.85em">{c.get("company_name","")}</td>'
            f'<td style="font-size:0.85em">{c.get("sector","—")}</td>'
            f'<td class="text-mono" style="font-size:0.85em">{price_str}</td>'
            f'<td style="font-size:0.85em">{change_str}</td>'
            f'<td style="font-size:0.82em;color:var(--text-muted)">{mcap_str}</td>'
            f'<td>{star_btn}</td></tr>\n'
        )

    return HTMLResponse(f"""
    <table style="width:100%;border-collapse:collapse">
      <thead>
        <tr style="font-size:0.75em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px">
          <th style="text-align:left;padding:4px 8px">Ticker</th>
          <th style="text-align:left;padding:4px 8px">Company</th>
          <th style="text-align:left;padding:4px 8px">Sector</th>
          <th style="text-align:right;padding:4px 8px">Price</th>
          <th style="text-align:right;padding:4px 8px">Δ1d</th>
          <th style="text-align:right;padding:4px 8px">Mkt Cap</th>
          <th style="text-align:center;padding:4px 8px">Watch</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>""")


from core.report_utils import render_md_to_html as _render_local_report_html


@router.post("/analyse-local", response_class=HTMLResponse)
async def analyse_local(
    request: Request,
    ticker: str = Form(...),
    region: str = Form("pakistan"),
):
    """
    Run full financial analysis locally — no AI API call.
    Returns an HTML card with ratios, DCF, Altman Z-Score, and a full
    structured analysis (Altman Z breakdown, DuPont, DCF scenarios, FinBERT).
    """
    require_session(request)
    raw_input = (ticker or "").strip()
    if not raw_input:
        return HTMLResponse('<div class="alert alert-error">Enter a company name or PSX ticker.</div>')

    # Resolve a company NAME to a PSX ticker (the field accepts either). This stops
    # the "kept trying to fetch" hang when a name (or a typo) was entered.
    ticker = raw_input.upper()
    try:
        from core.stage1 import _guess_ticker
        from database.connection import get_db as _gdb
        exists = _gdb().execute(
            "SELECT 1 FROM psx_companies WHERE UPPER(symbol)=? LIMIT 1", (ticker,)
        ).fetchone()
        if not exists:
            resolved = _guess_ticker(raw_input)
            if resolved:
                ticker = resolved.upper()
            else:
                return HTMLResponse(
                    f'<div class="alert alert-error">Could not find a PSX company matching '
                    f'"{raw_input}". Check the spelling or enter the ticker directly.</div>'
                )
    except Exception:
        pass

    loop = asyncio.get_running_loop()

    def _analyse():
        from intelligence.financial_model import (
            run_local_analysis_psx,
            generate_local_financial_report,
            score_announcements_sentiment,
        )
        result = run_local_analysis_psx(ticker)

        # Sector peers — direct sector_name match (exact, fast, no industry_tag mapping needed)
        peers = ""
        try:
            from database.connection import get_db as _get_db
            _db = _get_db()
            _co = _db.execute(
                "SELECT sector_name FROM psx_companies WHERE symbol=? LIMIT 1",
                (ticker,),
            ).fetchone()
            if _co and _co["sector_name"]:
                _sector = _co["sector_name"]
                # Derive P/E from latest price / latest annual EPS (psx_companies has
                # no pe_ratio column). LEFT JOIN so peers without EPS still list.
                _prows  = _db.execute(
                    """SELECT c.symbol, c.company_name, p.current AS price, f.eps AS eps
                       FROM psx_companies c
                       LEFT JOIN (
                           SELECT symbol, current FROM psx_prices
                           WHERE (symbol, trade_date) IN (
                               SELECT symbol, MAX(trade_date) FROM psx_prices GROUP BY symbol)
                       ) p ON c.symbol = p.symbol
                       LEFT JOIN (
                           SELECT symbol, eps FROM psx_financials
                           WHERE period_type='annual' AND eps IS NOT NULL
                             AND (symbol, period) IN (
                               SELECT symbol, MAX(period) FROM psx_financials
                               WHERE period_type='annual' AND eps IS NOT NULL GROUP BY symbol)
                       ) f ON c.symbol = f.symbol
                       WHERE c.sector_name = ? AND c.symbol != ? AND c.is_debt = 0
                       ORDER BY c.company_name LIMIT 10""",
                    (_sector, ticker),
                ).fetchall()
                if _prows:
                    _plines = [f"PSX SECTOR PEERS — {_sector}\n"]
                    for _p in _prows:
                        _peval = (_p["price"] / _p["eps"]) if (_p["price"] and _p["eps"] and _p["eps"] > 0) else None
                        _pe = f"  P/E {_peval:.1f}x" if _peval else "  P/E N/A"
                        _plines.append(f"  {_p['symbol']:8} {_p['company_name'][:35]}{_pe}")
                    peers = "\n".join(_plines)
        except Exception:
            pass

        sentiment = score_announcements_sentiment(ticker)
        full_md   = generate_local_financial_report(result, sentiment_scores=sentiment, peers_text=peers or None)
        return result, full_md

    try:
        # Hard cap so a slow PSX scrape/OCR can't hang the request indefinitely.
        result, full_md = await asyncio.wait_for(
            loop.run_in_executor(None, _analyse), timeout=45,
        )
    except asyncio.TimeoutError:
        return HTMLResponse(
            f'<div class="alert alert-error">Analysis for {ticker} timed out (financial '
            f'data is slow to fetch right now). Please try again in a moment.</div>'
        )
    except ValueError as e:
        return HTMLResponse(f'<div class="alert alert-error">{e}</div>')
    except Exception as e:
        log.warning("Local analysis failed", ticker=ticker, error=str(e))
        return HTMLResponse(f'<div class="alert alert-error">Could not analyse {ticker}: {str(e)[:120]}</div>')

    raw = result.get("_raw", {})

    def _pct(v, warn_below=None, good_above=None):
        if v is None: return '<span style="color:var(--text-muted)">N/A</span>'
        s = f"{v:.1%}"
        if good_above and v >= good_above: return f'<span style="color:#4ade80">{s}</span>'
        if warn_below and v < warn_below:  return f'<span style="color:#f87171">{s}</span>'
        return f'<span>{s}</span>'

    def _ratio(v, good_above=None, warn_below=None):
        if v is None: return '<span style="color:var(--text-muted)">N/A</span>'
        s = f"{v:.2f}x"
        if good_above and v >= good_above: return f'<span style="color:#4ade80">{s}</span>'
        if warn_below and v < warn_below:  return f'<span style="color:#f87171">{s}</span>'
        return f'<span>{s}</span>'

    def _num(v):
        if v is None: return '<span style="color:var(--text-muted)">N/A</span>'
        return f'<span>{v:.1f}</span>'

    health = raw.get("health_score")
    health_str   = f"{health:.1f}" if health is not None else "—"
    health_color = "#4ade80" if (health or 0) >= 7 else ("#facc15" if (health or 0) >= 5 else "#f87171")
    health_label = "Strong" if (health or 0) >= 7 else ("Adequate" if (health or 0) >= 5 else "Weak")

    z = raw.get("altman_z")
    z_str   = f"{z:.2f}" if z is not None else "N/A"
    z_color = "#4ade80" if result.get("altman_zone") == "SAFE" else ("#facc15" if result.get("altman_zone") == "GREY" else "#f87171")

    dcf_current = result.get("dcf_base", "N/A")
    exchange_id = get_region(region).exchange_id
    watched     = is_watched(exchange_id, ticker)
    watch_btn   = (
        f'<button class="btn" hx-delete="/markets/watchlist/{exchange_id}/{ticker}" '
        f'hx-target="#local-analysis-result" hx-swap="none" '
        f'hx-on::after-request="document.querySelector(\'#watch-btn-{ticker}\').textContent=\'☆ Add to Watchlist\'"'
        f' id="watch-btn-{ticker}">★ On Watchlist</button>'
    ) if watched else (
        f'<button class="btn" hx-post="/markets/watchlist/add" '
        f'hx-vals=\'{{"exchange_id":"{exchange_id}","ticker":"{ticker}","company_name":"{result.get("company","")}"}}\' '
        f'hx-target="#local-analysis-result" hx-swap="none" '
        f'hx-on::after-request="document.querySelector(\'#watch-btn-{ticker}\').textContent=\'★ On Watchlist\'"'
        f' id="watch-btn-{ticker}">☆ Add to Watchlist</button>'
    )

    ai_link = f'/intelligence?business={result.get("company","").replace(" ", "+")}&region={region}'

    errors_html = ""
    if result.get("errors"):
        errs = "; ".join(result["errors"][:2])
        errors_html = f'<div class="alert alert-warning" style="margin-top:8px;font-size:0.8em">Partial data: {errs}</div>'

    _SOURCE_LABELS = {
        "psx_summary":            ("📄 PSX summary", "badge-gray"),
        "psx_annual_report":      ("📑 PSX annual report", "badge-green"),
        "psx_annual_report_ocr":  ("🔍 PSX report (OCR — verify figures)", "badge-yellow"),
        "psx":                    ("📄 PSX", "badge-gray"),
        "psx_scraped":            ("📊 PSX scraped", "badge-gray"),
    }
    src_label, src_badge = _SOURCE_LABELS.get(result.get("source", ""), ("PSX", "badge-gray"))
    ocr_note = ""
    if result.get("ocr_derived"):
        ocr_note = ('<div class="alert alert-warning" style="margin-top:8px;font-size:0.78em">'
                    '🔍 Balance-sheet figures were read from a scanned report via OCR — '
                    'treat the deeper ratios (current ratio, debt/equity, Altman Z, ROE) as '
                    'indicative and verify against the source filing.</div>')

    html = f"""
    <div style="border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-top:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px">
        <div>
          <div style="font-weight:700;font-size:1.05em">{result.get("company", ticker)}</div>
          <div style="font-size:0.8em;color:var(--text-muted)">
            {ticker} · {result.get("currency","?")} · FY {result.get("fiscal_year","?")}
          </div>
          <div style="margin-top:4px"><span class="badge {src_badge}" style="font-size:0.72em">{src_label}</span></div>
        </div>
        <div style="text-align:right">
          <div style="font-size:1.3em;font-weight:700;color:{health_color}">{health_str}/10</div>
          <div style="font-size:0.75em;color:{health_color}">{health_label}</div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
        <div style="background:var(--bg-2);border-radius:6px;padding:12px">
          <div style="font-size:0.7em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Profitability</div>
          <div style="font-size:0.82em;display:grid;grid-template-columns:auto 1fr;gap:2px 8px">
            <span style="color:var(--text-muted)">Gross Margin</span>{_pct(raw.get("gross_margin"), warn_below=0.15, good_above=0.35)}
            <span style="color:var(--text-muted)">EBITDA Margin</span>{_pct(raw.get("ebitda_margin"), warn_below=0.10, good_above=0.25)}
            <span style="color:var(--text-muted)">Net Margin</span>{_pct(raw.get("net_margin"), warn_below=0.05, good_above=0.15)}
            <span style="color:var(--text-muted)">ROE</span>{_pct(raw.get("roe"), warn_below=0.08, good_above=0.20)}
            <span style="color:var(--text-muted)">ROA</span>{_pct(raw.get("roa"), warn_below=0.04, good_above=0.12)}
          </div>
        </div>
        <div style="background:var(--bg-2);border-radius:6px;padding:12px">
          <div style="font-size:0.7em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Liquidity & Leverage</div>
          <div style="font-size:0.82em;display:grid;grid-template-columns:auto 1fr;gap:2px 8px">
            <span style="color:var(--text-muted)">Current Ratio</span>{_ratio(raw.get("current_ratio"), good_above=2, warn_below=1)}
            <span style="color:var(--text-muted)">Quick Ratio</span>{_ratio(raw.get("quick_ratio"), good_above=1.5, warn_below=0.8)}
            <span style="color:var(--text-muted)">D/E Ratio</span>{_ratio(raw.get("debt_to_equity"))}
            <span style="color:var(--text-muted)">Net Debt/EBITDA</span>{_ratio(raw.get("debt_to_ebitda"), warn_below=None)}
            <span style="color:var(--text-muted)">Int. Coverage</span>{_ratio(raw.get("interest_coverage"), good_above=5, warn_below=2)}
          </div>
        </div>
        <div style="background:var(--bg-2);border-radius:6px;padding:12px">
          <div style="font-size:0.7em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Growth & Efficiency</div>
          <div style="font-size:0.82em;display:grid;grid-template-columns:auto 1fr;gap:2px 8px">
            <span style="color:var(--text-muted)">Revenue YoY</span>{_pct(raw.get("revenue_growth_yoy"), warn_below=0, good_above=0.15)}
            <span style="color:var(--text-muted)">3Y CAGR</span><span>{result.get("revenue_cagr_3y","N/A")}</span>
            <span style="color:var(--text-muted)">Asset Turnover</span>{_ratio(raw.get("asset_turnover"))}
            <span style="color:var(--text-muted)">CCC (days)</span>{_num(raw.get("ccc_days"))}
            <span style="color:var(--text-muted)">Receivables</span><span>{raw.get("receivables_days") and f'{raw["receivables_days"]:.0f}d' or "N/A"}</span>
          </div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
        <div style="background:var(--bg-2);border-radius:6px;padding:12px">
          <div style="font-size:0.7em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">
            DCF Valuation — WACC {result.get("wacc","?")}
          </div>
          <div style="display:flex;gap:16px;align-items:center">
            <div style="text-align:center">
              <div style="font-size:0.7em;color:var(--text-muted)">Bear</div>
              <div class="text-mono" style="color:#f87171">{result.get("dcf_bear","N/A")}</div>
            </div>
            <div style="text-align:center">
              <div style="font-size:0.7em;color:var(--text-muted)">Base</div>
              <div class="text-mono" style="font-weight:700;color:var(--accent)">{dcf_current}</div>
            </div>
            <div style="text-align:center">
              <div style="font-size:0.7em;color:var(--text-muted)">Bull</div>
              <div class="text-mono" style="color:#4ade80">{result.get("dcf_bull","N/A")}</div>
            </div>
          </div>
          <div style="font-size:0.75em;color:var(--text-dim);margin-top:6px">{result.get("dcf_note","")[:120]}</div>
        </div>
        <div style="background:var(--bg-2);border-radius:6px;padding:12px">
          <div style="font-size:0.7em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Risk Indicators</div>
          <div style="font-size:0.82em">
            <div style="display:flex;justify-content:space-between;padding:3px 0">
              <span style="color:var(--text-muted)">Altman Z-Score</span>
              <span style="color:{z_color};font-weight:600">{z_str} ({result.get("altman_zone","N/A")})</span>
            </div>
          </div>
          {"".join(f'<div style="font-size:0.78em;color:#f87171;margin-top:2px">⚠ {r}</div>' for r in result.get("key_risks",[])[:3])}
          {"".join(f'<div style="font-size:0.78em;color:#4ade80;margin-top:2px">✓ {s}</div>' for s in result.get("key_strengths",[])[:2])}
        </div>
      </div>

      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a href="{ai_link}" class="btn btn-primary" style="font-size:0.85em">Get Full AI Analysis →</a>
        {watch_btn}
      </div>
      {errors_html}
      {ocr_note}

      <details style="margin-top:16px">
        <summary style="cursor:pointer;font-size:0.82em;color:var(--text-muted);padding:6px 0;
                        user-select:none">
          ▸ Full Local Analysis (Altman Z breakdown · DuPont · DCF · Sentiment)
        </summary>
        <div style="margin-top:10px;padding:12px;background:var(--bg-2);border-radius:6px;
                    border:1px solid var(--border)">
          {_render_local_report_html(full_md)}
        </div>
      </details>
    </div>
    """
    return HTMLResponse(html)


# ── On-demand scrape ─────────────────────────────────────────────────────────

@router.get("/scrape/stream")
async def scrape_stream(request: Request):
    """
    SSE endpoint: runs all scrapers immediately and streams per-scraper results.
    Each event is a JSON object. Types: start | running | done | complete.
    """
    require_session(request)

    async def generate():
        from intelligence.exchange_scrapers import get_all_scrapers
        scrapers = get_all_scrapers()
        loop     = asyncio.get_running_loop()
        total    = len(scrapers)

        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        inserted_total = 0
        failed_count   = 0

        for i, scraper in enumerate(scrapers):
            yield f"data: {json.dumps({'type': 'running', 'scraper_id': scraper.scraper_id, 'index': i + 1, 'total': total})}\n\n"
            try:
                result = await loop.run_in_executor(None, scraper.run)
                inserted_total += result.rows_inserted
                if result.status == "failed":
                    failed_count += 1
                yield f"data: {json.dumps({'type': 'done', 'scraper_id': result.scraper_id, 'status': result.status, 'inserted': result.rows_inserted, 'skipped': result.rows_skipped, 'error': result.error_msg or ''})}\n\n"
            except Exception as e:
                failed_count += 1
                yield f"data: {json.dumps({'type': 'done', 'scraper_id': scraper.scraper_id, 'status': 'failed', 'inserted': 0, 'skipped': 0, 'error': str(e)[:120]})}\n\n"

        yield f"data: {json.dumps({'type': 'complete', 'total_inserted': inserted_total, 'failed': failed_count})}\n\n"
        log.info("On-demand scrape complete", inserted=inserted_total, failed=failed_count)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Watchlist endpoints ───────────────────────────────────────────────────────

@router.post("/watchlist/add", response_class=HTMLResponse)
async def watchlist_add(
    request: Request,
    exchange_id: str = Form(...),
    ticker: str = Form(...),
    company_name: str = Form(""),
):
    require_session(request)
    add_to_watchlist(exchange_id, ticker.upper(), company_name)
    return HTMLResponse("")


@router.delete("/watchlist/{exchange_id}/{ticker}", response_class=HTMLResponse)
async def watchlist_remove(request: Request, exchange_id: str, ticker: str):
    require_session(request)
    remove_from_watchlist(exchange_id, ticker.upper())
    return HTMLResponse("")


@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_fragment(request: Request):
    """HTMX fragment — returns the watchlist rows for dashboard embedding."""
    require_session(request)
    items = get_watchlist()
    if not items:
        return HTMLResponse('<div style="font-size:0.82em;color:var(--text-muted)">No stocks watched yet. Use the Markets page to add.</div>')

    rows = ""
    for item in items[:12]:
        from database.exchange_data import get_latest_company_price
        price = get_latest_company_price(item["exchange_id"], item["ticker"])
        price_str  = f"{price['close_price']:,.2f}" if price and price.get("close_price") else "—"
        change_str = ""
        if price and price.get("change_pct") is not None:
            cls  = "color:#4ade80" if price["change_pct"] >= 0 else "color:#f87171"
            sign = "+" if price["change_pct"] >= 0 else ""
            change_str = f'<span style="{cls};font-weight:600">{sign}{price["change_pct"]:.2f}%</span>'
        rows += (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:5px 0;border-bottom:1px solid var(--bg-3);font-size:0.82em">'
            f'<div><span class="text-mono" style="color:var(--accent)">{item["ticker"]}</span>'
            f'<span style="color:var(--text-muted);margin-left:6px;font-size:0.88em">{item["exchange_id"]}</span></div>'
            f'<div style="display:flex;gap:12px;align-items:center">'
            f'<span class="text-mono">{price_str}</span>'
            f'{change_str}</div></div>\n'
        )

    return HTMLResponse(f'<div id="watchlist-items">{rows}</div>')


@router.post("/ticker", response_class=HTMLResponse)
async def lookup_ticker(
    request: Request,
    ticker_input: str = Form(...),
):
    require_session(request)
    ticker = ticker_input.strip().upper()
    if not ticker:
        return HTMLResponse("")

    stock, err = fetch_stock_data(ticker)
    if err and not stock:
        return HTMLResponse(
            f'<div class="alert alert-error">Could not fetch data for "{ticker}": {err}</div>'
        )
    if not stock:
        return HTMLResponse(f'<div class="alert alert-error">No data for ticker "{ticker}"</div>')

    chg_class = "ticker-change-pos" if (stock.change_pct_1d or 0) >= 0 else "ticker-change-neg"
    sign = "+" if (stock.change_pct_1d or 0) >= 0 else ""

    html = f"""
    <div style="border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-top:8px">
      <div class="flex items-center justify-between mb-16">
        <div>
          <div style="font-weight:600">{stock.company_name}</div>
          <div style="font-size:0.8em;color:var(--text-muted)">{stock.ticker} · {stock.exchange} · {stock.currency}</div>
        </div>
        <div>
          <div class="text-mono" style="font-size:1.2em">{_fmt_num(stock.current_price, raw=True)}</div>
          <div class="{chg_class}" style="font-size:0.85em">{sign}{_fmt_num(stock.change_pct_1d, raw=True, pct=True)}</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;font-size:0.82em">
        <div><span style="color:var(--text-muted)">Market Cap</span><br>{_fmt_num(stock.market_cap)}</div>
        <div><span style="color:var(--text-muted)">P/E Ratio</span><br>{_fmt_num(stock.pe_ratio, raw=True)}</div>
        <div><span style="color:var(--text-muted)">Revenue</span><br>{_fmt_num(stock.revenue_ttm)}</div>
        <div><span style="color:var(--text-muted)">EBITDA</span><br>{_fmt_num(stock.ebitda)}</div>
        <div><span style="color:var(--text-muted)">52W High</span><br>{_fmt_num(stock.high_52w, raw=True)}</div>
        <div><span style="color:var(--text-muted)">52W Low</span><br>{_fmt_num(stock.low_52w, raw=True)}</div>
        <div><span style="color:var(--text-muted)">Analyst</span><br>
          <span class="badge badge-gray">{stock.analyst_rating or "N/A"}</span></div>
      </div>
      {f'<div class="alert alert-warning" style="margin-top:12px;font-size:0.8em">Partial data: {stock.error}</div>' if stock.error else ''}
    </div>
    """
    return HTMLResponse(html)


# ── Render helpers ────────────────────────────────────────────────────────────

def _render_index_row(idx) -> str:
    chg_str = ""
    if idx.change_pct_1d is not None:
        cls  = "ticker-change-pos" if idx.change_pct_1d >= 0 else "ticker-change-neg"
        sign = "+" if idx.change_pct_1d >= 0 else ""
        chg_str = f'<div class="{cls}">{sign}{idx.change_pct_1d:.2f}%</div>'
    ytd_str = ""
    if idx.change_pct_ytd is not None:
        ytd_str = f'<div style="font-size:0.8em;color:var(--text-muted)">YTD: {idx.change_pct_ytd:+.1f}%</div>'
    price_str = f"{idx.current_price:,.2f}" if idx.current_price else "—"
    partial   = '<span class="badge badge-yellow" title="Partial data">~</span>' if idx.is_partial else ""
    return f"""
    <div class="ticker-row">
      <div class="ticker-name">{idx.name} {partial}</div>
      <div class="ticker-price text-mono">{price_str} <span style="font-size:0.8em;color:var(--text-muted)">{idx.currency}</span></div>
      {chg_str}
      {ytd_str}
    </div>"""


def _render_db_index_row(row: dict) -> str:
    price_str = f"{row['close_price']:,.2f}" if row.get("close_price") else "—"
    chg_str   = ""
    if row.get("change_pct") is not None:
        cls  = "ticker-change-pos" if row["change_pct"] >= 0 else "ticker-change-neg"
        sign = "+" if row["change_pct"] >= 0 else ""
        chg_str = f'<div class="{cls}">{sign}{row["change_pct"]:.2f}%</div>'
    return f"""
    <div class="ticker-row">
      <div class="ticker-name">{row.get("index_name", row.get("index_code","?"))}
        <span class="badge badge-gray" style="font-size:0.7em;margin-left:4px">scraped</span>
      </div>
      <div class="ticker-price text-mono">{price_str}</div>
      {chg_str}
      <div style="font-size:0.75em;color:var(--text-dim)">{row.get("trade_date","")}</div>
    </div>"""


def _render_period_stats(summary: dict, period: str) -> str:
    if not summary:
        return ""
    pct = summary.get("pct_change", 0)
    pct_color = "#4ade80" if pct >= 0 else "#f87171"
    sign = "+" if pct >= 0 else ""
    return f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;padding:10px 12px;
                background:var(--bg-2);border-radius:var(--radius);font-size:0.82em">
      <div><span style="color:var(--text-muted)">Close</span>
           <span class="text-mono" style="margin-left:6px;font-weight:600">{summary.get("last_close",0):,.2f}</span></div>
      <div><span style="color:var(--text-muted)">{period.upper()}</span>
           <span class="text-mono" style="margin-left:6px;color:{pct_color};font-weight:600">{sign}{pct:.2f}%</span></div>
      <div><span style="color:var(--text-muted)">High</span>
           <span class="text-mono" style="margin-left:6px">{summary.get("high",0):,.2f}</span></div>
      <div><span style="color:var(--text-muted)">Low</span>
           <span class="text-mono" style="margin-left:6px">{summary.get("low",0):,.2f}</span></div>
      <div><span style="color:var(--text-muted)">Days</span>
           <span class="text-mono" style="margin-left:6px">{summary.get("trading_days",0)}</span></div>
    </div>"""


def _render_forex_section(forex_rates: list, region_id: str) -> str:
    if not forex_rates:
        return ""
    priority = {"USD": 0, "EUR": 1, "GBP": 2, "JPY": 3, "CNY": 4, "SAR": 5, "AED": 6}
    mid_rates = {
        r["quote_currency"]: r
        for r in forex_rates
        if r["rate_type"] in ("mid", "official", "interbank")
    }
    top_rates = sorted(mid_rates.values(), key=lambda r: priority.get(r["quote_currency"], 99))[:6]
    if not top_rates:
        return ""
    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
        f'border-bottom:1px solid var(--bg-3);font-size:0.82em">'
        f'<span>{r["base_currency"]}/{r["quote_currency"]}</span>'
        f'<span class="text-mono" style="color:var(--accent)">{r["rate"]:.4f}</span>'
        f'<span style="color:var(--text-dim);font-size:0.85em">{r["central_bank"]} · {r["rate_date"]}</span>'
        f'</div>'
        for r in top_rates
    )
    return f"""
    <div style="margin-top:16px">
      <div style="font-size:0.8em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">
        Central Bank Forex Rates
      </div>
      <div style="background:var(--bg-2);border-radius:var(--radius);padding:10px">{rows_html}</div>
    </div>"""


def _render_movers_section(exchange_id: str) -> str:
    if not exchange_id:
        return ""
    movers = get_top_movers(exchange_id, limit=5)
    if not movers or (not movers.get("gainers") and not movers.get("losers")):
        return ""
    gainers = movers.get("gainers", [])[:5]
    losers  = movers.get("losers", [])[:5]

    def _row(m, cls):
        sign = "+" if m["change_pct"] >= 0 else ""
        return (
            f'<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:0.82em">'
            f'<span class="text-mono" style="color:var(--accent)">{m["ticker"]}</span>'
            f'<span class="{cls}">{sign}{m["change_pct"]:.2f}%</span>'
            f'</div>'
        )

    g_html = "".join(_row(m, "ticker-change-pos") for m in gainers)
    l_html = "".join(_row(m, "ticker-change-neg") for m in losers)
    date_str = movers.get("trade_date", "")

    return f"""
    <div style="margin-top:16px">
      <div style="font-size:0.8em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">
        Top Movers · {date_str}
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div style="background:var(--bg-2);border-radius:var(--radius);padding:10px">
          <div style="font-size:0.7em;color:#4ade80;margin-bottom:4px">GAINERS</div>
          {g_html or '<div style="font-size:0.8em;color:var(--text-dim)">—</div>'}
        </div>
        <div style="background:var(--bg-2);border-radius:var(--radius);padding:10px">
          <div style="font-size:0.7em;color:#f87171;margin-bottom:4px">LOSERS</div>
          {l_html or '<div style="font-size:0.8em;color:var(--text-dim)">—</div>'}
        </div>
      </div>
    </div>"""


def _fmt_num(v, raw: bool = False, pct: bool = False) -> str:
    if v is None: return "N/A"
    try:
        f = float(v)
        if pct: return f"{f:.2f}%"
        if raw: return f"{f:.2f}"
        if abs(f) >= 1e9: return f"{f/1e9:.1f}B"
        if abs(f) >= 1e6: return f"{f/1e6:.1f}M"
        return f"{f:.2f}"
    except Exception:
        return str(v)


# ── Company Research ──────────────────────────────────────────────────────────

@router.get("/research/search")
async def research_search(
    request: Request,
    q: str = Query(""),
    exchange: str = Query("PSX"),
):
    """JSON autocomplete — search PSX symbols by name or ticker."""
    require_session(request)
    if exchange.upper() != "PSX" or not q.strip():
        return JSONResponse([])
    from intelligence.exchange_crawlers.psx import search_psx_company
    loop    = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, lambda: search_psx_company(q))
    return JSONResponse(results)


@router.post("/research/fetch", response_class=HTMLResponse)
async def research_fetch(
    request: Request,
    exchange: str    = Form("PSX"),
    ticker: str      = Form(""),
    fetch_board: str = Form("off"),
    fetch_ann: str   = Form("off"),
    fetch_meetings: str = Form("off"),
    fetch_dividends: str = Form("off"),
    fetch_financials: str = Form("off"),
    fetch_ratios: str = Form("off"),
):
    """Fetch deep company data from PSX portal and render as HTML."""
    require_session(request)
    ticker   = ticker.strip().upper()
    exchange = exchange.strip().upper()

    if not ticker:
        return HTMLResponse('<div class="alert alert-error">Enter a ticker symbol.</div>')

    if exchange != "PSX":
        return HTMLResponse(
            '<div class="alert alert-warning">Deep company research is available for PSX only.</div>'
        )

    fetch_types = []
    if fetch_board     == "on": fetch_types.append("board")
    if fetch_ann       == "on": fetch_types.append("announcements")
    if fetch_meetings  == "on": fetch_types.append("board_meetings")
    if fetch_dividends == "on": fetch_types.append("dividends")
    if fetch_financials == "on": fetch_types.append("financials")
    if fetch_ratios    == "on": fetch_types.append("ratios")

    if not fetch_types:
        fetch_types = ["board", "announcements", "board_meetings", "dividends", "financials", "ratios"]

    from intelligence.exchange_crawlers.psx import fetch_psx_company
    loop   = asyncio.get_running_loop()
    data   = await loop.run_in_executor(None, lambda: fetch_psx_company(ticker, fetch_types))

    if data.get("error"):
        return HTMLResponse(
            f'<div class="alert alert-error">Could not fetch data for {ticker}: {data["error"]}</div>'
        )

    # Persist announcements to DB
    for ann_key, ann_type in [
        ("financial_announcements", "financial_results"),
        ("board_meetings", "board_meeting"),
        ("dividend_notices", "dividend"),
    ]:
        if ann_key in data:
            save_announcements(exchange, ticker, ann_type, data[ann_key])

    html = _render_company_research(ticker, exchange, data)
    return HTMLResponse(html)


def _render_company_research(ticker: str, exchange: str, data: dict) -> str:
    parts = [
        f'<div style="border:1px solid var(--border);border-radius:var(--radius);padding:16px">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">'
        f'<div style="font-weight:600;font-size:1.05em">{ticker}'
        f'  <span class="badge badge-gray" style="margin-left:6px">{exchange}</span></div>'
        f'<a href="https://dps.psx.com.pk/company/{ticker}" target="_blank" rel="noopener" '
        f'style="color:var(--accent);font-size:0.8em">View on PSX ↗</a>'
        f'</div>'
    ]

    # Board members
    if "board" in data and data["board"]:
        rows_html = "".join(
            f'<tr><td style="padding:4px 8px;font-size:0.82em">{m["name"]}</td>'
            f'<td style="padding:4px 8px;font-size:0.82em;color:var(--text-muted)">{m["role"]}</td></tr>'
            for m in data["board"]
        )
        parts.append(
            f'<div style="margin-bottom:14px">'
            f'<div style="font-size:0.75em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Board & Management</div>'
            f'<table style="width:100%;border-collapse:collapse"><tbody>{rows_html}</tbody></table>'
            f'</div>'
        )

    # Annual financials
    for key, label in [("annual_financials", "Annual Financials"), ("quarterly_financials", "Quarterly Financials")]:
        if key in data and data[key]:
            parts.append(_render_financial_table_html(data[key], label))

    # Financial ratios
    if "ratios" in data and data["ratios"]:
        parts.append(_render_financial_table_html(data["ratios"], "Financial Ratios"))

    # Announcement sections
    for ann_key, ann_label in [
        ("financial_announcements", "Financial Results"),
        ("board_meetings", "Board Meeting Notices"),
        ("dividend_notices", "Dividend / Book Closure Notices"),
    ]:
        if ann_key in data and data[ann_key]:
            parts.append(_render_ann_section_html(data[ann_key], ann_label))

    parts.append("</div>")
    return "".join(parts)


def _render_financial_table_html(fin_data: dict, label: str) -> str:
    if not fin_data:
        return ""
    metrics = list(fin_data.keys())
    periods = []
    for m in metrics:
        for p in fin_data[m].keys():
            if p not in periods:
                periods.append(p)

    header_cells = "".join(
        f'<th style="padding:4px 8px;text-align:right;font-size:0.75em;color:var(--text-muted);'
        f'white-space:nowrap">{p}</th>' for p in periods
    )
    rows_html = ""
    for metric in metrics:
        row_cells = ""
        for p in periods:
            v = fin_data[metric].get(p)
            if v is None:
                cell_str = "—"
                color = "var(--text-dim)"
            elif isinstance(v, float):
                cell_str = f"{v:,.2f}" if abs(v) < 1e6 else f"{v/1e6:,.1f}M"
                color = "var(--text-secondary)"
            else:
                cell_str = str(v)
                color = "var(--text-secondary)"
            row_cells += (
                f'<td style="padding:4px 8px;text-align:right;font-size:0.82em;color:{color}">'
                f'{cell_str}</td>'
            )
        rows_html += (
            f'<tr><td style="padding:4px 8px;font-size:0.82em;color:var(--text-muted)">{metric}</td>'
            f'{row_cells}</tr>'
        )

    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="font-size:0.75em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">{label}</div>'
        f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr><th style="padding:4px 8px;text-align:left;font-size:0.75em;color:var(--text-dim)">Metric</th>'
        f'{header_cells}</tr></thead>'
        f'<tbody>{rows_html}</tbody></table></div>'
        f'</div>'
    )


def _render_ann_section_html(items: list, label: str) -> str:
    if not items:
        return ""
    rows_html = ""
    for ann in items[:10]:
        pdf_link = ""
        if ann.get("pdf_url"):
            pdf_link = (
                f' <a href="{ann["pdf_url"]}" target="_blank" rel="noopener" '
                f'style="color:var(--accent);font-size:0.75em;margin-left:8px">PDF ↗</a>'
            )
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'padding:5px 0;border-bottom:1px solid var(--bg-3);font-size:0.82em">'
            f'<div><span style="color:var(--text-dim);margin-right:10px;font-size:0.85em;'
            f'white-space:nowrap">{ann.get("date","")}</span>'
            f'<span style="color:var(--text-secondary)">{ann.get("title","")[:100]}</span>'
            f'{pdf_link}</div>'
            f'</div>\n'
        )
    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="font-size:0.75em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">{label}</div>'
        f'<div>{rows_html}</div>'
        f'</div>'
    )


# ── Sector list (DB) ─────────────────────────────────────────────────────────

@router.get("/sectors-list")
async def sectors_list(request: Request):
    """Return distinct sector values from psx_companies as JSON for the filter dropdown."""
    require_session(request)
    from database.connection import get_db
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT sector_name FROM psx_companies WHERE sector_name IS NOT NULL AND sector_name != '' ORDER BY sector_name"
    ).fetchall()
    return JSONResponse([r["sector_name"] for r in rows])


# ── Industry Overview ─────────────────────────────────────────────────────────

@router.get("/industry/sectors", response_class=HTMLResponse)
async def industry_sectors(
    request: Request,
    exchange: str = Query("PSX"),
):
    """Fetch live PSX sector breakdown and render as HTML."""
    require_session(request)

    if exchange.upper() != "PSX":
        return HTMLResponse(
            '<div class="alert alert-warning">Live sector breakdown is currently available for PSX only.</div>'
        )

    from intelligence.exchange_crawlers.psx import fetch_psx_market_summary
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, fetch_psx_market_summary)

    if data.get("error"):
        return HTMLResponse(f'<div class="alert alert-error">PSX market summary error: {data["error"][:120]}</div>')

    sectors  = data.get("sectors", [])
    mstatus  = data.get("market_status", {})

    if not sectors:
        return HTMLResponse(
            '<div class="empty-state">'
            '<div class="empty-icon">📊</div>'
            '<div class="empty-text">No sector data returned. PSX market may be closed.</div>'
            '</div>'
        )

    status_str = " · ".join(f"{k}: {v}" for k, v in list(mstatus.items())[:4])
    rows_html  = ""
    for sec in sectors:
        avg_ch = sec.get("avg_change")
        ch_color = "#4ade80" if (avg_ch or 0) >= 0 else "#f87171"
        ch_str   = f'<span style="color:{ch_color};font-weight:600">{avg_ch:+.2f}</span>' if avg_ch is not None else "—"
        vol_str  = _fmt_num(sec.get("total_vol"))
        sector_name = sec["sector"]
        sector_attr = _html.escape(sector_name, quote=True)
        rows_html += (
            f'<tr style="border-bottom:1px solid var(--bg-3)">'
            f'<td style="padding:6px 10px;font-size:0.85em;font-weight:500">{sector_name}</td>'
            f'<td style="padding:6px 10px;font-size:0.82em;text-align:center">{sec["count"]}</td>'
            f'<td style="padding:6px 10px;font-size:0.82em;text-align:right">{ch_str}</td>'
            f'<td style="padding:6px 10px;font-size:0.82em;text-align:right;color:var(--text-muted)">{vol_str}</td>'
            f'<td style="padding:6px 10px">'
            f'<button class="btn" style="padding:2px 8px;font-size:0.72em" '
            f'hx-post="/markets/companies" '
            f'hx-vals=\'{{"market_region":"pakistan","search":"","sector":"{sector_attr}"}}\' '
            f'hx-target="#company-table" hx-swap="innerHTML" '
            f'onclick="document.getElementById(\'sector-filter\').value=this.dataset.sn;'
            f'document.getElementById(\'company-table\').scrollIntoView({{behavior:\'smooth\'}});" '
            f'data-sn="{sector_attr}">View in Companies ↓</button>'
            f'</td>'
            f'</tr>\n'
        )

    return HTMLResponse(
        f'<div style="font-size:0.72em;color:var(--text-dim);margin-bottom:10px">{status_str}</div>'
        f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="font-size:0.72em;color:var(--text-muted);text-transform:uppercase;letter-spacing:1px">'
        f'<th style="text-align:left;padding:4px 10px">Sector</th>'
        f'<th style="text-align:center;padding:4px 10px">Stocks</th>'
        f'<th style="text-align:right;padding:4px 10px">Avg Chg</th>'
        f'<th style="text-align:right;padding:4px 10px">Volume</th>'
        f'<th style="padding:4px 10px">Companies</th>'
        f'</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        f'</table></div>'
    )


# ── Competitor Clusters ───────────────────────────────────────────────────────

@router.get("/clusters", response_class=HTMLResponse)
async def clusters_panel(request: Request):
    """HTMX: render the full clusters panel."""
    require_session(request)
    clusters = get_clusters_with_members()
    return HTMLResponse(_render_clusters_html(clusters))


@router.post("/clusters/create", response_class=HTMLResponse)
async def clusters_create(
    request: Request,
    cluster_name: str  = Form(""),
    exchange_id: str   = Form("PSX"),
    description: str   = Form(""),
):
    require_session(request)
    name = cluster_name.strip()
    if not name:
        return HTMLResponse('<div class="alert alert-error">Cluster name required.</div>')
    create_cluster(name, exchange_id, description)
    clusters = get_clusters_with_members()
    return HTMLResponse(_render_clusters_html(clusters))


@router.delete("/clusters/{cluster_id}", response_class=HTMLResponse)
async def clusters_delete(request: Request, cluster_id: int):
    require_session(request)
    delete_cluster(cluster_id)
    clusters = get_clusters_with_members()
    return HTMLResponse(_render_clusters_html(clusters))


@router.post("/clusters/{cluster_id}/add", response_class=HTMLResponse)
async def clusters_add_member(
    request: Request,
    cluster_id: int,
    exchange_id: str   = Form("PSX"),
    ticker: str        = Form(""),
    company_name: str  = Form(""),
):
    require_session(request)
    ticker = ticker.strip().upper()
    if ticker:
        add_cluster_member(cluster_id, exchange_id, ticker, company_name)
    clusters = get_clusters_with_members()
    return HTMLResponse(_render_clusters_html(clusters))


@router.delete("/clusters/{cluster_id}/members/{ticker}", response_class=HTMLResponse)
async def clusters_remove_member(request: Request, cluster_id: int, ticker: str):
    require_session(request)
    remove_cluster_member(cluster_id, ticker.upper())
    clusters = get_clusters_with_members()
    return HTMLResponse(_render_clusters_html(clusters))


def _render_clusters_html(clusters: list) -> str:
    if not clusters:
        return (
            '<div style="font-size:0.82em;color:var(--text-muted);padding:12px 0">'
            'No competitor groups yet. Create one below to start grouping companies for analysis.'
            '</div>'
        )
    parts = []
    for c in clusters:
        cid       = c["id"]
        cname     = c["name"]
        cexch     = c["exchange_id"]
        members   = c.get("members", [])

        ticker_tags_list = []
        for m in members:
            mticker = m["ticker"]
            ticker_tags_list.append(
                f'<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;'
                f'background:var(--bg-3);border-radius:12px;font-size:0.78em;margin:2px">'
                f'<span class="text-mono" style="color:var(--accent)">{mticker}</span>'
                f'<button style="background:none;border:none;color:var(--text-dim);cursor:pointer;'
                f'padding:0;font-size:0.9em;line-height:1" '
                f'hx-delete="/markets/clusters/{cid}/members/{mticker}" '
                f'hx-target="#clusters-panel" hx-swap="innerHTML" title="Remove">×</button>'
                f'</span>'
            )
        ticker_tags = "".join(ticker_tags_list)
        empty_msg   = '<span style="font-size:0.8em;color:var(--text-dim)">No stocks added yet</span>'
        nstock      = len(members)
        stock_label = "stocks" if nstock != 1 else "stock"
        parts.append(
            f'<div style="border:1px solid var(--border);border-radius:var(--radius);'
            f'padding:12px 14px;margin-bottom:10px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<div><span style="font-weight:600;font-size:0.95em">{cname}</span>'
            f'  <span class="badge badge-gray" style="font-size:0.72em">{cexch}</span>'
            f'  <span style="color:var(--text-dim);font-size:0.78em;margin-left:6px">'
            f'{nstock} {stock_label}</span></div>'
            f'<button class="btn" style="padding:2px 8px;font-size:0.72em;opacity:0.5" '
            f'hx-delete="/markets/clusters/{cid}" '
            f'hx-target="#clusters-panel" hx-swap="innerHTML" '
            f'title="Delete group">✕ Delete</button>'
            f'</div>'
            f'<div style="margin-bottom:8px;min-height:24px">{ticker_tags or empty_msg}</div>'
            f'<div style="display:flex;gap:6px;align-items:center">'
            f'<input type="text" placeholder="Add ticker…" '
            f'id="add-ticker-{cid}" style="width:120px;font-size:0.82em;padding:3px 8px;'
            f'background:var(--bg-3);border:1px solid var(--border);border-radius:4px;color:var(--text-primary)" '
            f"onkeydown=\"if(event.key==='Enter')document.getElementById('add-btn-{cid}').click()\">"
            f'<button class="btn btn-primary" id="add-btn-{cid}" style="padding:3px 10px;font-size:0.78em" '
            f"onclick=\"addClusterMember({cid}, '{cexch}')\">Add</button>"
            f'</div>'
            f'</div>'
        )
    return "".join(parts)
