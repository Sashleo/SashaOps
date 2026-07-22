"""
tools/pdf_validate.py - Holdout validation of the extraction mechanism.

Picks a SECOND sample of 2 firms per sector, deliberately EXCLUDING every firm used
in the first corpus (tools/pdf_corpus.py), so the parser is tested against reports it
was never tuned on. Overfitting to the first sample would show up here as a drop.

Unlike the first study this does not just count fields. It auto-flags anomalies, so
"missed, broken or just not working" surfaces without reading 76 reports by hand:

  NEGATIVE       a must-be-positive field came back negative (contra/deduction line)
  ABSURD_MAG     value outside any plausible range for a listed company
  BALANCE_FAIL   assets != liabilities + equity (the arithmetic gate failed)
  SUBITEM        the source line looks like a sub-item, not a statement total
  NO_SCALE       unit had to be guessed (no printed header found)
  SCALE_DEFAULT  fell back to the blanket x1000 default with nothing to infer from
  THIN           digital PDF whose text layer is too thin to parse (needs OCR)
  NOTHING        no fields at all

Usage (inside the container, PYTHONPATH=/app):
  python tools/pdf_validate.py fetch
  python tools/pdf_validate.py run
  python tools/pdf_validate.py report
"""

from __future__ import annotations

import io
import json
import random
import sys
import time
from pathlib import Path

BASE = Path("/app/.tmp/pdf_validate")
PDF_DIR = BASE / "pdfs"
STATE = BASE / "validate.json"
FIRST_CORPUS = Path("/app/.tmp/pdf_corpus/corpus.json")

PER_SECTOR = 2
SEED = 77042026          # different seed => different draw

# Plausible ranges for a PSX-listed company, in ACTUAL rupees after scaling.
PLAUSIBLE = {
    "revenue":             (1e6, 5e12),
    "total_assets":        (1e6, 1e14),   # big banks reach ~1e13
    "current_assets":      (1e5, 5e13),
    "current_liabilities": (1e5, 5e13),
    "total_liabilities":   (1e5, 1e14),
    "total_equity":        (-1e12, 5e12),
    "net_income":          (-5e11, 1e12),
    "gross_profit":        (-5e11, 2e12),
    "ebit":                (-5e11, 2e12),
}
MUST_BE_POSITIVE = {"revenue", "total_assets", "current_assets",
                    "current_liabilities", "total_liabilities"}
SUBITEM_HINT = ("long term investment", "spare part", "loose tool", "advance",
                "prepayment", "sales tax", "stores", "trade debt", "deferred")


def _load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"picks": {}, "results": {}}


