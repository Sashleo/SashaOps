# Scraper Verification Log

Every scraper in Phase 2 is verified against values read from the live source
BEFORE the scraper runs, then compared against what it actually stored.
Thresholds: numeric divergence > 1% = FAILED (5% for statistical series),
text mismatch = FAILED, missing field = FAILED, wrong period = FAILED.

Source reachability check (2026-07-14, from inside the app container): PSX,
SBP, er-api, World Bank, OGRA, PBS, FBR, NEPRA, PTA all returned HTTP 200.

---

## Priority 1 - PSX prices (`psx_market`)

**Status: PASSED**
**Date verified:** 2026-07-14
**Source URL:** `https://dps.psx.com.pk/company/{ticker}`, `https://dps.psx.com.pk/`
**Method used:** `json_api` (symbols) + `html_bs4` (market summary / indices)

### Values seen on source (ground truth, read before running the scraper)

| Target | Price | Open | High | Low | Volume |
|---|---|---|---|---|---|
| HBL | 298.04 | 305.56 | 307.44 | 297.01 | 2,264,948 |
| KSE-100 index | 173,518.81 | | | | |

### Values stored by the scraper

| Target | Stored | Divergence | Verdict |
|---|---|---|---|
| HBL current | 298.04 | 0.00% | PASS |
| HBL open/high/low | 305.56 / 307.44 / 297.01 | 0.00% | PASS |
| HBL volume | 2,264,948 | 0.00% (non-zero) | PASS |
| KSE-100 | 173,518.81 | 0.00 points | PASS |
| psx_companies rows | 756 | - | PASS |

`scrape_log` row written: `psx_market | success | 756 rows | 10.4s`.

### Parsing gotchas (read this first when it breaks)

- **Tabbed OHLC panels.** The company page renders Open/High/Low/Volume inside
  tabs: REG / DFC / CSF / ODL. Only the FIRST panel (REG = regular market) holds
  real values; the others are all zeros. Selecting `div.stats_item` across the
  whole page silently reads 0s. Always scope to
  `div.quote__stats div.tabs__panel` (first panel).
- **Delisted companies still serve a page.** ENGRO returns a normal-looking page
  with a price (485.38), but it is a frozen snapshot: "As of Fri, Jan 3, 2025".
  These must not be treated as live prices. The scraper sets `delisted` and skips
  the `psx_prices` write. (ENGRO was therefore replaced as a price-verification
  target; the original Phase 2 brief pre-dates its delisting.)

---

## Priority 2 - PSX company profile (`psx_company_profile`)

**Status: PASSED**
**Date verified:** 2026-07-14
**Source URL:** `https://dps.psx.com.pk/company/{ticker}`
**Method used:** `html_bs4` (server-rendered HTML; Playwright NOT needed)

New scraper: `intelligence/exchange_scrapers/psx_company.py`. It captures every
field the page displays, not just prices. `per_target(ticker)` does one company;
`fetch()` is the nightly BULK job over every ticker in `psx_companies`, so
**market cap is populated for every listed company, not only analysed ones**.

### Values seen on source (HBL, ground truth)

