"""
intelligence/browser.py — Playwright headless browser agent.

Used when web content requires JavaScript rendering or dynamic loading.
Primary use: scraping company websites, annual report pages, press releases.

Timeout: 20 seconds per page. On timeout: return empty with flag.
Never raises — always returns (content_or_none, error_or_none).
"""

import time
from typing import Optional, Tuple

import structlog

log = structlog.get_logger()

BROWSER_TIMEOUT_MS = 20000   # 20 seconds

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    log.warning("Playwright not installed - browser agent unavailable")


def fetch_page_text(url: str, wait_for: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch the text content of a web page using a headless browser.

    Args:
        url: The URL to fetch
        wait_for: Optional CSS selector to wait for before extracting text

    Returns:
        (page_text_or_none, error_or_none)
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None, "Playwright not installed"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (compatible; SashaOps Intelligence Bot/1.0)",
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.set_default_timeout(BROWSER_TIMEOUT_MS)

            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)

            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=5000)
                except Exception:
                    pass   # Continue even if selector not found

            # Extract visible text content
            text = page.evaluate("""() => {
                // Remove script, style, nav, footer tags
                const remove = document.querySelectorAll('script, style, nav, footer, header, iframe, aside');
                remove.forEach(el => el.remove());
                return document.body.innerText || document.body.textContent || '';
            }""")

            browser.close()

            if text:
                # Normalize whitespace
                import re
                text = re.sub(r'\n{3,}', '\n\n', text.strip())
                text = re.sub(r' {2,}', ' ', text)
                return text[:8000], None   # Cap at 8KB
            return None, "No text content extracted"

    except PlaywrightTimeout:
        log.warning("Browser timeout", url=url)
        return None, f"Page load timeout after {BROWSER_TIMEOUT_MS//1000}s"
    except Exception as e:
        log.warning("Browser fetch failed", url=url, error=str(e))
        return None, str(e)


def fetch_multiple_pages(urls: list[str]) -> dict[str, Optional[str]]:
    """
    Fetch multiple pages sequentially.
    Returns {url: text_or_none}.
    """
    results = {}
    for url in urls[:5]:   # Cap at 5 pages per run
        text, error = fetch_page_text(url)
        results[url] = text
        if error:
            log.warning("Page fetch error", url=url, error=error)
        time.sleep(1)  # Polite delay between requests
    return results


def extract_company_website_content(
    company_name: str,
    ticker: Optional[str] = None,
    psx_url: Optional[str] = None,
) -> Tuple[Optional[str], list, str, Optional[str]]:
    """
    Scrape a company's official website and derive industry signals.

    URL resolution order:
      1. psx_url (from the PSX company profile) — preferred for listed companies
      2. DuckDuckGo search for "{company} official website" — fallback

    After scraping, runs the deterministic keyword engine to detect industry and
    surface keywords (no AI).

    Returns: (page_text_or_none, keywords, industry, error_or_none)
    """
    from intelligence.keyword_engine import extract_keywords, detect_industry

    candidate_urls = []
    if psx_url and "http" in psx_url:
        candidate_urls.append(psx_url)

    if not candidate_urls:
        from intelligence.search import search
        results, error = search(f"{company_name} official website Pakistan", max_results=3)
        if error and not results:
            return None, [], "general", error or "No search results"
        for result in (results or [])[:3]:
            if result.url and "http" in result.url:
                candidate_urls.append(result.url)

    last_err = "Could not extract website content"
    for url in candidate_urls[:3]:
        content, err = fetch_page_text(url)
        if content:
            industry = detect_industry(content)
            keywords = extract_keywords(content)
            return content, keywords, industry, None
        last_err = err or last_err

    return None, [], "general", last_err
