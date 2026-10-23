# SashaOps financial statements pipeline: plan

Audience: Claude Code, with the owner reviewing each stage.
Replaces `SASHAOPS_DATA_PIPELINE_PLAN.md`, `SASHAOPS_PIPELINE_FIXES.md` and Part D of `SASHAOPS_NEXT_INSTRUCTIONS.md`.
Built from the code on `v2` at `cb89bc0`. Plain Python, standard library plus the PDF libraries the repo already has. No AI or language-model extraction. No orchestrator.

---

## 0. What this pipeline must achieve

**Purpose.** Turn the annual and interim reports that PSX-listed companies file into verified balance sheet, income statement and cash flow figures. Every figure must trace to a page of a filed document.

**Measures of success:**

| Measure | Target |
|---|---|
| Wrong figures accepted automatically (golden set and corpus) | **0**. Any wrong figure blocks a merge. |
| Verified annual balance sheets, KSE-100 non-banks, latest year | Set after the first full run. Today it is 0 of 635 tickers. |
| Every published figure has a document, page, label and parser version | 100% |
| Re-running any stage on unchanged input | Produces identical output |
| Processing a new filing | Under 2 minutes for a text PDF; no re-download of anything already held |

---

## 1. What exists today, and why it can't be patched

Read from the code on `v2`.

1. **"Scanned" means "nothing extracted".** `psx_report_parser.fetch_psx_report_financials()` runs the text extractor. If it returns no fields, it hands the PDF to background OCR. A text PDF whose labels the parser can't match is treated as a scan. That is why LUCK and HUBC, both text PDFs, were reported as scanned.
2. **The same PDF is downloaded several times.** `select_report_document()` downloads every candidate to count its pages, then `fetch_psx_report_financials()` downloads the chosen one again. The cache is keyed by ticker *and today's date* (`{ticker}_{date}.json`), so everything is fetched again the next day.
3. **The bulk refresh never reads annual reports.** `psx_financials.py` skips the report step in bulk mode, measured at about 104 seconds per ticker, about 22 hours for 757 companies. Balance sheets therefore only ever come from one-off, per-ticker requests. The 104 seconds is mostly repeated downloads and whole-document scanning, which this design removes.
4. **Partial, unverified fields are merged in.** When a report is "reliable but partial", the code:
   - infers the scale from the ratio of report revenue to summary revenue;
   - snaps it to a power of 1000;
   - merges any balance-sheet fields the summary lacks, after a plausibility check only.
   Figures can enter the analysis without the statement ever adding up. Nothing checks that the report's fiscal year matches the summary row it merges into.
5. **Selection mislabels documents.** If nothing matches the annual or interim patterns, the newest announcement of any kind is returned and tagged `interim`. Only the announcements shown on the company page are searched, about the latest 15 according to `docs/scraper_verification.md`, so older annual reports are never found.
6. **One wide row per period with no per-field origin.** `psx_financials` holds one row per symbol, period and period type, with 20 value columns, `raw_json`, and three document columns that describe the whole row. The write keeps the old value whenever the new one is empty. So a bad stored value can't be cleared by a correct run that lacks that field, and there's no record of which document or page any single value came from.
7. **Four overlapping parsers:**
   - `psx_report_parser._parse_financials` with `_LABELS`;
   - `statement_parser.parse_financials_accurate` with `_FIELDS`;
   - `statement_locator`'s own two-column parser;
   - the extractor chain in `pdf_extractor.py`.
   They know different field sets.
8. **No period end, basis or column provenance is stored.** The period is a label like `FY2024`, inferred rather than read from the statement header.

These are design problems, not bugs. The plan builds a separate, layered pipeline next to the existing code. It switches the app over once the new pipeline beats the old one on the same documents, then deletes the old code.

---

## 2. Design

### 2.1 Layers

```
 PSX announcements ──► BRONZE ──► SILVER ──► checks ──► GOLD ──► v_financials ──► analysis
                       (files)    (rows)       │                  (one view)
                                               └──► QUARANTINE ──► review queue ──► GOLD (owner-verified)
```

| Layer | Holds | Rules |
|---|---|---|
| **Bronze** | Every downloaded PDF, unchanged, named by its SHA-256 hash, plus a `filings` row | Immutable. Downloaded once. Never edited or deleted by the pipeline. |
| **Silver** | Every line item read from a statement: printed label, value, column, page, scale, period end, basis | Fully rebuildable from Bronze. Every row carries `parser_version` and `run_id`. |
| **Gold** | One verified figure per ticker, period end, period type, basis and field, with its source statement | Written only by the publish stage, only for statements that passed every check, or that the owner verified. |
| **Quarantine** | Statements that failed a check, with every check result | Feeds the review queue. Never read by the analysis. |

