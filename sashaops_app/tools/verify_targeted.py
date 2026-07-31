"""
tools/verify_targeted.py - verify targeted statement extraction on the scanned
annual reports identified by Track B.

Reports per ticker: pages located, pages OCR'd, fields extracted, reconciliation.
Target: 3 of 5 reconciling.
"""
import sys
import time

from database.connection import init_database
init_database()

from intelligence.psx_report_parser import select_report_document, _download_by_url
from intelligence.statement_locator import extract_statements

# The five scanned annual-grade documents from the Track B run, smallest document
# first so partial results arrive early rather than blocking behind a 608-page one.
TICKERS = ["GLAXO", "ABOT", "ENGRO", "MCB", "FFC"]
PREPROCESS = "--preprocess" in sys.argv

rows = []
for tk in TICKERS:
    t0 = time.time()
    sel = select_report_document(tk)
    if not sel:
        print(f"{tk}: no document selected", flush=True)
        rows.append({"t": tk, "ok": None, "why": "no document"})
        continue
    pdf = _download_by_url(sel["url"])
    if not pdf:
        print(f"{tk}: download failed", flush=True)
        rows.append({"t": tk, "ok": None, "why": "download failed"})
        continue

    r = extract_statements(pdf, ticker=tk, preprocess=PREPROCESS)
    loc = {k: v for k, v in (r.get("located") or {}).items()}
    cur = r.get("current") or {}
    rows.append({"t": tk, "ok": r.get("reconciled"), "why": r.get("reason"),
                 "pages": r.get("ocr_pages"), "loc": loc, "n": len(cur),
                 "basis": r.get("basis"), "secs": round(time.time() - t0)})
    print(f"{tk:6} doc={sel['doc_type']:14} {sel.get('pages')}p  "
          f"located={ {k: v[:3] for k, v in loc.items()} }  "
          f"ocr_pages={r.get('ocr_pages')}  basis={r.get('basis')}  "
          f"fields={len(cur)}  reconciled={r.get('reconciled')} ({r.get('reason')})  "
          f"{round(time.time() - t0)}s", flush=True)
    if cur:
        print("        " + ", ".join(f"{k}={v/1e6:,.0f}mn" for k, v in
                                     sorted(cur.items())[:8]), flush=True)
    if r.get("prior"):
        print(f"        prior period fields: {len(r['prior'])}", flush=True)

print("\n=== SUMMARY (preprocess=%s) ===" % PREPROCESS)
rec = sum(1 for r in rows if r.get("ok") is True)
print(f"reconciled: {rec}/{len(TICKERS)}")
for r in rows:
    print(f"  {r['t']:6} reconciled={str(r.get('ok')):5} fields={r.get('n', 0):2} "
          f"{r.get('why')}")
