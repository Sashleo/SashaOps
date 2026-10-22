# Known issues

## OGRA fuel prices — intermittent `parser_failed`

**Scraper:** `ogra_prices`  
**Status seen:** `parser_failed`, error `scrape() returned no data`  
**Date confirmed:** 2026-10-22  
**URL:** https://www.ogra.org.pk/notified-petroleum-prices  

**Evidence:** The OGRA listing page mixes document types — Petrol/Diesel price
computations, Kerosene-only SRO notifications, and scanned images with no text
layer. The scraper checks up to 8 recent entries and uses the first one that is
both digital and contains a Petrol/Diesel computation. On 2026-10-22, none of
the 8 most recent entries met this criterion.

**Not a code bug.** The scraper is designed to skip scanned or wrong-product PDFs.
The `target_success_rate = 0.6` comment in `ogra_prices.py` records that this is
expected: "1 of the 5 most recent notifications was usable". A `parser_failed`
after checking 8 scanned-only entries is the intended fall-through.

**Resolution:** No code change. The scraper will succeed on the next run cycle
once OGRA publishes a digital Petrol/Diesel computation PDF. OCR work is deferred
until the real-text pipeline is verified.

---

## FBR revenue / NEPRA tariff — `success` with 0 new rows

**Scrapers:** `fbr_revenue`, `nepra_power`  
**Status seen:** `success rows=0`  
**Date confirmed:** 2026-10-22  

**Evidence:** Both scrapers run weekly and return `success` even when no new data
has been published since the last run. Previously the dashboard showed these as
green ("success"), which was misleading — they ran fine but stored nothing new.

**Fixed in C7:** The data_health card now shows amber with "no new rows since DATE"
for any scraper that returns `success` with `rows_inserted = 0`.
