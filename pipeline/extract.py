"""pipeline/extract.py — S5 Extract: parse statement pages into line_items.

Inputs:  doc_statements rows (from S4 Locate) + Bronze PDFs
Outputs: line_items rows (one per field × column) + statements rows

Each (statement_type, basis) for a filing gets one statements row and N
line_items rows — column 0 for current year, column 1 for prior year.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()

EXTRACTOR_VERSION = "1.0"

_EXTRACTABLE_CLASSES = {"annual_report", "interim_report"}


def _read_pages(pdf_path: str, page_start: int, n_pages: int = 3) -> str:
    """Read up to n_pages from page_start, joined.  Flushes cache after each."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            texts = []
            for i in range(page_start, min(page_start + n_pages, total)):
                t = pdf.pages[i].extract_text() or ""
                texts.append(t)
                try:
                    pdf.pages[i].flush_cache()
                except Exception:
                    pass
            return "\n".join(texts)
    except Exception as e:
        log.info("extract: page read failed",
                 path=pdf_path, page=page_start, error=str(e)[:80])
        return ""


def extract_one(db, doc_sha256: str) -> dict:
    """Extract line items from one filing's located statement pages.

    Outcomes: extracted | already_extracted | skipped:<reason> | failed:<reason>
    """
    filing = db.execute(
        "SELECT ticker, file_path, doc_class FROM filings WHERE doc_sha256=?",
        (doc_sha256,),
    ).fetchone()
    if filing is None:
        return {"doc_sha256": doc_sha256, "outcome": "not_found",
                "statements_written": 0, "items_written": 0}

    ticker = filing["ticker"]
    file_path = filing["file_path"]
    doc_class = filing["doc_class"]

    if doc_class not in _EXTRACTABLE_CLASSES:
        return {"doc_sha256": doc_sha256,
                "outcome": f"skipped:{doc_class or 'unclassified'}",
                "statements_written": 0, "items_written": 0}

    existing = db.execute(
        "SELECT count(*) FROM statements "
        "WHERE doc_sha256=? AND parser_version=?",
        (doc_sha256, EXTRACTOR_VERSION),
    ).fetchone()[0]
    if existing:
        return {"doc_sha256": doc_sha256, "outcome": "already_extracted",
                "statements_written": 0, "items_written": 0}

    if not file_path or not Path(file_path).exists():
        return {"doc_sha256": doc_sha256, "outcome": "failed:file_missing",
                "statements_written": 0, "items_written": 0}

    stmt_rows = db.execute(
        "SELECT statement_type, basis, pdf_page_start, period_ends_json "
        "FROM doc_statements WHERE doc_sha256=?",
        (doc_sha256,),
    ).fetchall()
    if not stmt_rows:
        return {"doc_sha256": doc_sha256, "outcome": "skipped:no_statements",
                "statements_written": 0, "items_written": 0}

    # Clean stale rows from prior extractor versions
    db.execute("DELETE FROM line_items WHERE doc_sha256=?", (doc_sha256,))
    db.execute("DELETE FROM statements WHERE doc_sha256=?", (doc_sha256,))

    run_id = (
        f"m5:{doc_sha256[:8]}:"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    months = 12 if doc_class == "annual_report" else 6

    stmts_written = 0
    items_written = 0

    from intelligence.statement_parser import parse_statements

    for stmt_row in stmt_rows:
        statement_type = stmt_row["statement_type"]
        basis = stmt_row["basis"]
        pdf_page_start = stmt_row["pdf_page_start"]
        period_ends = json.loads(stmt_row["period_ends_json"] or "[]")

        period_end = period_ends[0] if period_ends else None
        if period_end is None:
            log.warning("S5 Extract: no period_end, skipping",
                        doc_sha256=doc_sha256[:12],
                        statement_type=statement_type, basis=basis)
            continue

        prior_end = period_ends[1] if len(period_ends) > 1 else None

        text = _read_pages(file_path, pdf_page_start)
        if not text.strip():
            log.warning("S5 Extract: empty page text",
                        doc_sha256=doc_sha256[:12], page=pdf_page_start)
            continue

        try:
            parsed = parse_statements(text)
        except Exception as e:
            log.warning("S5 Extract parse failed",
                        doc_sha256=doc_sha256[:12],
                        statement_type=statement_type, error=str(e)[:80])
            continue

        data = {k: v for k, v in parsed.items() if not k.startswith("_")}
        if not data:
            log.info("S5 Extract: no fields found",
                     doc_sha256=doc_sha256[:12],
                     statement_type=statement_type, basis=basis)
            continue

        statement_id = f"{doc_sha256[:16]}:{statement_type}:{basis}"

        db.execute(
            "INSERT OR REPLACE INTO statements "
            "(statement_id, doc_sha256, ticker, period_end, months, basis, "
            "statement_type, status, parser_version, run_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (statement_id, doc_sha256, ticker, period_end, months,
             basis, statement_type, "extracted",
             EXTRACTOR_VERSION, run_id, now_iso),
        )
        stmts_written += 1

        for line_no, (field, item) in enumerate(data.items()):
            scale_factor = item.get("scale", 1) or 1
            value_cur = int(item.get("value", 0) or 0)

            db.execute(
                "INSERT OR REPLACE INTO line_items "
                "(doc_sha256, statement_type, basis, pdf_page, line_no, column_no, "
                "label_raw, label_norm, field, value_rupees, printed_text, "
                "is_dash, is_bracketed, scale, period_end, months, "
                "parser_version, run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)",
                (doc_sha256, statement_type, basis, pdf_page_start,
                 line_no, 0,
                 item.get("source_line", ""), field,
                 field, value_cur, item.get("source_line", ""),
                 str(scale_factor), period_end, months,
                 EXTRACTOR_VERSION, run_id),
            )
            items_written += 1

            all_cols = item.get("all_columns") or []
            if len(all_cols) > 1 and prior_end:
                value_pri = int(all_cols[1] * scale_factor)
                db.execute(
                    "INSERT OR REPLACE INTO line_items "
                    "(doc_sha256, statement_type, basis, pdf_page, line_no, column_no, "
                    "label_raw, label_norm, field, value_rupees, printed_text, "
                    "is_dash, is_bracketed, scale, period_end, months, "
                    "parser_version, run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)",
                    (doc_sha256, statement_type, basis, pdf_page_start,
                     line_no, 1,
                     item.get("source_line", ""), field,
                     field, value_pri, item.get("source_line", ""),
                     str(scale_factor), prior_end, months,
                     EXTRACTOR_VERSION, run_id),
                )
                items_written += 1

    db.commit()

    if stmts_written == 0:
        return {"doc_sha256": doc_sha256, "outcome": "failed:no_data",
                "statements_written": 0, "items_written": 0}

    log.info("S5 Extract extracted",
             doc_sha256=doc_sha256[:12], ticker=ticker,
             statements=stmts_written, items=items_written)
    return {
        "doc_sha256": doc_sha256,
        "outcome": "extracted",
        "statements_written": stmts_written,
        "items_written": items_written,
    }
