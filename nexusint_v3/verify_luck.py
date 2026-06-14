"""
verify_luck.py — Verify the full local analysis pipeline for LUCK.
Run from nexusint_v3/: python verify_luck.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

def check(label, result, expected_type=None):
    ok = result is not None
    if expected_type:
        ok = ok and isinstance(result, expected_type)
    status = "✅" if ok else "❌"
    print(f"{status} {label}")
    if not ok:
        print(f"   Got: {result!r}")
    return ok

print("\n=== SashaOps — LUCK Pipeline Verification ===\n")

# 1. PSX companies table
try:
    from database.connection import get_db
    db = get_db()
    row = db.execute(
        "SELECT * FROM psx_companies WHERE ticker = 'LUCK'"
    ).fetchone()
    check("psx_companies has LUCK", row)
    if row:
        print(f"   sector={row['sector']} price={row.get('current_price')} mcap={row.get('market_cap')}")
except Exception as e:
    print(f"❌ psx_companies query failed: {e}")

# 2. PSX financials table
try:
    row = db.execute(
        "SELECT * FROM psx_financials WHERE ticker = 'LUCK' ORDER BY period DESC LIMIT 1"
    ).fetchone()
    check("psx_financials has LUCK", row)
    if row:
        print(f"   revenue={row.get('revenue')} net_income={row.get('net_income')} total_assets={row.get('total_assets')}")
except Exception as e:
    print(f"❌ psx_financials query failed: {e}")

# 3. Financial model
try:
    from intelligence.financial_model import run_local_analysis_psx
    result = run_local_analysis_psx("LUCK")
    has_ratios = (
        result and
        result.get("gross_margin") is not None or
        result.get("current_ratio") is not None
    )
    check("run_local_analysis_psx returns ratios", has_ratios)
    if result:
        print(f"   gross_margin={result.get('gross_margin')} current_ratio={result.get('current_ratio')} de={result.get('debt_to_equity')}")
except Exception as e:
    print(f"❌ financial model failed: {e}")

# 4. Competitor lookup
try:
    from intelligence.financial_model import _compute_comparables
    peers = _compute_comparables("LUCK")
    check("_compute_comparables returns peers", peers and len(peers) >= 1, list)
    if peers:
        print(f"   peers: {[p.get('ticker') for p in peers[:5]]}")
except Exception as e:
    print(f"❌ comparables failed: {e}")

# 5. Feed headlines
try:
    import asyncio
    from intelligence.feeds import fetch_feeds_for_region
    items = asyncio.run(fetch_feeds_for_region("pakistan"))
    has_titles = items and any(getattr(i, 'title', None) for i in items[:10])
    check("feeds return items with titles", has_titles)
    if items:
        sample = next((getattr(i, 'title', None) for i in items if getattr(i, 'title', None)), None)
        print(f"   sample: {sample}")
except Exception as e:
    print(f"❌ feed fetch failed: {e}")

# 6. News brief format
try:
    from intelligence.news_analyser import analyse_news_for_business, format_brief_for_prompt
    brief = asyncio.run(analyse_news_for_business(items[:50], "Lucky Cement", "pakistan"))
    text = format_brief_for_prompt(brief)
    has_content = text and len(text) > 100 and "•" in text
    check("format_brief_for_prompt returns content", has_content)
    print(f"   length={len(text)} chars")
except Exception as e:
    print(f"❌ news brief failed: {e}")

# 7. Macro data
try:
    from database.macro_data import get_macro_summary_text
    macro = get_macro_summary_text("general")
    has_values = macro and any(c.isdigit() for c in macro)
    check("get_macro_summary_text returns numeric data", has_values)
    if macro:
        print(f"   {macro[:150]}")
except Exception as e:
    print(f"❌ macro data failed: {e}")

print("\n=== Done ===\n")
