"""
intelligence/psx_report_parser.py — Parse PSX-linked annual/financial report PDFs.

PSX's company page only publishes a summary (Sales, Profit after Tax, EPS). The full
balance sheet — needed for liquidity, leverage, Altman Z and ROE — lives in the annual
report PDF that PSX links under company announcements. This module:

  1. Finds the most recent annual/financial-results PDF for a ticker (from PSX announcements)
  2. Downloads it (cached to .tmp/psx_reports/)
  3. Extracts balance-sheet + P&L line items via pdfplumber text parsing
  4. Returns a dict keyed for financial_model.analyse_from_document()

Pure Python, no AI. Never raises — returns {} / None on any failure.
"""

from __future__ import annotations

import io
import json
import re
import threading
from datetime import date
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()

CACHE_DIR = Path(".tmp/psx_reports")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
}
MAX_PAGES = 80          # annual reports are long; cap pages scanned
OCR_MAX_PAGES = 40      # OCR is slow — cap pages when falling back to OCR
OCR_DPI = 200
DOWNLOAD_TIMEOUT = 20   # hard cap on the annual-report PDF download
MIN_TEXT_LAYER_CHARS = 400   # below this, treat the PDF as scanned and OCR it

# Line-item label aliases → analyse_from_document key. Order matters: the first
# matching label on a line wins. Pakistani statement terminology included.
_LABELS: list[tuple[str, list[str]]] = [
    # Revenue — industrial: net sales/turnover; bank: total income / mark-up earned;
    # insurance: net premium income; NBFC / leasing: total income.
    ("revenue",             ["net sales", "net revenue", "revenue from contracts",
                             "revenue", "turnover", "sales",
                             # Banking / financial institutions
                             "total income", "net interest income", "interest income",
                             "mark-up / return / interest earned", "mark-up earned",
                             "net markup income", "total revenue",
                             "net income from operations", "operating revenue",
                             # Insurance
                             "net premium income", "net premiums earned",
                             "gross premium income",
                             # Misc financial services
                             "fee and commission income", "income from operations",
                             "total operating income"]),
    ("gross_profit",        ["gross profit",
                             # Banks: net interest income after provisions often acts as gross
                             "net interest income after provisions",
                             "net spread income"]),
    ("ebit",                ["profit from operations", "operating profit",
                             "earnings before interest and tax", "ebit",
                             # Banks report pre-tax profit as their primary metric
                             "profit before taxation", "profit before tax",
                             "profit before provision and taxation"]),
    ("net_income",          ["profit after taxation", "profit for the year",
                             "profit after tax", "net profit for the year",
                             "net income", "net profit", "net earnings",
                             "profit/(loss) for the year", "profit / loss for the year",
                             "profit attributable to shareholders",
                             "profit after tax (pat)"]),
    ("total_assets",        ["total assets"]),
    ("current_assets",      ["total current assets", "current assets"]),
    ("current_liabilities", ["total current liabilities", "current liabilities"]),
    ("total_liabilities",   ["total liabilities",
                             "total non-current liabilities and current liabilities",
                             # Banks: deposits dominate liabilities
                             "total liabilities and equity"]),
    ("shareholders_equity", ["total equity", "total shareholders' equity",
                             "total shareholders equity", "shareholders' equity",
                             "total share holders equity", "total stockholders equity",
                             "owners equity", "net assets",
                             "share capital and reserves", "capital and reserves",
                             "equity attributable to owners of the bank",
                             "total equity attributable to shareholders"]),
    ("retained_earnings",   ["unappropriated profit", "unappropriated profits",
                             "retained earnings", "accumulated profit"]),
    ("total_debt",          ["long term financing", "long-term financing",
                             "long term borrowings", "long-term borrowings",
                             "long term loans", "borrowings", "long-term debt",
                             "total borrowings",
                             # Banks: deposits are their primary liability (used as debt proxy)
                             "deposits and other accounts", "customer deposits",
                             "bills payable and deposits"]),
    ("cash",                ["cash and bank balances", "cash and cash equivalents",
                             "cash & bank balances", "cash and equivalents",
                             # Banks hold central bank reserves / nostro balances
                             "cash and balances with treasury banks",
                             "balances with other banks", "cash and due from banks"]),
    ("inventory",           ["stock in trade", "stock-in-trade", "inventories",
                             "inventory"]),
    ("receivables",         ["trade debts", "trade receivables", "accounts receivable",
                             "trade and other receivables",
                             # Banks: advances / loans are their primary receivable
                             "advances - net of provision", "net advances",
                             "loans and advances", "advances to customers"]),
    ("payables",            ["trade and other payables", "trade payables",
                             "creditors, accrued", "accounts payable"]),
    ("interest_expense",    ["finance cost", "finance costs", "interest expense",
                             "interest charges", "interest paid",
                             # Banks: mark-up / interest expensed on deposits
                             "mark-up / return / interest expensed",
                             "mark-up expensed", "interest expensed"]),
    ("depreciation",        ["depreciation and amortisation", "depreciation & amortization",
                             "depreciation and amortization", "depreciation"]),
    ("ocf",                 ["net cash from operating activities",
                             "cash generated from operations",
                             "net cash used in operating activities"]),
    ("capex",               ["purchase of property, plant and equipment",
                             "additions to property, plant and equipment",
                             "capital expenditure", "capex",
                             "acquisition of fixed assets"]),
    # Additional generic labels for simplified quarterly / audit reports
    ("net_income",          ["net profit", "profit for the period",
                             "profit attributable to equity holders",
                             "profit / (loss) for the period"]),
    ("ebit",                ["operating income", "income from operations",
                             "profit before finance cost"]),
    ("shareholders_equity", ["equity", "total equity attributable to equity holders",
                             "net assets", "proprietors equity", "owners equity",
                             "retained earnings and reserves"]),
    ("revenue",             ["gross sales", "gross revenue", "net sales (local)",
                             "net sales (export)", "total net sales"]),
    ("cash",                ["cash & bank balances", "cash in hand and at bank",
                             "cash and short-term investments"]),
    ("receivables",         ["net receivables", "sundry debtors",
                             "receivables from customers"]),
    ("payables",            ["sundry creditors", "payables to suppliers",
                             "trade and other creditors"]),
    ("interest_expense",    ["mark-up expense", "interest on borrowings",
                             "profit on financing", "bank charges and commission"]),
    ("depreciation",        ["amortisation", "amortization",
                             "depreciation on right-of-use assets"]),
    ("inventory",           ["finished goods", "raw materials", "work in progress",
                             "stores, spares and loose tools"]),
]

