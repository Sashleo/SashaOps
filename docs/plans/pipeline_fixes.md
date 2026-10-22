# SashaOps pipeline fixes (response to the 20-ticker test, Oct 22)

Audience: Claude Code. Replaces Part D of `SASHAOPS_NEXT_INSTRUCTIONS.md`. Do this after Parts A to C.
The hygiene rules in Part A apply to every step. Every fix deletes the code it makes redundant, in the same commit.

## What the 20-ticker test got wrong

Check these before acting on the test's "known gaps" list.

1. **The "scanned" label is unreliable.** The test marks LUCK (428 pages) and HUBC (423 pages) as scanned. Both annual reports on PSX have a working text layer:
   - LUCK: `dps.psx.com.pk/download/document/282262.pdf`, checked Oct 17. Its contents page lists the unconsolidated statements from page 229 and the consolidated statements from page 281.
   - HUBC: `dps.psx.com.pk/download/document/283369.pdf`, checked today. Its contents page lists the statement of financial position at page 276 and the cash flow statement at page 277.
   
   The "65% scanned, so OCR is critical" conclusion is not supported. The likely cause is that "no fields extracted" is being reported as "scanned". Confirm in the code.
2. **Missing letters (likely ligatures) may be breaking label matching.** In HUBC's text layer "Officer" comes out as "Ocer" and "affairs" as "aairs": the "ffi" and "ff" ligatures drop out. If pdfplumber does the same, then:
   - "Profit" becomes "Prot";
   - "financing activities" becomes "nancing activities";
   - "fixed assets" becomes "xed assets".
   
   Every anchored label regex would then miss. This drop was seen in a different PDF reader, so it is a hypothesis to test with pdfplumber on LUCK and HUBC.
3. **ENGRO can't be a test case.** The repo's own `docs/scraper_verification.md` records ENGRO as delisted, with its page frozen "As of Fri, Jan 3, 2025". Remove it from the sample and replace it with a listed company. Don't re-trigger its scrape.
4. **Banks shouldn't be scored on current assets and liabilities.** Bank balance sheets don't split current and non-current, so "BS /5" is the wrong measure for HBL, MCB, UBL and BAHL. Banks are the second wave in the pipeline plan. Report them separately and leave them out of this round's score.
5. **Interim reports aren't annual reports.** UBL, FFC, SYS and EFERT used interim reports. An interim balance sheet is valid at its period end, but interim income figures cover a part-year. Tag each stored period as annual or interim. Annual ratios, the DCF and the trend charts must never mix the two.
6. **"Working" statuses have no evidence.** The parser and extractor rows say "Working" while 1 of 20 tickers reconciles. Use only the plan's measure: verified statements.

## Fixes, in order

Each one: write a failing test from a real page (saved text or a trimmed PDF fixture), fix, rerun the scorer, push.

### P1. Make "scanned" mean scanned
- Classify a document by the text layer **on its statement pages** (found from the contents page, or from the page classifier when there is none), not by whether extraction found fields.
- Record three separate outcomes: `scanned` (no text on the statement pages), `digital_extraction_failed` (text present, fields not found) and `wrong_document`.
- Rerun the sample. Expect LUCK and HUBC to move to `digital_extraction_failed` or to succeed.

### P2. Remove background OCR threads now
- Delete `_schedule_background_ocr()` and the daemon-thread path. It causes the out-of-memory kills (exit 137), and its result is never used.
- OCR becomes a separate batch step in the later scanned-PDF phase. Until then, a scanned document is recorded as `scanned` and goes to the review queue.
- Do not build subprocess OCR in this round.

### P3. Normalise text before matching
- Apply Unicode NFKC normalisation to the extracted text, so ligature characters become plain letters. Collapse runs of whitespace.
- If pdfplumber drops ligatures entirely rather than emitting ligature characters, confirm it on LUCK and HUBC first. Then fix it at extraction, for example by checking pdfplumber's character handling. Don't add loose regexes like `pro?f?i?t`.
- Add a test with real lines from both reports.

### P4. Find statement pages from the contents page
- Read the contents page for the printed page numbers of the statement of financial position, profit or loss, and cash flow statement, for both the unconsolidated and consolidated sets. Work out the printed-to-PDF page offset from the page footers.
- This replaces `_SEEK_CAP` and the early-exit logic (gap 6 in the test). Delete those.
- Choose the basis (unconsolidated or consolidated) as the pipeline plan says: the one whose revenue and profit after tax match the PSX summary. Store the basis.

### P5. Add the missing fields properly (gap 1 in the test)
Add `retained_earnings`, `receivables`, `payables`, `ocf`, `capex`, `depreciation` and `total_debt` to `statement_parser._FIELDS`. Use anchored, total-line patterns, each with a test on real lines. Don't copy `_LABELS` from the old parser as they are; it has no scale detection or balance check.
- **retained_earnings:** the unappropriated profit or accumulated profit line in equity. Altman Z'' needs it.
- **ocf:** "Net cash generated from (used in) operating activities".
- **capex:** the purchase of property, plant and equipment line in investing activities, plus capital work in progress additions where printed separately. Record which lines were summed.
- **depreciation:** the depreciation line in the cash flow adjustments, or the note total if the statement points to it.
- **total_debt:** not one printed line. Define it once: long-term financing plus its current portion, plus short-term borrowings, plus lease liabilities, both current and non-current. Record which lines were summed. If any component is missing, leave total_debt empty; don't produce a partial sum.

Then **delete** `psx_report_parser._parse_financials()` and `_LABELS`, the dead parser, in the same commit, so only one parser remains.

### P6. Current assets and liabilities (gap 3)
- Accept "CURRENT ASSETS" section headings and "Total current assets" lines in their real forms (colon, all capitals, indented). Take the value only from the total line, never from the heading.
- Test with lines from NCL, SYS and INDU.

### P7. Failed statements store nothing, and existing bad values get removed (gap 4)
- If the balance check fails, store none of that statement's figures. Send the statement to the review queue with the difference stated. Never write zeros.
- **Important:** `upsert_psx_financials()` uses COALESCE, so a NULL never overwrites an existing value. Nulling the fields will not remove the bad UBL figures that are already stored. Add an explicit delete:
  - delete the statement's values for that ticker and period, but only values whose source is a parsed PDF;
  - never touch scraped or owner-entered values.
- Run it once for every stored statement that fails the check today, and list the ones removed.

### P8. Document selection (gap 5)
- Prefer the "Transmission of Annual Report" announcement, searching back through earlier announcements.
- Reject a document that has no statement pages, whatever its page count. Use page count only as a quick first filter: under 20 pages is not an annual report.
- Use an interim report only if no annual report exists for the period, and tag it `interim`, as in item 5 above.

## Scoring after these fixes

Rerun on the same tickers: the 19 left once ENGRO is removed, plus one replacement. Report non-banks and banks separately, with one row per ticker:
- document type;
- bucket: `scanned`, `digital_extraction_failed`, `wrong_document` or `no_annual_report_on_psx`;
- basis;
- statements found;
- each acceptance check passed or failed: adds up, matches the PSX summary within 0.5%, matches last year's report;
- verified yes or no.

Headline: verified non-bank statements out of the non-bank sample. Then extend to the KSE-100.

Show the owner the result before any OCR work.

## Deletions expected in this round
- `_schedule_background_ocr()` and its thread code.
- `psx_report_parser._parse_financials()` and `_LABELS`.
- `_SEEK_CAP` and the early-exit seek logic, once the contents page lookup replaces them.
- Any part of `pdf_extractor.py` left unused after P1 to P4.

List them, with the net lines removed, in the commit messages.