| Field | Value on page |
|---|---|
| Sector | COMMERCIAL BANKS |
| CEO | Muhammad Nassir Salim |
| Chairperson | Sultan Ali Allana |
| Company Secretary | Uzman Naveed Chaudhary |
| Market Cap (000's) | 437,180,721.48 |
| Shares | 1,466,852,508 |
| Free Float | 586,741,003 (40.00%) |
| P/E (TTM) | 6.96 |
| 52-week range | 214.00 - 369.99 |
| Website | www.hbl.com |

### Values stored by the scraper

All 16 checked fields matched exactly (0.00% divergence): sector, ceo, chairman,
company_secretary, market_cap (437,180,721,480 PKR), shares_outstanding,
free_float, free_float_pct, price, open, high, low, volume, pe_ttm, week52_low,
week52_high. Also captured: 15 announcements (date/title/PDF), 3 data tables
(annual + quarterly financials, payouts), website, address, registrar, business
description. **HBL: 16/16 PASS.**

Cross-check: 1,466,852,508 shares x 298.04 = 437.16bn, matching the stated
market cap of 437.18bn.

### Multi-ticker run (all 7 required)

| Ticker | Status | Sector | Market cap | Price | P/E | CEO |
|---|---|---|---|---|---|---|
| HBL | success | COMMERCIAL BANKS | 437.2bn | 298.04 | 6.96 | Muhammad Nassir Salim |
| LUCK | success | CEMENT | 642.7bn | 438.68 | 39.59 | Muhammad Ali Tabba |
| ENGRO | success | FERTILIZER | 260.5bn | (delisted) | - | Ahsan Zafar Syed |
| BFBIO | success | PHARMACEUTICALS | 11.6bn | 130.92 | 20.81 | Muhammad Farhan Rafiq |
| SYS | success | TECHNOLOGY & COMM | 203.5bn | 138.13 | 25.25 | Asif Peer |
| FFC | success | FERTILIZER | 781.7bn | 543.22 | 9.97 | Jahangir Piracha |
| PSO | success | OIL & GAS MARKETING | 162.7bn | 346.59 | 3.72 | Jawwad Ahmed Cheema |

All 7 extract correctly across different sectors and page variations.

### Parsing gotchas

- **"Market Cap (000's)" is in THOUSANDS.** Multiply by 1000 before storing PKR.
  Storing it raw understates every company by 1000x.
- **"Free Float" is printed TWICE** - once as an absolute share count and again as
  a percentage. A first-wins dict silently drops the percentage. Scan every
  `div.stats_item` and branch on whether the value contains "%".
- **Key people are name/role pairs**, not a labelled list. The page shows 3 key
  people (CEO, Chairperson, Company Secretary), NOT a full board of 5+. The Phase 2
  brief expected "at least 5 board members"; PSX does not publish that on this page,
  so the realistic target is the 3 key people, all of which are captured.
- Same tabbed-panel gotcha as Priority 1 applies to OHLC.

---

## Infrastructure fix found during verification

**`/app/.tmp` was not writable.** The Phase 1 non-root uid (10001) could not write
to `.tmp`, which is a Windows bind mount owned by root. This silently broke EVERY
scraper's disk cache (`.tmp/gov_cache`, `.tmp/psx_reports`). Fixed by making `.tmp`
a Docker-managed volume (`sashaops_tmp`) in docker-compose.yml, since the bind mount
cannot be reliably chowned on Windows. `.tmp` is disposable cache, so a volume is
the correct home for it.

---

## Priority 4 - SBP monetary policy (`sbp_monetary`)

**Status: PASSED**
**Date verified:** 2026-07-14
**Source URL:** `https://www.sbp.org.pk/m_policy/index.asp` (also checks ecodata + homepage)
**Method used:** `html_bs4`

### Values seen on source (ground truth)

| Field | Value on page |
|---|---|
| SBP Policy Rate | **11.50% p.a.** |
| Overnight Reverse Repo (Ceiling) | 12.50% p.a. |
| Overnight Repo (Floor) | 10.50% p.a. |

### Value stored by the scraper

`sbp_policy_rate = 11.5` (%), period 2026-07. Divergence 0.00%. **PASS.**
The historical 3%-instead-of-11% bug does NOT recur: the scraper enforces a
Pakistan-realistic plausibility band (5-30) so a stray number near a policy-rate
keyword (e.g. "3 members voted") cannot be stored as the rate.

### Provenance (the verifiable pipeline)

Stored WITH the value, so a future discrepancy is auditable:

```
source_url     https://www.sbp.org.pk/m_policy/index.asp
source_snippet "Policy Rate 11.50% p.a. SBP Overnight Reverse Repo (Ceiling Rate) 12.50% p.a. ..."
method_used    html_bs4
scraper_id     sbp_monetary
sanity_flag    (null = within the 5-30 band)
```

---

## The verifiable data pipeline (applies to every macro scraper)

Requested explicitly: source websites change shape often (PSX alone produced three
traps in this phase), so a number with no provenance cannot be audited.

`database/provenance.py` is the single write path for macro observations.
`record_observation()` REFUSES to store a value without `source_url` and
`source_snippet`. Every observation carries:

| Column | Meaning |
|---|---|
| `source_url` | exact page/endpoint the value came from |
| `source_snippet` | raw as-printed text the number was parsed from |
| `method_used` | html_bs4 / json_api / playwright / pdf / ocr |
| `scraper_id`, `fetched_at` | who produced it, and when |
| `sanity_flag` | `implausible` when outside its documented band - FLAGGED, never deleted |

Read side (what the tool consumes; UI design TBD):
`get_observation(indicator)`, `get_observations()`, `get_flagged()`,
`resweep_sanity()`. These return the value together with its source so a page can
render "11.5% (SBP, as of 2026-07)" with a link back, and a human can confirm it
against the live site.

---

## Priority 5 - Forex rates (`sbp_forex`)

**Status: PASSED**
**Date verified:** 2026-07-28
**Source URL:** `https://open.er-api.com/v6/latest/USD` (SBP's own rate endpoints
return 404 / fail DNS resolution from containerised environments — documented in
the scraper's own header comment; ExchangeRate-API is the working substitute)
**Method used:** `json_api`

### Values seen on source (ground truth, manually derived from the live API response)

The API is USD-based (`1 USD = N units of quote`), so a PKR cross-rate is
`rate_PKR = usd_rates["PKR"] / usd_rates[quote]`. Computed by hand before running
the scraper, per protocol:

| Pair | Ground truth (derived from live API, 2026-07-28) |
|---|---|
| USD/PKR | 277.86 |
| EUR/PKR | 316.18 |
| GBP/PKR | 369.54 |
| AED/PKR | 75.66 |
| SAR/PKR | 74.10 |

### Values stored by the scraper

| Pair | Stored | Divergence |
|---|---|---|
| USD/PKR | 277.859092 | 0.00% |
| EUR/PKR | 316.179403 | 0.00% |
| GBP/PKR | 369.539174 | 0.00% |
| AED/PKR | 75.659385 | 0.00% |
| SAR/PKR | 74.095758 | 0.00% |

All 5 within 1% (exact match). **PASS.** A fresh run (today's date-partition cleared
first, to separate "already inserted" from a real failure) returned
`ScrapeStatus.SUCCESS`, 13/13 rows inserted, 0 failed — covering all 13
`ERAPI_STORE_QUOTES` pairs (USD, EUR, GBP, JPY, CHF, CNY, SAR, AED, KWT, QAR, OMR,
INR, CNH), not only the 5 checked here.

### Gotcha for future debugging

`INSERT OR IGNORE` on `(rate_date, quote_currency)` means re-running the scraper on
a day already fetched reports `rows_inserted=0` — that is deduplication working, NOT
a failure. Verifying "does this scraper still work" must clear or check the
day-partition first, or a healthy scraper looks PARTIAL/failed by row count alone.

### Provenance

`pkr_forex` stores `source` (the API URL) and `fetched_at` per row, so every rate is
traceable to its endpoint and fetch time. It is a lighter provenance than
`record_observation()`'s (no raw source_snippet of the exact API field) — acceptable
for a machine-readable JSON API where the URL alone reproduces the value, unlike an
HTML/PDF page where the snippet proves what was actually read.

---

## Priority 6 - FBR tax revenue (`fbr_revenue`)

**Status: PASSED (rewritten - the previous implementation could never have worked)**
**Date verified:** 2026-07-28
**Source URL:** `https://www.fbr.gov.pk/revenue-collections/142253/131355`, PDF:
`https://download1.fbr.gov.pk/Docs/20261151114844602MONTHWISE-TAXWISENETCOLLECTIONDURINGFY2024-25.pdf`
**Method used:** `pdf`

### What was actually wrong (Step D of the protocol)

The previous scraper scraped `fbr.gov.pk/Newsroom/` for a number near keywords like
"revenue collection". Inspecting the page live: it is a **news/navigation homepage**
(press releases, menu links) with **no tax-collection figures anywhere in its text**
- confirmed by dumping the full extracted text. The keyword-proximity approach could
never have succeeded against this page; it was scraping the wrong page entirely.

Followed the source to its real location: Newsroom -> "Dte Gen of Revenue Analysis"
-> "Revenue Collections" -> a list of PDFs, one per fiscal year, titled "MONTH WISE /
TAX WISE NET COLLECTION DURING FY <year>", listed newest-first. Rewrote the scraper
to fetch that listing, download the first (latest) PDF, and read its table directly.

### Values seen on source (ground truth, read by hand from the PDF before running the scraper)

```
MONTH WISE / TAX WISE NET COLLECTION DURING FY: 2024-25   (Rs. in Billion)
...
JUNE   890.9  397.9  93.7  127.9  1,510.4
...
Total  5,791.7  3,901.4  766.6  1,284.6  11,744.3
```
Latest reported month: **June, Rs 1,510.4bn**. FY2024-25 total: **Rs 11,744.3bn**.
(No FY2025-26 PDF exists on the live source yet, so June FY24-25 genuinely is the
latest published figure - the scraper is not stale, the source has not updated.)

### Value stored by the scraper

`fbr_revenue(tax_type='total', period='FY2024-25-June', collected_pkr_bn=1510.4)`
`fbr_revenue(tax_type='total', period='FY2024-25', collected_pkr_bn=11744.3)`
Both exact matches. Divergence 0.00%. **PASS.** `ScrapeStatus.SUCCESS`, 2 rows.

### Gotcha for future debugging

FBR occasionally omits the FY dash format (`2024-25` vs `2024-2025`) between years;
`_FY_TITLE` accepts 2-4 digit second years. The PDF table sometimes right-pads a
"Total" row differently across years - `_last_number()` always takes the rightmost
numeric column rather than a fixed index, so a format tweak in one year's PDF
(observed across the 2003-04 through 2024-25 archive) does not silently misalign.

### Provenance

Routed through `record_observation()` for the monthly figure (`source_url` = the
exact PDF, `source_snippet` = the matched table row) plus the existing
`fbr_revenue`/`pk_macro` tables for the app's normal read path — same dual-write
pattern as Priority 4 (SBP). Sanity band `fbr_tax_collection_monthly_bn: (50, 5000)`
added to `SANITY_BOUNDS`.

---

## Priority 7 - OGRA petroleum prices (`ogra_prices`)

**Status: PASSED (rewritten; required a Playwright browser install)**
**Date verified:** 2026-07-28
**Source URL:** `https://www.ogra.org.pk/notified-petroleum-prices`
**Method used:** `pdf` (via a Playwright-rendered listing page)

### What was found (Steps A/D of the protocol)

The page returns 200 and 265KB of HTML, but a raw-text search for "HSD"/"high speed
diesel"/"petrol" (excluding nav-menu links) found **nothing** - no PDF link, no
`wp-json`/`/api/`/GraphQL hint, no embedded JSON, no iframe. Confirmed, not assumed:
OGRA's price table is rendered client-side after page load, so `requests` +
BeautifulSoup can never see it.

Per the plan's remediation step 4, the fix is Playwright (already a listed
dependency) to render the page and read the DOM after JS executes. The Chromium
binary was not installed in the running container
(`BrowserType.launch: Executable doesn't exist`). Before installing it, checked
free disk space given a prior full-disk/ext4-corruption incident on this machine
([[disk_space_constraint]]): host C: had ~6.8GB free. Installed with live monitoring
(checked free space every ~8s, ready to abort) rather than blindly; it completed
cleanly, using ~700MB (6.8GB -> 6.1GB free). `playwright install chromium` is
already in the Dockerfile, so a proper image rebuild will have it baked in - this
was only needed to activate it in the already-running container.

### What the rendered page actually contains

With Chromium rendering the page, the real download links appeared
(`<a class="download-pdf" href="https://www.ogra.org.pk/download/NNNNN">` next to a
sibling `<p>` title - the anchor's own text is empty, which is why an even simpler
Playwright pass that read link text directly found nothing). The feed mixes document
types by day, verified by downloading and inspecting the 5 most recent entries:

| Date | Entry | Digital? | Content |
|---|---|---|---|
| Jul 28 | id 14801 | No (scanned) | - |
| Jul 25 | id 14759 | No (scanned) | - |
| **Jul 24** | **id 14714** | **Yes** | **Petrol(MS) + Diesel(HSD) computation** |
| Jul 23 | id 14655 | No (scanned) | - |
| Jul 22 | id 14605 | No (scanned) | - |

Only 1 of 5 was both digital and the right product. A separate list, "Detail
Computation Ex-Depot Sale Price", looked like a dedicated feed but turned out to be a
DIFFERENT document (an ex-refinery/ex-depot Annex-I/II feedstock calculation for
Kerosene/Naphtha/HSFO/Gasoil) - not Petrol/Diesel retail prices. Confirmed by
downloading and reading its content before using it, rather than assuming from the
link title.

### Values seen on source (ground truth, read by hand/eye from the rendered PDF before running the scraper)

The scanned July 27 kerosene notification (id 14801) was read directly as an image:
**Kerosene Max ex-depot 303.10 PKR/litre**, effective July 28, 2026 - correctly
recognised as the wrong product and skipped, not misused.

The digital July 24 computation (id 14714), read from its extracted text:
```
PETROL (MS) Price Effective 23 July 2026 ... Total Petrol Price ... 327.12
Diesel (HSD) Price Effective 23 July 2026 ... Total Diesel Price ... 375.04
```
**Petrol: Rs 327.12/litre. Diesel (HSD): Rs 375.04/litre.** Both effective 23 July 2026.

### Value stored by the scraper

`ogra_prices(product='petrol', price_date='2026-07-23', price_pkr=327.12)`
`ogra_prices(product='hsd',    price_date='2026-07-23', price_pkr=375.04)`
Both exact matches. Divergence 0.00%. **PASS.** `ScrapeStatus.SUCCESS`, 2 rows. The
period stored is the PRICE'S OWN effective date (read from inside the PDF), not the
scrape date - so a verified-but-slightly-older figure is never mislabeled as today's.

### Gotcha for future debugging

The newest entry in a government PDF feed is not always the right one to use: it can
be scanned (unreadable without OCR) or cover a different, unrelated product entirely.
`_extract_prices()` rejects both cases explicitly (text-length floor, and requiring
"petrol"/"diesel" to actually appear) rather than guessing, and the scraper walks
backward through up to 8 recent entries to find one that is genuinely usable.

### Provenance

Routed through `record_observation()` per product (`ogra_petrol_price_pkr_litre`,
`ogra_hsd_price_pkr_litre`), `source_url` = the exact PDF, `source_snippet` = the
listing title. Sanity bands added to `SANITY_BOUNDS`.

---

## Priority 8 - NEPRA tariff determinations (`nepra_power`)

**Status: PASSED (rewritten)**
**Date verified:** 2026-07-28
**Source URL:** `https://nepra.org.pk/` (no `www` — the `www.` host 301-redirects here
and had separately been reported 403 in an earlier check)
**Method used:** `html_bs4`

### What was actually wrong

The previous scraper targeted `www.nepra.org.pk` pages that were reportedly 403-
blocked, and searched for a single numeric "industrial tariff" figure. Checked live:
`nepra.org.pk` (bare domain) returns 200 and its homepage IS reachable and current
(determinations dated up to the day of verification). NEPRA does not publish one
headline rate on its homepage — tariffs vary by DISCO and consumer class and are set
out inside each determination PDF — so the plan's actual Priority 8 target ("latest
determination date and reference") was achievable directly from the homepage's
determinations table, which lists entries newest-first with a date and a PDF link
per row.

### Values seen on source (ground truth, read by hand before running the scraper)

First (most recent) row in the determinations table:
```
17-07-2026 | Decision of the Authority in the matter of Application filed by
Aerem Energy (Private) Limited for approval of tariff upto AC 240 MW Site
Neutral Hybrid Project at K-Electric Dhab...
-> tariff/Tariff/TRF-623 Decision Aerem Energy 17-07-2026 12993-13103.pdf
```
Latest determination: **date 17-07-2026, reference TRF-623**.

### Value stored by the scraper

`nepra_power(metric='latest_tariff_determination', period='17-07-2026', unit='TRF-623')`.
Exact match on both date and reference. **PASS.** `ScrapeStatus.SUCCESS`, 1 row.

### Gotcha for future debugging

The homepage also lists **Admission Notices** (e.g. a scanned `.jpeg`) ahead of and
interleaved with tariff determinations in page order - matching on "first `view`
link" (as an earlier probe did) picks up a notice, not a determination. The fix is
to require the link path contain `tariff/Tariff/` AND end in `.pdf`, and to read the
date/reference from that link's own `<tr>`, not page order alone.

### Provenance

This finding is a date + reference, not a scalar rate, so it does not fit
`record_observation()` (which requires a numeric value and refuses to store `None` -
by design, so a text finding is never mistaken for a missing number). Verifiability
comes instead from `nepra_power.unit` (the reference) plus `ScrapeResult.artifacts`
(the full row text, PDF URL, and homepage URL — logged by `BasePakGovScraper.fetch()`
via `r.artifacts = {"data": data}`).

---

## Priority 9 - PBS: LSM/QIM index + CPI inflation (`pbs_lsmi`)

**Status: PASSED (rewritten; CPI extraction added - it did not exist before)**
**Date verified:** 2026-07-28
**Source URLs:** `https://www.pbs.gov.pk/industry-2/` (LSM), `https://www.pbs.gov.pk/?page_id=11078` (CPI)
**Method used:** LSM `xlsx`, CPI `pdf`

### What was actually wrong

The previous scraper searched on-page HTML text for a growth number near "LSM"/
"QIM" keywords - the real figures are only in downloadable monthly Excel releases
("Table 1 <Month>-<Year>.xlsx"), never in page text, so this could not reliably
find them. More importantly: **there was no CPI extraction at all**, despite
`database/macro_data.py`'s `get_macro_summary_text()` already calling
`get_sbp_indicator("cpi")` and expecting a value - a real, previously invisible gap
where the macro summary silently showed no inflation figure.

Both real feeds list several months of past releases NOT reliably sorted by date in
the DOM (confirmed by inspection), so "take the first link" is unsafe. The rewrite
parses month/year out of each candidate's title/href and selects the maximum by
date, for both series independently.

### Values seen on source (ground truth, read by hand before running the scraper)

QIM Excel table (`Table-1-May-2026.xlsx`), the "QIM" summary row:
```
QIM  weight=78.37  index(May-26)=116.098  %Chg(May-26 vs Apr-26)=+1.21  %Chg(May-26 vs May-25)=-0.98
```
CPI press release (`Press-Release-June-2026.pdf`), headline table:
```
Inflation Rate, June, 2026 over June, 2025 (Year on Year)   11.07
```
**LSM/QIM index: 116.10 (May 2026), YoY -0.98%. CPI: 11.07% YoY (June 2026).**

### Value stored by the scraper

`pbs_lsmi(sector='overall', period='2026-05', index_value=116.1, yoy_change_pct=-0.98)`
`sbp_indicators(indicator='cpi', period='2026-06', value=11.07, source='PBS')`
Both exact matches. Divergence 0.00%. **PASS.** `ScrapeStatus.SUCCESS`, 2 rows.

### Gotcha for future debugging

PBS's own upload has a typo in at least one filename: `Press-Release-Febuary-2026.pdf`
(missing the first "r" in February). `_MONTH_YEAR_RE` matches "febuary" explicitly
so that month's release is not silently skipped. Both listing pages mix release
months out of chronological DOM order - selection must parse and compare dates, not
trust position on the page.

### Provenance

CPI routed through `record_observation()` (`cpi_yoy`, already in `SANITY_BOUNDS`
from Phase 2's original SBP work). LSM stored via the existing `pbs_lsmi`/`pk_macro`
tables (a level + YoY series is not a single scalar "value" in the same shape
`record_observation` expects, matching the NEPRA precedent at Priority 8).

---

## Priorities 3, 10-11

Pending verification. Entries added as each is completed. All will be routed
through `record_observation()` so every value is verifiable.
