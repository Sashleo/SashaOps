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
