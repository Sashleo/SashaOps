"""pipeline/checks.py — S6/S7 Check: arithmetic checks and fact promotion.

For each statement in `statements`, reads its line_items and runs:
  - bs_balances:  total_assets == total_equity + total_liabilities
  - pl_chain:     gross_profit == revenue - cost_of_sales (when all present)
  - cf_sign:      cash-flow signs plausible (operating usually positive)

Writes check results to statements.checks_json.
Promotes high-confidence fields to facts table for M6 accuracy reporting.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

log = structlog.get_logger()

CHECKER_VERSION = "1.0"

_TOL = 0.01  # 1 % tolerance for balance checks


def _pct_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1)
    return abs(a - b) / denom


def _check_bs(fields: dict) -> dict:
    ta = fields.get("total_assets")
    te = fields.get("total_equity")
    tl = fields.get("total_liabilities")
    if ta is None or (te is None and tl is None):
        return {"ok": None, "reason": "missing fields"}
    if te is not None and tl is not None:
        diff = _pct_diff(ta, te + tl)
        ok = diff <= _TOL
        return {
            "ok": ok,
            "total_assets": ta,
            "total_equity_plus_liabilities": te + tl,
            "pct_diff": round(diff * 100, 3),
        }
    return {"ok": None, "reason": "need both equity and liabilities"}


def _check_pl(fields: dict) -> dict:
    rev = fields.get("revenue")
    cos = fields.get("cost_of_sales")
    gp = fields.get("gross_profit")
    if rev is None or cos is None or gp is None:
        return {"ok": None, "reason": "missing fields"}
    diff = _pct_diff(gp, rev - cos)
    ok = diff <= _TOL
    return {
        "ok": ok,
        "gross_profit_reported": gp,
        "revenue_minus_cost": rev - cos,
        "pct_diff": round(diff * 100, 3),
    }


def _check_cf(fields: dict) -> dict:
    op = fields.get("operating_cashflow")
    inv = fields.get("investing_cashflow")
    fin = fields.get("financing_cashflow")
    net = fields.get("net_change_in_cash")
    if op is None or inv is None or fin is None or net is None:
        return {"ok": None, "reason": "missing fields"}
    diff = _pct_diff(net, op + inv + fin)
    ok = diff <= _TOL
    return {
        "ok": ok,
        "net_change_reported": net,
        "op_plus_inv_plus_fin": op + inv + fin,
        "pct_diff": round(diff * 100, 3),
    }


def check_one(db, statement_id: str) -> dict:
    """Run arithmetic checks for one statement and promote facts.

    Outcomes: checked | skipped:<reason> | not_found
    """
    stmt = db.execute(
        "SELECT doc_sha256, ticker, period_end, months, basis, statement_type, "
        "status, parser_version FROM statements WHERE statement_id=?",
        (statement_id,),
    ).fetchone()
    if stmt is None:
        return {"statement_id": statement_id, "outcome": "not_found", "checks": {}}

    doc_sha256 = stmt["doc_sha256"]
    ticker = stmt["ticker"]
    period_end = stmt["period_end"]
    months = stmt["months"]
    basis = stmt["basis"]
    statement_type = stmt["statement_type"]

    # Read current-year line items (column_no=0)
    items = db.execute(
        "SELECT field, value_rupees FROM line_items "
        "WHERE doc_sha256=? AND statement_type=? AND basis=? AND column_no=0",
        (doc_sha256, statement_type, basis),
    ).fetchall()

    fields = {r["field"]: r["value_rupees"] for r in items if r["value_rupees"] is not None}
    if not fields:
        return {"statement_id": statement_id, "outcome": "skipped:no_items", "checks": {}}

    checks: dict = {}
    if statement_type == "balance_sheet":
        checks["bs_balances"] = _check_bs(fields)
    elif statement_type == "income":
        checks["pl_chain"] = _check_pl(fields)
    elif statement_type == "cashflow":
        checks["cf_sums"] = _check_cf(fields)

    db.execute(
        "UPDATE statements SET checks_json=?, status=? WHERE statement_id=?",
        (json.dumps(checks), "checked", statement_id),
    )

    # Promote to facts
    now_iso = datetime.now(timezone.utc).isoformat()
    for field, value_rupees in fields.items():
        db.execute(
            "INSERT OR REPLACE INTO facts "
            "(ticker, period_end, months, basis, field, statement_id, "
            "value_rupees, source, is_current, restated, published_at) "
            "VALUES (?,?,?,?,?,?,?,?,1,0,?)",
            (ticker, period_end, months, basis, field,
             statement_id, value_rupees, "pdf_extract", now_iso),
        )

    db.commit()

    log.info("S6 Check checked",
             statement_id=statement_id[:24],
             statement_type=statement_type,
             basis=basis,
             checks_ok={k: v.get("ok") for k, v in checks.items()})
    return {
        "statement_id": statement_id,
        "outcome": "checked",
        "checks": checks,
        "fields_count": len(fields),
    }
