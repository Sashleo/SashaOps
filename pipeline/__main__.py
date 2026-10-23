"""pipeline/__main__.py — Command-line interface for the SashaOps data pipeline.

Usage:
    python -m pipeline status
    python -m pipeline run [--universe kse100] [--tickers LUCK,HUBC] [--stages discover..publish]
    python -m pipeline reprocess --from extract|map|check
    python -m pipeline unmapped [--top 50]
    python -m pipeline explain <TICKER> <PERIOD> <FIELD>
"""
from __future__ import annotations
import argparse
import json
import sys


def cmd_status(args) -> int:
    from database.connection import init_database, get_db
    init_database()
    from pipeline.store import get_status

    s = get_status(get_db())

    if s["last_run"] is None:
        print("No pipeline runs recorded.")
    else:
        run = s["last_run"]
        state = "in progress" if not run["finished_at"] else "completed"
        print(f"Last run: {run['run_id']}  started {run['started_at']}  [{state}]")
        if run["counts_json"]:
            counts = json.loads(run["counts_json"])
            for stage, c in counts.items():
                print(f"  {stage}: ok={c.get('ok', 0)} failed={c.get('failed', 0)} "
                      f"skipped={c.get('skipped', 0)}")

    print(
        f"Filings: {s['filings']}  "
        f"Statements: {s['statements']} "
        f"({s['verified']} verified, {s['quarantined']} quarantined)  "
        f"Facts: {s['facts']}"
    )
    return 0


def cmd_run(args) -> int:
    from database.connection import init_database, get_db
    init_database()
    db = get_db()

    from pipeline.store import create_pipeline_tables
    create_pipeline_tables(db)

    from pipeline.tickers import M2_TICKERS
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = list(M2_TICKERS)

    stages = (args.stages or "discover..fetch").lower()
    run_discover = "discover" in stages
    run_fetch    = "fetch"    in stages
    run_classify = "classify" in stages
    run_locate   = "locate"   in stages
    run_extract  = "extract"  in stages
    run_check    = "check"    in stages

    from pipeline.discover import discover_one
    from pipeline.fetch    import fetch_one

    counts: dict[str, dict] = {}

    for ticker in tickers:
        disc_result = {"outcome": "skipped", "candidates": []}
        if run_discover:
            disc_result = discover_one(ticker)
            _inc(counts, "discover", disc_result["outcome"])
            print(f"  discover {ticker}: {disc_result['outcome']} "
                  f"({len(disc_result['candidates'])} candidates)")

        if not run_fetch:
            continue

        candidates = disc_result.get("candidates") or []
        # discover_one already returns only the best selection:
        # up to 2 annual_reports, or 1 fallback if none found.
        for c in candidates:
            r = fetch_one(db, c)
            _inc(counts, "fetch", r["outcome"])
            pe = c.get("period_end") or "-"
            print(f"  fetch   {ticker}: {r['outcome']}  "
                  f"sha256={(r.get('doc_sha256') or '')[:12] or '-'}  "
                  f"period={pe}  type={c['ann_type']}")

    if run_classify or run_locate or run_extract or run_check:
        ticker_placeholders = ", ".join("?" * len(tickers))
        filing_rows = db.execute(
            f"SELECT doc_sha256, ticker FROM filings "
            f"WHERE ticker IN ({ticker_placeholders})",
            tickers,
        ).fetchall()
    else:
        filing_rows = []

    if run_classify:
        from pipeline.classify import classify_one
        for filing_sha256, filing_ticker in filing_rows:
            r = classify_one(db, filing_sha256)
            _inc(counts, "classify", r["outcome"])
            tp = r.get("text_pages")
            print(f"  classify {filing_ticker}: {r['outcome']}  "
                  f"sha256={filing_sha256[:12]}  "
                  f"class={r.get('doc_class') or '-'}  "
                  f"text_pages={tp if tp is not None else '-'}")

    if run_locate:
        from pipeline.locate import locate_one
        for filing_sha256, filing_ticker in filing_rows:
            r = locate_one(db, filing_sha256)
            _inc(counts, "locate", r["outcome"])
            stmts = r.get("statements") or []
            types_str = ",".join(sorted({s["statement_type"][:2] for s in stmts})) or "-"
            bases_str = ",".join(sorted({s["basis"][:3] for s in stmts})) or "-"
            print(f"  locate  {filing_ticker}: {r['outcome']}  "
                  f"sha256={filing_sha256[:12]}  "
                  f"stmts={len(stmts)}  types={types_str}  basis={bases_str}")

    if run_extract:
        from pipeline.extract import extract_one
        for filing_sha256, filing_ticker in filing_rows:
            r = extract_one(db, filing_sha256)
            _inc(counts, "extract", r["outcome"])
            print(f"  extract {filing_ticker}: {r['outcome']}  "
                  f"sha256={filing_sha256[:12]}  "
                  f"stmts={r.get('statements_written', 0)}  "
                  f"items={r.get('items_written', 0)}")

    if run_check:
        from pipeline.checks import check_one
        stmt_ids = db.execute(
            "SELECT statement_id, ticker, statement_type, basis "
            "FROM statements WHERE ticker IN "
            f"({', '.join('?' * len(tickers))})",
            tickers,
        ).fetchall()
        for sid_row in stmt_ids:
            r = check_one(db, sid_row["statement_id"])
            _inc(counts, "check", r["outcome"])
            checks_summary = "  ".join(
                f"{k}={'ok' if v.get('ok') else ('fail' if v.get('ok') is False else '?')}"
                for k, v in r.get("checks", {}).items()
            ) or "-"
            print(f"  check   {sid_row['ticker']}: {r['outcome']}  "
                  f"{sid_row['statement_type']}:{sid_row['basis']}  "
                  f"fields={r.get('fields_count', '-')}  {checks_summary}")

    print("\nSummary:")
    for stage, c in counts.items():
        parts = "  ".join(f"{k}={v}" for k, v in sorted(c.items()))
        print(f"  {stage}: {parts}")
    return 0


