"""pipeline/fetch.py — S2 Fetch: download, SHA-256 hash, and store Bronze PDFs."""
from __future__ import annotations

import hashlib
import io
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from pipeline.store import upsert_fetch_log, upsert_filing

log = structlog.get_logger()

# Module-level so tests can monkeypatch.
BRONZE_DIR = Path(".tmp/pipeline/bronze")

MAX_FILE_BYTES = 50 * 1024 * 1024   # 50 MB
DOWNLOAD_TIMEOUT = 30
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
}

# Doubles on every 429 received; resets when the process restarts (i.e. per run).
_delay_multiplier: float = 1.0


def fetch_one(db, candidate: dict) -> dict:
    """
    Download one candidate URL, hash it, and write a Bronze file + filings row.

    Returns {
        "ticker":     str,
        "url":        str,
        "outcome":    "stored" | "duplicate" | "already_fetched" |
                      "http_<code>" | "not_pdf" | "too_large" | "rate_limited",
        "doc_sha256": str | None,
        "file_path":  str | None,
        "pages":      int | None,
    }

    fetch_log is written for every attempt, including failures.
    already_fetched: the URL was attempted and succeeded (stored or duplicate) in
    an earlier run; no HTTP request is made.
    Failures (http_error, not_pdf, too_large, rate_limited) are retried on the next run.
    duplicate: a different URL delivered the same content (same hash). The new URL
               is recorded in fetch_log; filings keeps the original URL.
    """
    url    = candidate["url"]
    ticker = candidate["ticker"]
    base   = {"ticker": ticker, "url": url, "doc_sha256": None,
              "file_path": None, "pages": None}

    # ── Fast path: already succeeded for this URL in a prior run ─────────────
    # Only skip on stored/duplicate — failures are retried.
    prior = db.execute(
        "SELECT outcome, doc_sha256 FROM fetch_log "
        "WHERE url=? AND outcome IN ('stored', 'duplicate')",
        (url,)
    ).fetchone()
    if prior:
        log.info("S2 Fetch already_fetched", ticker=ticker, url=url[:80],
                 prior_outcome=prior[0])
        return {**base, "outcome": "already_fetched", "doc_sha256": prior[1]}

    now = datetime.now(timezone.utc).isoformat()

    # ── Download ──────────────────────────────────────────────────────────────
    pdf_or_err = _download(url)
    if isinstance(pdf_or_err, str):
        _log(db, url, ticker, now, pdf_or_err, None, None)
        return {**base, "outcome": pdf_or_err}
    pdf_bytes = pdf_or_err

    if len(pdf_bytes) > MAX_FILE_BYTES:
        _log(db, url, ticker, now, "too_large", None, len(pdf_bytes))
        log.info("S2 Fetch too_large", ticker=ticker, url=url[:80],
                 bytes=len(pdf_bytes))
        return {**base, "outcome": "too_large"}

    if pdf_bytes[:4] != b"%PDF":
        _log(db, url, ticker, now, "not_pdf", None, len(pdf_bytes))
        log.info("S2 Fetch not_pdf", ticker=ticker, url=url[:80])
        return {**base, "outcome": "not_pdf"}

    sha256    = hashlib.sha256(pdf_bytes).hexdigest()
    file_path = BRONZE_DIR / sha256[:2] / f"{sha256}.pdf"

    # ── Duplicate: same hash already in filings ───────────────────────────────
    existing = db.execute(
        "SELECT file_path FROM filings WHERE doc_sha256=?", (sha256,)
    ).fetchone()
    if existing:
        _log(db, url, ticker, now, "duplicate", sha256, len(pdf_bytes))
        log.info("S2 Fetch duplicate", ticker=ticker, sha256=sha256[:12],
                 url=url[:80])
        return {**base, "outcome": "duplicate", "doc_sha256": sha256,
                "file_path": existing[0]}

    # ── New file ──────────────────────────────────────────────────────────────
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(pdf_bytes)

    pages = _count_pages(pdf_bytes)
    upsert_filing(db,
        doc_sha256 = sha256,
        ticker     = ticker,
        source_url = url,
        ann_title  = (candidate.get("title") or "")[:200],
        ann_date   = candidate.get("ann_date", ""),
        file_path  = str(file_path),
        file_bytes = len(pdf_bytes),
        pages      = pages,
        fetched_at = now,
    )
    _log(db, url, ticker, now, "stored", sha256, len(pdf_bytes))
    db.commit()

    log.info("S2 Fetch stored", ticker=ticker, sha256=sha256[:12],
             bytes=len(pdf_bytes), pages=pages, url=url[:80])
    return {**base, "outcome": "stored", "doc_sha256": sha256,
            "file_path": str(file_path), "pages": pages}


def _log(db, url: str, ticker: str, attempted_at: str,
         outcome: str, sha256, nbytes) -> None:
    upsert_fetch_log(db,
        url          = url,
        ticker       = ticker,
        attempted_at = attempted_at,
        outcome      = outcome,
        doc_sha256   = sha256,
        bytes        = nbytes,
    )
    db.commit()


def _count_pages(pdf_bytes: bytes):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return len(pdf.pages)
    except Exception:
        return None


def _download(url: str) -> bytes | str:
    """
    Fetch url with up to 3 attempts and exponential back-off.

    HTTP 429 triggers an immediate wait (Retry-After or 60 s), doubles
    _delay_multiplier for all subsequent requests this run, then retries.
    Returns 'rate_limited' if all retries are consumed by 429 responses.
    """
    global _delay_multiplier
    import requests
    rate_limited_count = 0
    for attempt in range(3):
        delay = (2.0 + random.uniform(0, 1.0) + attempt * 2.0) * _delay_multiplier
        time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=DOWNLOAD_TIMEOUT,
                             verify=True)
            if r.status_code == 200:
                return r.content
            if r.status_code == 429:
                rate_limited_count += 1
                raw_retry = r.headers.get("Retry-After", "")
                try:
                    wait = max(int(raw_retry), 60)
                except (ValueError, TypeError):
                    wait = 60
                _delay_multiplier *= 2
                log.warning("S2 Fetch rate_limited", url=url[:80], wait=wait,
                            new_multiplier=_delay_multiplier)
                time.sleep(wait)
                continue
            log.info("S2 Fetch http_error", url=url[:80], status=r.status_code,
                     attempt=attempt)
            if r.status_code in (403, 404, 410):
                return f"http_{r.status_code}"
        except Exception as e:
            log.info("S2 Fetch network_error", url=url[:80],
                     error=str(e)[:80], attempt=attempt)
    return "rate_limited" if rate_limited_count else "http_error"
