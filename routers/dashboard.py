"""
routers/dashboard.py — Command centre / dashboard routes.
"""

from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import structlog

from security.auth import require_session, update_session_client
from database.clients import list_clients, create_client, touch_client
from database.connection import get_db

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_sidebar_data(request: Request) -> dict:
    """Build the data needed by the base template sidebar."""
    session     = getattr(request.state, "session", {})
    client_id   = session.get("client_id") if session else None
    clients     = list_clients()
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


def _data_health() -> list[dict]:
    """Real per-scraper status (Task 2.6), read from scrape_log via the job
    registry itself so this list can never drift from what actually runs."""
    from core.data_health import compute_data_health
    return compute_data_health()


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_session(request)
    from core.data_health import get_sanity_violations
    sidebar = _get_sidebar_data(request)
    return templates.TemplateResponse("dashboard.html", {
        "request":     request,
        "active_page": "dashboard",
        "data_health": _data_health(),
        "violations":  get_sanity_violations(),
        **sidebar,
    })


@router.get("/data-health", response_class=HTMLResponse)
async def data_health_partial(request: Request):
    """HTMX partial: current per-job status. Polled while a manual refresh is
    in flight so rows update live without a full page reload."""
    require_session(request)
    from core.data_health import any_running, get_sanity_violations
    return templates.TemplateResponse("partials/data_health.html", {
        "request": request,
        "data_health": _data_health(),
        "violations": get_sanity_violations(),
        "any_running": any_running(),
    })


@router.post("/data-health/refresh")
async def data_health_refresh(request: Request, job_id: str = Form("")):
    """Queue a manual refresh (one job, or every job if job_id is blank) and
    return the partial immediately so polling can take over from there."""
    require_session(request)
    from core.data_health import trigger_refresh, trigger_refresh_all, any_running, get_sanity_violations
    if job_id:
        trigger_refresh(job_id)
    else:
        trigger_refresh_all()
    return templates.TemplateResponse("partials/data_health.html", {
        "request": request,
        "data_health": _data_health(),
        "violations": get_sanity_violations(),
        "any_running": any_running(),
    })


@router.post("/set-client")
async def set_client(request: Request, client_id: str = Form("")):
    """
    Change the active client in the session. This must never 500 — a non-2xx here
    fires the global htmx error toast ("SashaOps encountered an error"), which is
    exactly the spurious top-right error seen when selecting a client. All mutations
    are best-effort and the indicator is always returned with a 200.
    """
    from fastapi.responses import HTMLResponse
    require_session(request)

    active_client = None
    try:
        token = request.cookies.get("sashaops_session")
        if token and client_id:
            update_session_client(token, client_id)
            touch_client(client_id)
        clients       = list_clients()
        active_client = next((c for c in clients if c["id"] == client_id), None)
    except Exception as e:
        log.warning("set_client failed (returning indicator anyway)", error=str(e)[:120])

    if active_client:
        html = (f'<div id="client-indicator" style="margin-bottom:6px">'
                f'<span style="color:var(--accent)">●</span> {active_client["name"]}</div>')
    else:
        html = ('<div id="client-indicator" style="margin-bottom:6px">'
                '<span style="color:var(--text-dim)">●</span> No client selected</div>')
    return HTMLResponse(html)


@router.get("/client/{client_id}/peek", response_class=HTMLResponse)
async def client_peek(request: Request, client_id: str):
    """Return an HTML popup fragment showing the last analysis for a client."""
    require_session(request)
    db = get_db()

    client_row = db.execute(
        "SELECT * FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if not client_row:
        return HTMLResponse("<div class='alert alert-error'>Client not found.</div>")
    client = dict(client_row)

    # Last report
    last_report = db.execute(
        """
        SELECT id, business_name, created_at, pdf_path, docx_path
        FROM reports
        WHERE client_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (client_id,),
    ).fetchone()

    report_count = db.execute(
        "SELECT count(*) FROM reports WHERE client_id = ?", (client_id,)
    ).fetchone()[0]

    accent = client.get("accent_color") or "#00ff87"
    region = "Pakistan"

    report_section = ""
    if last_report:
        rdate = (last_report["created_at"] or "")[:10]
        report_section = f"""
        <div style="margin:10px 0; font-size:0.82em; color:var(--text-muted)">
          Last report: <strong style="color:var(--text-secondary)">{last_report["business_name"]}</strong> - {rdate}
        </div>"""

    html = f"""
    <div style="padding:4px">
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px">
        <div style="width:10px; height:10px; border-radius:50%; background:{accent}; flex-shrink:0"></div>
        <div>
          <div style="font-weight:700; font-size:1.05em">{client["name"]}</div>
          <div style="font-size:0.82em; color:var(--text-muted)">{client.get("industry") or "Industry not set"} · {region}</div>
        </div>
      </div>
      <div style="display:flex; gap:16px; font-size:0.82em; color:var(--text-muted); margin-bottom:4px">
        <span>{report_count} report{"s" if report_count != 1 else ""}</span>
      </div>
      {report_section}
      <div style="display:flex; gap:8px; margin-top:14px">
        <button class="btn btn-primary" style="flex:1"
                onclick="fetch('/dashboard/set-client',{{method:'POST',credentials:'same-origin',body:new URLSearchParams({{client_id:'{client_id}'}})}}).then(()=>window.location='/psx')">
          Open PSX Analyser →
        </button>
        <a href="/reports" class="btn" style="flex:0; white-space:nowrap">Reports</a>
      </div>
    </div>
    """
    return HTMLResponse(html)


@router.post("/clients")
async def create_client_post(
    request: Request,
    name: str = Form(...),
    industry: str = Form(""),
    accent_color: str = Form("#00ff87"),
):
    require_session(request)
    client = create_client(
        name=name,
        industry=industry or None,
        accent_color=accent_color,
    )
    # Set as active client
    token = request.cookies.get("sashaops_session")
    if token:
        update_session_client(token, client["id"])

    return RedirectResponse(url="/dashboard", status_code=302)