The app reads **only** the view `v_financials`, which presents Gold in the shape the analysis already expects. That is one read path and no compatibility code.

### 2.2 Code layout

A new package, one file per stage, each with a small public function and no cross-imports except `contract.py` and `store.py`.

```
pipeline/
  contract.py     field definitions, periods, bases, units (section 3)
  store.py        all SQL for pipeline tables; no SQL anywhere else
  discover.py     S1  announcements -> candidate documents
  fetch.py        S2  download, hash, write Bronze
  classify.py     S3  document type, text layer, statement presence
  locate.py       S4  statement pages, basis, period ends
  extract.py      S5  words with coordinates -> columns -> raw line items
  mapping.py      S6  printed label -> contract field (label table lives here)
  checks.py       S7  validation rules -> verified or quarantined
  publish.py      S8  Gold write, supersede, invalidate analysis cache
  runs.py         run records, metrics, diff between runs
  __main__.py     command line (section 8)
tests/pipeline/   unit tests and fixtures
tools/eval_corpus.py   scores the pipeline against the corpus and golden set
```

### 2.3 Principles each stage follows

1. **One job per stage, with a stated input and output.** A stage reads only the layer before it.
2. **Repeatable.** Every write is keyed by content (document hash, page, column, line), so re-running replaces rows instead of duplicating them.
3. **Incremental.** A stage processes only inputs that are new, or whose upstream changed, or whose parser version changed.
4. **Fail loudly, per item.** One bad document never stops the run. It's recorded with a reason, and the rest continue.
5. **Nothing is filled in.** No guessed scale, period or basis. If a stage can't decide, the item goes to quarantine with the question it couldn't answer.
6. **Exact numbers.** Values are stored as whole rupees in SQLite integers, which comfortably hold the largest Pakistani balance sheets. Converting printed thousands or millions is integer multiplication. Ratios are computed as floats only in the analysis.

---

## 3. Data contract (`pipeline/contract.py`)

One Python module is the single definition of every field. The parser, the checks, the view, the entry form and the docs all import it. Each entry records:
- the field key;
- the statement it belongs to;
- a plain definition;
- the printed labels it maps from (they live in `mapping.py`, keyed by field);
- its sign convention;
- whether it applies to non-banks, banks or both;
- whether it is a printed total or a defined sum.

### 3.1 Fields for the first wave (non-banks)

| Field | Statement | Definition | Notes |
|---|---|---|---|
| revenue | P&L | Revenue from contracts with customers, net of sales tax and discounts | Never the gross line before sales tax |
| cost_of_sales | P&L | Cost of sales | Stored positive; used for gross profit |
| gross_profit | P&L | Printed gross profit | Check: revenue minus cost of sales |
| operating_profit | P&L | Printed operating profit, or profit before finance cost | Called `ebit` in the app |
| finance_cost | P&L | Finance cost | Called `interest_expense` in the app |
| profit_before_tax | P&L | Printed | |
| taxation | P&L | Printed total tax charge | |
| profit_after_tax | P&L | Profit for the year | Called `net_income` in the app. Check: PBT minus tax |
| eps_basic | P&L | Basic EPS in rupees | Not scaled |
| total_non_current_assets, total_current_assets, total_assets | BS | Printed totals | Check: the two parts sum to total assets |
| total_equity | BS | Printed total equity | Including non-controlling interest on the consolidated basis; record the NCI line separately |
| retained_earnings | BS | Unappropriated or accumulated profit | Needed for Altman Z'' |
| total_non_current_liabilities, total_current_liabilities | BS | Printed totals | |
| total_liabilities | BS | Printed total, or a **defined sum** of the two totals when not printed | Record which |
| cash_and_equivalents | BS | Cash and bank balances | |
| inventory, trade_receivables, trade_payables | BS | Stock-in-trade, trade debts, trade and other payables | Label variants listed in the mapping table |
| borrowings_long_term, borrowings_current_portion, borrowings_short_term, lease_liabilities | BS | Printed lines | Components only |
| total_debt | derived | Sum of the four borrowing components | Empty if any component is missing. Never a partial sum. |
| cfo | CF | Net cash generated from (used in) operating activities | Called `ocf` in the app |
| capex | CF | Purchase of property, plant and equipment plus additions to capital work in progress where printed separately | Stored positive. Record the lines summed. |
| depreciation | CF | Depreciation in the operating cash flow adjustments | |
| cfi, cff, net_change_in_cash, cash_opening, cash_closing | CF | Printed | Used by the cash flow checks |

