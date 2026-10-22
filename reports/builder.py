"""
reports/builder.py — Report assembly and generation pipeline.

Takes analysis results from the database and assembles them into
PDF and Word reports with post-quantum digital signatures.
"""

import os
import re
import uuid
import json
from datetime import datetime
from typing import Optional, List, Tuple
import structlog

import base64

from database.connection import get_db
from config.settings import settings
from reports.pdf_renderer import render_pdf
from reports.signer import sign_report
from security.crypto import encrypt_bytes, decrypt_bytes

log = structlog.get_logger()

REPORT_OUTPUT_PATH = "/app/data/reports"


def read_report_file(file_path: str, client_key: bytes) -> Optional[bytes]:
    """
    Read a report file from disk and decrypt it in memory.

    Handles two cases:
    - Encrypted file (.enc suffix): decrypt with client_key
    - Legacy plaintext file (no .enc): serve raw bytes with a log warning

    Returns decrypted bytes, or None if file missing/corrupted.
    """
    if not file_path or not os.path.exists(file_path):
        return None
    with open(file_path, "rb") as f:
        raw = f.read()
    if file_path.endswith(".enc"):
        decrypted = decrypt_bytes(raw, client_key)
        if decrypted is None:
            log.error("Report decryption failed — wrong key or tampered file", path=file_path)
        return decrypted
    # Legacy plaintext file — warn and serve as-is
    log.warning("Serving legacy unencrypted report", path=file_path)
    return raw