# A financial figure: thousands-separated, or a plain run of >=4 digits. Parentheses
# denote negatives. Note-reference columns (small 1-3 digit ints) are skipped.
_NUM = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d{4,}(?:\.\d+)?\)?")


def fetch_psx_report_financials(ticker: str) -> Optional[dict]:
    """
    Return a dict of balance-sheet + P&L items parsed from the latest PSX-linked
    annual/financial report PDF, keyed for analyse_from_document(). None on failure.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    cached = _load_cache(ticker)
    if cached is not None:
        return cached or None   # cached {} means "tried, found nothing"

    url = find_latest_report_url(ticker)
    if not url:
        _save_cache(ticker, {})
        return None

    pdf_bytes = _download(url, ticker)
    if not pdf_bytes:
        _save_cache(ticker, {})
        return None

    # Fast path (sub-second): digital PDFs via pdfplumber. Safe in the request.
    from intelligence.pdf_extractor import extract_financials
    res = extract_financials(pdf_bytes, ocr=False)
    doc = res.get("financials") or {}
    if doc:
        doc["_method"] = res.get("method", "none")
        _save_cache(ticker, doc)
        log.info("PSX report parsed", ticker=ticker, fields=sorted(doc.keys()),
                 method=doc["_method"], url=url)
        return doc

    # Scanned PDF (no text layer): OCR takes minutes and must never block the request
    # (the caller has a hard timeout). Run it in the background; it caches the result
    # so the next analysis is instant. This run falls back to the psx_summary source.
    _schedule_background_ocr(ticker, pdf_bytes, url)
    return None


# ── Background OCR: keeps the slow Tesseract/OpenCV chain off the request path ──
_ocr_lock    = threading.Lock()
_ocr_pending: set[str] = set()


def _schedule_background_ocr(ticker: str, pdf_bytes: bytes, url: str) -> None:
    """OCR a scanned report off-thread and cache the result. At most one job per
    ticker at a time; the per-day file cache prevents re-OCR on later analyses."""
    with _ocr_lock:
        if ticker in _ocr_pending:
            return
        _ocr_pending.add(ticker)

    def _worker():
        try:
            from intelligence.pdf_extractor import extract_financials
            res    = extract_financials(pdf_bytes, ocr=True)
            doc    = res.get("financials") or {}
            method = res.get("method", "none")
            if doc:
                doc["_method"] = method
                if method.startswith(("tesseract", "paddle")):
                    doc["_ocr"] = True
            _save_cache(ticker, doc)   # cache even when empty: no re-OCR today
            log.info("Background OCR complete", ticker=ticker, fields=len(doc),
                     method=method, url=url)
        except Exception as e:
            log.warning("Background OCR failed", ticker=ticker, error=str(e)[:120])
            _save_cache(ticker, {})    # cache empty so we do not retry all day
        finally:
            with _ocr_lock:
                _ocr_pending.discard(ticker)

    threading.Thread(target=_worker, name=f"ocr-{ticker}", daemon=True).start()
    log.info("Scanned PDF - OCR scheduled in background", ticker=ticker)


def find_latest_report_url(ticker: str) -> Optional[str]:
    """Pick the most recent annual/financial-results PDF from PSX announcements."""
    try:
        from intelligence.exchange_crawlers.psx import fetch_psx_company
        data = fetch_psx_company(ticker, fetch_types=["announcements"])
    except Exception:
        return None
    anns = (data or {}).get("financial_announcements") or []
    candidates = []
    for a in anns:
        title = (a.get("title") or "").lower()
        url = a.get("pdf_url")
        if not url:
            continue
        score = 0
        if "annual" in title:                          score += 3
        if "financial" in title or "accounts" in title: score += 2
        if "result" in title:                           score += 1
        if score:
            candidates.append((score, a.get("date", ""), url))
    if not candidates:
        # Fall back to the first announcement that has a PDF
        for a in anns:
            if a.get("pdf_url"):
                return a["pdf_url"]
        return None
    # Prefer highest score, then most recent date
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2]


def _parse_financials(text: str) -> dict:
    """Extract line items from report text. Returns {key: value} (PKR units as printed)."""
    if not text:
        return {}

    out: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        for key, aliases in _LABELS:
            if key in out:
                continue
            for alias in aliases:
                if low.startswith(alias) or (alias in low and low.index(alias) < 40):
                    val = _first_figure(line)
                    if val is not None:
                        out[key] = val
                    break
    return out


# ── PDF + number helpers ──────────────────────────────────────────────────────

def _pdf_text(pdf_bytes: bytes) -> tuple:
    """
    Return (text, used_ocr). Tries the PDF text layer first; if the report is a
    scanned image (little/no text), falls back to OCR (Tesseract). OCR is bounded
    by OCR_MAX_PAGES and only runs when the text layer is essentially empty.
    """
    text = ""
    try:
        import pdfplumber
        chunks = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                t = page.extract_text() or ""
                if t:
                    chunks.append(t)
        text = "\n".join(chunks)
    except Exception as e:
        log.info("PDF text extraction failed", error=str(e)[:100])

    if len(text) >= MIN_TEXT_LAYER_CHARS:
        return text, False

    ocr_text = _ocr_pdf(pdf_bytes)
    if len(ocr_text) > len(text):
        return ocr_text, True
    return text, False


def _ocr_pdf(pdf_bytes: bytes) -> str:
    """OCR a scanned PDF with Tesseract. Bounded by OCR_MAX_PAGES. Returns "" on failure."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception as e:
        log.info("OCR libraries unavailable", error=str(e)[:80])
        return ""
    try:
        images = convert_from_bytes(
            pdf_bytes, dpi=OCR_DPI, first_page=1, last_page=OCR_MAX_PAGES, fmt="png",
        )
    except Exception as e:
        log.info("PDF→image conversion failed (poppler?)", error=str(e)[:100])
        return ""

    parts = []
    for img in images:
        try:
            parts.append(pytesseract.image_to_string(img) or "")
        except Exception as e:
            log.info("OCR page failed", error=str(e)[:80])
    log.info("OCR completed", pages=len(images), chars=sum(len(p) for p in parts))
    return "\n".join(parts)


def _first_figure(line: str) -> Optional[float]:
    """Return the first financial figure on a line (current-year column)."""
    m = _NUM.search(line)
    if not m:
        return None
    tok = m.group(0)
    neg = tok.startswith("(") or tok.startswith("-")
    tok = tok.strip("()").replace(",", "").lstrip("-")
    try:
        v = float(tok)
    except ValueError:
        return None
    return -v if neg else v


def _download(url: str, ticker: str) -> Optional[bytes]:
    path = CACHE_DIR / f"{ticker}_{date.today().isoformat()}.pdf"
    if path.exists():
        try:
            return path.read_bytes()
        except Exception:
            pass
    try:
        import requests
        r = requests.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT, verify=False)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return r.content
    except Exception as e:
        log.info("PSX report download failed", ticker=ticker, error=str(e)[:100])
    return None


def _cache_json(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker}_{date.today().isoformat()}.json"


def _load_cache(ticker: str) -> Optional[dict]:
    p = _cache_json(ticker)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cache(ticker: str, doc: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_json(ticker).write_text(json.dumps(doc), encoding="utf-8")
    except Exception:
        pass
