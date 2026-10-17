"""
routers/debug.py - Developer/verification endpoints.

Session-auth protected; expose no data and perform no mutations.

Available endpoints:
  GET  /debug/scrapers   - Run gov scrapers and show status (DEBUG=true required)
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import html

from security.auth import require_session
from config.settings import settings

router = APIRouter()


@router.get("/scrapers", response_class=HTMLResponse)
async def test_scrapers(request: Request):
    """
    Run every government scraper and show its status + row count. DEBUG-only.
    Also surfaces the SBP policy-rate value, trend and next MPC meeting so the
    operator can confirm the macro data is current.
    """
    require_session(request)
    if not settings.DEBUG:
        return HTMLResponse(
            '<div style="padding:40px;text-align:center;color:#f87171">'
            '/debug/scrapers is only available when DEBUG=true.</div>',
            status_code=403,
        )

    import asyncio
    from database.connection import get_db

    def _run_all():
        from intelligence.gov_scrapers import get_scrapers_for_run
        db = get_db()
        rows = []
        scrapers = get_scrapers_for_run("general", include_ministries=True)
        for sc in scrapers:
            sid = getattr(sc, "scraper_id", sc.__class__.__name__)
            try:
                res    = sc.fetch()                 # ScrapeResult (BaseScraper contract)
                status = getattr(res.status, "value", res.status)
                nrows  = res.rows_inserted
                err    = res.error_message or ""
                data   = (res.artifacts or {}).get("data")
                fields = ", ".join(sorted(data.keys())) if isinstance(data, dict) else ""
            except Exception as e:
                status, nrows, err, fields = "error", 0, str(e)[:200], ""
            rows.append({"id": sid, "status": status, "rows": nrows, "error": err, "fields": fields})
        return rows

    loop = asyncio.get_running_loop()
    try:
        results = await asyncio.wait_for(loop.run_in_executor(None, _run_all), timeout=120)
    except asyncio.TimeoutError:
        results = [{"id": "(timeout)", "status": "error", "rows": 0,
                    "error": "scraper batch exceeded 120s", "fields": ""}]

    # SBP rate status panel
    try:
        from intelligence.gov_scrapers.sbp_monetary import get_rate_status
        st = get_rate_status()
        sbp_panel = (
            f"<div style='margin:1rem 0;padding:12px;border:1px solid #222;border-radius:6px'>"
            f"<b>SBP policy rate:</b> {st.get('rate')}% (as of {st.get('as_of')}) · "
            f"trend: {st.get('trend')} · next MPC: {st.get('next_mpc')} · "
            f"refresh due: {st.get('refresh_due')}</div>"
        )
    except Exception as e:
        sbp_panel = f"<div style='color:#f87171'>SBP status error: {html.escape(str(e))}</div>"

    rows_html = ""
    for r in results:
        color = "#00ff88" if r["status"] == "success" else "#ffaa00" if r["status"] == "partial" else "#ff4444"
        rows_html += (
            f"<tr><td>{html.escape(r['id'])}</td>"
            f"<td style='color:{color}'>{html.escape(r['status'])}</td>"
            f"<td>{r['rows']}</td>"
            f"<td style='font-size:0.8em;color:#888'>{html.escape(r['error'] or r['fields'])}</td></tr>"
        )

    page = f"""<html><head><style>
    body{{background:#0a0a0a;color:#ccc;font-family:monospace;padding:2rem}}
    table{{border-collapse:collapse;width:100%}}
    th{{background:#1a1a1a;padding:8px;text-align:left}}
    td{{padding:8px;border-bottom:1px solid #222}}
    </style></head><body>
    <h2>Government Scraper Status</h2>
    {sbp_panel}
    <table><tr><th>Scraper</th><th>Status</th><th>Rows</th><th>Detail</th></tr>{rows_html}</table>
    <p style="color:#666;margin-top:2rem">
    Impact map - SBP→banks · FBR→PSO/SNGP · NEPRA→HUBC/KAPCO/KEL ·
    OGRA→PSO/OGDC · PBS LSMI→LUCK/FFC/NML · PTA→PTCL/TRG · World Bank→KSE-100 macro.
    </p></body></html>"""
    return HTMLResponse(content=page)
