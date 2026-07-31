# SashaOps - Architecture Specification

A locally-run, PQC-secured financial analyst's workbook for the Pakistan Stock
Exchange. Every figure is scraped or parsed from a primary source, computed
locally, and traceable back to the document it came from.

**No AI anywhere in the analysis path.** Phase 1 removed the Gemini client, the
three-stage pipeline and the token-contract layer entirely. Output sentences are
facts from the database or conditional templates evaluating those facts.

---

## Directory Structure

```
sashaops_app/
|
+-- CLAUDE.md                    # Build rules, including the accuracy rules
+-- Architecture.md              # This file
+-- app.py                       # FastAPI entry point
|
+-- config/
|   +-- settings.py              # App constants, env config
|   +-- regions.py               # Region + feed definitions
|   +-- markets/pakistan.yaml    # Portability anchor: WACC, market hours,
|                                #   regulators, currency, audit labels
|
+-- security/                    # crypto, pqc, auth, hash-chained audit
+-- database/
|   +-- connection.py            # SQLCipher (AES-256) connection manager
|   +-- schema.py                # Tables + idempotent column migrations
|   +-- psx_data.py              # PSX company/price/financial upserts
|   |                            #   upsert COALESCEs: a thin refresh must not
|   |                            #   null out fields a richer parse extracted
|   +-- provenance.py            # record_observation + SANITY_BOUNDS + sweep
|   +-- scrape_log.py            # One row per fetch(); the measurement surface
|   +-- macro_data.py, documents.py, clients.py, watchlist.py, ...
|
+-- core/
|   +-- scheduler.py             # 16 jobs, market-hours aware, boot plan log
|   +-- data_health.py           # Per-job status for the Data Health card
|   +-- narrative_fallbacks.py   # Jinja sentence templates (no generation)
|   +-- feed_cache.py            # RSS cache
|
+-- intelligence/
|   +-- # -- Scraping contract ------------------------------------
|   +-- scraper_base.py          # ScrapeStatus, ScrapeResult, BaseScraper
|   +-- gov_scrapers/            # SBP, FBR, OGRA, NEPRA, PBS, PTA, World Bank
|   +-- exchange_scrapers/       # psx_market, psx_company, SBP forex
|   +-- exchange_crawlers/psx.py # PSX company page crawler
|   |
|   +-- # -- Statement extraction ---------------------------------
|   +-- psx_report_parser.py     # Annual-report selection BY DOCUMENT TYPE
|   |                            #   (annual_report > annual_results > interim),
|   |                            #   page-count verified, type tagged onto the row
|   +-- pdf_extractor.py         # Escalating router: pdfplumber -> OCR -> deskew
|   +-- statement_parser.py      # Accuracy-first parser (six defences)
|   +-- psx_financials.py        # Summary + report merge, with the OCR trust gate
|   |
|   +-- # -- Analysis -------------------------------------------
|   +-- psx_analyser.py          # PSXAnalysis + build_analysis: THE entry point
|   +-- psx_charts.py            # All eight chart payloads (built once, in Python)
|   +-- financial_model.py       # Ratios, Altman, DuPont, DCF, health score
|   +-- validation.py            # validate_metric: plausibility bounds
|   +-- sector_medians.py        # Sector medians + peer counts
|   +-- compliance_rules.py      # Static Pakistan regulatory rules
|   +-- news_relevance.py, news_analyser.py, announcement_classifier.py
|
+-- ml/                          # FinBERT, MiniLM, IsolationForest, ARIMA (CPU)
+-- routers/                     # auth, dashboard, psx, private, regulatory,
|                                #   newsfeed, documents, reports, settings
+-- reports/
|   +-- psx_report.py            # Twelve-section export + provenance page
|   +-- chart_render.py          # Static PNG of the SAME chart specs
|   +-- pdf_renderer.py, docx_renderer.py, signer.py (ML-DSA)
|
+-- templates/
|   +-- psx.html                 # Landing: search, sector filter, company list
|   +-- psx_analysis.html        # Header + lazily-loaded section cards
|   +-- psx/                     # _macros.html + section_01..12 partials
|   +-- partials/data_health.html
|
+-- static/js/                   # htmx, chart.umd.min, charts.js (all vendored;
|                                #   the strict CSP blocks CDN scripts)
+-- tests/                       # Unit + @pytest.mark.live scraper battery
+-- tools/                       # Corpus studies, sweeps, verification scripts
```

---

## Data flow: PSX listed company

```
Scheduled jobs (core/scheduler.py)
  psx_market            prices, index
  psx_company_profile   bulk nightly over every ticker
  psx_financials        bulk weekly, summary only (income statement)
  gov scrapers          SBP, FBR, OGRA, NEPRA, PBS, PTA, World Bank
        |
        v  every fetch() writes a scrape_log row; macro values go through
           record_observation() with source_url + snippet + method
        |
        v
Local encrypted database (SQLCipher)
        |
        v
intelligence/psx_analyser.build_analysis(ticker)   <- the ONLY entry point
  - reads local data only, NEVER scrapes on request
  - applies PKR unit scaling exactly once
  - runs every metric through validate_metric()
  - records missing_requirements per section
  - builds chart payloads via psx_charts.build_charts()
        |
        v
PSXAnalysis  -->  twelve section templates  (web, HTMX)
             -->  reports/psx_report.py     (PDF, static PNG charts)
```

