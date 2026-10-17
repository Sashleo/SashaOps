# Coverage report

Generated 2026-10-17 from live database.
635 listed tickers (debt instruments excluded).

## Summary

| Feature | Tickers |
|---|---|
| Income statement present | 403 / 635 |
| Income statement ≥2 periods (trend charts render) | 394 / 635 |
| Balance sheet present (total_assets not null) | **0 / 635** |
| Cash flow present (ocf not null) | **0 / 635** |
| DuPont (section 7) computes | **0 / 635** |
| Altman Z-Score (section 8) computes | **0 / 635** |
| No financial data at all | 232 / 635 |

## What this means

Balance sheet columns (`total_assets`, `total_equity`, `current_assets`, `current_liabilities`, `inventory`, `receivables`, `payables`) and cash flow columns (`ocf`, `capex`) are present in `psx_financials` but are NULL for every row in the database.

The PSX HTML summary that the scraper parses provides only income statement figures. Balance sheet and cash flow data requires either:
- PDF parsing of the annual report (not yet implemented / not passing accuracy gate), or
- Manual entry via the Data Entry tab.

Sections and charts that need balance sheet or cash flow data compute for **0 out of 635 tickers** until data is entered.

These numbers replace any use of the word 'complete' or 'accurate' in status reports. A feature is done only for the count shown above.

## PDF parsing accuracy gate

No rows in `psx_financials` have a non-null `source_document_type`. PDF parsing has not been used to populate any balance sheet or cash flow figures. Pass rate on the golden set is unknown (Step 2 pending).

## Tickers with no income statement data

**232 tickers** have no annual income statement rows at all (revenue IS NULL or no rows exist). Subset:

ACIETF, AGHA-OCT, AGIC, AGP-OCTB, AHCL, AICL, AICL-OCTB, AIRLINK-OCT, AKBL-OCT, ALAC, ALIFE, ANLNV, ASHT, ASIC, ASL-OCT, ASLCPS, ASLPS, ATBA, ATIL, ATRL-OCTB, AWTX, BAFL-OCT, BAHL-OCTB, BAPL, BECO-OCT, BELA, BIPL-OCTB, BML-OCT, BNL-OCT, BOP-OCTB, BRRG, CENI, CENIR, CJPL, CLCPS, CNERGY-NOV, CNERGY-OCT, CPHL-OCT, CSIL, CSIL-NOV

…and 192 more — see `docs/coverage.csv` column `n_inc_periods=0`.

## Tickers with income statement but no balance sheet

**403 tickers** have income statement data but no balance sheet. DuPont, Altman Z-Score, leverage and liquidity do not compute for these tickers until balance sheet figures are entered via the Data Entry tab.

786, AABS, AATM, ABL, ABOT, ACPL, ADAMS, ADMM, AGHA, AGIL, AGL, AGP, AGSML, AGTL, AHL, AHTM, AIRLINK, AKBL, AKDHL, AKDSL, AKGL, ALNRS, ALTN, AMBL, AMTEX, ANL, ANSM, ANTM, APL, ARCTM, ARMG, ARPAK, ARPL, ARUJ, ASC, ASL, ASTL, ASTM, ATLH, ATRL

…and 363 more — see CSV.
