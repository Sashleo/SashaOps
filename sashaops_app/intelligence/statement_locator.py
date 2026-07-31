"""
intelligence/statement_locator.py - targeted statement-page extraction.

OCRing a 412-page annual report to read a two-page balance sheet is slow and
noisy. This locates the statement pages first, then spends OCR effort only on
those.

  STEP 1  cheap locate: pdfplumber text where a text layer exists, else 150 DPI
          --psm 3, matching page-TITLE patterns only. Stops as soon as all four
          statement types are found.
  STEP 2  high-quality OCR on located pages plus the 2 following (statements
          span pages): 400 DPI, deskew, denoise, adaptive threshold, --psm 6.
  STEP 3  column-aware parse: Pakistani statements print current period then
          prior period, left to right. Both are captured from one document.
  STEP 4  prefer unconsolidated (standalone) to match the PSX summary basis;
          tag basis='consolidated' when only consolidated exists so the two are
          never silently mixed.
  STEP 5  reconciliation gate: assets = equity + liabilities within 0.5%, else
          reject and log the extracted fields so the failure is diagnosable.
"""

from __future__ import annotations

import io
import re
from typing import Optional

import structlog

log = structlog.get_logger()

BALANCE_SHEET_TITLES = [
    r"statement of financial position",
    r"balance sheet",
    r"consolidated statement of financial position",
    r"unconsolidated statement of financial position",
]
INCOME_TITLES = [
    r"statement of profit or loss",
    r"profit and loss account",
    r"statement of comprehensive income",
]
CASHFLOW_TITLES = [
    r"statement of cash flows",
    r"cash flow statement",
]
AUDIT_TITLES = [
    r"independent auditor'?s report",
    r"auditor'?s report to the members",
]

STATEMENT_TYPES = {
    "balance_sheet": BALANCE_SHEET_TITLES,
    "income": INCOME_TITLES,
    "cashflow": CASHFLOW_TITLES,
    "audit": AUDIT_TITLES,
}

LOCATE_DPI = 150
EXTRACT_DPI = 400
PAGE_SPAN = 2          # statements frequently continue onto following pages
LOCATE_TIMEOUT = 30
EXTRACT_TIMEOUT = 120
MAX_LOCATE_PAGES = 650
# Hard cap on low-DPI OCR during the locate pass. Without it a report whose pages
# all lack a text layer costs one OCR per page before the statements are found.
MAX_LOCATE_OCR_PAGES = 120
# How many pages to probe for a text layer before concluding the document is
# scanned. A real digital report has text on its first pages; continuing to parse
# 600 page objects to confirm what the first 25 already showed is pure cost.
_TEXT_PROBE_PAGES = 25
RECONCILE_TOLERANCE = 0.005   # 0.5%
# No PSX-listed company has total assets below this. Used as an order-of-magnitude
# gate on a misread unit scale, not as a judgement about company size.
MIN_TOTAL_ASSETS_PKR = 100_000_000

# A title line is short. Requiring that avoids matching a contents-page entry or a
# cross-reference sentence in the notes ("see the statement of financial position
# on page 84"), which would otherwise send high-DPI OCR to the wrong page.
_MAX_TITLE_LINE_CHARS = 90


# Where the financial statements actually sit. An annual report opens with the
# narrative (chairman's review, governance, sustainability) and puts the audited
# statements well past the middle. Scanning blank pages front-to-back spends the
# OCR budget on the narrative: in a 608-page report a 120-page budget would never
# reach page 200. Scanning from here forward, then wrapping to the front, finds
# them far sooner without excluding any page.
_STATEMENT_ZONE_START = 0.35


def _scan_order(blank_pages: list, n_pages: int) -> list:
    """Blank pages ordered by how likely they are to hold a statement."""
    if not blank_pages or not n_pages:
        return blank_pages
    cut = int(n_pages * _STATEMENT_ZONE_START)
    later = [p for p in blank_pages if p >= cut]
    earlier = [p for p in blank_pages if p < cut]
    return later + earlier


# A statement page is a dense numeric TABLE. A narrative page is prose. Verified
# on FFC: pages 48 and 77 both matched a title pattern but are a SWOT page and an
# MD&A ratio commentary - neither carries a statement. Multi-column narrative also
# defeats a short-line test, because OCR of a 3-column layout emits short
# fragments ("Liquidity Ratios", "Profitability Ratios") that look like titles.
# Requiring the title near the top AND real numeric density separates them.
_TITLE_SEARCH_LINES = 12      # a statement title sits at the head of its page
_MIN_NUMERIC_ROWS = 6         # rows carrying a financial figure
_STATEMENT_NUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d{5,}")


