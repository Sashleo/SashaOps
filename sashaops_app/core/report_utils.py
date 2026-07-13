"""
core/report_utils.py — Shared HTML rendering helpers for local financial reports.

Used by both routers/markets.py and routers/intelligence.py so neither
imports from the other (avoids circular router imports).
"""

from __future__ import annotations
import html as _html
import re as _re


def render_md_to_html(md: str) -> str:
    """
    Convert the generate_local_financial_report() markdown subset to safe HTML.
    Handles headings, pipe tables, bullet lists, code fences, bold, and italic.
    """

    def _inline(text: str) -> str:
        text = _html.escape(text)
        text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = _re.sub(r'_(.+?)_',       r'<em>\1</em>',         text)
        return text

    out: list[str] = []
    in_table   = False
    in_list    = False
    in_code    = False
    thead_done = False

    for raw_line in md.split("\n"):
        line = raw_line

        # Toggle code fence
        if line.strip() == "```":
            if in_code:
                out.append('</pre>')
            else:
                out.append(
                    '<pre style="font-size:0.78em;background:var(--bg-3);'
                    'border-radius:4px;padding:8px;overflow-x:auto;white-space:pre-wrap">'
                )
            in_code = not in_code
            continue

        if in_code:
            out.append(_html.escape(line))
            continue

        is_table_row = line.strip().startswith("|") and line.strip().endswith("|")
        is_list_item = line.strip().startswith("- ")

        # Close open blocks when their content ends
        if in_table and not is_table_row:
            out.append("</tbody></table>")
            in_table   = False
            thead_done = False
        if in_list and not is_list_item:
            out.append("</ul>")
            in_list = False

        if line.startswith("## "):
            out.append(
                f'<h4 style="margin:18px 0 6px;font-size:0.95em">'
                f'{_inline(line[3:])}</h4>'
            )
        elif line.startswith("### "):
            out.append(
                f'<h5 style="margin:14px 0 5px;font-size:0.82em;color:var(--text-muted);'
                f'text-transform:uppercase;letter-spacing:0.5px">'
                f'{_inline(line[4:])}</h5>'
            )
        elif is_table_row:
            cells = [c.strip() for c in line.strip()[1:-1].split("|")]
            # Skip separator rows (---|--- etc.)
            if all(set(c.replace(" ", "")) <= set("-:") for c in cells):
                continue
            if not in_table:
                out.append(
                    '<table style="font-size:0.8em;width:100%;border-collapse:collapse;'
                    'margin:4px 0 8px">'
                )
                in_table   = True
                thead_done = False
            td_s = 'style="padding:3px 8px;border:1px solid var(--border)"'
            if not thead_done:
                row = "".join(f"<th {td_s}>{_inline(c)}</th>" for c in cells)
                out.append(f"<thead><tr>{row}</tr></thead><tbody>")
                thead_done = True
            else:
                row = "".join(f"<td {td_s}>{_inline(c)}</td>" for c in cells)
                out.append(f"<tr>{row}</tr>")
        elif is_list_item:
            if not in_list:
                out.append(
                    '<ul style="margin:4px 0 8px;padding-left:18px;font-size:0.82em">'
                )
                in_list = True
            out.append(f'<li>{_inline(line.strip()[2:])}</li>')
        elif not line.strip():
            out.append("")
        else:
            out.append(
                f'<p style="margin:2px 0;font-size:0.82em">{_inline(line)}</p>'
            )

    if in_table:
        out.append("</tbody></table>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")

    return "\n".join(out)
