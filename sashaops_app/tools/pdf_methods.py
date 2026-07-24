"""
tools/pdf_methods.py - Per-method accuracy matrix for the extraction chain.

The chain normally stops at the best method. To DESIGN the chain we need the
opposite: run EVERY method against EVERY document independently and record how each
one performs, per document type. That matrix is the evidence for a router that picks
the right mechanism per report instead of always walking the same fixed order.

For each (document, method) we record:
    n_fields      how many graded fields came back
    n_high        how many were high-confidence
    balance_ok    did Assets == Liabilities + Equity reconcile
    scale         unit scale chosen
    anomalies     NEGATIVE / ABSURD_MAG / SUBITEM / BALANCE_FAIL (value corruption)
    secs          wall-clock cost - the router must weigh accuracy against time

Document features the router can key on are recorded too: kind (digital/scanned),
pages, chars in the text layer, sector.

Usage (inside the container, PYTHONPATH=/app):
  python tools/pdf_methods.py run pdfplumber_tables       [limit]
  python tools/pdf_methods.py run pdfplumber_text         [limit]
  python tools/pdf_methods.py run tesseract_raw           [limit]
  python tools/pdf_methods.py run tesseract_preprocessed  [limit]
  python tools/pdf_methods.py matrix
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE = Path("/app/.tmp/pdf_methods")
STATE = BASE / "methods.json"

# Both corpora feed the matrix: more documents, more reliable routing evidence.
SOURCES = [
    (Path("/app/.tmp/pdf_corpus/corpus.json"), Path("/app/.tmp/pdf_corpus/pdfs")),
    (Path("/app/.tmp/pdf_validate/validate.json"), Path("/app/.tmp/pdf_validate/pdfs")),
]

METHODS = {
    "pdfplumber_tables": "PdfplumberTablesExtractor",
    "pdfplumber_text": "PdfplumberTextExtractor",
    "tesseract_raw": "TesseractRawExtractor",
    "tesseract_preprocessed": "TesseractPreprocessedExtractor",
    "tesseract_deskew": "TesseractDeskewExtractor",
}

PLAUSIBLE = {
    "revenue": (1e6, 5e12), "total_assets": (1e6, 2e13),
    "current_assets": (1e5, 5e12), "current_liabilities": (1e5, 5e12),
    "total_liabilities": (1e5, 2e13), "total_equity": (1e5, 5e12),
    "net_income": (1e5, 1e12), "gross_profit": (1e5, 2e12), "ebit": (1e5, 2e12),
}
MUST_POS = {"revenue", "total_assets", "current_assets",
            "current_liabilities", "total_liabilities"}
SUBITEM_HINT = ("long term investment", "spare part", "loose tool", "stores",
                "sales tax", "of operator", "equity and liabilit")


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"docs": {}}


def _save(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def _index_docs(st: dict) -> None:
    """Build the document list (with routing features) from both corpora."""
    for state_path, pdf_dir in SOURCES:
        if not state_path.exists():
            continue
        data = json.loads(state_path.read_text(encoding="utf-8"))
        for sym, r in data.get("results", {}).items():
            if r.get("kind") not in ("digital", "scanned"):
                continue
            p = pdf_dir / f"{sym}.pdf"
            if not p.exists():
                continue
            d = st["docs"].setdefault(sym, {})
            d.update({
                "path": str(p),
                "kind": r.get("kind"),
                "pages": r.get("pages"),
                "chars": r.get("chars"),
                "sector": r.get("sector"),
            })
            d.setdefault("methods", {})


def _anomalies(fin: dict) -> list:
    flags, prov = [], (fin.get("_provenance") or {})
    core = {k: v for k, v in fin.items() if not k.startswith("_") and v is not None}
    for f, v in core.items():
        if f in MUST_POS and v <= 0:
            flags.append(f"NEGATIVE:{f}")
        lo_hi = PLAUSIBLE.get(f)
        if lo_hi and v != 0 and not (lo_hi[0] <= abs(v) <= lo_hi[1]):
            flags.append(f"ABSURD:{f}")
        if any(h in (prov.get(f) or {}).get("source_line", "").lower() for h in SUBITEM_HINT):
            flags.append(f"SUBITEM:{f}")
    if fin.get("_balance_ok") is False:
        flags.append("BALANCE_FAIL")
    return flags


def cmd_run(method: str, limit: int = 999, kind: str = "", only: str = ""):
    import intelligence.pdf_extractor as px
    st = _load()
    _index_docs(st)
    _save(st)

    cls = getattr(px, METHODS[method])
    extractor = cls()

    todo = [s for s, d in sorted(st["docs"].items())
            if method not in d["methods"]
            and (not kind or d["kind"] == kind)]
    if only:
        # Restrict to documents that already have a result for `only`, so the two
        # methods can be compared head-to-head on exactly the same documents.
        todo = [s for s in todo if only in st["docs"][s]["methods"]]
    print(f"[{method}] {len(todo)} documents pending; running {min(limit, len(todo))}")

    for sym in todo[:limit]:
        d = st["docs"][sym]
        t0 = time.time()
        try:
            res = extractor.extract(Path(d["path"]).read_bytes())
            fin = (res or {}).get("financials") or {}
            core = {k: v for k, v in fin.items() if not k.startswith("_") and v is not None}
            d["methods"][method] = {
                "n": len(core),
                "n_high": fin.get("_n_high"),
                "balance_ok": fin.get("_balance_ok"),
                "scale": fin.get("_unit_scale"),
                "fields": sorted(core.keys()),
                "anomalies": _anomalies(fin),
                "secs": round(time.time() - t0, 1),
            }
        except Exception as e:
            d["methods"][method] = {"n": 0, "error": f"{type(e).__name__}: {str(e)[:80]}",
                                    "anomalies": ["EXC"], "secs": round(time.time() - t0, 1),
                                    "fields": []}
        m = d["methods"][method]
        print(f"  {sym:9} {d['kind']:8} n={m['n']:<3} bal={str(m.get('balance_ok')):5} "
              f"{m['secs']:>6}s {','.join(m.get('anomalies', []))[:40]}")
        _save(st)


def cmd_matrix():
    st = _load()
    docs = st["docs"]
    if not docs:
        print("no data yet")
        return

    print("=== PER-METHOD ACCURACY MATRIX ===")
    print("(the evidence for a router that picks the best mechanism per report type)")
    print()
    for kind in ("digital", "scanned"):
        subset = {s: d for s, d in docs.items() if d["kind"] == kind}
        if not subset:
            continue
        print(f"--- {kind.upper()} documents ({len(subset)}) ---")
        print(f"{'METHOD':24} {'RAN':>4} {'GOTdata':>8} {'HITRATE':>8} {'AVGflds':>8} "
              f"{'BALok':>6} {'ANOM':>5} {'AVGsec':>7}")
        for method in METHODS:
            runs = [d["methods"][method] for d in subset.values() if method in d["methods"]]
            if not runs:
                continue
            got = [r for r in runs if r["n"] > 0]
            bal = sum(1 for r in runs if r.get("balance_ok") is True)
            anom = sum(1 for r in runs if r.get("anomalies"))
            avg_n = (sum(r["n"] for r in got) / len(got)) if got else 0
            avg_s = sum(r["secs"] for r in runs) / len(runs)
            print(f"{method:24} {len(runs):>4} {len(got):>8} "
                  f"{len(got)/len(runs)*100:>7.0f}% {avg_n:>8.1f} {bal:>6} {anom:>5} {avg_s:>7.1f}")
        print()

    # Head-to-head: where does a slower method actually beat a faster one?
    print("=== HEAD-TO-HEAD (does the slower method earn its cost?) ===")
    pairs = [("pdfplumber_tables", "pdfplumber_text"),
             ("pdfplumber_text", "tesseract_raw"),
             ("tesseract_raw", "tesseract_preprocessed")]
    for a, b in pairs:
        both = [(s, d["methods"][a], d["methods"][b]) for s, d in docs.items()
                if a in d["methods"] and b in d["methods"]]
        if not both:
            continue
        b_wins = [s for s, ra, rb in both if rb["n"] > ra["n"]]
        a_wins = [s for s, ra, rb in both if ra["n"] > rb["n"]]
        ties = len(both) - len(b_wins) - len(a_wins)
        extra = sum(rb["secs"] - ra["secs"] for _, ra, rb in both) / len(both)
        print(f"  {b} vs {a}: on {len(both)} docs -> {b} better on {len(b_wins)}, "
              f"worse on {len(a_wins)}, tie {ties}; avg extra cost {extra:+.0f}s")
        if b_wins:
            print(f"      {b} wins: {', '.join(b_wins[:10])}")
    print()

    print("=== ROUTING SIGNAL: best method per document feature ===")
    buckets = {}
    for s, d in docs.items():
        ran = {m: r for m, r in d["methods"].items() if r.get("n", 0) > 0}
        if not ran:
            continue
        best = max(ran.items(), key=lambda kv: (kv[1].get("balance_ok") is True,
                                                kv[1]["n"], -kv[1]["secs"]))[0]
        key = (d["kind"], "big" if (d.get("pages") or 0) > 50 else "small")
        buckets.setdefault(key, {}).setdefault(best, 0)
        buckets[key][best] += 1
    for key, counts in sorted(buckets.items()):
        tot = sum(counts.values())
        win = ", ".join(f"{m} {c}/{tot}" for m, c in sorted(counts.items(), key=lambda x: -x[1]))
        print(f"  {key[0]:8} {key[1]:6} -> {win}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "matrix"
    if cmd == "run":
        cmd_run(sys.argv[2],
                int(sys.argv[3]) if len(sys.argv) > 3 else 999,
                sys.argv[4] if len(sys.argv) > 4 else "",
                sys.argv[5] if len(sys.argv) > 5 else "")
    else:
        cmd_matrix()
