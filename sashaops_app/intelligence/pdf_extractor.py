"""
intelligence/pdf_extractor.py — Financial-statement extraction from PDF bytes.

Chain-of-responsibility: each extractor exposes the same interface and is tried in
order until one yields at least 3 financial line items. This makes scanned PSX
reports (no text layer, Creator "Scan Assistant") extractable via OCR while keeping
the fast pdfplumber path for digital PDFs.

    extract(pdf_bytes) -> {"financials": {...}, "method": str, "confidence": 0-1}
                          (empty financials on failure)

All parsing reuses psx_report_parser._parse_financials + _LABELS so label coverage
stays in one place. OCR is CPU-blocking; callers already run this inside an executor
(stage1/stage2/markets), so no async is added here.
"""

from __future__ import annotations

import io
import os

import structlog

from intelligence.psx_report_parser import (
    _parse_financials, MAX_PAGES, OCR_MAX_PAGES, OCR_DPI,
)
from intelligence.statement_parser import parse_financials_accurate, quality_score

log = structlog.get_logger()

_MIN_FIELDS = 3


def _matched(fin: dict) -> int:
    return sum(1 for k, v in (fin or {}).items()
               if not k.startswith("_") and v is not None)


class PdfplumberTablesExtractor:
    """Digital PDFs: parse pdfplumber tables (fastest, highest confidence)."""
    method = "pdfplumber_tables"
    confidence = 0.9

    def extract(self, pdf_bytes: bytes) -> dict:
        import pdfplumber
        lines: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                for table in (page.extract_tables() or []):
                    for row in table:
                        cells = [str(c).strip() for c in row if c is not None]
                        if cells:
                            lines.append(" ".join(cells))
        fin = parse_financials_accurate("\n".join(lines))
        return {"financials": fin, "method": self.method, "confidence": self.confidence}


class PdfplumberTextExtractor:
    """Digital PDFs without clean tables: parse the text layer."""
    method = "pdfplumber_text"
    confidence = 0.7

    def extract(self, pdf_bytes: bytes) -> dict:
        import pdfplumber
        chunks: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                t = page.extract_text() or ""
                if t:
                    chunks.append(t)
        fin = parse_financials_accurate("\n".join(chunks))
        return {"financials": fin, "method": self.method, "confidence": self.confidence}


def _pdf_to_images(pdf_bytes: bytes, dpi: int):
    from pdf2image import convert_from_bytes
    return convert_from_bytes(pdf_bytes, dpi=dpi, first_page=1, last_page=OCR_MAX_PAGES, fmt="png")


# Per-page OCR cap. A single page must never hang the background job. A legitimate
# dense page finishes well under this; anything longer is a pathological render.
OCR_PAGE_TIMEOUT = 90


class TesseractRawExtractor:
    """Scanned PDFs: render at 200 DPI and OCR each page with Tesseract."""
    method = "tesseract_raw"
    confidence = 0.5

    def extract(self, pdf_bytes: bytes) -> dict:
        import pytesseract
        images = _pdf_to_images(pdf_bytes, OCR_DPI)
        parts = []
        for img in images:
            try:
                parts.append(pytesseract.image_to_string(img, timeout=OCR_PAGE_TIMEOUT) or "")
            except Exception as e:
                log.info("OCR page failed", error=str(e)[:80])
        text = "\n".join(parts)
        log.info("Scanned PDF detected - running OCR", method=self.method,
                 pages=len(images), chars=len(text))
        return {"financials": parse_financials_accurate(text), "method": self.method,
                "confidence": self.confidence}