Banks get their own field list in the second wave (advances, deposits, investments, and so on). Their balance sheets don't split current and non-current.

### 3.2 Identity of a figure

`(ticker, period_end, months, basis, field)`
- `period_end`: an ISO date read from the statement header ("As at June 30, 2026"; "for the year ended December 31, 2025"). Never inferred from the filing date.
- `months`: 12 for annual reports. 3, 6 or 9 for interims, read from the header.
- `basis`: `unconsolidated` or `consolidated`, from the statement title.

The view maps `months = 12` to the existing `FY<year of period end>` label and `period_type = annual`. Interims never feed annual ratios, the DCF or trend charts.

### 3.3 Units and signs
- Every value is stored as whole rupees, together with the scale printed on the statement ("Rupees", "Rupees in '000", "Rupees in million").
- Brackets mean negative. A dash means zero, flagged as `dash`.
- Costs, capex and dividends paid are stored as positive amounts with a sign rule in the contract, so the checks know how to add them.

---

## 4. Storage

New tables, all created in `database/schema.py`, all accessed only through `pipeline/store.py`. They sit in the existing SQLCipher database. Bronze files sit in a data folder on the app's Docker volume.

| Table | Key | Columns (main) |
|---|---|---|
| `filings` | `doc_sha256` | ticker, source URL, announcement title, announcement date, file path, bytes, pages, fetched_at, `doc_class`, `text_pages` (pages with a text layer), classifier version |
| `doc_statements` | `(doc_sha256, statement_type, basis)` | pdf_page_start, pdf_page_end, printed_page, located_by (`contents_page` or `classifier`), period ends of the columns found, scale, locator version |
| `line_items` (Silver) | `(doc_sha256, statement_type, basis, pdf_page, line_no, column)` | label_raw, label_norm, field (empty if unmapped), value_rupees, printed_text, scale, period_end, months, x0, x1, top, parser_version, run_id |
| `statements` | `statement_id` = hash of `(doc_sha256, statement_type, basis, period_end)` | ticker, period_end, months, basis, statement_type, status (`verified`, `quarantined`, `superseded`, `owner_verified`), checks (JSON list of `{check, passed, detail}`), parser_version, run_id |
| `facts` (Gold) | `(ticker, period_end, months, basis, field, statement_id)` | value_rupees, source (`pdf`, `owner`, `psx_summary`), is_current (true for the figure the view shows), restated (true when a later report changed it), published_at |
| `runs` | `run_id` | started, finished, git commit, parser version, stages run, counts per stage and outcome, duration |

**Rules:**
- **Restatement.** When a newer audited report restates a prior year, the newer figure becomes current and the older row stays with `is_current` false and `restated` true. History is never deleted.
- **Owner figures win.** A figure the owner saves through the entry form is written to `facts` with source `owner` and becomes current. The existing `financial_overrides` table is migrated into `facts` once, then dropped.
- **The PSX summary is stored as facts** with source `psx_summary`, for revenue, profit after tax and EPS. It serves both as a fallback for those fields and as the cross-check in S7.
- **Retirement of the old table.** Once the app reads only `v_financials`, the PDF-to-`psx_financials` write path and its merge logic are deleted. `psx_financials` stays only as the summary scraper's landing table, until it too is folded into `facts`.

**Why `filings`.** The database already has a `documents` table for client uploads (id, client_id, filename, content_path). The pipeline's table is therefore named `filings`, so the two never mix.

---

## 5. Stages

Each stage lists its input, output, behaviour, failure outcomes and tests.

### S1 Discover
- **In:** a ticker list: the KSE-100 for the first wave, with its source and date recorded. **Out:** candidate document rows with URL, title and announcement date.
- Read the company's PSX announcements. Pick out titles that are annual or interim report transmissions, with the patterns in one list and tests on real titles.
- The company page shows only recent announcements. Check whether PSX's announcement search (`dps.psx.com.pk/announcements`) can list older ones for a symbol. If it can, use it to reach the previous year's annual report, which S7's prior-year check needs. If it can't, record that as a known limit.
- **Outcomes:** `found`, `no_report_announcements`, `fetch_failed: reason`.

