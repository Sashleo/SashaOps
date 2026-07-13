"""
routers/dashboard.py — Command centre / dashboard routes.
"""

import json
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


def _age_status(ts: str | None):
    """Return (display, status) where status is ok|amber|red|none from an ISO/date string."""
    if not ts:
        return "never", "red"
    raw = str(ts).strip().replace("T", " ")
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw[:len(fmt) + 6], fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return str(ts)[:19], "ok"
    hours = (datetime.utcnow() - parsed).total_seconds() / 3600.0
    status = "red" if hours > 72 else "amber" if hours > 24 else "ok"
    return str(ts)[:19], status


def _max_ts(db, table: str, col: str) -> str | None:
    try:
        row = db.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _data_health(db) -> list[dict]:
    """Freshness of the core data sources, read from the scraper-populated tables."""
    prices     = _max_ts(db, "psx_prices", "fetched_at")
    financials = _max_ts(db, "scraper_runs", "completed_at")  # psx financials via scraper batch
    macro      = _max_ts(db, "pk_macro", "fetched_at")
    medians    = _max_ts(db, "psx_sector_medians", "computed_date")

    # Feed sources OK / total from the most recent scraper cycle status counts.
    feeds_val, feeds_status = "n/a", "amber"
    try:
        ok    = db.execute("SELECT COUNT(*) FROM scraper_runs WHERE status='success'").fetchone()[0]
        total = db.execute("SELECT COUNT(DISTINCT scraper_id) FROM scraper_runs").fetchone()[0]
        if total:
            feeds_val = f"{ok}/{total}"
            feeds_status = "ok" if ok >= total else "amber" if ok else "red"
    except Exception:
        pass

    rows = []
    for label, ts in (
        ("PSX prices last refreshed", prices),
        ("PSX financials last refreshed", financials),
        ("Macro (SBP/PBS) last updated", macro),
        ("Sector medians last computed", medians),
    ):
        disp, status = _age_status(ts)
        rows.append({"label": label, "value": disp, "status": status})
    rows.insert(3, {"label": "Feed sources OK", "value": feeds_val, "status": feeds_status})
    return rows


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    require_session(request)
    db = get_db()
    sidebar = _get_sidebar_data(request)
    return templates.TemplateResponse("dashboard.html", {
        "request":     request,
        "active_page": "dashboard",
        "data_health": _data_health(db),
        **sidebar,
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

    # Last completed run
    last_run = db.execute(
        """
        SELECT run_id, business_name, started_at, completed_at,
               modules_complete, modules_failed, modules_requested,
               analysis_summary, data_summary
        FROM analysis_runs
        WHERE client_id = ? AND status = 'complete'
        ORDER BY completed_at DESC LIMIT 1
        """,
        (client_id,),
    ).fetchone()

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

    # Aggregate counts
    run_count    = db.execute(
        "SELECT count(*) FROM analysis_runs WHERE client_id = ?", (client_id,)
    ).fetchone()[0]
    report_count = db.execute(
        "SELECT count(*) FROM reports WHERE client_id = ?", (client_id,)
    ).fetchone()[0]

    # Parse last run summary
    key_findings = []
    modules_complete = 0
    modules_total    = 0
    if last_run:
        try:
            a_sum = json.loads(last_run["analysis_summary"] or "{}")
            key_findings     = a_sum.get("key_findings", [])[:4]
            modules_complete = a_sum.get("modules_complete", 0)
            modules_total    = a_sum.get("total_modules", 0)
        except Exception:
            pass

    accent = client.get("accent_color") or "#00ff87"
    region = "Pakistan"

    # Build popup HTML inline
    run_section = ""
    if last_run:
        date_str = (last_run["completed_at"] or last_run["started_at"] or "")[:10]
        run_section = f"""
        <div style="margin:14px 0; padding:12px; background:var(--bg-3); border-radius:var(--radius)">
          <div style="font-size:0.7em; color:var(--text-dim); text-transform:uppercase; letter-spacing:1px; margin-bottom:6px">Last Analysis</div>
          <div style="font-weight:600; margin-bottom:2px">{last_run["business_name"]}</div>
          <div style="font-size:0.82em; color:var(--text-muted); margin-bottom:8px">{date_str} · {modules_complete}/{modules_total} modules · {region}</div>
          {"".join(f'<div style="font-size:0.8em; color:var(--text-secondary); padding:2px 0; border-bottom:1px solid var(--bg-2)">· {kf.get("finding","")[:100]}</div>' for kf in key_findings)}
        </div>"""
    else:
        run_section = """
        <div style="margin:14px 0; padding:12px; background:var(--bg-3); border-radius:var(--radius); color:var(--text-dim); font-size:0.85em; text-align:center">
          No analyses run yet
        </div>"""

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
        <span>{run_count} run{"s" if run_count != 1 else ""}</span>
        <span>{report_count} report{"s" if report_count != 1 else ""}</span>
      </div>
      {run_section}
      {report_section}
      <div style="display:flex; gap:8px; margin-top:14px">
        <button class="btn btn-primary" style="flex:1"
                onclick="fetch('/dashboard/set-client',{{method:'POST',credentials:'same-origin',body:new URLSearchParams({{client_id:'{client_id}'}})}}).then(()=>window.location='/intelligence')">
          Open Intelligence →
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
