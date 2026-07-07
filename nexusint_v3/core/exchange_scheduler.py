"""
core/exchange_scheduler.py — Background task for exchange data scraping.

Runs all scrapers on startup (after a 30s boot delay) then every 6 hours.
Each scraper writes directly to the DB and logs its run to scraper_runs.
Scrapers are staggered by SCRAPER_DELAY seconds to avoid hammering sources.
"""

import asyncio
import structlog

log = structlog.get_logger()

REFRESH_INTERVAL = 6 * 60 * 60   # 6 hours between full cycles
BOOT_DELAY       = 45             # Seconds after startup before first run
SCRAPER_DELAY    = 8              # Seconds between individual scrapers


async def run_exchange_scheduler() -> None:
    """
    Background task: keep all exchange index and forex rate data fresh.
    Called once at startup via asyncio.create_task().
    """
    await asyncio.sleep(BOOT_DELAY)   # Let feeds scheduler + app fully boot first
    log.info("Exchange scraper scheduler started")

    while True:
        await _run_all_scrapers()
        await _compute_sector_medians()
        log.info("Exchange scraper cycle complete, sleeping", hours=REFRESH_INTERVAL // 3600)
        await asyncio.sleep(REFRESH_INTERVAL)


async def _compute_sector_medians() -> None:
    """Recompute PSX sector-median benchmarks (M9) after each scrape cycle."""
    loop = asyncio.get_running_loop()
    try:
        from intelligence.sector_medians import compute_sector_medians
        rows = await loop.run_in_executor(None, compute_sector_medians)
        log.info("Sector medians refreshed", rows=rows)
    except Exception as e:
        log.warning("Sector median computation failed", error=str(e)[:120])


async def _run_all_scrapers() -> None:
    from intelligence.exchange_scrapers import get_all_scrapers
    scrapers = get_all_scrapers()
    loop = asyncio.get_running_loop()
    log.info("Exchange scraper cycle starting", total=len(scrapers))

    for scraper in scrapers:
        try:
            result = await loop.run_in_executor(None, scraper.run)
            log.info(
                "Exchange scraper done",
                scraper=result.scraper_id,
                status=result.status,
                inserted=result.rows_inserted,
                skipped=result.rows_skipped,
                error=result.error_msg or None,
            )
        except Exception as e:
            log.warning("Exchange scraper raised", scraper=scraper.scraper_id, error=str(e))
        await asyncio.sleep(SCRAPER_DELAY)