### S2 Fetch
- **In:** candidate URLs. **Out:** Bronze files and `filings` rows.
- One request at a time, a 2 to 3 second delay, a timeout, and three retries with growing waits. Keep a record of URLs already fetched, so a URL is never requested twice.
- The file is saved as `bronze/<first 2 hash chars>/<sha256>.pdf`. If that hash already exists, only the new URL is recorded.
- **Outcomes:** `stored`, `duplicate`, `http_<code>`, `not_pdf`, `too_large` (above a set limit).

### S3 Classify
- **In:** a document. **Out:** `doc_class` and `text_pages`.
- Count the pages that have a text layer with pdfplumber. Detect statement headings on text pages.
- **Classes:**
  - `annual_report`: has statement headings for the year and a full set of statements;
  - `interim_report`;
  - `results_sheet`: short, P&L only;
  - `cover_letter`;
  - `scanned`: statement pages have no text layer;
  - `unknown`.
- **"Scanned" is decided by the text layer only, never by whether fields were found.** A title or a page count alone never decides the class.

### S4 Locate
- **In:** a text-layer document. **Out:** `doc_statements` rows.
- **Contents page first.** Find it in the first 15 pages. Read the printed page numbers for each statement, for both the unconsolidated and consolidated sets. Work out the printed-to-PDF offset by matching printed footers on 3 pages, and accept it only when all 3 agree.
- **Fallback:** the existing page classifier logic from `statement_locator.classify_page()`, which tells statements from multi-year summaries. Move it here rather than keeping two copies.
- **Read the column headers.** Each statement's header gives the period end and months of each column ("2026", "2025", "June 30, 2026"), plus the scale. If either can't be read, quarantine with the reason `period_unreadable` or `scale_unreadable`.

### S5 Extract
- **In:** the located pages. **Out:** raw line items (label, values per column, positions).
- **Text normalisation:** apply Unicode NFKC so ligature characters become plain letters, then collapse whitespace.
  - First test on LUCK and HUBC whether pdfplumber returns the ligatures or drops them entirely. The PDF reader used on Oct 22 dropped them ("Ocer" for "Officer").
  - If they're dropped, fix it at extraction, by reading characters with their font information or reading the PDF's text map for those glyphs. Never by loose regexes.
- **Lines:** group words by their vertical position.
- **Columns:** find the numeric column boundaries from the x-positions of the header row's period labels and the "Note" column. Assign each number to the nearest column by its right edge. Never join numbers across a column boundary, which was the defect found in commit `c7ffe44`.
- **Numbers:** parse commas, brackets, dashes and decimal points. Keep the printed text. Apply the scale as integer multiplication.
- **Every line is kept, not only the ones the contract needs.** Each line also records its structure:
  - its indent level (from x-position);
  - whether it is a heading, an item, a subtotal or a total (subtotal and total lines are usually bold or ruled; record font weight and any ruling line above or below);
  - its note reference.
  S7 needs the full statement to check that every line adds up.
- **Labels split over two lines** are joined to the line that carries the numbers. The join is recorded.

### S6 Map
- **In:** raw line items. **Out:** `line_items`, with the field set where a label maps.
- The label table in `mapping.py`, keyed by contract field, matches the whole normalised label (anchored), with section context: "Total current assets" only maps inside the assets section.
- Sub-item guards from the existing `_SUBITEM_GUARD`. Sign guards for fields that can't be negative.
- **Unmapped labels are kept**, with their values. The `unmapped` report lists the most frequent ones, so the label table grows from evidence.
- Defined sums (`total_debt`, `capex`, `total_liabilities` when not printed) are computed here, and record which lines were used.
- **One field, one line.** A contract field must match exactly one line in its section. Two candidates, for example two "Trade debts" lines, one of them under non-current assets, give `ambiguous_label` and quarantine the statement. No candidate gives a missing field, never a guess.

### S7 Check
- **In:** one statement's line items. **Out:** `statements.status` and the check results.