Sections never query the database. If `build_analysis` does not carry it, it is
not shown.

---

## Statement extraction: how a balance sheet is actually obtained

The PSX company page publishes only a summary (sales, profit after tax, EPS). The
balance sheet lives in a filing PDF, and *which* filing is chosen decides whether
the section can render at all.

```
PSX announcements for a ticker
        |
        v  select_report_document()  - by TITLE PATTERN, not recency
   1. annual report      verified >= 40 pages (a shorter one is an extract)
   2. annual results     shorter, but audited and carries the balance sheet
   3. interim            tagged, so the UI can name the limitation
        |
        v  extract_financials()  - escalating router
   tier 1  pdfplumber text -> tables      (digital, sub-second)
   tier 2  tesseract raw                  (scanned; only if tier 1 is thin)
   tier 3  tesseract deskew               (last resort)
        |
        v  statement_parser  - six defences
   unit scale detected, total-line anchoring, explicit column choice,
   balance-sheet cross-check, number-noise repair, provenance per value
        |
        v  OCR TRUST GATE (psx_financials._report_is_reliable)
   digital  -> trusted
   OCR      -> trusted ONLY if assets = liabilities + equity within 1%
   otherwise -> DISCARDED; the field stays missing and says why
```

Measured across 20 tickers (2026-07): 7 of 20 yield an annual-grade document,
of which 5 are scanned. Sourcing is largely solved; OCR quality on scanned annual
reports is the remaining barrier, and it is deliberately gated rather than
trusted.

---

## The twelve sections

| # | Section | Primary input |
|---|---------|---------------|
| 1 | Company Profile | psx_companies profile scrape |
| 2 | Statement of Financial Position | annual report balance sheet |
| 3 | Statement of Profit or Loss | PSX summary, multi-period |
| 4 | Statement of Cash Flows | annual report cash flow statement |
| 5 | Ratio Analysis | computed; benchmarked to sector medians |
| 6 | Statement Quality | auditor's report text |
| 7 | DuPont Decomposition | net income, revenue, assets, equity |
| 8 | Distress Prediction | complete balance sheet (Z or Z'') |
| 9 | Valuation | multiples; DCF once cash flow lands |
| 10 | Regulatory Context | static compliance rules |
| 11 | Sector Position | peers + percentiles |
| 12 | Investment Considerations | deterministic rules over validated values |

Banks take different paths: Z'' rather than the five-factor Z, P/B and P/E rather
than an enterprise DCF, a banking balance-sheet presentation with no imposed
current/non-current split, and no cash conversion cycle.

---

## The eight charts

Payloads are built once in `intelligence/psx_charts.py` and rendered by
`static/js/charts.js` (Chart.js) on screen and `reports/chart_render.py`
(matplotlib, 150 DPI) in the PDF. One source of truth, so the two cannot drift.

| # | Chart | Section |
|---|-------|---------|
| 1 | Ratio trend small multiples | 5 |
| 2 | Revenue and profit history | 3 |
| 3 | DuPont components vs median | 7 |
| 4 | Sector percentile dot plot | 11 |
| 5 | Cash conversion cycle | 5 (suppressed for banks) |
| 6 | Altman Z-Score gauge | 8 |
| 7 | DCF sensitivity | 9 (non-banks, once DCF computable) |
| 8 | Peer comparison scatter | 9 / 11 |

Universal rules, enforced in the builder: units in axis labels, a title naming the
periods, a source line, gaps (never interpolation) for values that failed
validation, and nothing rendered below two valid points.

---

## Verifiability

Two mechanisms make a number defensible:

1. **Provenance on every macro observation.** `record_observation()` refuses a
   value without `source_url` and `source_snippet`. Government sites change shape
   without warning; a number with no receipt cannot be audited when they do.
2. **Document provenance on every statement figure.** `source_document_type`,
   pages, URL and date are stored on the `psx_financials` row, so a report can
   state which filing a figure came from, and a gap can name what was actually
   available instead of reporting a generic absence.

An hourly sanity sweep re-applies `SANITY_BOUNDS` plus the PSX price rule, flags
implausible rows (never deletes them), and surfaces them on the Data Health card
with a manual-check prompt.

---

## Security

- SQLCipher AES-256 at rest; no plaintext on disk.
- Argon2id password hashing, TOTP 2FA, hash-chained audit log.
- ML-KEM-768 + X25519 hybrid KEM; ML-DSA report signing.
- Per-client HKDF key isolation; client key lives in session memory only.
- Strict CSP: all front-end libraries are vendored under `static/js`.

---

## Portability

`config/markets/pakistan.yaml` is the anchor: exchange URLs, currency and unit
scale, central bank, sector regulators, WACC assumptions, market hours, audit
report labels, feed file.

Honest status: the engine-level constants (WACC, market hours) live in the yaml
with the Python copies pointing at it as the value of record. The intelligence
layer (search query strings, the sanitiser entity dictionary, sector taxonomy,
report templates) is still written directly against Pakistan. Making that
genuinely swappable is a Phase 6 change, which is where the plan wires the yaml
into a loader.
