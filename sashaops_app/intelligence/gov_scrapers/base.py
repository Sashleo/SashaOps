"""
intelligence/gov_scrapers/base.py — Base class for Pakistan government scrapers.

Provides: 24h disk cache (.tmp/gov_cache), polite HTTP via requests, BeautifulSoup
parsing, numeric extraction helpers, and scraper_runs logging. Scrapers never raise —
fetch() returns dict | None and the run wrapper records the outcome.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

import structlog

from intelligence.scraper_base import BaseScraper, ScrapeResult, ScrapeStatus

log = structlog.get_logger()


def _is_network_error(exc: Exception) -> bool:
    """True when the source could not be reached (vs. reached but unparseable)."""
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "dns", "ssl", "proxy")):
        return True
    try:
        import requests
        return isinstance(exc, requests.exceptions.RequestException)
    except Exception:
        return False


CACHE_DIR = Path(".tmp/gov_cache")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_TIMEOUT = 8


class BasePakGovScraper(BaseScraper):
    """
    Pakistan government scraper. Implements the shared BaseScraper contract:
    fetch() returns a ScrapeResult and logs to scrape_log. Subclasses still
    implement scrape() -> dict and persist(dict) -> int; the parsed dict is
    carried on result.artifacts["data"] for callers that want it.
    """
    scraper_id: str = ""
    label: str = ""
    category: str = "regulator"
    default_freshness_hours: int = 720
    target_success_rate: float = 0.0
    cache_ttl_hours: int = 24
    source_url: str = ""

    # ── Public entry point ────────────────────────────────────────────────────
    def fetch(self) -> ScrapeResult:
        """Cache -> parse -> persist. Never raises. Returns ScrapeResult."""
        r = self.new_result(source_url=self.source_url or None)
        started_iso = r.started_at.isoformat()

        cached = self._load_cache()
        if cached is not None:
            r.artifacts = {"data": cached, "cached": True}
            r.method_used = "cache"
            status = ScrapeStatus.SUCCESS if cached else ScrapeStatus.NO_DATA
            return self.log(r.finish(status))

        data: Optional[dict] = None
        try:
            data = self.scrape()
        except Exception as e:
            err = str(e)[:300]
            log.info("Gov scraper failed", scraper=self.scraper_id, error=err)
            self._log_run("failed", 0, err, started_iso)
            status = (ScrapeStatus.UNREACHABLE
                      if _is_network_error(e) else ScrapeStatus.PARSER_FAILED)
            return self.log(r.finish(status, error_message=err))

        if not data:
            self._log_run("partial", 0, "", started_iso)
            return self.log(r.finish(
                ScrapeStatus.PARSER_FAILED,
                error_message="scrape() returned no data",
            ))

        rows = self._persist(data)
        self._save_cache(data)
        r.artifacts = {"data": data}
        r.rows_inserted = rows
        r.method_used = r.method_used or getattr(self, "method_used", None)
        self._log_run("success" if rows else "partial", rows, "", started_iso)
        return self.log(r.finish(
            ScrapeStatus.SUCCESS if rows else ScrapeStatus.PARTIAL
        ))

    def fetch_data(self) -> Optional[dict]:
        """Convenience for callers that only want the parsed dict."""
        return (self.fetch().artifacts or {}).get("data")

    # ── Subclasses implement these ────────────────────────────────────────────
    @abstractmethod
    def scrape(self) -> Optional[dict]:
        """Fetch + parse the source. Return a dict of data, or None."""

    def persist(self, data: dict) -> int:  # noqa: D401
        """Persist parsed data to DB. Override. Return rows written."""
        return 0

    def _persist(self, data: dict) -> int:
        try:
            return self.persist(data)
        except Exception as e:
            log.info("Gov scraper persist failed", scraper=self.scraper_id, error=str(e)[:120])
            return 0

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def http_get(self, url: str, timeout: int = REQUEST_TIMEOUT):
        import requests
        return requests.get(url, headers=HEADERS, timeout=timeout, verify=False)

    def get_text(self, url: str) -> Optional[str]:
        try:
            r = self.http_get(url)
            if r.status_code == 200:
                return r.text
        except Exception as e:
            log.info("gov http_get failed", scraper=self.scraper_id, url=url, error=str(e)[:80])
        return None

    def get_json(self, url: str) -> Optional[dict]:
        try:
            r = self.http_get(url)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log.info("gov get_json failed", scraper=self.scraper_id, url=url, error=str(e)[:80])
        return None

    def get_soup(self, url: str):
        html = self.get_text(url)
        if not html:
            return None
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml")

    # ── Extraction helpers ────────────────────────────────────────────────────
    @staticmethod
    def number_near(text: str, *keywords: str, window: int = 60) -> Optional[float]:
        """
        Find the first number appearing within `window` chars of any keyword.
        """
        if not text:
            return None
        low = text.lower()
        for kw in keywords:
            idx = low.find(kw.lower())
            if idx == -1:
                continue
            seg = text[idx: idx + window]
            m = re.search(r"(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?)", seg)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        return None

    @staticmethod
    def number_near_ctx(text: str, *keywords: str, window: int = 100):
        """
        Like number_near(), but also returns the RAW SNIPPET the number was parsed
        from. Provenance requires the snippet: when a source silently changes shape,
        the stored snippet is what lets a human see what we actually read.

        Returns (value, snippet) or (None, "").
        """
        if not text:
            return (None, "")
        low = text.lower()
        for kw in keywords:
            idx = low.find(kw.lower())
            if idx == -1:
                continue
            seg = text[idx: idx + window]
            m = re.search(r"(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?)", seg)
            if m:
                try:
                    return (float(m.group(1).replace(",", "")), " ".join(seg.split())[:300])
                except ValueError:
                    pass
        return (None, "")

    @staticmethod
    def extract_relevant_sentences(text: str, keywords: list[str], n: int = 6) -> list[str]:
        """
        Split text into sentences and return up to n sentences that contain
        any of the given keywords (case-insensitive). Requires no dependencies.
        """
        if not text:
            return []
        low_kws = [k.lower() for k in keywords]
        # Simple sentence split on . ? ! followed by space/newline
        sentences = re.split(r"(?<=[.?!])\s+", text)
        found: list[str] = []
        for sent in sentences:
            sent = sent.strip()
            if not sent or len(sent) < 15:
                continue
            low = sent.lower()
            if any(kw in low for kw in low_kws):
                found.append(sent[:300])
            if len(found) >= n:
                break
        return found

    def nlp_extract(self, text: str, domain_keywords: list[str]) -> dict:
        """
        NLP-assisted extraction. Returns:
          - 'regulatory_text': list of relevant sentences (always populated if text available)
          - spaCy MONEY/PERCENT entities found near domain keywords (if spaCy available)
        Falls back gracefully if spaCy is not loaded.
        """
        result: dict = {
            "regulatory_text": self.extract_relevant_sentences(text, domain_keywords),
        }
        try:
            import spacy
            nlp = spacy.load("en_core_web_sm")
            doc = nlp(text[:50000])  # cap for speed
            money_vals = []
            for ent in doc.ents:
                if ent.label_ in ("MONEY", "PERCENT", "CARDINAL"):
                    ctx = text[max(0, ent.start_char - 60): ent.end_char + 60]
                    low_ctx = ctx.lower()
                    if any(kw.lower() in low_ctx for kw in domain_keywords):
                        money_vals.append({
                            "value": ent.text,
                            "label": ent.label_,
                            "context": ctx.strip(),
                        })
            if money_vals:
                result["nlp_entities"] = money_vals[:10]
        except Exception:
            pass  # spaCy not loaded or model absent — carry on
        return result

    def get_press_release_text(self, press_url: str, max_releases: int = 3) -> str:
        """
        Fetch a press-release listing page and concatenate the text of the
        first `max_releases` linked articles. Returns empty string on failure.
        """
        soup = self.get_soup(press_url)
        if not soup:
            return ""
        texts: list[str] = []
        links = soup.find_all("a", href=True)
        seen: set[str] = set()
        for a in links:
            href = a["href"]
            if not href or href in seen:
                continue
            # Look for links that appear to be article/release links
            low = href.lower()
            if any(x in low for x in ("press", "release", "news", "article", "notif", "circular")):
                full = href if href.startswith("http") else f"{press_url.rstrip('/')}/{href.lstrip('/')}"
                sub_soup = self.get_soup(full)
                if sub_soup:
                    texts.append(sub_soup.get_text(" ", strip=True)[:3000])
                    seen.add(href)
                if len(texts) >= max_releases:
                    break
        return " ".join(texts)

    # ── Cache ─────────────────────────────────────────────────────────────────
    def _cache_path(self) -> Path:
        return CACHE_DIR / f"{self.scraper_id}_{date.today().isoformat()}.json"

    def _load_cache(self) -> Optional[dict]:
        p = self._cache_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _save_cache(self, data: dict) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_path().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    # ── Run logging ───────────────────────────────────────────────────────────
    def _log_run(self, status: str, rows: int, err: str, started: str) -> None:
        try:
            from database.connection import get_db
            db = get_db()
            db.execute(
                """INSERT INTO scraper_runs
                   (scraper_id, status, rows_inserted, rows_skipped, error_msg, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.scraper_id, status, rows, 0, err or None,
                 started, datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
        except Exception:
            pass