def list_reports(client_id: str, limit: int = 50) -> List[dict]:
    """List reports for a client."""
    db = get_db()
    rows = db.execute(
        """
        SELECT id, business_name, report_type, modules_included,
               pdf_path, docx_path, signature_path, signing_algo, created_at
        FROM reports
        WHERE client_id = ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        if d.get("modules_included"):
            try:
                d["modules_included"] = json.loads(d["modules_included"])
            except Exception:
                d["modules_included"] = []
        d["pdf_available"]  = d.get("pdf_path")  is not None
        d["docx_available"] = d.get("docx_path") is not None
        d["signed"]         = d.get("signature_path") is not None
        results.append(d)

    return results


def get_report(report_id: str) -> Optional[dict]:
    """Get a single report record."""
    db  = get_db()
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("modules_included"):
        try:
            d["modules_included"] = json.loads(d["modules_included"])
        except Exception:
            d["modules_included"] = []
    d["pdf_available"]  = d.get("pdf_path")  is not None
    d["docx_available"] = d.get("docx_path") is not None
    d["signed"]         = d.get("signature_path") is not None
    return d


def _render_html_report(report_data: dict) -> str:
    """Render report data to HTML for PDF conversion."""
    sections_html = ""
    for section in report_data.get("sections", []):
        content_html = _markdown_to_html(section.get("content", ""))
        sections_html += f"""
        <section class="report-section">
            <h1 class="section-title">{section.get('icon', '')} {section.get('heading', '')}</h1>
            <div class="section-content">{content_html}</div>
        </section>
        <div class="page-break"></div>
        """

    modules_list = ", ".join(str(m) for m in report_data.get("modules", []))

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #1a1a1a; line-height: 1.6; margin: 0; padding: 0; }}
  .cover {{ background: #0a0a0a; color: #fff; padding: 80px 60px; min-height: 100vh; display: flex; flex-direction: column; justify-content: center; }}
  .cover h1 {{ font-size: 2.2em; color: #00ff87; margin-bottom: 0.5em; }}
  .cover .tagline {{ color: #888; font-size: 1em; margin-top: 2em; }}
  .cover .meta {{ margin-top: 1.5em; color: #ccc; line-height: 2; }}
  .cover .brand {{ margin-top: 3em; color: #444; font-size: 0.85em; border-top: 1px solid #333; padding-top: 1em; }}
  .page-break {{ page-break-after: always; }}
  .report-section {{ padding: 40px 60px; }}
  .section-title {{ color: #00aa55; font-size: 1.6em; border-bottom: 2px solid #00aa55; padding-bottom: 0.3em; margin-bottom: 1em; }}
  h2 {{ color: #1a5a3a; font-size: 1.2em; margin-top: 1.5em; }}
  h3 {{ color: #2a6a4a; font-size: 1em; }}
  p {{ margin: 0.8em 0; }}
  ul {{ padding-left: 1.5em; }}
  li {{ margin: 0.4em 0; }}
  strong {{ color: #000; }}
  .report-table {{ width: 100%; border-collapse: collapse; margin: 0.8em 0; font-size: 0.9em; }}
  .report-table th {{ background: #f0f5f2; color: #1a5a3a; text-align: left; padding: 6px 10px; border: 1px solid #cfe0d6; }}
  .report-table td {{ padding: 5px 10px; border: 1px solid #e2e8e4; }}
  .report-table tr:nth-child(even) td {{ background: #f9fbfa; }}
  .report-pre {{ background: #f5f7f6; border: 1px solid #e0e6e2; border-radius: 4px; padding: 10px; font-size: 0.82em; white-space: pre-wrap; font-family: 'Consolas', monospace; }}
  em {{ color: #555; }}
  .footer-note {{ color: #888; font-size: 0.8em; font-style: italic; text-align: center; padding: 20px; border-top: 1px solid #ddd; margin-top: 2em; }}
</style>
</head>
<body>

<div class="cover">
  <div>
    <h1>{report_data.get('business_name', '')} Intelligence Report</h1>
    <p style="color:#00ff87; font-size:1.1em">Private Business Intelligence. Built for Emerging Markets.</p>
    <div class="meta">
      <div>Prepared for: <strong style="color:#fff">{report_data.get('client_name', '')}</strong></div>
      <div>Date: <strong style="color:#fff">{report_data.get('date', '')}</strong></div>
      <div>Modules: <strong style="color:#fff">{len(report_data.get('modules', []))}</strong> analysis sections</div>
    </div>
    <div class="brand">Prepared by SashaOps Intelligence Platform</div>
  </div>
</div>

<div class="page-break"></div>

{sections_html}

<div class="footer-note">
  Methodology: figures and ratios in this report are computed locally by SashaOps from PSX financial
  statements, government/regulatory data and relevance-filtered news — no external AI generated these numbers.
  Verify independently before making business decisions. Report ID embedded in the digital signature.
</div>

</body>
</html>"""


def _slugify(text: str) -> str:
    """Convert a name to a safe filename slug: lowercase, hyphens, max 40 chars."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:40]


def _markdown_to_html(text: str) -> str:
    """
    Convert the module markdown to HTML for the PDF report. Handles headings, pipe
    TABLES (ratio/competitor/DCF tables — previously dumped as raw '| ... |' text),
    bullet lists, code fences, bold, and italic (both *..* and _.._).
    """
    import re

    def _inline(s: str) -> str:
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<em>\1</em>", s)
        s = re.sub(r"_(.+?)_", r"<em>\1</em>", s)
        return s

    lines  = text.split("\n")
    output = []
    in_ul  = in_table = in_code = thead_done = False

    def _close_blocks():
        nonlocal in_ul, in_table, thead_done
        if in_ul:
            output.append("</ul>"); in_ul = False
        if in_table:
            output.append("</tbody></table>"); in_table = False; thead_done = False

    for line in lines:
        line = line.rstrip()

        if line.strip() == "```":
            _close_blocks()
            output.append("</pre>" if in_code else '<pre class="report-pre">')
            in_code = not in_code
            continue
        if in_code:
            output.append(re.sub(r"[<>&]", lambda m: {"<": "&lt;", ">": "&gt;", "&": "&amp;"}[m.group()], line))
            continue

        is_table_row = line.strip().startswith("|") and line.strip().endswith("|")

        if in_table and not is_table_row:
            output.append("</tbody></table>"); in_table = False; thead_done = False

        if line.startswith("## "):
            _close_blocks(); output.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("### "):
            _close_blocks(); output.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("**") and line.endswith("**") and len(line) > 4:
            _close_blocks(); output.append(f"<h3>{_inline(line.strip('*'))}</h3>")
        elif is_table_row:
            cells = [c.strip() for c in line.strip()[1:-1].split("|")]
            if all(set(c.replace(" ", "")) <= set("-:") for c in cells):
                continue  # separator row
            if not in_table:
                output.append('<table class="report-table">'); in_table = True; thead_done = False
            if not thead_done:
                output.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                thead_done = True
            else:
                output.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
        elif line.startswith("- ") or line.startswith("* "):
            if not in_ul:
                output.append("<ul>"); in_ul = True
            output.append(f"<li>{_inline(line[2:])}</li>")
        elif line.strip() == "":
            if in_ul:
                output.append("</ul>"); in_ul = False
            output.append("")
        else:
            _close_blocks(); output.append(f"<p>{_inline(line)}</p>")

    _close_blocks()
    if in_code:
        output.append("</pre>")
    return "\n".join(output)