class TesseractPreprocessedExtractor:
    """Scanned PDFs with poor quality: opencv preprocess (grayscale, deskew, Otsu,
    denoise) at 300 DPI, then Tesseract with a block-of-text page-segmentation mode.
    Requires opencv (opencv-python-headless); skips gracefully if opencv is absent."""
    method = "tesseract_preprocessed"
    confidence = 0.6

    def extract(self, pdf_bytes: bytes) -> dict:
        try:
            import cv2  # noqa: F401
            import numpy as np  # noqa: F401
        except Exception:
            log.info("opencv not installed - skipping preprocessed OCR extractor")
            return {}
        import pytesseract
        images = _pdf_to_images(pdf_bytes, 300)
        parts = []
        for img in images:
            try:
                parts.append(pytesseract.image_to_string(
                    self._preprocess(img), config="--psm 6", timeout=OCR_PAGE_TIMEOUT) or "")
            except Exception as e:
                log.info("preprocessed OCR page failed", error=str(e)[:80])
        text = "\n".join(parts)
        log.info("Scanned PDF detected - running OCR", method=self.method,
                 pages=len(images), chars=len(text))
        return {"financials": parse_financials_accurate(text), "method": self.method,
                "confidence": self.confidence}

    @staticmethod
    def _preprocess(pil_img, denoise: bool = True):
        import cv2
        import numpy as np
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
        # Deskew: estimate the dominant text angle from non-zero (dark) pixels.
        inv = cv2.bitwise_not(img)
        coords = cv2.findNonZero(inv)
        if coords is not None and len(coords) > 50:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            if abs(angle) > 0.3:
                h, w = img.shape
                M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
                img = cv2.warpAffine(img, M, (w, h),
                                     flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        # Denoise is optional: the method study showed fastNlMeansDenoising HURTS
        # accuracy more than it helps (it lost to raw OCR 15-2) and is the slow part.
        if denoise:
            img = cv2.fastNlMeansDenoising(img, h=10)
        _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return img


class TesseractDeskewExtractor:
    """Scanned PDFs: deskew + Otsu threshold at 300 DPI, but NO denoise. Tests the
    hypothesis that the denoise step (not the deskew) is what degraded accuracy."""
    method = "tesseract_deskew"
    confidence = 0.55

    def extract(self, pdf_bytes: bytes) -> dict:
        try:
            import cv2  # noqa: F401
            import numpy as np  # noqa: F401
        except Exception:
            return {}
        import pytesseract
        images = _pdf_to_images(pdf_bytes, 300)
        parts = []
        for img in images:
            try:
                pre = TesseractPreprocessedExtractor._preprocess(img, denoise=False)
                parts.append(pytesseract.image_to_string(
                    pre, config="--psm 6", timeout=OCR_PAGE_TIMEOUT) or "")
            except Exception as e:
                log.info("deskew OCR page failed", error=str(e)[:80])
        text = "\n".join(parts)
        log.info("Scanned PDF detected - running OCR", method=self.method,
                 pages=len(images), chars=len(text))
        return {"financials": parse_financials_accurate(text), "method": self.method,
                "confidence": self.confidence}


class PaddleOCRExtractor:
    """Optional, gated behind PADDLEOCR=true. Better table-structure recognition but
    heavy and slow on CPU. Not installed by default — enabling requires paddleocr +
    paddlepaddle (documented opt-in; not in requirements)."""
    method = "paddleocr"
    confidence = 0.7

    def extract(self, pdf_bytes: bytes) -> dict:
        try:
            from paddleocr import PaddleOCR
        except Exception:
            log.info("paddleocr not installed - skipping (set PADDLEOCR=true and install to enable)")
            return {}
        ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        images = _pdf_to_images(pdf_bytes, OCR_DPI)
        import numpy as np
        lines = []
        for img in images:
            for block in (ocr.ocr(np.array(img), cls=True) or []):
                for entry in (block or []):
                    try:
                        lines.append(entry[1][0])
                    except Exception:
                        pass
        return {"financials": parse_financials_accurate("\n".join(lines)), "method": self.method,
                "confidence": self.confidence}


def extract_financials(pdf_bytes: bytes, ocr: bool = True) -> dict:
    """
    Try each extractor in order; return the first result with >= 3 matched line items.
    Returns {"financials": {}, "method": "none", "confidence": 0.0} if all fail.

    ocr=False runs only the fast pdfplumber stages (digital PDFs, sub-second) and skips
    the slow Tesseract stages. Callers use this in the request path so a scanned PDF
    (minutes of OCR) never blocks; the OCR stages are run separately in the background.

    Escalating router, derived from a 131-document, 5-method study (tools/pdf_methods.py):

      TIER 1 (cheap, always): pdfplumber_text then pdfplumber_tables.
        - text beat tables 43-0 head to head, so text leads; tables is a tie-break.
      TIER 2 (OCR, only if tier 1 fails => scanned): tesseract_raw.
        - raw OCR turned pdfplumber's 1% into 73% on scanned reports.
      TIER 3 (only if raw is thin): tesseract_deskew (deskew-only, no denoise).
        - EVERY OpenCV preprocessing variant LOST to raw 15-2 on identical docs, so it
          is a last resort that only runs when raw returned too little. denoise is
          dropped entirely (same 2 wins as deskew but far slower).

    Escalation is the point: a digital PDF that tier 1 handles never pays for OCR, and
    a scanned PDF that raw handles never pays for the slow preprocessing stage.
    """
    best = None
    best_score = None

    def _consider(result) -> bool:
        """Fold a result into the running best. Returns True if it is authoritative
        (a reconciled balance sheet), meaning we can stop escalating."""
        nonlocal best, best_score
        if not (result and result.get("financials")):
            return False
        fin = result["financials"]
        n = _matched(fin)
        score = quality_score(fin)
        log.debug("Extractor result", method=result.get("method"), matched_fields=n,
                  balance_ok=fin.get("_balance_ok"), n_high=fin.get("_n_high"))
        if n >= _MIN_FIELDS and (best_score is None or score > best_score):
            best, best_score = result, score
        return bool(fin.get("_balance_ok") is True and n >= _MIN_FIELDS)

    def _run(extractor) -> bool:
        try:
            return _consider(extractor.extract(pdf_bytes))
        except Exception as e:
            log.warning("Extractor failed", method=extractor.__class__.__name__, error=str(e)[:120])
            return False

    def _best_n() -> int:
        return _matched(best["financials"]) if best else 0

    # ── Tier 1: cheap pdfplumber (text leads, tables tie-breaks) ──────────────
    if _run(PdfplumberTextExtractor()) or _run(PdfplumberTablesExtractor()):
        pass  # reconciled early

    # ── Tier 2: OCR, only when tier 1 could not read the document ─────────────
    if ocr and _best_n() < _MIN_FIELDS:
        authoritative = _run(TesseractRawExtractor())
        # ── Tier 3: preprocessing, ONLY if raw was still thin ────────────────
        if not authoritative and _best_n() < _MIN_FIELDS:
            _run(TesseractDeskewExtractor())
            if os.environ.get("PADDLEOCR") == "true":
                _run(PaddleOCRExtractor())

    if best is not None:
        log.info("Extraction succeeded", method=best["method"],
                 matched_fields=_matched(best["financials"]),
                 balance_ok=best["financials"].get("_balance_ok"))
        return best

    if ocr:
        log.warning("All extractors failed - no financial data")
    else:
        log.debug("No digital text layer - OCR required (deferred to background)")
    return {"financials": {}, "method": "none", "confidence": 0.0}
