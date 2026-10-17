#!/usr/bin/env bash
# scripts/check.sh — fast offline quality gate; runs inside or outside Docker.
# Usage:  bash scripts/check.sh
#
# Requires: pytest, httpx (pip install pytest httpx)
# The conftest.py patches pysqlcipher3 and oqs so no native libs are needed.
#
# Live scraper tests are in tests/test_scrapers.py and are skipped here
# (-m "not live").  Run `pytest -m live` separately with a network connection.

set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Unit tests (offline)"
pytest tests/ -m "not live" -q --tb=short

echo ""
echo "==> Template syntax check (Jinja2 parse)"
python - <<'PY'
import os, sys
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
errors = []
for root, _, files in os.walk("templates"):
    for f in files:
        if not f.endswith(".html"):
            continue
        path = os.path.relpath(os.path.join(root, f), "templates")
        try:
            env.parse(open(os.path.join(root, f), encoding="utf-8").read())
        except TemplateSyntaxError as e:
            errors.append(f"  {path}: {e}")
if errors:
    print("TEMPLATE ERRORS:")
    for e in errors: print(e)
    sys.exit(1)
print(f"  All templates OK ({sum(1 for r,_,fs in os.walk('templates') for f in fs if f.endswith('.html'))} files)")
PY

echo ""
echo "==> Unused JS check (marked.min.js must not exist)"
if [ -f "static/js/marked.min.js" ]; then
  echo "  ERROR: static/js/marked.min.js still present — delete it"
  exit 1
else
  echo "  OK: marked.min.js absent"
fi

echo ""
echo "==> Private Analyser field name check (tv_share_of_ev)"
python - <<'PY'
import sys, re
hits = []
for fname in ["templates/private.html", "templates/psx/_dcf_result.html"]:
    txt = open(fname, encoding="utf-8").read()
    if "tv_share_pct" in txt:
        hits.append(fname)
if hits:
    print("ERROR: stale tv_share_pct found in:", hits)
    sys.exit(1)
print("  OK: tv_share_pct absent from both DCF templates")
PY

echo ""
echo "All checks passed."