def _save(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def _first_corpus_symbols() -> set:
    try:
        return set(json.loads(FIRST_CORPUS.read_text(encoding="utf-8"))["picks"].keys())
    except Exception:
        return set()


def pick_holdout() -> dict:
    from database.connection import init_database, get_db
    init_database()
    used = _first_corpus_symbols()
    rows = get_db().execute(
        "SELECT symbol, sector_name FROM psx_companies "
        "WHERE sector_name IS NOT NULL AND COALESCE(is_debt,0)=0"
    ).fetchall()
    by_sector: dict = {}
    for r in rows:
        if r["symbol"] in used:            # holdout: never reuse a tuned-on firm
            continue
        by_sector.setdefault(r["sector_name"], []).append(r["symbol"])
    rnd = random.Random(SEED)
    picks = {}
    for sector, syms in sorted(by_sector.items()):
        for s in rnd.sample(sorted(syms), min(PER_SECTOR, len(syms))):
            picks[s] = sector
    return picks


def classify(pdf_bytes: bytes) -> dict:
    import pdfplumber
    info = {"pages": 0, "chars": 0, "kind": "unknown"}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            info["pages"] = len(pdf.pages)
            info["chars"] = sum(len(p.extract_text() or "") for p in pdf.pages[:6])
        info["kind"] = "digital" if info["chars"] > 400 else "scanned"
    except Exception as e:
        info["kind"] = "broken"
        info["error"] = f"{type(e).__name__}"
    return info


def cmd_fetch():
    from intelligence.psx_report_parser import find_latest_report_url, _download
    st = _load()
    if not st["picks"]:
        st["picks"] = pick_holdout()
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
                    res["kind"] = "no_report"
                    print(f"[{i}] {sym:8} {sector[:20]:22} NO REPORT")
                    _save(st); continue
                data = _download(url, sym)
                if not data:
                    res["kind"] = "download_failed"
                    print(f"[{i}] {sym:8} {sector[:20]:22} DL FAILED")
                    _save(st); continue
                p.write_bytes(data)
                res["url"] = url
            res.update(classify(p.read_bytes()))
            print(f"[{i}] {sym:8} {sector[:20]:22} {res['kind']:8} pages={res['pages']:<4} chars={res['chars']}")
        except Exception as e:
            res["kind"] = "error"; res["error"] = str(e)[:100]
            print(f"[{i}] {sym:8} {sector[:20]:22} ERROR {str(e)[:50]}")
        _save(st)
        time.sleep(0.2)


def _anomalies(fin: dict) -> list:
    """The heart of the validator: what is wrong with this extraction?"""
    flags = []
    prov = fin.get("_provenance") or {}
    core = {k: v for k, v in fin.items() if not k.startswith("_") and v is not None}

    if not core:
        return ["NOTHING"]

    for f, v in core.items():
        if f in MUST_BE_POSITIVE and v <= 0:
            flags.append(f"NEGATIVE:{f}")
        lo_hi = PLAUSIBLE.get(f)
        if lo_hi and not (lo_hi[0] <= abs(v) <= lo_hi[1]) and v != 0:
            flags.append(f"ABSURD_MAG:{f}={v:.3g}")
        src = (prov.get(f) or {}).get("source_line", "").lower()
        if any(h in src for h in SUBITEM_HINT):
            flags.append(f"SUBITEM:{f}")

    if fin.get("_balance_ok") is False:
        flags.append("BALANCE_FAIL")

    ev = (fin.get("_unit_evidence") or "")
    if "no explicit unit header" in ev:
        flags.append("SCALE_DEFAULT" if "default x1000" in ev else "NO_SCALE")

    return flags


def cmd_run():
    from intelligence.pdf_extractor import extract_financials
    st = _load()
    todo = [s for s, r in sorted(st["results"].items())
            if r.get("kind") in ("digital", "scanned") and "fin" not in r]
    print(f"running fast path on {len(todo)} holdout PDFs")
    for sym in todo:
        res = st["results"][sym]
        p = PDF_DIR / f"{sym}.pdf"
        if not p.exists():
            continue
        try:
            r = extract_financials(p.read_bytes(), ocr=False)
            fin = r.get("financials") or {}
            core = {k: v for k, v in fin.items() if not k.startswith("_") and v is not None}
            res["fin"] = {
                "method": r.get("method"),
                "n": len(core),
                "scale": fin.get("_unit_scale"),
                "balance_ok": fin.get("_balance_ok"),
                "fields": sorted(core.keys()),
                "values": {k: core[k] for k in sorted(core)},
                "prov": {k: (fin.get("_provenance") or {}).get(k, {}).get("source_line", "")[:90]
                         for k in sorted(core)},
                "flags": _anomalies(fin),
            }
            if res["kind"] == "digital" and not core:
                res["fin"]["flags"] = list(set(res["fin"]["flags"] + ["THIN"]))
        except Exception as e:
            res["fin"] = {"method": "error", "n": 0, "flags": [f"EXC:{type(e).__name__}"],
                          "error": str(e)[:120], "fields": [], "values": {}, "prov": {}}
        f = res["fin"]
        print(f"{sym:8} {res['kind']:8} n={f['n']:<3} scale=x{f.get('scale')} {','.join(f['flags'])[:60]}")
        _save(st)


def cmd_report():
    st = _load()
    rs = st["results"]
    kinds, flagged = {}, {}
    digital_n, digital_docs = 0, 0
    for sym, r in rs.items():
        kinds[r.get("kind", "?")] = kinds.get(r.get("kind", "?"), 0) + 1
        f = r.get("fin")
        if not f:
            continue
        if r["kind"] == "digital":
            digital_docs += 1
            digital_n += f["n"]
        for fl in f["flags"]:
            key = fl.split(":")[0]
            flagged.setdefault(key, []).append(f"{sym}:{fl.split(':',1)[1] if ':' in fl else ''}")

    print("=== HOLDOUT CORPUS (sample 2, no overlap with sample 1) ===")
    print(f"  firms: {len(rs)}   sectors: {len(set(r['sector'] for r in rs.values()))}")
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {k:16} {v}")
    if digital_docs:
        print(f"  digital avg fields: {digital_n/digital_docs:.1f}")

    print()
    print("=== ANOMALIES (what is missed / broken / not working) ===")
    if not flagged:
        print("  none")
    for k, items in sorted(flagged.items(), key=lambda x: -len(x[1])):
        print(f"  {k:16} {len(items):3}   {', '.join(items[:6])[:96]}")

    print()
    print("=== WORST OFFENDERS (detail) ===")
    shown = 0
    for sym, r in sorted(rs.items()):
        f = r.get("fin")
        if not f or not f["flags"]:
            continue
        hard = [x for x in f["flags"] if x.split(":")[0] in
                ("NEGATIVE", "ABSURD_MAG", "BALANCE_FAIL", "SUBITEM")]
        if not hard or shown >= 12:
            continue
        shown += 1
        print(f"  {sym} ({r['sector'][:22]}, {r['kind']}, x{f.get('scale')}) {f['flags']}")
        for fld in f["fields"]:
            if any(fld in x for x in hard):
                print(f"      {fld} = {f['values'][fld]:,.0f}   <- {f['prov'].get(fld,'')[:70]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"fetch": cmd_fetch, "run": cmd_run}.get(cmd, cmd_report)()