| Check | Rule | Fails when |
|---|---|---|
| `bs_balances` | total assets equals total equity plus total liabilities | difference greater than the tolerance (below) |
| `bs_subtotals` | non-current plus current equals the total, for assets and for liabilities | as above |
| `pl_chain` | gross profit equals revenue minus cost of sales; profit after tax equals profit before tax minus taxation | as above, where printed |
| `cf_sums` | CFO plus CFI plus CFF equals the net change; opening cash plus the net change (plus any exchange difference line) equals closing cash | as above |
| `matches_psx` | revenue and profit after tax match the PSX summary facts for the same year, within 0.5%, after units | mismatch, or the PSX summary is missing (status: needs review, not a failure) |
| `matches_prior_report` | this report's prior-year column matches the previous report's current column | mismatch not labelled "restated" in the document |
| `period_consistent` | all statements of one document share the same period ends | different |
| `scale_consistent` | one scale across the statement set | different |
| `signs` | fields that can't be negative are not negative | violation |
| `footing` | **Within every section, the item lines sum to the printed subtotal, and the subtotals sum to the printed total, in every column.** This proves each individual number was read correctly, in the right column, with the right sign. | any section off by more than the tolerance; the failing section is named |
| `ties_cash` | closing cash in the cash flow statement equals cash and bank balances in the balance sheet, after any running finance the cash flow statement's note says it includes | difference not explained by a printed reconciling line |
| `ties_profit` | profit after tax in the P&L equals the profit line in the statement of changes in equity and the starting line of the cash flow statement (profit before tax there, where printed) | different |
| `ties_equity` | retained earnings and total equity in the balance sheet equal the closing balances in the statement of changes in equity | different |
| `ties_depreciation` | depreciation in the cash flow adjustments equals the depreciation charge in the property, plant and equipment note, where the note is located | different (a warning only until notes are located reliably) |
| `notes_tie` | for inventory, trade receivables, trade payables, borrowings and capex, the note the statement points to has a total equal to the statement figure | different (a warning only in the first wave) |

- **Tolerance:** a printed rounding allowance of half a unit of the printed scale for each line added, with a minimum of 0.01% of total assets. A printed total that differs by less than that passes.
- **Statement outcome:**
  - `verified` if every applicable check passes;
  - `quarantined` if any fails, with every result kept;
  - `needs_review` if checks pass but `matches_psx` couldn't run.
- **What the checks prove, and what they don't:**
  - `footing` proves every number on the statement was **read** correctly;
  - `ambiguous_label`, the section context and the `ties_*` checks prove the right **line** was chosen for each field;
  - `matches_psx` and `matches_prior_report` prove the **period, scale and basis**.
  A figure is verified only when all three hold for it.
- **Field-level status.** Besides the statement status, each published fact records how it was proven:
  - `footed`: inside a section that footed;
  - `tied`: also matched a second statement or note;
  - `cross_checked`: also matched PSX or the prior report.
  The analysis shows this on hover. The golden-set report measures accuracy for each level.
- **Basis choice:** `matches_psx` picks the basis that the PSX summary figures agree with. Both bases are kept in Silver, and the matched one becomes current in Gold.

### S8 Publish
- Write `facts` for verified statements, and mark any earlier figure as restated or superseded.
- Clear the analysis cache for the affected tickers (the `_invalidate()` added in C3).
- Record what changed: for each ticker, the fields that became current and their old and new values.

### S9 Report
- At the end of every run, print and store:
  - counts per stage and outcome;
  - verified statements, total and newly verified;
  - quarantine reasons ranked by count;
  - the most common unmapped labels;
  - duration per stage.
- `docs/pipeline/runs/<date>_<run_id>.md` keeps the history.
- The dashboard gets one "Data pipeline" card: the last run, verified coverage, a coverage-over-time line, and a link to the review queue.

---

## 6. Review queue

Replaces "fallback to the summary" and every silent merge.

- **The list:** one row per quarantined or `needs_review` statement, with ticker, period, statement and the first failing check in plain words, for example "Balance sheet does not add up: assets 812,330,114 vs equity plus liabilities 812,317,714 (difference PKR 12,400, tolerance 900)".
- **Opening a row** shows the PDF page next to the shared entry form, pre-filled from Silver, with each value tagged "parsed p. 231". The owner corrects the figures and saves.
- **On save:** the checks re-run on her figures. If they pass, the statement becomes `owner_verified` and publishes to Gold. If they don't, the form shows which check failed and still allows saving, recorded as `owner_verified_with_exceptions` with the reason.

---

## 7. Lineage, versions and reprocessing

- `PARSER_VERSION` lives in `contract.py` as a plain string, raised whenever S4, S5 or S6 behaviour changes. Every Silver row and every statement records it.
- Each run records the git commit.
- `python -m pipeline reprocess --from extract` rebuilds Silver and re-checks everything from Bronze, with no downloads. It then writes a **diff report**:
  - statements whose status changed;
  - figures whose value changed;
  - new verifications;
  - new quarantines.
- **A run that turns any `verified` figure into a different value fails the release**, unless the diff is reviewed and explained in the commit message.
- Every figure in the analysis can show its lineage on hover or in the provenance list: document link, PDF page, printed label, check results, parser version.

---

## 8. Commands