def _numeric_row_count(text: str) -> int:
    """Lines carrying at least one grouped/large number, as a statement table has."""
    return sum(1 for line in (text or "").split("\n")
               if _STATEMENT_NUM.search(line))


# Rows on an audited statement carry two money columns (current and prior). A page
# whose identity rows carry three or more is a multi-year summary - "Six Years at a
# Glance", "Financial Highlights", "Key Operating and Financial Data". Verified on
# ABOT page 77, which carries six periods per row and is NOT the statement of
# financial position, though it matched the same title pattern.
_IDENTITY_ROW = re.compile(
    r"^\s*(total\s+assets|total\s+equity|total\s+liabilit|"
    r"current\s+assets|current\s+liabilit|net\s+assets)\b", re.I)
SUMMARY_MIN_COLUMNS = 3
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def count_money_columns(text: str) -> int:
    """
    Money columns per identity row, taken as the MODE across those rows.

    The mode rather than the max: a stray note reference or a percentage column on
    one row should not reclassify the page, whereas a genuine six-year summary
    carries six figures on every identity row.
    """
    counts = []
    for line in (text or "").split("\n"):
        if not _IDENTITY_ROW.match(_clean_line(line)):
            continue
        clean = _clean_line(line)
        m = _IDENTITY_ROW.match(clean)
        n = len(_columns(clean, after=m.end()))
        if n:
            counts.append(n)
    if not counts:
        return 0
    return max(set(counts), key=counts.count)


def classify_page(text: str) -> tuple:
    """
    ('statement' | 'summary' | 'neither', n_columns).

    Two money columns per identity row is an audited statement; three or more is a
    multi-year summary. A page with no identity row carrying figures is neither -
    ABOT page 89 is a bar chart whose only numbers are axis percentages, and
    calling that a statement would send the parser looking for lines it cannot
    have.
    """
    n = count_money_columns(text)
    if n >= SUMMARY_MIN_COLUMNS:
        return "summary", n
    if n == 0:
        return "neither", 0
    return "statement", n


def extract_period_labels(text: str, n_columns: int,
                          latest_year: Optional[int] = None) -> list:
    """
    Period label per column, newest first.

    Prefers years actually printed in a header row. Falls back to counting back
    from the latest known fiscal year, which is the convention in every Pakistani
    multi-year summary seen so far (leftmost most recent, descending rightwards).
    """
    for line in (text or "").split("\n")[:25]:
        years = [int(m.group()) for m in _YEAR_RE.finditer(line)]
        # A header row, not a sentence that happens to mention a year.
        if len(years) >= n_columns and len(set(years)) == len(years):
            return [str(y) for y in years[:n_columns]]
    if latest_year is None:
        all_years = [int(m.group()) for m in _YEAR_RE.finditer(text or "")]
        latest_year = max(all_years) if all_years else None
    if latest_year is None:
        return [f"col{i + 1}" for i in range(n_columns)]
    return [str(latest_year - i) for i in range(n_columns)]


def parse_multi_period(text: str) -> dict:
    """
    Every period on a multi-year summary page, not just the first two.

    Returns {period_label: {field: raw_value}}. Summaries carry totals only, so a
    caller must not read the absence of granular lines as a failure.
    """
    lines = [_clean_line(l) for l in (text or "").split("\n") if l.strip()]
    n_cols = count_money_columns(text)
    if n_cols < SUMMARY_MIN_COLUMNS:
        return {}
    labels = extract_period_labels(text, n_cols)

    by_field: dict = {}
    for field, anchors in _FIELDS.items():
        for conf, pat in anchors:
            rx = re.compile(pat, re.I)
            hit = None
            for line in lines:
                m = rx.match(line)
                if not m or _SUBITEM_GUARD.search(line):
                    continue
                cols = _columns(line, after=m.end())
                # Only a row carrying the full period set is a summary row; a
                # partial row is a different table that happens to share a label.
                if len(cols) < n_cols:
                    continue
                if field in _MUST_BE_POSITIVE and cols[0] <= 0:
                    continue
                hit = cols[:n_cols]
                break
            if hit:
                by_field[field] = hit
                break

    out: dict = {}
    for idx, label in enumerate(labels):
        period = {f: vals[idx] for f, vals in by_field.items() if idx < len(vals)}
        if period:
            out[label] = period
    return out


