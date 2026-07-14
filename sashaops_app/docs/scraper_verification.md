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

## Priorities 3, 5-11

Pending verification. Entries added as each is completed. All will be routed
through `record_observation()` so every value is verifiable.
