# Step 1 check — status against code

Generated 2026-10-17. Each item verified by reading the code, not by running the app.

---

## 1a. Coverage vs "done" claim

The analytical engine depends on balance sheet data for: Altman Z-Score, DuPont,
ROE, ROA, leverage, liquidity, and most of the health score. The PSX scraper
extracts an income summary from `psx_financials` but does **not** extract a balance
sheet for most tickers. Therefore these sections compute **only for tickers that
have balance sheet data entered via the Data Entry tab**.

The claim "accurate and complete" in earlier status reports was wrong for any ticker
without balance sheet data. A formal coverage report (Step 1a output — `docs/coverage.md`)
requires querying the live database per ticker. That query and CSV have not been
produced yet: **Step 1a is open**.

---

## 1b. Passing tests vs accuracy

61 tests pass. They verify that the code runs as written (correct calculation paths,
plausibility validation, chart spec generation). They do **not** compare app figures
against published annual report figures. **Step 2 (golden set) is required before
any accuracy claim can stand.**

---

## 1c. Oct 13 item status

### 1. Expanded charts showing the wrong chart; "Show data" doing nothing

**FIXED.** Four chart bugs corrected in session 2026-10-17:
- chart5: wrong metric key `days_sales_outstanding` → `days_receivables`
- chart3 DuPont: type `stacked_bar` → `grouped_bar` (values were stacking, not comparing)
- chart7: dead code, never reached — removed from `build_charts()`
- chart8 peer scatter: subject company always absent — fallback to raw income statement added

"Show data" works for all chart types now in use (`grouped_bar`, `stacked_bar`,
`scatter`, `small_multiples`, `dot_plot`). The `gauge` type (Altman Z-Score) shows
"No tabular data available" — acceptable for a single-value display.

No dedicated end-to-end test. Verified by code inspection.

### 2. Entered figures not saved into the analysis

**OPEN.** The PSX entry tab (`/psx/{ticker}/tab/entry`) runs `analyse-entry` which
merges form values over scanner data and computes ratios + DCF transiently. Values
are **not persisted** to the database. After navigating away, entered balance sheet
and cash flow figures are lost. The next visit pre-fills from scanner data again.

Fix 2 of this plan addresses this: make the entry form save to `psx_financials`
(or a separate `manual_entries` table), and feed the analyser from saved values.
That fix has not been implemented.

### 3. Tab underline not moving; slow tab switches

**OPEN.** The active tab `class="tab-link active"` is set server-side on initial
render. When HTMX loads a new panel into `#panel`, the `<nav class="tab-bar">` is
not replaced, so the active highlight stays on the first tab. A JS listener on
`htmx:afterSettle` that updates `.active` based on `window.location.search` would
fix this. Not implemented.

Slow tab switches: each tab calls `build_analysis()` which takes 1–3 s. This is
expected with the current architecture (full analysis rebuild per tab). A cache
keyed on ticker + scrape timestamp would help but is outside this freeze.

### 4. Tall charts on tabs other than Overview

**STATUS UNKNOWN.** Chart frames on the Overview tab use explicit heights; the
behaviour on other tabs depends on the containing `.chart-frame` CSS height in
the specific template. Cannot confirm without running the app. Need live test.

### 5. Private Analyser internal error

**STATUS UNKNOWN.** The `POST /private/analyse` handler has no try/except wrapper.
If `compute_all()` or `compute_dcf()` raises (e.g. from an unexpected None in
arithmetic), the user gets an unhandled 500 error. Cannot confirm the specific
trigger without running the app. The fix is to wrap the handler body in try/except
and return a structured error response. Not implemented yet.

### 6. Newsfeed split by source instead of category

**OPEN.** `newsfeed.html` shows source filter buttons and lists items sorted
chronologically. `classify_headlines()` classifies each item as THREAT, OPPORTUNITY
or none, and badges are displayed on each item. But there is no category filter tab
and no grouping by classification. Fix: add filter buttons for THREAT / OPPORTUNITY
/ All; sort THREAT items first within each group.

### 7. Dashboard reports

| Report job | Status |
|---|---|
| PSX prices intraday | Documented in `docs/known_issues.md` — gated to 09:30–15:30 PKT |
| PSX prices closing | No known open issue |
| FBR revenue | Documented in `docs/known_issues.md` — irregular publication schedule |
| NEPRA | Documented in `docs/known_issues.md` — same |
| OGRA "scrape() returned no data" | **Not documented.** Not in `known_issues.md`; needs investigation |
| PSX financials bulk "tuple index out of range" | Documented in `docs/known_issues.md` — single-period tickers |

OGRA issue: confirm whether the scraper URL or CSS selectors are still valid.
If confirmed broken, document in `known_issues.md` with the evidence.

### 8. Settings report count showing 0 without a client selected

**STATUS UNKNOWN / POSSIBLY FIXED.** The settings page (`settings.html`) does not
show a report count at all. The `client_peek` popup on the dashboard shows a report
count per client from `SELECT count(*) FROM reports WHERE client_id = ?`. If the
original issue was about an incorrect "0" in a context that no longer exists, it may
be implicitly resolved. Cannot confirm without running the app.

### 9. "New client" missing

**FIXED.** "+ New client" / "+ Add client" button is present in:
- `templates/dashboard.html` (line 14)
- `templates/private.html` (line 14)
- `templates/settings.html` (line 54)

All three call `POST /settings/clients` to create the client.

---

## Blockers before Step 2 and Step 3 can proceed

### B1. Git not installed on host machine

`git` is not found anywhere in the system PATH. Step 0 (safe baseline commit,
push and tag) cannot run. **All code changes since `acf52e3` exist only on this
laptop with no backup.**

Git is available inside the Docker container (installed in Dockerfile via apt-get).
As a workaround, `docker compose exec sashaops git -C /app ...` can commit if the
container is running. But pushing to GitHub requires credentials configured in the
container — not straightforward.

**Recommended action:** Install Git for Windows (https://git-scm.com/download/win)
then run `git init`, `git remote add origin <url>`, `git add -A`, `git commit`, `git push`.

### B2. Docker engine unhealthy

The Docker Linux engine crashed mid-session (after a test run reached 53%).
`docker ps` returns 500. Tests cannot run.

**Recommended action:** Restart Docker Desktop (right-click tray icon → Restart).
Once the engine is healthy, restart the sashaops container and run the test suite.

### B3. Owner decisions needed

1. **Golden set (Step 2):** Select 8 tickers covering bank, cement, fertiliser,
   oil-and-gas, textile, and one company with an unusual PSX page layout. Read
   ~10 figures per company per year from the published annual report.

2. **chart7 (Fix 5):** The plan's default (delete function, shade DCF table with
   CSS) has been implemented. If a full heatmap chart is wanted instead, that
   would require a Chart.js plugin (new dependency, out of scope for this freeze).
   Confirm or reverse.

---

## Fixes done in this session (2026-10-17)

| Fix | Files changed | Tests |
|---|---|---|
| Fix 3: Statement Quality removed from Ratios tab | `tab_ratios.html`, `routers/psx.py`, deleted `section_06_quality.html` | No test existed; template delete confirmed |
| Fix 5: chart7_dcf_sensitivity deleted; DCF table shaded | `psx_charts.py`, `tests/test_psx_charts.py`, `_dcf_result.html` | 1 test deleted (for deleted function) |

Container restart required to pick up all Python changes from this session.
