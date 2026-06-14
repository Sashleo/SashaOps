"""
routers/debug.py — Developer/verification endpoints.

These routes are protected by session auth — only accessible when logged in.
They expose no data and perform no mutations; they exist purely for verification.

Available endpoints:
  GET  /debug/sanitise              — Sanitiser test form
  POST /debug/sanitise              — Show original, sanitised, and restored versions
  GET  /debug/tokensheet/{run_id}   — Token sheet inspector (DEBUG=true required)
"""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import html

from security.auth import require_session
from security.sanitiser import build_sanitiser_for_client
from config.regions import list_region_ids
from config.settings import settings

router    = APIRouter()
templates = Jinja2Templates(directory="templates")

# Sample Systems Limited prompt for the pre-filled test
_SAMPLE_PROMPT = (
    "Analyse Systems Limited, the Lahore-based IT services company listed on PSX (KSE-100). "
    "The company employs approximately 12,000 staff and reported PKR 32B in revenue last year. "
    "Their CEO Asif Peer has guided strong growth. Key competitors include NetSol Technologies "
    "and TRG Pakistan. The company is regulated by SECP and SBP. "
    "Contact: info@systemsltd.com, +92-42-35761999."
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def sanitise_debug_page(request: Request):
    require_session(request)
    region_ids = list_region_ids()
    return HTMLResponse(_render_form(sample_prompt=_SAMPLE_PROMPT, region_ids=region_ids))


@router.post("", response_class=HTMLResponse)
@router.post("/", response_class=HTMLResponse)
async def sanitise_debug_submit(
    request: Request,
    prompt: str = Form(...),
    business_name: str = Form("Systems Limited"),
    region_id: str = Form("pakistan"),
    industry: str = Form("IT Services"),
):
    require_session(request)

    sanitiser = build_sanitiser_for_client(
        business_name=business_name,
        region_id=region_id,
        industry=industry,
    )

    sanitised  = sanitiser.sanitise(prompt)
    restored   = sanitiser.restore(sanitised)
    summary    = sanitiser.get_mapping_summary()
    region_ids = list_region_ids()

    return HTMLResponse(
        _render_form(
            sample_prompt=prompt,
            region_ids=region_ids,
            original=prompt,
            sanitised=sanitised,
            restored=restored,
            summary=summary,
            business_name=business_name,
            region_id=region_id,
            industry=industry,
        )
    )


def _render_form(
    sample_prompt: str = "",
    region_ids: list = None,
    original: str = None,
    sanitised: str = None,
    restored: str = None,
    summary: dict = None,
    business_name: str = "Systems Limited",
    region_id: str = "pakistan",
    industry: str = "IT Services",
) -> str:
    region_options = "".join(
        f'<option value="{r}"{" selected" if r == region_id else ""}>{r.replace("_"," ").title()}</option>'
        for r in (region_ids or ["pakistan"])
    )

    result_html = ""
    if original is not None:

        def _box(label: str, content: str, colour: str) -> str:
            escaped = html.escape(content)
            return f"""
            <div style="margin-bottom:16px">
              <div style="font-size:0.75em;text-transform:uppercase;letter-spacing:1px;
                          color:{colour};margin-bottom:6px;font-weight:600">{label}</div>
              <pre style="background:var(--bg-3);padding:14px;border-radius:var(--radius);
                          font-size:0.78em;white-space:pre-wrap;word-break:break-word;
                          border-left:3px solid {colour};max-height:280px;overflow-y:auto">{escaped}</pre>
            </div>"""

        entity_rows = "".join(
            f'<div style="display:flex;justify-content:space-between;padding:3px 0;'
            f'border-bottom:1px solid var(--bg-3);font-size:0.8em">'
            f'<span style="color:var(--text-muted)">{html.escape(cat)}</span>'
            f'<span style="color:var(--accent)">{count}</span></div>'
            for cat, count in (summary or {}).get("categories", {}).items()
        )

        status_colour = "var(--accent)" if (summary or {}).get("primary_company_anonymised") else "#f87171"
        result_html = f"""
        <div class="card" style="margin-top:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <div class="card-title" style="margin:0">Sanitisation Result</div>
            <div style="font-size:0.8em;color:{status_colour}">
              {'✓ Primary company anonymised' if (summary or {}).get('primary_company_anonymised') else '⚠ Primary company NOT found in output'}
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
            <div>
              <div style="font-size:0.75em;text-transform:uppercase;letter-spacing:1px;
                          color:var(--text-muted);margin-bottom:8px">Mapping Summary</div>
              <div style="background:var(--bg-3);padding:12px;border-radius:var(--radius)">
                <div style="font-size:0.9em;color:var(--accent);font-weight:600;margin-bottom:8px">
                  {(summary or {}).get('total_entities_anonymised', 0)} entities anonymised
                </div>
                {entity_rows or '<div style="color:var(--text-dim);font-size:0.8em">No entities mapped</div>'}
              </div>
            </div>
            <div>
              <div style="font-size:0.75em;text-transform:uppercase;letter-spacing:1px;
                          color:var(--text-muted);margin-bottom:8px">Flags</div>
              <div style="background:var(--bg-3);padding:12px;border-radius:var(--radius);font-size:0.82em;display:grid;gap:4px">
                <div>Company anonymised: <span style="color:{status_colour}">{'Yes' if (summary or {}).get('primary_company_anonymised') else 'No'}</span></div>
                <div>Region anonymised: <span style="color:var(--accent)">{'Yes' if (summary or {}).get('region_anonymised') else 'No'}</span></div>
              </div>
            </div>
          </div>

          {_box('A) Original prompt (what you wrote)', original, 'var(--text-muted)')}
          {_box('B) Sanitised prompt (what the AI API receives)', sanitised, '#f59e0b')}
          {_box('C) Restored response (what you see after restore())', restored, 'var(--accent)')}
        </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Prompt Sanitiser Test — SashaOps</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div style="max-width:900px;margin:40px auto;padding:0 20px">

  <div style="margin-bottom:24px">
    <a href="/settings" style="color:var(--accent);font-size:0.85em">← Back to Settings</a>
  </div>

  <div style="margin-bottom:20px">
    <div style="font-size:1.3em;font-weight:700;margin-bottom:4px">Prompt Sanitiser Test</div>
    <div style="font-size:0.85em;color:var(--text-muted)">
      Verify what the AI API actually receives. No data is sent to any external service on this page.
    </div>
  </div>

  <div class="card">
    <form method="POST" action="/debug/sanitise">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">
        <div class="form-group" style="margin:0">
          <label class="form-label">Business Name</label>
          <input type="text" name="business_name" class="form-input"
                 value="{html.escape(business_name)}" required>
        </div>
        <div class="form-group" style="margin:0">
          <label class="form-label">Region</label>
          <select name="region_id" class="form-select">{region_options}</select>
        </div>
        <div class="form-group" style="margin:0">
          <label class="form-label">Industry</label>
          <input type="text" name="industry" class="form-input"
                 value="{html.escape(industry)}">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Prompt to test</label>
        <textarea name="prompt" class="form-input"
                  style="height:140px;font-size:0.85em;font-family:var(--font-mono)"
                  required>{html.escape(sample_prompt)}</textarea>
      </div>
      <button type="submit" class="btn btn-primary">Run Sanitiser</button>
    </form>
  </div>

  {result_html}

</div>
</body>
</html>"""


# ── Token sheet inspector ──────────────────────────────────────────────────────

@router.get("/tokensheet/{run_id}", response_class=HTMLResponse)
async def tokensheet_inspector(request: Request, run_id: str, module: str = ""):
    """
    Show the token sheet and prompt/validation state for a completed run.
    Only available when DEBUG=true.
    """
    require_session(request)

    if not settings.DEBUG:
        return HTMLResponse(
            '<div style="padding:40px;text-align:center;color:#f87171">'
            'Token sheet inspector is only available when DEBUG=true.</div>',
            status_code=403,
        )

    # Load run state from run_manager
    try:
        from core.run_manager import load_run
        run = load_run(run_id)
    except Exception as e:
        return HTMLResponse(
            f'<div style="padding:40px;color:#f87171">Run not found: {html.escape(str(e))}</div>',
            status_code=404,
        )

    if run is None:
        return HTMLResponse(
            f'<div style="padding:40px;color:#f87171">Run {html.escape(run_id)} not found.</div>',
            status_code=404,
        )

    # Extract token sheet if stored on run state
    token_sheet = getattr(run, "token_sheet", None)
    masked = token_sheet.masked_view() if token_sheet else {}

    # Gather per-module debug info stored during Stage 2
    module_debug = getattr(run, "module_debug", {})  # {module_name: {prompt, validator_result, restored}}

    def _esc(s):
        return html.escape(str(s) if s else "")

    token_rows = "".join(
        f'<tr><td style="font-family:monospace;color:#f59e0b">{_esc(tok)}</td>'
        f'<td style="color:#6b7280">***</td></tr>'
        for tok in sorted(masked.keys())
    ) or "<tr><td colspan=2 style='color:var(--text-dim)'>No token sheet attached to this run</td></tr>"

    module_list = list(module_debug.keys()) if module_debug else []
    selected = module if module in module_list else (module_list[0] if module_list else "")
    mod_info = module_debug.get(selected, {})

    module_tabs = "".join(
        f'<a href="/debug/tokensheet/{_esc(run_id)}?module={_esc(m)}" '
        f'style="padding:6px 12px;border-radius:4px;font-size:0.82em;text-decoration:none;'
        f'{"background:var(--accent);color:#000" if m == selected else "color:var(--text-muted)"}">'
        f'{_esc(m)}</a>'
        for m in module_list
    ) or "<span style='color:var(--text-dim);font-size:0.82em'>No module debug data yet</span>"

    def _pre(label, content, colour):
        return (
            f'<div style="margin-bottom:16px">'
            f'<div style="font-size:0.72em;text-transform:uppercase;letter-spacing:1px;'
            f'color:{colour};margin-bottom:6px">{_esc(label)}</div>'
            f'<pre style="background:var(--bg-3);padding:12px;border-radius:4px;'
            f'font-size:0.78em;white-space:pre-wrap;word-break:break-word;'
            f'border-left:3px solid {colour};max-height:300px;overflow-y:auto">'
            f'{_esc(str(content or "(empty)"))}</pre></div>'
        )

    validator_html = ""
    if "validator_result" in mod_info:
        vr = mod_info["validator_result"]
        passed = getattr(vr, "passed", True)
        strat = getattr(vr, "strategy", "pass")
        viols = getattr(vr, "violations", [])
        colour = "var(--accent)" if passed else "#f87171"
        validator_html = (
            f'<div style="margin-bottom:16px;padding:10px 14px;background:var(--bg-3);'
            f'border-radius:4px;border-left:3px solid {colour};font-size:0.82em">'
            f'<strong style="color:{colour}">{"PASS" if passed else "FAIL"}</strong> '
            f'— strategy: <code>{_esc(strat)}</code>'
            + ("".join(f'<div style="color:#f87171;margin-top:4px">• {_esc(v)}</div>' for v in viols) if viols else "")
            + "</div>"
        )

    module_detail = ""
    if selected:
        module_detail = (
            f'<div style="margin-top:20px">'
            + validator_html
            + _pre("Prompt sent to Gemini", mod_info.get("prompt", "(not captured)"), "#f59e0b")
            + _pre("Final restored output", mod_info.get("restored", "(not captured)"), "var(--accent)")
            + "</div>"
        )

    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Token Sheet — {_esc(run_id[:8])} — SashaOps</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div style="max-width:1100px;margin:40px auto;padding:0 20px">
  <div style="margin-bottom:16px">
    <a href="/debug" style="color:var(--accent);font-size:0.85em">← Debug</a>
    &nbsp;/&nbsp;
    <a href="/intelligence" style="color:var(--accent);font-size:0.85em">Intelligence</a>
  </div>

  <div style="font-size:1.2em;font-weight:700;margin-bottom:4px">Token Sheet Inspector</div>
  <div style="font-size:0.82em;color:var(--text-muted);margin-bottom:20px">
    Run: <code>{_esc(run_id)}</code> · Real values masked · DEBUG mode only
  </div>

  <div style="display:grid;grid-template-columns:300px 1fr;gap:20px">
    <div>
      <div class="card">
        <div class="card-title">Registered Tokens ({len(masked)})</div>
        <table style="width:100%;font-size:0.8em;border-collapse:collapse">
          <tr>
            <th style="text-align:left;color:var(--text-muted);padding:4px 0">Token</th>
            <th style="text-align:left;color:var(--text-muted);padding:4px 0">Value</th>
          </tr>
          {token_rows}
        </table>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-title" style="margin-bottom:12px">Module Inspector</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
          {module_tabs}
        </div>
        {module_detail or '<div style="color:var(--text-dim);font-size:0.85em">Select a module above to inspect its prompt and output.</div>'}
      </div>
    </div>
  </div>
</div>
</body>
</html>""")


@router.get("/debug/run-tests", response_class=HTMLResponse)
async def run_tests(request: Request):
    """Run the test suite and return results as HTML. DEBUG only."""
    import subprocess
    import os
    project_root = os.path.dirname(os.path.dirname(__file__))
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=60,
    )
    output = result.stdout + result.stderr
    passed = "passed" in output
    color = "#00ff88" if passed else "#ff4444"
    html = f"""
    <html>
    <head><style>
        body {{ background: #0a0a0a; color: #ccc; font-family: monospace; padding: 2rem; }}
        pre {{ background: #111; padding: 1rem; border-radius: 6px; 
               border-left: 4px solid {color}; white-space: pre-wrap; }}
        h2 {{ color: {color}; }}
    </style></head>
    <body>
        <h2>{" Tests Passed" if passed else " Tests Failed"}</h2>
        <pre>{output}</pre>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