def _inc(counts: dict, stage: str, outcome: str) -> None:
    c = counts.setdefault(stage, {})
    c[outcome] = c.get(outcome, 0) + 1


def cmd_reprocess(args) -> int:
    print("pipeline reprocess: not implemented yet")
    return 1


def cmd_unmapped(args) -> int:
    print("pipeline unmapped: not implemented yet (available from M5)")
    return 1


def cmd_explain(args) -> int:
    print("pipeline explain: not implemented yet (available from M7)")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="pipeline", description="SashaOps data pipeline")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Last run summary and coverage")

    p_run = sub.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--universe", default="kse100", help="Ticker universe (default: kse100)")
    p_run.add_argument("--tickers", help="Comma-separated ticker override")
    p_run.add_argument("--stages", help="Stage range, e.g. fetch..check")

    p_repr = sub.add_parser("reprocess", help="Reprocess from a given stage")
    p_repr.add_argument("--from", dest="from_stage", required=True,
                        choices=["extract", "map", "check"],
                        help="Earliest stage to re-run")

    p_unm = sub.add_parser("unmapped", help="Most frequent unmapped label strings")
    p_unm.add_argument("--top", type=int, default=50)

    p_exp = sub.add_parser("explain", help="Show lineage for one figure")
    p_exp.add_argument("ticker")
    p_exp.add_argument("period", help="e.g. FY2026 or 2026-06-30")
    p_exp.add_argument("field",  help="Contract field key, e.g. total_assets")

    args = parser.parse_args()

    dispatch = {
        "status":    cmd_status,
        "run":       cmd_run,
        "reprocess": cmd_reprocess,
        "unmapped":  cmd_unmapped,
        "explain":   cmd_explain,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    sys.exit(dispatch[args.command](args))


if __name__ == "__main__":
    main()
