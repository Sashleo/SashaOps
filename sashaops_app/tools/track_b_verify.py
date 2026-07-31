"""
tools/track_b_verify.py - Phase 3 Track B verification.

For each ticker: which document type the new selection logic picks, its page
count, and whether total_assets / total_equity actually extract from it. The
headline number (annual-grade documents out of 20) decides whether Sections 2,
6, 7, 8 and Charts 3, 6 ship in Phase 3 or move to Phase 3.5.
"""
import sys

from database.connection import init_database
init_database()

from intelligence.psx_report_parser import select_report_document, _download_by_url
from intelligence.pdf_extractor import extract_financials

TICKERS = ["HBL", "LUCK", "ENGRO", "FFC", "PSO", "MCB", "UBL", "DGKC", "MLCF",
           "OGDC", "PPL", "GLAXO", "ABOT", "BFBIO", "HUBC", "KAPCO", "SYS",
           "TRG", "ILP", "NML"]

rows = []
for tk in TICKERS:
    sel, ta, te, note = None, False, False, ""
    try:
        sel = select_report_document(tk)
    except Exception as e:
        note = f"selection error: {str(e)[:50]}"
    if sel:
        try:
            pdf = _download_by_url(sel["url"])
            if pdf:
                # ocr=False keeps this bounded; scanned docs are reported as such.
                fin = extract_financials(pdf, ocr=False).get("financials") or {}
                ta = fin.get("total_assets") is not None
                te = fin.get("total_equity") is not None or \
                     fin.get("shareholders_equity") is not None
                if not fin:
                    note = note or "no text layer (scanned)"
            else:
                note = note or "download failed"
        except Exception as e:
            note = note or f"extract error: {str(e)[:40]}"
    rows.append({
        "ticker": tk,
        "doc_type": (sel or {}).get("doc_type", "none"),
        "pages": (sel or {}).get("pages"),
        "date": (sel or {}).get("date"),
        "ta": ta, "te": te, "note": note,
    })
    r = rows[-1]
    print(f"{tk:7} {r['doc_type']:15} pages={str(r['pages']):>5} "
          f"TA={'Y' if ta else 'n'} TE={'Y' if te else 'n'} {r['note']}", flush=True)

print("\n=== TRACK B RESULT TABLE ===")
print(f"{'TICKER':8} {'DOC TYPE':16} {'PAGES':>6} {'DATE':14} {'TA':>3} {'TE':>3}  NOTE")
print("-" * 78)
for r in rows:
    print(f"{r['ticker']:8} {r['doc_type']:16} {str(r['pages']):>6} "
          f"{str(r['date'])[:14]:14} {'Y' if r['ta'] else '.':>3} "
          f"{'Y' if r['te'] else '.':>3}  {r['note']}")

annual_grade = sum(1 for r in rows if r["doc_type"] in ("annual_report", "annual_results"))
both = sum(1 for r in rows if r["ta"] and r["te"])
either = sum(1 for r in rows if r["ta"] or r["te"])
print(f"\nannual-grade documents (annual_report|annual_results): {annual_grade}/20")
print(f"  annual_report: {sum(1 for r in rows if r['doc_type']=='annual_report')}")
print(f"  annual_results: {sum(1 for r in rows if r['doc_type']=='annual_results')}")
print(f"  interim only:  {sum(1 for r in rows if r['doc_type']=='interim')}")
print(f"  none/failed:   {sum(1 for r in rows if r['doc_type']=='none')}")
print(f"total_assets AND total_equity extracted: {both}/20")
print(f"either extracted:                        {either}/20")
