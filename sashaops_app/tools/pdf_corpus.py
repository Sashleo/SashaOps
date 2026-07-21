"""
tools/pdf_corpus.py - Empirical PDF extraction study.

Picks N firms at random from EVERY PSX sector, fetches each one's latest annual /
financial report PDF, classifies it (digital text layer vs scanned image), then runs
the extraction chain and records exactly which method won and which line items landed.

The point: do NOT design the fallback chain by guessing. Measure a real corpus across
every sector first, find the actual failure modes, then finalise the chain.

Stages (so a slow OCR pass never blocks the fast evidence):
  fetch     download + classify every PDF (fast)
  fast      run pdfplumber tables + text on all (fast)
  ocr       run the OCR stages on whatever the fast path missed (slow)
  report    print the matrix

Usage (inside the container):
  python tools/pdf_corpus.py fetch
  python tools/pdf_corpus.py fast
  python tools/pdf_corpus.py ocr [limit]
  python tools/pdf_corpus.py report
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
import time
from pathlib import Path

CORPUS_DIR = Path("/app/.tmp/pdf_corpus")
PDF_DIR = CORPUS_DIR / "pdfs"
STATE = CORPUS_DIR / "corpus.json"

PER_SECTOR = 2
RANDOM_SEED = 20260714   # reproducible "random" pick

# The 9 balance-sheet / P&L fields Phase 2 grades against.
# Each maps to the actual key(s) the parser emits (the brief's "total_equity" is
# emitted as "shareholders_equity"; a firm may report only total_liabilities).
GRADED = [
    "revenue", "gross_profit", "ebit", "net_income", "total_assets",
    "total_equity", "current_assets", "current_liabilities", "total_debt",
]
GRADE_KEYS = {
    "revenue": ["revenue"],
    "gross_profit": ["gross_profit"],
    "ebit": ["ebit"],
    "net_income": ["net_income"],
    "total_assets": ["total_assets"],
    "total_equity": ["shareholders_equity", "total_equity"],
    "current_assets": ["current_assets"],
    "current_liabilities": ["current_liabilities"],
    "total_debt": ["total_debt", "total_liabilities"],
}


def _graded_hits(fin: dict) -> list:
    got = set(fin.keys())
    return [g for g, keys in GRADE_KEYS.items() if any(k in got for k in keys)]


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"picks": {}, "results": {}}


def _save(d: dict) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def pick_sample() -> dict:
    """2 random firms per sector."""
    from database.connection import init_database, get_db
    init_database()
    db = get_db()
    rows = db.execute(
        "SELECT symbol, sector_name FROM psx_companies "
        "WHERE sector_name IS NOT NULL AND COALESCE(is_debt,0)=0"
    ).fetchall()
    by_sector: dict = {}
    for r in rows:
        by_sector.setdefault(r["sector_name"], []).append(r["symbol"])
    rnd = random.Random(RANDOM_SEED)
    picks = {}
    for sector, syms in sorted(by_sector.items()):
        for s in rnd.sample(sorted(syms), min(PER_SECTOR, len(syms))):
            picks[s] = sector
    return picks


def classify(pdf_bytes: bytes) -> dict:
    """Digital (real text layer) vs scanned (image only). Cheap, no OCR."""
    import pdfplumber
    info = {"pages": 0, "chars": 0, "kind": "unknown", "producer": ""}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            info["pages"] = len(pdf.pages)
            info["producer"] = str((pdf.metadata or {}).get("Producer", ""))[:60]
            chars = 0
            for page in pdf.pages[:6]:
                chars += len(page.extract_text() or "")
            info["chars"] = chars
        # A digital report has a real text layer on its first pages.
        info["kind"] = "digital" if info["chars"] > 400 else "scanned"
    except Exception as e:
        info["kind"] = "broken"
        info["producer"] = f"ERR {type(e).__name__}"
    return info


def cmd_fetch():
    from intelligence.psx_report_parser import find_latest_report_url, _download
    st = _load()
    if not st["picks"]:
        st["picks"] = pick_sample()
        _save(st)
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    for i, (sym, sector) in enumerate(sorted(st["picks"].items()), 1):
        res = st["results"].setdefault(sym, {"sector": sector})
        if res.get("kind"):
            continue
        p = PDF_DIR / f"{sym}.pdf"
        try:
            if not p.exists():
                url = find_latest_report_url(sym)
                if not url:
                    res.update({"kind": "no_report", "url": None})
                    print(f"[{i}] {sym:8} {sector[:22]:24} NO REPORT URL")
                    _save(st)
                    continue
                data = _download(url, sym)
                if not data:
                    res.update({"kind": "download_failed", "url": url})
                    print(f"[{i}] {sym:8} {sector[:22]:24} DOWNLOAD FAILED")
                    _save(st)
                    continue
                p.write_bytes(data)
                res["url"] = url
            info = classify(p.read_bytes())
            res.update(info)
            res["size_kb"] = p.stat().st_size // 1024
            print(f"[{i}] {sym:8} {sector[:22]:24} {info['kind']:8} "
                  f"pages={info['pages']:<4} chars={info['chars']:<7} {res['size_kb']}KB")
        except Exception as e:
            res.update({"kind": "error", "error": str(e)[:120]})
            print(f"[{i}] {sym:8} {sector[:22]:24} ERROR {str(e)[:60]}")
        _save(st)
        time.sleep(0.2)


def _run_chain(pdf_bytes: bytes, ocr: bool) -> dict:
    from intelligence.pdf_extractor import extract_financials
    r = extract_financials(pdf_bytes, ocr=ocr)
    fin = {k: v for k, v in (r.get("financials") or {}).items()
           if not k.startswith("_") and v is not None}
    return {
        "method": r.get("method"),
        "n_fields": len(fin),
        "fields": sorted(fin.keys()),
        "graded_hits": sorted(_graded_hits(fin)),
    }


def cmd_fast():
    st = _load()
    for sym, res in sorted(st["results"].items()):
        if res.get("kind") not in ("digital", "scanned"):
            continue
        if "fast" in res:
            continue
        p = PDF_DIR / f"{sym}.pdf"
        if not p.exists():
            continue
        try:
            res["fast"] = _run_chain(p.read_bytes(), ocr=False)
        except Exception as e:
            res["fast"] = {"method": "error", "n_fields": 0, "fields": [],
                           "graded_hits": [], "error": str(e)[:100]}
        f = res["fast"]
        print(f"{sym:8} {res['kind']:8} {res['sector'][:20]:22} "
              f"fast={f['method'] or 'none':18} graded={len(f['graded_hits'])}/9 {f['graded_hits']}")
        _save(st)


def cmd_ocr(limit: int = 999):
    st = _load()
    todo = [s for s, r in sorted(st["results"].items())
            if r.get("kind") in ("digital", "scanned")
            and "ocr" not in r
            and len((r.get("fast") or {}).get("graded_hits", [])) < 6]
    print(f"OCR candidates (fast path got <6/9): {len(todo)}; running {min(limit,len(todo))}")
    for sym in todo[:limit]:
        res = st["results"][sym]
        p = PDF_DIR / f"{sym}.pdf"
        if not p.exists():
            continue
        t0 = time.time()
        try:
            res["ocr"] = _run_chain(p.read_bytes(), ocr=True)
            res["ocr"]["secs"] = round(time.time() - t0, 1)
        except Exception as e:
            res["ocr"] = {"method": "error", "n_fields": 0, "fields": [],
                          "graded_hits": [], "error": str(e)[:100],
                          "secs": round(time.time() - t0, 1)}
        o = res["ocr"]
        print(f"{sym:8} {res['kind']:8} ocr={o['method'] or 'none':22} "
              f"graded={len(o['graded_hits'])}/9 {o['secs']}s {o['graded_hits']}")
        _save(st)


def cmd_report():
    st = _load()
    rs = st["results"]
    kinds: dict = {}
    for r in rs.values():
        kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
    print("=== CORPUS ===")
    print(f"  firms sampled : {len(rs)}  (2 per sector, {len(set(r['sector'] for r in rs.values()))} sectors)")
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {k:16} {v}")

    print()
    print("=== EXTRACTION (graded fields out of 9) ===")
    print(f"{'SYMBOL':8} {'KIND':9} {'SECTOR':24} {'BEST METHOD':22} {'GRADED':7}")
    tally: dict = {}
    for sym, r in sorted(rs.items(), key=lambda x: (x[1].get("kind", ""), x[0])):
        if r.get("kind") not in ("digital", "scanned"):
            continue
        fast = r.get("fast") or {}
        ocr = r.get("ocr") or {}
        best = fast if len(fast.get("graded_hits", [])) >= len(ocr.get("graded_hits", [])) else ocr
        n = len(best.get("graded_hits", []))
        m = best.get("method") or "none"
        tally.setdefault(m, []).append(n)
        print(f"{sym:8} {r['kind']:9} {r['sector'][:23]:24} {m:22} {n}/9")

    print()
    print("=== METHOD WIN-RATE ===")
    for m, hits in sorted(tally.items(), key=lambda x: -len(x[1])):
        avg = sum(hits) / len(hits) if hits else 0
        print(f"  {m:22} won {len(hits):3} docs   avg {avg:.1f}/9 graded")

    # Which graded fields are hardest?
    print()
    print("=== FIELD HIT-RATE (across docs where anything was extracted) ===")
    from collections import Counter
    c = Counter()
    docs = 0
    for r in rs.values():
        fast = r.get("fast") or {}
        ocr = r.get("ocr") or {}
        best = fast if len(fast.get("graded_hits", [])) >= len(ocr.get("graded_hits", [])) else ocr
        if best.get("graded_hits"):
            docs += 1
            c.update(best["graded_hits"])
    for f in GRADED:
        n = c.get(f, 0)
        pctv = (n / docs * 100) if docs else 0
        print(f"  {f:22} {n:3}/{docs}  {pctv:5.1f}%")


def cmd_regrade():
    """Recompute graded_hits from stored field lists (no re-parsing)."""
    st = _load()
    for r in st["results"].values():
        for stage in ("fast", "ocr"):
            if stage in r and "fields" in r[stage]:
                r[stage]["graded_hits"] = sorted(_graded_hits({f: 1 for f in r[stage]["fields"]}))
    _save(st)
    print("regraded from stored fields")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "regrade":
        cmd_regrade()
    elif cmd == "fetch":
        cmd_fetch()
    elif cmd == "fast":
        cmd_fast()
    elif cmd == "ocr":
        cmd_ocr(int(sys.argv[2]) if len(sys.argv) > 2 else 999)
    else:
        cmd_report()
