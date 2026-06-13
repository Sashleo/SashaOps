"""
reports/pdf_renderer.py — PDF generation using WeasyPrint.

Takes a rendered HTML string and converts it to PDF bytes.
"""

from typing import Optional, Tuple
import structlog

log = structlog.get_logger()

try:
    import weasyprint
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    log.warning("WeasyPrint not installed — PDF generation unavailable")


def render_pdf(html_content: str) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Convert an HTML string to PDF bytes.

    Returns: (pdf_bytes_or_none, error_or_none)
    """
    if not WEASYPRINT_AVAILABLE:
        return None, "WeasyPrint not installed. Run: pip install weasyprint"

    try:
        pdf = weasyprint.HTML(string=html_content).write_pdf()
        log.info("PDF rendered", size_kb=len(pdf) // 1024)
        return pdf, None
    except Exception as e:
        log.error("PDF render failed", error=str(e))
        return None, str(e)


def save_pdf(pdf_bytes: bytes, output_path: str) -> Tuple[bool, Optional[str]]:
    """Save PDF bytes to disk. Returns (success, error_or_none)."""
    try:
        import os
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)
        return True, None
    except Exception as e:
        return False, str(e)
