"""pipeline/fetch.py — S2 Fetch: download, SHA-256 hash, and store Bronze PDFs."""
from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()

# Module-level so tests can monkeypatch it.
BRONZE_DIR = Path(".tmp/pipeline/bronze")

MAX_FILE_BYTES = 50 * 1024 * 1024   # 50 MB — a 400-page colour annual report is ~20 MB
DOWNLOAD_TIMEOUT = 30
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
}


def fetch_one(db, candidate: dict) -> dict:
    """
    Download one candidate URL, hash it, and write a Bronze file + filings row.

    Returns {
        "ticker":     str,
        "url":        str,
        "outcome":    "stored" | "duplicate" | "http_<code>" | "not_pdf" | "too_large",
        "doc_sha256": str | None,
        "file_path":  str | None,
    }
    A "duplicate" means the hash already exists in filings; the new URL is NOT
    stored again — only the first URL per hash is kept.  A second run on the same
    ticker returns "duplicate" for every previously-fetched file, so the fetch log
    is safe to re-run idempotently.
    """
    url = candidate["url"]
    ticker = candidate["ticker"]
    base = {"ticker": ticker, "url": url, "doc_sha256": None, "file_path": None}

    pdf_or_err = _download(url)
    if isinstance(pdf_or_err, str):
        return {**base, "outcome": pdf_or_err}
    pdf_bytes = pdf_or_err

    if len(pdf_bytes) > MAX_FILE_BYTES:
        log.info("S2 Fetch too_large", ticker=ticker, url=url[:80],
                 bytes=len(pdf_bytes))
        return {**base, "outcome": "too_large"}

    if pdf_bytes[:4] != b"%PDF":
        log.info("S2 Fetch not_pdf", ticker=ticker, url=url[:80],
                 first_bytes=pdf_bytes[:20])
        return {**base, "outcome": "not_pdf"}

    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    file_path = BRONZE_DIR / sha256[:2] / f"{sha256}.pdf"

    # Check for duplicate by hash (not URL — same PDF may arrive from two URLs).
    existing = db.execute(
        "SELECT file_path FROM filings WHERE doc_sha256=?", (sha256,)
    ).fetchone()
    if existing:
        log.info("S2 Fetch duplicate", ticker=ticker, sha256=sha256[:12],
                 url=url[:80])
        return {**base, "outcome": "duplicate", "doc_sha256": sha256,
                "file_path": existing[0]}

    # New file.
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(pdf_bytes)

    from pipeline.store import upsert_filing
    now = datetime.now(timezone.utc).isoformat()
    upsert_filing(db,
        doc_sha256 = sha256,
        ticker     = ticker,
        source_url = url,
        ann_title  = (candidate.get("title") or "")[:200],
        ann_date   = candidate.get("ann_date", ""),
        file_path  = str(file_path),
        file_bytes = len(pdf_bytes),
        fetched_at = now,
    )
    db.commit()

    log.info("S2 Fetch stored", ticker=ticker, sha256=sha256[:12],
             bytes=len(pdf_bytes), url=url[:80])
    return {**base, "outcome": "stored", "doc_sha256": sha256,
            "file_path": str(file_path)}


def _download(url: str) -> bytes | str:
    """
    Fetch url with up to 3 attempts and exponential back-off.
    Returns bytes on success or a string outcome tag on permanent failure.
    """
    import requests
    for attempt in range(3):
        delay = 2.0 + random.uniform(0, 1.0) + attempt * 2.0
        time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=DOWNLOAD_TIMEOUT,
                             verify=True)
            if r.status_code == 200:
                return r.content
            log.info("S2 Fetch http_error", url=url[:80], status=r.status_code,
                     attempt=attempt)
            if r.status_code in (403, 404, 410):
                return f"http_{r.status_code}"
        except Exception as e:
            log.info("S2 Fetch network_error", url=url[:80],
                     error=str(e)[:80], attempt=attempt)
    return "http_error"