def _match_type(text: str, require_density: bool = True) -> Optional[str]:
    """
    Which statement type this page is, or None.

    The title must appear in the first few lines (a heading, not a mention in
    prose), and unless disabled the page must also read like a table rather than
    an essay about one.
    """
    if not text:
        return None
    lines = text.split("\n")
    hit = None
    for line in lines[:_TITLE_SEARCH_LINES]:
        s = line.strip().lower()
        if not s or len(s) > _MAX_TITLE_LINE_CHARS:
            continue
        for st, patterns in STATEMENT_TYPES.items():
            for p in patterns:
                if re.search(p, s):
                    hit = st
                    break
            if hit:
                break
        if hit:
            break
    if not hit:
        return None
    # The auditor's report is genuinely prose, so density does not apply to it.
    if hit == "audit" or not require_density:
        return hit
    return hit if _numeric_row_count(text) >= _MIN_NUMERIC_ROWS else None


def _is_consolidated(text: str) -> bool:
    low = (text or "").lower()
    return "consolidated" in low and "unconsolidated" not in low


def locate_statement_pages(pdf_bytes: bytes) -> dict:
    """
    STEP 1. Return {statement_type: [page_index, ...]} (0-based), cheaply.

    Uses the embedded text layer when a page has one (free), and only falls back
    to low-DPI OCR for pages that do not. Stops once all four types are found.
    """
    found: dict = {}
    try:
        import pdfplumber
    except Exception:
        return found

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            n_pages = min(len(pdf.pages), MAX_LOCATE_PAGES)
            text_pages, blank_pages = {}, []
            for i in range(n_pages):
                try:
                    t = pdf.pages[i].extract_text() or ""
                except Exception:
                    t = ""
                if len(t.strip()) > 40:
                    text_pages[i] = t
                else:
                    blank_pages.append(i)
                # pdfplumber builds a page object model even when a page carries no
                # text, so this pre-pass alone stalls on a large scanned report
                # (measured: no progress in 15 minutes on a 608-page document).
                # Once enough consecutive pages have come back empty, the document
                # is scanned and the text pass has nothing left to contribute.
                if i >= _TEXT_PROBE_PAGES and not text_pages:
                    blank_pages = list(range(n_pages))
                    log.info("locate: no text layer found in probe, treating as scanned",
                             probed=_TEXT_PROBE_PAGES, pages=n_pages)
                    break
                # Free the cached page object; pdfplumber otherwise retains every
                # page it has parsed and the pass grows to hundreds of megabytes.
                try:
                    pdf.pages[i].flush_cache()
                except Exception:
                    pass
    except Exception as e:
        log.info("locate: pdf open failed", error=str(e)[:80])
        return found

    # page index -> 'statement' | 'summary', so a caller can tell an audited
    # statement from a multi-year highlights page without re-reading the page.
    page_types: dict = {}

    def _record(idx: int, text: str) -> None:
        st = _match_type(text)
        if st:
            found.setdefault(st, [])
            if idx not in found[st]:
                found[st].append(idx)
            if st in ("balance_sheet", "income"):
                page_types[idx] = classify_page(text)[0]

    for i, t in text_pages.items():
        _record(i, t)

    # Only OCR pages with no text layer, and only while something is still missing.
    # Stopping on the three FINANCIAL statements, not all four types: the auditor's
    # report is optional for extraction, and waiting for it made the cheap pass OCR
    # every remaining page of a 176-page report (measured 100s on ABOT) purely
    # because that one title never matched. The whole point of this pass is to be
    # cheap, so it is also hard-capped.
    # A summary match is useful but must NOT end the search: ABOT's "Six Years at
    # a Glance" page matches the same title pattern as the real statement, and
    # stopping there would forfeit the granular lines Section 2 needs. The pass
    # therefore continues until an actual 2-column STATEMENT balance sheet is
    # found, or the budget runs out.
    def _have_financials() -> bool:
        if not all(k in found for k in ("balance_sheet", "income", "cashflow")):
            return False
        return any(page_types.get(p) == "statement"
                   for p in found.get("balance_sheet", []))

    if blank_pages and not _have_financials():
        try:
            import pytesseract
            from pdf2image import convert_from_bytes
            budget = MAX_LOCATE_OCR_PAGES
            for i in _scan_order(blank_pages, n_pages):
                if _have_financials() or budget <= 0:
                    break
                budget -= 1
                try:
                    imgs = convert_from_bytes(pdf_bytes, dpi=LOCATE_DPI,
                                             first_page=i + 1, last_page=i + 1)
                    if not imgs:
                        continue
                    t = pytesseract.image_to_string(
                        imgs[0], config="--psm 3", timeout=LOCATE_TIMEOUT) or ""
                    _record(i, t)
                except Exception:
                    continue
        except Exception as e:
            log.info("locate: OCR pass unavailable", error=str(e)[:80])

    found["_page_types"] = page_types
    return found


