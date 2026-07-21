# How report extraction avoids errors

The problem this solves: a financial figure pulled from a PDF can be *present and
well-formatted and still completely wrong*. A number that is off by 1000x, or read
off the wrong line, or taken from last year's column, still looks like a clean
number. It then flows silently into ratios, Altman Z, and DCF, and the analysis
looks plausible while being false.

So extraction is not graded on "did it return a number". It is graded on "is this
the number the report actually states". Six defences enforce that.

---

## The evidence this is built on

A corpus study of **76 firms - 2 picked at random from each of the 38 PSX sectors**
(`tools/pdf_corpus.py`). Every PDF was downloaded, classified, and run through the
parsers. Findings:

| | count |
|---|---|
| scanned (no text layer) | 37 |
| digital (real text layer) | 30 |
| no report published | 9 |

Scanned reports are the **majority**, so OCR is mandatory, not a fallback nicety.
Page counts ranged from 3 to 349. An accuracy audit of the *successful* digital
extractions then exposed three analysis-corrupting bugs that a field-count metric
had completely hidden. Those bugs are what the defences below exist to stop.

---

## Defence 1 - Unit scale is detected, never assumed

**The bug found:** the engine hardcoded `_SCALE = 1_000`, assuming every report
prints in thousands. Reports actually use three different units:

| Firm | Report prints | Correct factor | Blind x1000 gives |
|---|---|---|---|
| PAKT | "Rs. million" | x1,000,000 | 1000x too small |
| ABL | "Rupees in '000" | x1,000 | correct (by luck) |
| JDWS | actual rupees (71,297,290,598) | x1 | **71 trillion** |

**The defence** (`statement_parser.detect_scale`): read the unit header printed on
the statement - `Rupees in '000` / `Rs. in million` / `in billions` / actual - and
apply that factor. If no header exists, infer from magnitude: a raw `total_assets`
already >= 1e8 is actual rupees (x1), otherwise it is a thousands table (x1000).
The applied factor is stored with every value, and the balance-sheet check
(Defence 4) independently validates the choice.

The engine no longer rescales: values arrive marked `_already_scaled`, and
`financial_model` skips its historical x1000 for those. This is the "omitted zeros"
class of error, closed.

**Verified:** JDWS x1, ABL x1000, PAKT x1,000,000 - all auto-detected correctly.

---

## Defence 2 - Total-line anchoring, not keyword hunting

**The bug found:** the old parser took the *first line containing a keyword*. For
JDWS it stored:

- `current_liabilities` = 1,049,750,000, actually read from
  **"Non-current liabilities: Long term investments"**
- `net_income` = a value that appeared in **none of the columns** on the profit line

A wrong current-liabilities figure corrupts the current ratio, quick ratio, working
capital and Altman Z - so this breaks *ratios*, not just absolute figures.

**The defence:** each field has ordered, anchored patterns that must match at the
*start* of the line, and balance-sheet totals must literally say "total":

```
current_liabilities : ^\s*total\s+current\s+liabilities\b
total_assets        : ^\s*total\s+assets\b
revenue             : ^revenue from contracts with customers | ^net sales | ^turnover
```

A `_SUBITEM_GUARD` additionally rejects lines containing sub-item words
(long term investments, spare parts, stores, advances, deposits, prepayments,
trade receivables) so a sub-line can never be promoted to a statement total.

**Verified:** JDWS revenue now resolves to the real IFRS-15 line
("Revenue from contracts with customers" 61.78bn) rather than the 71.3bn gross line.

---

### Defence 2b - Sign guard against contra/deduction lines

Wiring the parser in caught a further real failure: the pattern `^sales?` matched
**"Sales tax (57,706,226)"** on PAKT and stored revenue as **-57,706 bn**. Deduction
and contra lines sit right next to the real revenue line and read very similarly.

Two fixes, one specific and one general:

