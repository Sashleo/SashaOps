"""pipeline/checks.py — S7 Check: arithmetic checks and fact promotion.

Check results per check:
  passed       — check ran and the numbers agree
  failed       — check ran and found a discrepancy (numbers included)
  not_run      — check could not run (required fields absent from Silver)
  not_applicable — check does not apply to this statement type

Statement status after check_one():
  verified     — every applicable check passed (no failed, no not_run)
  quarantined  — at least one applicable check is failed or not_run

Facts are promoted ONLY for verified statements.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

log = structlog.get_logger()

CHECKER_VERSION = "1.0"

_TOL = 0.01   # 1 % tolerance
_NA  = {"result": "not_applicable"}


def _pct_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1)
    return abs(a - b) / denom


def _passed(a: float, b: float, label_a: str, label_b: str) -> dict:
    diff = _pct_diff(a, b)
    ok = diff <= _TOL
    d = {
        "result": "passed" if ok else "failed",
        label_a: a,
        label_b: b,
        "pct_diff": round(diff * 100, 3),
    }
    return d


def _not_run(reason: str) -> dict:
    return {"result": "not_run", "reason": reason}


# ── Individual check functions ────────────────────────────────────────────────

def _check_bs_balances(fields: dict) -> dict:
    ta = fields.get("total_assets")
    te = fields.get("total_equity")
    tl = fields.get("total_liabilities")
    if ta is None:
        return _not_run("total_assets missing")
    if te is None or tl is None:
        return _not_run("total_equity or total_liabilities missing")
    return _passed(ta, te + tl, "total_assets", "equity_plus_liabilities")


def _check_pl_chain(fields: dict) -> dict:
    rev = fields.get("revenue")
    cos = fields.get("cost_of_sales")
    gp  = fields.get("gross_profit")
    if rev is None or cos is None or gp is None:
        missing = [k for k, v in {"revenue": rev, "cost_of_sales": cos,
                                   "gross_profit": gp}.items() if v is None]
        return _not_run(f"missing: {', '.join(missing)}")
    return _passed(gp, rev - cos, "gross_profit", "revenue_minus_cos")


def _check_cf_sums(fields: dict) -> dict:
    op  = fields.get("operating_cashflow")
    inv = fields.get("investing_cashflow")
    fin = fields.get("financing_cashflow")
    net = fields.get("net_change_in_cash")
    missing = [k for k, v in {
        "operating_cashflow": op, "investing_cashflow": inv,
        "financing_cashflow": fin, "net_change_in_cash": net,
    }.items() if v is None]
    if missing:
        return _not_run(f"missing: {', '.join(missing)}")
    return _passed(net, op + inv + fin, "net_change", "op_plus_inv_plus_fin")


# ── check_one ─────────────────────────────────────────────────────────────────

def check_one(db, statement_id: str) -> dict:
    """Run arithmetic checks for one statement.

    Promotes to facts only when status becomes 'verified'.
    Outcomes: verified | quarantined | skipped:<reason> | not_found
    """
    stmt = db.execute(
        "SELECT doc_sha256, ticker, period_end, months, basis, statement_type "
        "FROM statements WHERE statement_id=?",
        (statement_id,),
    ).fetchone()
    if stmt is None:
        return {"statement_id": statement_id, "outcome": "not_found", "checks": {}}

    doc_sha256 = stmt["doc_sha256"]
    ticker     = stmt["ticker"]
    period_end = stmt["period_end"]
    months     = stmt["months"]
    basis      = stmt["basis"]
    stype      = stmt["statement_type"]

    items = db.execute(
        "SELECT field, value_rupees FROM line_items "
        "WHERE doc_sha256=? AND statement_type=? AND basis=? AND column_no=0",
        (doc_sha256, stype, basis),
    ).fetchall()

    fields = {r["field"]: r["value_rupees"]
              for r in items if r["value_rupees"] is not None}
    if not fields:
        return {"statement_id": statement_id,
                "outcome": "skipped:no_items", "checks": {}}

    # ── Run applicable checks ─────────────────────────────────────────────────
    checks: dict = {}
    if stype == "balance_sheet":
        checks["bs_balances"]  = _check_bs_balances(fields)
        checks["pl_chain"]     = _NA
        checks["cf_sums"]      = _NA
    elif stype == "income":
        checks["bs_balances"]  = _NA
        checks["pl_chain"]     = _check_pl_chain(fields)
        checks["cf_sums"]      = _NA
    elif stype == "cashflow":
        checks["bs_balances"]  = _NA
        checks["pl_chain"]     = _NA
        checks["cf_sums"]      = _check_cf_sums(fields)
    else:
        checks["bs_balances"]  = _NA
        checks["pl_chain"]     = _NA
        checks["cf_sums"]      = _NA

    # ── Determine status ──────────────────────────────────────────────────────
    # quarantined if any applicable check is not_run or failed
    bad = {k for k, v in checks.items()
           if v.get("result") in ("not_run", "failed")}
    status = "quarantined" if bad else "verified"

    db.execute(
        "UPDATE statements SET checks_json=?, status=? WHERE statement_id=?",
        (json.dumps(checks), status, statement_id),
    )

    # ── Promote facts only for verified statements ────────────────────────────
    if status == "verified":
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

    log.info("S7 Check %s", status,
             statement_id=statement_id[:24],
             stype=stype,
             basis=basis,
             bad_checks=sorted(bad) or None)
    return {
        "statement_id": statement_id,
        "outcome": status,
        "checks": checks,
        "fields_count": len(fields),
    }