def _preprocess(pil_img, denoise: bool = True):
    """Grayscale, deskew, optional denoise, adaptive threshold."""
    import cv2
    import numpy as np
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
    inv = cv2.bitwise_not(img)
    coords = cv2.findNonZero(inv)
    if coords is not None and len(coords) > 50:
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        if abs(angle) > 0.3:
            h, w = img.shape
            M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                                borderMode=cv2.BORDER_REPLICATE)
    if denoise:
        img = cv2.fastNlMeansDenoising(img, h=10)
    img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 10)
    return img


def ocr_pages(pdf_bytes: bytes, page_indexes: list, preprocess: bool = False) -> str:
    """
    STEP 2. High-quality OCR of specific pages at 400 DPI, --psm 6.

    preprocess defaults OFF on evidence, not preference. Phase 2's method study
    found every OpenCV preprocessing variant lost to raw OCR 15-2, and that
    reproduced here at 400 DPI: on FFC page 48 the preprocessed pass returned
    character soup ("ay al OI sl ol (Ol 5] Sy FS ei") while the raw pass returned
    readable English from the same page. Deskew plus adaptive threshold is
    available via preprocess=True for genuinely skewed scans, but it must be
    opted into rather than inflicted by default.
    """
    if not page_indexes:
        return ""
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except Exception:
        return ""

    parts = []
    for i in sorted(set(page_indexes)):
        try:
            imgs = convert_from_bytes(pdf_bytes, dpi=EXTRACT_DPI,
                                     first_page=i + 1, last_page=i + 1)
            if not imgs:
                continue
            img = imgs[0]
            if preprocess:
                try:
                    img = _preprocess(img)
                except Exception:
                    pass   # fall back to the raw render rather than losing the page
            parts.append(pytesseract.image_to_string(
                img, config="--psm 6", timeout=EXTRACT_TIMEOUT) or "")
        except Exception as e:
            log.info("targeted OCR page failed", page=i, error=str(e)[:70])
    return "\n".join(parts)


def expand_pages(pages: list, span: int = PAGE_SPAN) -> list:
    """A located page plus the `span` pages after it."""
    out = set()
    for p in pages:
        for k in range(span + 1):
            out.add(p + k)
    return sorted(out)


# ── STEP 3: column-aware parsing ──────────────────────────────────────────────

from intelligence.statement_parser import (   # noqa: E402
    _FIELDS, _SUBITEM_GUARD, _MUST_BE_POSITIVE, _clean_line, _columns,
    detect_scale, _choose_scale,
)


def parse_two_column(text: str) -> dict:
    """
    Parse current AND prior period from one statement.

    Pakistani statements print current period then prior period, left to right,
    so the first number after a label is the current period and the second is the
    prior. Capturing both gives multi-period data from a single document.

    Returns {field: {"current", "prior", "source_line", "confidence"}}.
    """
    lines = [_clean_line(l) for l in (text or "").split("\n") if l.strip()]
    out: dict = {}
    for field, anchors in _FIELDS.items():
        for conf, pat in anchors:
            rx = re.compile(pat, re.I)
            hit = None
            for line in lines:
                m = rx.match(line)
                if not m or _SUBITEM_GUARD.search(line):
                    continue
                cols = _columns(line, after=m.end())
                if not cols:
                    continue
                if field in _MUST_BE_POSITIVE and cols[0] <= 0:
                    continue
                hit = {"current": cols[0],
                       "prior": cols[1] if len(cols) > 1 else None,
                       "all_columns": cols[:4],
                       "source_line": line.strip()[:120],
                       "confidence": conf}
                break
            if hit:
                out[field] = hit
                break
    return out


def _scaled(parsed: dict, text: str) -> tuple:
    """Apply the unit scale once, to both periods. Returns (current, prior, scale)."""
    header_scale, evidence = detect_scale(text)
    as_raw = {f: {"raw": h["current"]} for f, h in parsed.items()}
    scale, _ = _choose_scale(as_raw, header_scale, evidence)
    cur = {f: h["current"] * scale for f, h in parsed.items() if h["current"] is not None}
    pri = {f: h["prior"] * scale for f, h in parsed.items() if h.get("prior") is not None}
    return cur, pri, scale