- the revenue patterns now exclude tax / returns / discount / commission variants;
- a **sign guard** rejects any negative candidate for a field that can never be
  negative (`revenue`, `total_assets`, `current_assets`, `current_liabilities`,
  `total_liabilities`, `cash`, `inventory`) and keeps searching. Profit lines are
  deliberately excluded from this rule, because losses are real.

After the guard, PAKT resolves to **+139.018 bn "Net Turnover"** - the correct line.

---

## Defence 3 - Explicit column choice

Statements print 2-4 numeric columns: current year / prior year, consolidated /
standalone, quarter / cumulative. Taking the wrong one silently reports last year's
business, or the group instead of the company.

**The defence:** the first numeric column after the label is taken as the current
reporting period (universal in PSX statements), **every** column is retained in
`all_columns`, and the chosen index is stored. The choice is therefore visible and
auditable rather than implicit.

---

## Defence 4 - The balance sheet must reconcile

This is the strongest defence, because it is arithmetic the report itself must obey:

```
Total assets  ==  Total liabilities + Total equity      (within 1%)
```

If the extraction balances, the scale, the lines and the columns were all almost
certainly right together - one error would break the identity. `_balance_check`
records `ok`, both sides, and the percentage difference. A failure does not silently
ship; it lowers confidence and is surfaced.

---

## Defence 5 - Number-noise repair

Even digital PDFs produce corrupted text. Real examples from ABL:

| Extracted text | Naive parse | Repaired |
|---|---|---|
| `3,441 ,959,708` | 0.003 bn (!) | 3,441,959,708 |
| `8.189.744` | 8.19 | 8,189,744 |
| `3,691 ,029,638` | wrong | 3,691,029,638 |

`_normalize_numbers()` rejoins digits split by stray spaces and converts
periods-used-as-thousands-separators, while leaving genuine decimals alone. Before
this, ABL's total liabilities parsed as 0.003bn instead of 3.44 trillion.

---

## Defence 6 - Provenance on every single value

Every extracted figure carries the receipt that proves it:

```
revenue = 61,782,057,256
  raw          61782057256        <- the number as printed
  scale        x1                 <- unit factor applied, and why
  column       0 of [61.78bn, 63.10bn, 35.25bn, 26.45bn]
  source_line  "Revenue from contracts with customers 61,782,057,256 ..."
  confidence   high
```

So any figure can be traced back to the exact line of the exact report. When a
number looks wrong, you can see immediately whether the cause was the scale, the
line, or the column - and fix that specific thing. This mirrors the provenance rule
already applied to the scraped macro data.

---

## Choosing between methods: accuracy beats quantity

The chain (pdfplumber tables -> pdfplumber text -> Tesseract raw -> Tesseract +
OpenCV preprocess) no longer returns the *first* method that finds 3 fields. It
scores every method with `quality_score()`:

```
(balance sheet reconciles, count of high-confidence values, total fields)
```

and keeps the best. A method whose balance sheet reconciles is authoritative and
short-circuits the rest. A method returning *more* fields that do not reconcile
loses to one returning fewer that do.

Corpus evidence for the chain: plain `tesseract_raw` recovered **5-8 of 9 fields in
127-200s** on scanned reports (AGIL 8/9), so the OCR tier is genuinely productive
rather than a token fallback.

---

## What is deliberately NOT done

- **No guessed values.** A field that cannot be anchored is absent, not estimated.
  Missing data lowers confidence; it never gets filled in with an assumption.
- **No silent rescaling.** Values are scaled exactly once, at parse time, by a
  detected factor.
- **Nothing is deleted.** Implausible values are flagged for review, preserving the
  audit trail.

---

## Known limits (honest)

- Some digital PDFs merge adjacent columns in text extraction (PAKT:
  `139,018121,078`). Table-mode extraction reads these more reliably and is
  preferred by the scorer where it reconciles.
- Heavily garbled text (ABL: `8+2609557`) can still mis-parse a single line. The
  balance check is what catches this class, and OCR often reads such pages cleaner
  than the embedded text layer.
- The balance check needs all three of assets / liabilities / equity to be found; on
  summary or highlights pages it returns "unknown" rather than pass or fail.
