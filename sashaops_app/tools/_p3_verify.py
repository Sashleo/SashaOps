"""Priority-3 verification harness. For each target: select report -> extract
(OCR allowed) -> report the 9 fields and count. Banks omit current assets/liabilities
by design. Also checks a non-bank digital report to prove current-item extraction."""
import sys
from intelligence.psx_report_parser import find_latest_report_url, _download
from intelligence.pdf_extractor import extract_financials

NINE = ["revenue", "gross_profit", "ebit", "net_income", "total_assets",
        "total_equity", "current_assets", "current_liabilities", "total_liabilities"]
BANKS = {"HBL", "MCB", "UBL", "MEBL", "BAHL", "ABL", "BML"}


def check(t, ocr):
    print(f"\n########## {t} (ocr={ocr}) ##########", flush=True)
    url = find_latest_report_url(t)
    print("  url:", url, flush=True)
    b = _download(url, t) if url else None
    if not b:
        print("  no PDF"); return
    res = extract_financials(b, ocr=ocr)
    fin = res.get("financials", {})
    print("  method:", res.get("method"), "scale:", fin.get("_unit_scale"),
          "balance_ok:", fin.get("_balance_ok"), flush=True)
    got = 0
    for k in NINE:
        v = fin.get(k)
        if v is None and k == "total_equity":
            v = fin.get("shareholders_equity")
        if v is not None:
            got += 1
        print(f"    {'OK ' if v is not None else '-- '}{k:20} = "
              f"{round(v/1e9,3) if isinstance(v,(int,float)) else v}", flush=True)
    na = 2 if t in BANKS else 0
    print(f"  >>> {got}/9 fields ({'bank: current items N/A' if t in BANKS else 'non-bank'})", flush=True)


targets = sys.argv[1:] or ["LUCK:1", "ENGRO:1", "IMAGE:1", "FFC:0", "PSO:0", "DGKC:0"]
for spec in targets:
    t, _, o = spec.partition(":")
    check(t, bool(int(o or "0")))
