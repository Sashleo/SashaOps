# SashaOps: next instructions for Claude Code (Oct 22)

Put this file in `docs/plans/` and follow it in order. It replaces the agent's own suggested next steps.

## Part A. Code hygiene rules (apply to every change from now on)

The owner wants a small codebase with no future liabilities. Every change obeys these rules. A change that breaks one is not done.

1. **Delete before you add.** Before writing new code, look for existing code that does the job, and remove what the change makes redundant in the same commit.
2. **No speculative code:**
   - no "fallback", "legacy", "compat", "v2" or "optional" paths;
   - no helpers "for later";
   - no feature flags;
   - no parameters nothing passes;
   - no config keys nothing reads.
3. **No dead code:**
   - no commented-out blocks;
   - no unused imports, variables or arguments;
   - no copies (`_old`, `_new`, `.bak`, `_v2`);
   - no unused templates, CSS or JS;
   - no scripts nobody runs.
4. **No new files or dependencies** unless the step says so. New docs only where a step names them. Don't add a new markdown file to explain a change; the commit message does that.
5. **Every commit message ends with:** files added, files deleted, lines added, lines removed (net), and the tests added. If net lines go up, say why in one sentence.
6. **Enforce it automatically.** Add `ruff` (rules F401 unused import, F841 unused variable, F811 redefinition, ERA001 commented-out code) and `vulture` (min confidence 80) as dev-only tools. Run them in `scripts/check.sh`. FastAPI route functions are reported as unused by vulture, so whitelist them in one `vulture_whitelist.py`. Never whitelist anything else. `check.sh` fails on any finding.
7. **Prove it ran.** Every fix is reproduced in the running app first (Docker up, real browser or Playwright), then fixed, then covered by a test. "Verified by code inspection" doesn't count.
8. **Push after every fix** to the `v2` branch. Only the baseline commit is on GitHub now.

## Part B. Cleanup pass (do this first, as one commit)

A scan of the `v2` baseline (`8fbe846`) with vulture and grep found the following. Confirm each item, remove it, and run the tests.

1. **Unused imports:**
   - `reports/builder.py`: `save_pdf`, `render_docx`, `save_docx`, `sign_report` (see Part C1 before removing `sign_report`; it must be *called*, not deleted);
   - `reports/docx_renderer.py`: `Inches`;
   - `routers/auth.py`: `generate_totp_qr_base64`;
   - `routers/settings.py`: `verify_chain`;
   - `security/audit.py`: `asdict`;
   - `security/auth.py`: `secure_compare`;
   - `intelligence/pdf_extractor.py`: `_parse_financials`.
   Check `generate_totp_qr_base64`, `verify_chain` and `secure_compare` specifically: an unused security import can mean a security check that is meant to run but doesn't. Report which ones they are.
2. **Unused variables:**
   - `exchange_id`: 6 places in `database/exchange_data.py`, 4 in `database/watchlist.py`. This is residue of the multi-country design, which is out of scope for v1. Remove the parameter and its callers' arguments;
   - `rate_type` in `exchange_scrapers/base.py`;
   - `view` in `routers/psx.py`;
   - `app_name` in `security/auth.py`;
   - `business_context` and `region_context` in `feeds.py`.
3. **Headline classifier.** `classify_headlines` and the THREAT / OPPORTUNITY / NEUTRAL logic in `intelligence/feeds.py`, its call in `routers/newsfeed.py`, and the badges in `templates/newsfeed.html`. The newsfeed has no scoring, by the owner's decision. Delete all of it and its tests.
4. **The rest of vulture's list.** Vulture at 60% confidence lists 238 candidates, 114 of them unused functions or classes. After whitelisting routes, go through the remainder and delete what is truly unused. List anything kept, with the reason, in the commit message.
5. **Overlapping extraction modules.** `pdf_extractor.py`, `statement_locator.py`, `statement_parser.py`, `psx_report_parser.py` and `annual_report_finder.py` overlap. Don't merge them now. Record in `Architecture.md` which ones are used by which entry point, and mark the losers for deletion at the end of the pipeline work.

## Part C. Stabilisation fixes still open

Work in this order. Each fix: reproduce in the running app, test, fix, push.

1. **Reports are never signed.** On `v2`, `reports/builder.py` imports `sign_report` but nothing in the app calls it. `routers/reports.py` (around line 97) counts only reports `WHERE signature_path IS NOT NULL`. That is the likely reason Settings shows 0 reports. Confirm both in the running app. Then:
   - call `sign_report` when a report is built, and store `signature_path`;
   - show the total reports, the signed count and the last signed time, across all clients and PSX exports;
   - when verification fails, refuse to serve the file with a clear message. Today `routers/reports.py` logs "signature mismatch" and serves the file anyway. Owner decision: refuse, or show a warning page with a download option; the default is to refuse;
   - add a test that a built report has a valid signature and that a tampered file is refused.
2. **Saving entered figures.** No ticker has balance sheet data (0 of 635), so this is now the only route to it. Save the entry form to a `financial_overrides` table, keyed by subject, period and field, with source "edited" and a timestamp. The analysis reads saved values over scraped ones in every tab. "Clear overrides" deletes them. Test: save a balance sheet, reload the analysis, and DuPont and Altman compute.
3. **Tab underline and speed.** The server swaps the tab bar together with the panel (`hx-swap-oob`), so the active state comes from the server, not from a JS listener. Cache `build_analysis()` per ticker plus data version, cleared on save, clear and refresh. Record the before and after times of a tab switch.
4. **Tall charts on other tabs.** Measure them in the running app. Every chart uses the one chart frame component, and the old canvas markup is deleted.
5. **Private Analyser error.** Reproduce it with arbitrary figures in PKR thousands and read the traceback from the logs. Fix the cause. Don't wrap the handler in a catch-all try/except. Add a fuzz test: zero, negative, huge and empty values must give results or field messages, never a 500.
6. **Newsfeed.** One tab per source (ARY, Dawn and the others configured) plus All, newest first. No categories, no classification.
7. **Dashboard reports.** For PSX closing prices and OGRA "scrape() returned no data", reproduce, read the logs, and fix or record them in `docs/known_issues.md` with evidence (URL, date, what the page shows). For FBR, NEPRA and OGRA showing "success" with 0 rows, show "ran, no new rows since date" instead of "success". Every report shows one of: never run, ran with no new rows, failed with a reason, or ok at a time.
8. **"Tuple index out of range" in PSX financials bulk.** Name the failing tickers from the logs. Handle single-period pages explicitly and add a fixture test.

## Part D. Pipeline survey, redone properly (only after Part C)

1. **Document selection.**
   - Use the "Transmission of Annual Report" announcement, going back to earlier announcements when the latest isn't one.
   - Reject cover letters and results sheets by page count and by whether statement pages are present.
   - If nothing qualifies, record `no_annual_report_on_psx`.
2. **Put each ticker in one bucket:** no annual report on PSX, wrong document only, real-text PDF, or scanned PDF. Rerun the same 20 tickers, then the KSE-100.
3. **Score real-text PDFs only by the acceptance checks in the pipeline plan:**
   - the statement adds up;
   - revenue and profit after tax match the PSX summary within 0.5%;
   - the prior-year column matches last year's report.
   Report "verified statements", not "fields extracted".
4. **A statement that fails any check stores none of its figures** and goes to the review queue with the reason. Never store zeros.
5. **No OCR work** until the owner has seen the real-text result.

## Part E. What to report back to the owner

One short message per part:
- what changed;
- net lines and files deleted;
- tests added;
- what is still open and why.
No claims of "complete" or "accurate" without a number beside them.