```
python -m pipeline run --universe kse100 [--tickers LUCK,HUBC] [--stages discover..publish]
python -m pipeline reprocess --from extract|map|check
python -m pipeline status            # last run summary and coverage
python -m pipeline unmapped --top 50 # most frequent unmapped labels
python -m pipeline explain LUCK FY2026 total_assets   # lineage of one figure
python -m tools.eval_corpus          # score against the corpus and golden set
```

- **Schedule.** The existing scheduler runs `pipeline run` nightly at 02:00 PKT, and only new announcements are processed.
- **The "Refresh all PSX companies" button** starts the same command in the background. Only one run happens at a time, enforced by a lock row in `runs`.

---

## 9. Tests

1. **Unit tests per stage**, with small fixtures saved from real pages: a few lines of extracted words or text, not whole PDFs. Each fixture records its source document hash and page. They cover:
   - number formats;
   - ligature normalisation;
   - column assignment with a Note column;
   - two-column and four-column statements;
   - section context for "Total current assets";
   - each defined sum;
   - each check, both passing and failing.
2. **Corpus regression.** About 40 documents chosen from the first run to cover the hard cases:
   - consolidated and unconsolidated;
   - each scale;
   - restatements;
   - multi-year summary pages;
   - December and June year ends;
   - long reports.
   Expected values live in `tests/fixtures/pipeline/expected.yaml`. The owner spot-checks 1 in 10 against the PDF. PDFs stay out of git; the test reads them from Bronze by hash and is skipped, with a message, if Bronze is missing.
3. **Golden set.** The owner's hand-read figures for 8 companies (section 12). One wrong figure fails the build.
4. **Accuracy is measured per field, not per statement.** `tools/eval_corpus.py` reports, for each contract field over the golden set and corpus:
   - **correct**: published and equal to the expected value;
   - **wrong**: published and different; the target is 0, always;
   - **withheld**: not published because a check failed or the label was ambiguous;
   - **missing**: never found.
   Wrong and withheld are kept apart on purpose. Withholding is the system working. A wrong figure is the system failing.
5. **Deliberately broken fixtures.** Each must be caught by a named check:
   - a prior-year column swapped with the current one;
   - a value read from the Note column;
   - a bracketed negative read as positive;
   - a statement in millions read as thousands;
   - a sub-item ("Long term deposits") mapped as a total;
   - a two-line label split from its numbers;
   - a consolidated figure placed on the unconsolidated basis.
6. **Repeatability test.** Run the pipeline twice on the same Bronze: no new rows, identical Gold.
7. **`scripts/check.sh`** runs 1, 5, 6, ruff and vulture. The corpus and golden tests run before every merge to `main`.

---

## 10. Build order

Each milestone runs on a branch from `v2`, is merged when its acceptance passes, and is pushed. Stop points are marked; at each, the owner reviews the output, and so can I.

| # | Milestone | Acceptance |
|---|---|---|
| M0 | CI workflow, hashed requirements, branch protection on `main`, backup and restore, `/health`, one log format, `RUNBOOK.md` skeleton | A failing test blocks a pull request. A restore test passes in CI. `/health` shows every item in 11A.5. |
| M1 | `contract.py`, `store.py`, the new tables, `runs`, the command skeleton | Tables are created on startup. `pipeline status` works on an empty store. Schema test passes. |
| M2 | S1 Discover and S2 Fetch, for the golden 8 plus 12 more (the same 20 as Oct 22, ENGRO replaced) | Every PDF is stored once by hash. A second run fetches nothing. The fetch log is complete. |
| M3 | S3 Classify | Each of the 20 has a class. LUCK and HUBC are not `scanned`. **Stop point: owner reviews the class table.** |
| M4 | S4 Locate | Statement pages, basis, period ends and scale found for every text-layer annual report, or a stated reason. |
| M5 | S5 Extract and S6 Map | Silver rows for each located statement. The `unmapped` report exists. The ligature question has been answered with evidence. |
| M6 | S7 Check | The status table for the 20, and the per-field accuracy table from `eval_corpus` with 0 wrong. Every deliberately broken fixture is caught. **Stop point: owner reviews the verified count, the quarantine reasons and the per-field table.** |
| M7 | S8 Publish, the `v_financials` view, the app switched to read it, the entry form writing owner facts | The analysis for a verified ticker computes DuPont and Altman Z from Gold. The golden test passes. The old merge path is deleted. |
| M8 | The review queue | A quarantined statement can be corrected and published by the owner. |
| M9 | Scale to the KSE-100 nightly. Corpus chosen. Dashboard card. | Full run report. **Stop point: owner reviews coverage.** |
| M10 | Delete the old pipeline (list below). Source contract tests, nightly golden re-check, freshness limits, the display helper (11A.3 and 11A.4). Final documentation and runbook. | ruff, vulture and the tests are clean. Net lines reported. Every number in the templates goes through the display helper (a test greps the templates). |
| R1 | First release `v1.0.0` | Signed `release_check.json` with 0 wrong. CI builds and checks the image on the tag. The owner upgrades and rolls back once by following the runbook alone. |
| M11 | Banks: their own fields, checks and corpus | Same acceptance as M6, for banks. |
| M12 | Scanned PDFs, only if M9 shows they matter: OCR as a separate command, run as its own process with a memory and time limit, feeding the same S4 to S7 | No change in the wrong-figure count. Coverage gain reported. |

