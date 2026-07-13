"""
intelligence/search.py — DuckDuckGo search as supplementary data source.

Used for structured data queries via DuckDuckGo (no external AI).
Timeout: 8 seconds. Never raises — always returns (data_or_empty, error_or_none).
"""

import time
import json
import hashlib
import threading
import urllib.parse
from typing import List, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import structlog

log = structlog.get_logger()

SEARCH_TIMEOUT = 8
MAX_RESULTS    = 10

DDGR_URL = "https://api.duckduckgo.com/"
HEADERS  = {
    "User-Agent": "SashaOps Intelligence/1.0 (business research tool)",
    "Accept": "application/json",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = "duckduckgo"


def search(
    query: str,
    max_results: int = MAX_RESULTS,
) -> Tuple[List[SearchResult], Optional[str]]:
    """
    Search DuckDuckGo for a query.

    Returns: (results, error_or_none)
    Never raises — any exception is caught and returned as an error string.
    """
    try:
        params = {
            "q":      query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
            "no_redirect": "1",
        }

        start    = time.time()
        response = requests.get(
            DDGR_URL,
            params=params,
            headers=HEADERS,
            timeout=SEARCH_TIMEOUT,
        )
        elapsed  = int((time.time() - start) * 1000)

        if response.status_code != 200:
            return [], f"HTTP {response.status_code}"

        data    = response.json()
        results = []

        # DuckDuckGo instant answer
        abstract = data.get("AbstractText", "").strip()
        abstract_url = data.get("AbstractURL", "")
        if abstract:
            results.append(SearchResult(
                title=data.get("Heading", query),
                url=abstract_url,
                snippet=abstract[:400],
            ))

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(SearchResult(
                    title=topic.get("Text", "")[:100],
                    url=topic.get("FirstURL", ""),
                    snippet=topic.get("Text", "")[:400],
                ))

        log.info(
            "DDG search complete",
            query=query[:50],
            results=len(results),
            duration_ms=elapsed,
        )
        return results[:max_results], None

    except requests.Timeout:
        log.warning("DDG search timed out", query=query[:50])
        return [], "Search timed out"
    except Exception as e:
        log.warning("DDG search error", error=str(e), query=query[:50])
        return [], str(e)


def multi_search(queries: List[str], max_per_query: int = 5) -> List[SearchResult]:
    """
    Run multiple searches in parallel and combine results.
    Each query fires concurrently — total time = slowest single query, not the sum.
    """
    if not queries:
        return []

    all_results: List[SearchResult] = []
    seen_urls: set = set()
    lock = threading.Lock()

    def _fetch(query: str) -> List[SearchResult]:
        results, _ = search(query, max_results=max_per_query)
        return results or []

    with ThreadPoolExecutor(max_workers=min(len(queries), 5)) as pool:
        futures = {pool.submit(_fetch, q): q for q in queries}
        for fut in as_completed(futures):
            try:
                for r in fut.result():
                    with lock:
                        if r.url not in seen_urls:
                            seen_urls.add(r.url)
                            all_results.append(r)
            except Exception:
                pass  # individual query failures are non-fatal

    return all_results


def format_search_results(results: List[SearchResult]) -> str:
    """Format search results as a text block for AI prompt injection."""
    if not results:
        return "No supplementary search results available."

    lines = [f"SUPPLEMENTARY WEB DATA ({len(results)} results)\n"]
    for r in results:
        lines.append(f"[{r.title}]\n{r.snippet}\nSource: {r.url}\n")

    return "\n".join(lines)
