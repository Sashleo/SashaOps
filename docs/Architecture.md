# Architecture: PDF extraction module map

## Entry points and the modules they reach

```
fetch_psx_report_financials()   [psx_report_parser.py]
  └─ select_report_document()   [psx_report_parser.py]
  └─ _download_by_url()         [psx_report_parser.py]
  └─ extract_financials()       [pdf_extractor.py]          ← ACTIVE path (digital PDFs)
       └─ PdfplumberTextExtractor   calls parse_financials_accurate()  [statement_parser.py]
       └─ PdfplumberTablesExtractor calls parse_financials_accurate()  [statement_parser.py]
       └─ TesseractRawExtractor     calls parse_financials_accurate()  [statement_parser.py]
       └─ TesseractDeskewExtractor  calls parse_financials_accurate()  [statement_parser.py]
  └─ _schedule_background_ocr() [psx_report_parser.py]      ← scanned PDFs only
       └─ extract_statements()  [statement_locator.py]      ← targeted OCR (tries first)
       └─ extract_financials()  [pdf_extractor.py]          ← full OCR fallback
```

## Module status

| Module | Used by | Status |
|---|---|---|
| `psx_report_parser.py` | `psx_financials.py` (entry point) | Active |
| `pdf_extractor.py` | `psx_report_parser.py` | Active |
| `statement_parser.py` | `pdf_extractor.py` | Active |
| `statement_locator.py` | `psx_report_parser._schedule_background_ocr` | Active (scanned path only) |
| `annual_report_finder.py` | `psx_report_parser._schedule_background_ocr` (gated: `IR_ANNUAL_REPORT=true`) | Dormant — flag never set in production |

## Dead code in psx_report_parser.py

- `_parse_financials()` — old simple label-matcher, replaced by `parse_financials_accurate()`. Called by nothing.
- `_pdf_text()` / `_ocr_pdf()` / `_first_figure()` / `_download()` — old extraction path, replaced by `pdf_extractor.py`. Called by nothing.
- `find_latest_report_url()` — superseded by `select_report_document()`. Called by nothing.

**These functions are marked for deletion at the end of pipeline work (Part D).**