### Deleted in M7 and M10 (net lines reported)
- `psx_report_parser.py`:
  - `_parse_financials` and `_LABELS`;
  - `select_report_document`;
  - `fetch_psx_report_financials`;
  - `_schedule_background_ocr` and the thread state;
  - the date-keyed JSON cache;
  - `_download`, `_download_by_url`, `_pdf_text` and `_ocr_pdf`.
- `pdf_extractor.py`: the whole chain, replaced by S3 to S6.
- `statement_parser.py`: its useful parts (scale detection, anchors, sub-item guard) move into `extract.py` and `mapping.py`, then the file is deleted.
- `statement_locator.py`: the page classifier moves to `locate.py`, the OCR parts move to M12 or are deleted, then the file is deleted.
- `annual_report_finder.py`: web search is no longer used.
- In `psx_financials.py`: the report step, the "reliable but partial" merge, the revenue-ratio scale snapping and `_SkipReport`.
- The `financial_overrides` table, after its rows are migrated into `facts`.

---

## 11. Rules for Claude Code

1. The hygiene rules in Part A of `SASHAOPS_NEXT_INSTRUCTIONS.md` apply in full: delete before you add, no dead code, net lines in every commit message, ruff and vulture clean.
2. **Dependencies:** pinned with hashes. Python standard library plus `pdfplumber`, which the repo already has. `pytesseract` and `pdf2image` only in M12. No new packages without the owner's approval.
3. **All SQL goes in `store.py`.** No stage writes to the database any other way.
4. **No figure enters Gold except through S7 or the owner's review.** No fallbacks, no merges, no inferred scales.
5. **Every milestone ends with a short report to the owner:**
   - what changed;
   - the numbers from `pipeline status`;
   - net lines;
   - tests added;
   - open questions.
6. **Do not start a milestone after a stop point** until the owner says so.

---

## 11A. Production standard: the app runs and protects itself

The app must stay correct without anyone, Claude Code included, watching or fixing it. Correctness is enforced by the code, the tests and the release process, not by later patches. Every failure the app can meet must be detected by the app and shown to the owner in plain words.

### 11A.1 Code cannot reach `main` unless it passes (CI)
- `.github/workflows/ci.yml` runs on every push and pull request:
  1. install the pinned requirements, with hashes (`pip install --require-hashes`);
  2. generate throwaway secrets;
  3. run `scripts/check.sh`: unit tests, repeatability test, the deliberately broken fixtures, ruff, vulture, the template check, and the schema migration test on a copy of an old-schema database.
- Branch protection on `main`: CI must pass, there are no direct pushes, and history can't be rewritten.
- The corpus and golden tests need the stored PDFs, which aren't in git. `tools/eval_corpus.py --release` runs them locally and writes a signed result file (`docs/pipeline/release_check.json`: commit, date, per-field counts). CI fails a release tag if that file is missing, is for a different commit, or shows any wrong figure.

### 11A.2 Releases (CD, proportionate to a local app)
- A release is a git tag, `vX.Y.Z`. On a tag, CI builds the Docker image, runs `check.sh` inside it, and creates a GitHub release with notes generated from the commit messages. No image is published to a public registry.
- **Upgrade:** pull the tag, then `docker compose up --build`. Startup runs the migrations, which are tested in CI and copy rather than drop data.
- **Rollback:** check out the previous tag and restore the backup taken automatically before migration (11A.5).