def _coherent(d: dict) -> Optional[str]:
    """
    Cross-checks the accounting identity alone cannot catch.

    ABOT passed reconciliation at 0.00% while reporting current_assets of
    30,993mn against total_assets of 47mn - impossible, and invisible to
    assets = equity + liabilities because all three of those lines happened to be
    misread at the same scale. A subtotal larger than its own total means the page
    was read at inconsistent scales, so the extraction must be rejected.
    """
    ta = d.get("total_assets")
    if ta:
        for sub in ("current_assets", "cash", "inventory", "receivables"):
            v = d.get(sub)
            if v is not None and abs(v) > abs(ta):
                return (f"{sub} ({v:,.0f}) exceeds total_assets ({ta:,.0f}); "
                       f"lines were read at inconsistent scales")
    cl, tl = d.get("current_liabilities"), d.get("total_liabilities")
    if tl and cl is not None and abs(cl) > abs(tl):
        return (f"current_liabilities ({cl:,.0f}) exceeds total_liabilities "
               f"({tl:,.0f}); lines were read at inconsistent scales")

    # Order-of-magnitude floor. A PSX-listed company with total assets under
    # 100mn PKR does not exist; such a value means the statement's unit scale was
    # misread. ABOT's 46.8mn (printed in millions, scaled as thousands) is caught
    # by this rule alone.
    if ta is not None and abs(ta) < MIN_TOTAL_ASSETS_PKR:
        return (f"total_assets ({ta:,.0f} PKR) is below the {MIN_TOTAL_ASSETS_PKR:,.0f} "
               f"floor for a listed company; the unit scale was misread")

    # A going concern has positive equity. Negative equity is real but rare, and
    # far more often signals that the equity line was misread, so it is rejected
    # here rather than silently feeding leverage and Altman.
    te = d.get("total_equity")
    if te is not None and te <= 0:
        return f"total_equity ({te:,.0f}) is not positive; the equity line is suspect"

    # Current + non-current assets should rebuild total assets within 1%.
    nca = d.get("non_current_assets")
    ca = d.get("current_assets")
    if ta and ca is not None and nca is not None:
        drift = abs((ca + nca) - ta) / abs(ta)
        if drift > 0.01:
            return (f"current_assets + non_current_assets ({ca + nca:,.0f}) does not "
                   f"rebuild total_assets ({ta:,.0f}), off by {drift * 100:.1f}%")
    return None


def _reconciles(d: dict) -> tuple:
    """STEP 5. assets = equity + liabilities within 0.5%, plus coherence."""
    ta = d.get("total_assets")
    te = d.get("total_equity")
    tl = d.get("total_liabilities")
    if ta is None or te is None or tl is None:
        missing = [k for k, v in (("total_assets", ta), ("total_equity", te),
                                 ("total_liabilities", tl)) if v is None]
        return None, f"incomplete: missing {', '.join(missing)}"
    if not ta:
        return False, "total_assets is zero"
    incoherent = _coherent(d)
    if incoherent:
        return False, incoherent
    diff = abs(ta - (te + tl)) / abs(ta)
    return diff <= RECONCILE_TOLERANCE, f"difference {diff * 100:.2f}%"


def merge_periods(statement: dict, summary: dict) -> dict:
    """
    Merge audited-statement periods over summary periods.

    Precedence rule: for any period present in both, the audited statement wins
    for every field it carries, and a summary value never overwrites one sourced
    from a statement. The summary only fills periods, and fields within a period,
    that the statement does not cover.

    Returns {period: {"fields": {...}, "source_page_type": "statement"|"summary"}}.
    """
    out: dict = {}
    for period, fields in (summary or {}).items():
        out[period] = {"fields": dict(fields), "source_page_type": "summary"}
    for period, fields in (statement or {}).items():
        if period in out:
            merged = dict(out[period]["fields"])
            merged.update(fields)           # statement wins field by field
            out[period] = {"fields": merged, "source_page_type": "statement"}
        else:
            out[period] = {"fields": dict(fields), "source_page_type": "statement"}
    return out


