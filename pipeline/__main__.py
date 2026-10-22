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
    print("pipeline run: not implemented yet (available from M2)")
    return 1


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