### 11A.3 The pipeline checks the outside world, not only itself
- **Source contract tests at run time.** Before the pipeline uses any PSX page or announcement list, it checks the expected structure: table headings, column count, date formats. A changed page gives `source_changed: <what differed>` and nothing from it is stored. Today a layout change can put the wrong table in the right slot silently.
- **A nightly golden re-check.** After each nightly run, the stored golden-set figures are compared with Gold. Any difference means a published figure has changed. That run is marked failed, its new figures are held in quarantine, and the dashboard says so.
- **Freshness limits per source,** for example: PSX prices 1 trading day, PSX summary 7 days, annual-report discovery 7 days, macro sources at their own publication rhythm. Past the limit, the figure shows its age in amber. Twice past it, the dashboard shows the source as stale.

### 11A.4 Every figure the app shows is accurate to its source
- **No number is shown without:**
  - its unit and scale ("PKR bn");
  - its period ("FY2026, year to 30 June 2026");
  - its basis;
  - its source ("annual report p. 231", "PSX summary", "entered by owner").
  One display helper formats every number, and templates never format numbers themselves.
- **Rounding is for display only.** Calculations use the stored whole rupees. The same figure is rounded the same way everywhere, on screen and in the PDF and Word reports.
- **Ratios record their inputs.** Hovering a ratio shows its formula and the two figures it used, with their sources. A ratio built from figures of different periods or bases is not computed.
- **Withheld beats wrong.** Anything not verified shows as "Not available" with the exact reason and a link, never as a zero, a dash or the previous period's value.
- **Exported reports carry the same provenance** and the release version that produced them. They are signed, as already built.

### 11A.5 Operations
- **Backups.** An encrypted database backup is taken daily and before every migration, and the last 14 are kept. A restore test runs in CI on a fixture database. Settings shows the last backup time.
- **Health page (`/health`, for the owner after login):**
  - database open;
  - signing key present;
  - audit chain verified;
  - last pipeline run and its result;
  - stale sources;
  - review queue size;
  - disk use of Bronze.
- **One log format,** JSON with a run ID or request ID. An internal error page shows its request ID, and the log line for that ID holds the full traceback.
- **`docs/RUNBOOK.md`,** written for the owner. Each common failure gets what it looks like, what it means and what to do:
  - a source changed;
  - a pipeline run failed;
  - the golden re-check failed;
  - a backup failed;
  - a migration failed;
  - a release needs rolling back.
  No step needs an AI agent.

### 11A.6 Independence from Claude Code
- The repo must build, test, run, release and recover from its own files, the README and the runbook.
- Plans written for Claude Code live in `docs/plans/` as history. Nothing in the app reads them, and the README does not depend on them.
- Any rule an agent was told to follow ("no silent fallback", "no dead code", "no wrong figure") must also exist as a check in CI, a test, or a runtime check. A rule that exists only in a plan is not a rule.

---

## 12. The owner's part

1. **Golden set.** 8 tickers, two latest years, from the annual report, with page number and basis for each figure.
   - **Weight it towards the figures the checks can't fully prove**: inventory, trade receivables, trade payables, retained earnings, long-term and short-term borrowings, lease liabilities, capex, depreciation, CFO, finance cost.
   - Totals such as total assets prove themselves through `footing`, so 2 or 3 totals per company are enough.
   - About 15 figures per company, roughly 240 in all. This is the only independent proof that the right line was chosen.
2. **Decisions:**
   - **Bronze storage:** plain files on the Docker volume (recommended; they are public filings) or encrypted.
   - **Default basis:** the one that matches the PSX summary (recommended).
   - **The KSE-100 list:** its source and date. Claude Code finds the PSX source and the owner confirms it.
3. **At each stop point,** read the report. Open 3 verified statements and compare them with the PDF.
4. **The review queue,** once M8 exists.

---

## 13. Portfolio material produced along the way

All of it is real output from the pipeline, not written separately.

- **`pipeline/README.md`:** the problem, the layer diagram, the stages, the commands and the results table.
- **`docs/pipeline/contract.md`:** generated from `contract.py`, so it never goes out of date.
- **`docs/pipeline/decisions/`:** one short page per decision: context, options, choice, consequence.
  1. Deterministic extraction instead of AI, because figures must trace to a page.
  2. A layered store with immutable raw files.
  3. Whole rupees as integers.
  4. The contents page before page classification.
  5. "Scanned" decided by the text layer.
  6. Quarantine instead of partial merges.
  7. Restatements kept as history.
- **`docs/pipeline/runs/`:** the run reports, including the first one, as the "before".
- **`docs/pipeline/RESULTS.md`, written at M10:**
  - coverage before and after, for example "0 of 635 tickers with a verified balance sheet, to N of the KSE-100";
  - wrong figures on the golden set (target 0);
  - the defects found and how each was proven (labels treated as scans, ligatures, glued columns, scale, mislabelled documents);
  - runtime per document.

