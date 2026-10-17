"""
tools/bulk_sweep.py - Run the PSX bulk financials sweep and report coverage.

Manual trigger for Phase 3 STEP 1: populate psx_financials for every listed
company (income statement from the PSX summary, no per-ticker PDF work), then
recompute sector medians and report how many sectors reach a usable peer count.
"""
import time
from database.connection import init_database, get_db

init_database()
from intelligence.psx_financials import bulk_refresh_psx_financials

t0 = time.time()
done = bulk_refresh_psx_financials()
print(f"SWEEP_DONE tickers_with_data={done} elapsed={time.time()-t0:.0f}s", flush=True)

# Recompute sector medians now that coverage has changed.
try:
    from intelligence.sector_medians import compute_sector_medians
    n = compute_sector_medians()
    print(f"SECTOR_MEDIANS_ROWS={n}", flush=True)
except Exception as e:
    print("median computation failed:", str(e)[:120], flush=True)

db = get_db()
print("\n=== COVERAGE ===", flush=True)
tot = db.execute("SELECT COUNT(*) c FROM psx_companies WHERE COALESCE(is_debt,0)=0").fetchone()["c"]
withrev = db.execute(
    "SELECT COUNT(DISTINCT symbol) c FROM psx_financials WHERE revenue IS NOT NULL"
).fetchone()["c"]
withboth = db.execute(
    "SELECT COUNT(DISTINCT symbol) c FROM psx_financials "
    "WHERE revenue IS NOT NULL AND net_income IS NOT NULL"
).fetchone()["c"]
multi = db.execute(
    "SELECT COUNT(*) c FROM (SELECT symbol FROM psx_financials WHERE period_type='annual' "
    "GROUP BY symbol HAVING COUNT(*) >= 2)"
).fetchone()["c"]
print(f"listed (non-debt):            {tot}")
print(f"with revenue:                 {withrev}")
print(f"with revenue AND net income:  {withboth}")
print(f"with 2+ annual periods:       {multi}")

print("\n=== BY SECTOR (companies with revenue AND net income) ===", flush=True)
rows = db.execute(
    """SELECT c.sector_name AS sector, COUNT(DISTINCT f.symbol) AS n
       FROM psx_financials f JOIN psx_companies c ON c.symbol = f.symbol
       WHERE f.revenue IS NOT NULL AND f.net_income IS NOT NULL
         AND COALESCE(c.is_debt,0)=0
       GROUP BY c.sector_name ORDER BY n DESC"""
).fetchall()
ge4 = 0
for r in rows:
    d = dict(r)
    mark = " <-- 4+" if d["n"] >= 4 else ""
    if d["n"] >= 4:
        ge4 += 1
    print(f"  {str(d['sector'])[:40]:42} {d['n']:3}{mark}")
print(f"\nsectors with data:      {len(rows)}")
print(f"sectors with 4+ valid:  {ge4}")
