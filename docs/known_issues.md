# Known Issues

Issues confirmed as genuine external constraints or deliberate trade-offs.
None require a code fix unless the upstream situation changes.

---

## PSX prices intraday job "never runs" in a given session

**Symptom:** The `psx_prices_intraday` job shows no scrape_log entries during a
container run started outside market hours.

**Root cause:** The job is gated to 09:30–15:30 PKT Mon–Fri. A container started
at 22:00 PKT will not log a price run until the next trading session opens.

**Mitigation:** The `psx_prices_close` job runs at 17:00 PKT daily and always
updates prices regardless of the intraday gate. If even that is missing, check
that the container's system clock is set correctly and that `Asia/Karachi` resolves
(requires `tzdata` in the image, which is already present).

---

## FBR / NEPRA / OGRA rows stale despite scraper showing "success"

**Symptom:** Scraper status is `success` with `rows_inserted = 0`, yet the most
recent row in the table is weeks old.

**Root cause:** These government sites publish at irregular, unannounced intervals.
A successful fetch that finds no new data is correct behaviour — the source simply
hasn't updated. The scheduler cadence (weekly or bi-weekly) is already set to match
the sites' typical publication rhythm.

**What to check if stale > 4 weeks:** Open the source URL (listed in
`docs/scraper_verification.md`) and verify the page is still the same shape. If
the site redesigned, update the CSS selector in the scraper.

---

## PSX financials "tuple index out of range" (psx_financials bulk job)

**Symptom:** `scrape_log` row for `psx_financials` shows `PARSER_FAILED` with
a short error message. Full traceback now available in structured logs via
`log.warning("Job raised", traceback=...)`.

**Root cause:** The PSX company financials page occasionally returns a table with
fewer columns than expected (e.g. for a newly listed company with only one year of
data, or for a company in suspension). The parser assumes ≥2 periods.

**Mitigation:** The failing ticker is skipped; all others proceed. Check logs for
`"tuple index out of range"` with `job=psx_financials` to identify the ticker and
verify the page manually.

---

## OCR quality on scanned annual reports

**Symptom:** Balance sheet and cash flow sections show "N/A — no filing found"
for tickers whose PSX page only has a scanned PDF.

**Root cause:** Tesseract OCR on low-DPI scans (common in pre-2020 reports)
produces garbled column alignment. The OCR trust gate rejects any parse where
`assets ≠ liabilities + equity` within 1%. This is intentional — a wrong number
is worse than a missing one.

**Workaround:** Use the Data Entry tab to enter the figures manually from the
original PDF. They feed the same ratios, DuPont and distress pipeline.

---

## No exchange holiday calendar

**Symptom:** Intraday scraper runs on a PSX trading holiday and finds stale data
(market closed, no price movement). `rows_inserted = 0` is logged as `NO_DATA`.

**Root cause:** PSX does not publish a machine-readable holiday calendar. A static
list would require manual maintenance.

**Impact:** Negligible — the scraper fetches the previous day's close, which is
already in the database, so `UNIQUE(symbol, trade_date)` means no duplicate is
inserted. The `NO_DATA` status on those days is correct and expected.