def _pick_basis(pdf_bytes: bytes, pages: list) -> tuple:
    """Prefer unconsolidated (standalone) to match the PSX summary basis."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            unconsolidated, consolidated = [], []
            for p in pages:
                try:
                    t = (pdf.pages[p].extract_text() or "").lower()
                except Exception:
                    t = ""
                (consolidated if _is_consolidated(t) else unconsolidated).append(p)
        if unconsolidated:
            return unconsolidated, "unconsolidated"
        if consolidated:
            return consolidated, "consolidated"
    except Exception:
        pass
    return pages, "unknown"


def extract_statements(pdf_bytes: bytes, ticker: str = "",
                       preprocess: bool = False) -> dict:
    """
    Full targeted pipeline, capturing BOTH page types.

    An audited statement gives two periods of granular lines; a multi-year summary
    ("Six Years at a Glance") gives totals only but across every period it prints.
    Both are useful and they are not interchangeable, so both are extracted and
    merged under statement-wins precedence.
    """
    located = locate_statement_pages(pdf_bytes)
    page_types = located.get("_page_types") or {}
    bs_pages = located.get("balance_sheet") or []
    if not bs_pages:
        return {"located": located, "ocr_pages": [], "current": {}, "prior": {},
                "reconciled": None, "periods": {},
                "reason": "no statement of financial position page located"}

    stmt_pages = [p for p in bs_pages
                  if page_types.get(p) not in ("summary", "neither")]
    summ_pages = [p for p in bs_pages if page_types.get(p) == "summary"]

    chosen, basis = _pick_basis(pdf_bytes, stmt_pages or bs_pages)
    pages = expand_pages(chosen)
    text = ocr_pages(pdf_bytes, pages, preprocess=preprocess)
    if not text.strip():
        return {"located": located, "ocr_pages": pages, "basis": basis,
                "current": {}, "prior": {}, "reconciled": None, "periods": {},
                "reason": "targeted OCR produced no text"}

    # Classify authoritatively from the high-quality render, not the cheap pass.
    page_type, n_cols = classify_page(text)

    statement_periods: dict = {}
    summary_periods: dict = {}
    scale = None

    if page_type == "statement":
        parsed = parse_two_column(text)
        current, prior, scale = _scaled(parsed, text)
        ok, why = _reconciles(current)
        if current:
            statement_periods["current"] = current
        if prior:
            statement_periods["prior"] = prior
        provenance = {f: h["source_line"] for f, h in parsed.items()}
    else:
        # A summary page carries totals only. Its lack of granular lines is the
        # nature of the page, not an extraction failure.
        raw_periods = parse_multi_period(text)
        as_raw = {f: {"raw": v} for f, v in
                 (next(iter(raw_periods.values()), {}) or {}).items()}
        header_scale, evidence = detect_scale(text)
        scale, _ = _choose_scale(as_raw, header_scale, evidence)
        summary_periods = {p: {f: v * scale for f, v in fields.items()}
                          for p, fields in raw_periods.items()}
        current = next(iter(summary_periods.values()), {}) if summary_periods else {}
        prior = {}
        ok, why = _reconciles(current)
        provenance = {}

    # If the statement page did not reconcile, a summary page may still carry a
    # usable multi-year total set, so extract it too rather than returning nothing.
    if page_type == "statement" and summ_pages and ok is not True:
        try:
            s_text = ocr_pages(pdf_bytes, expand_pages(summ_pages[:1], span=0),
                              preprocess=preprocess)
            raw_periods = parse_multi_period(s_text)
            if raw_periods:
                as_raw = {f: {"raw": v} for f, v in
                         next(iter(raw_periods.values())).items()}
                hs, ev = detect_scale(s_text)
                s_scale, _ = _choose_scale(as_raw, hs, ev)
                summary_periods = {p: {f: v * s_scale for f, v in fields.items()}
                                  for p, fields in raw_periods.items()}
        except Exception as e:
            log.info("summary page extraction skipped", ticker=ticker,
                     error=str(e)[:70])

    if ok is not True:
        log.info("targeted extraction did not reconcile", ticker=ticker,
                 reason=why, basis=basis, pages=pages, page_type=page_type,
                 fields={k: round(v) for k, v in current.items()})

    return {"located": located, "ocr_pages": pages, "basis": basis, "scale": scale,
            "page_type": page_type, "n_columns": n_cols,
            "current": current, "prior": prior,
            "summary_periods": summary_periods,
            "periods": merge_periods(statement_periods, summary_periods),
            "reconciled": ok, "reason": why, "provenance": provenance,
            "text_chars": len(text)}
