"""
core/scraper_dashboard.py — Gov-scraper status dashboard helpers.

Read-only helpers for the Data Sources card (status, previews) plus the background
task that refreshes all gov scrapers + feeds on demand. Split out of
routers/intelligence.py — logic unchanged.
"""

import asyncio

import structlog

log = structlog.get_logger()


def _scraper_data_preview(scraper_id: str, data: dict) -> list[tuple[str, str]]:
    """
    Return up to 6 (label, value) pairs for display in the Data Sources card.
    Each scraper's cache has a different shape — we pick the most readable fields.
    """
    items: list[tuple[str, str]] = []
    if not data or not isinstance(data, dict):
        return items

    def _fmt(v) -> str:
        if v is None:
            return "—"
        if isinstance(v, dict):
            val = v.get("value")
            unit = v.get("unit", "")
            period = v.get("period", "")
            if val is not None:
                return f"{val:,.2f}{' ' + unit if unit else ''}{' (' + period + ')' if period else ''}"
            return str(v)
        if isinstance(v, (int, float)):
            return f"{v:,.2f}"
        return str(v)[:60]

    if scraper_id == "worldbank_pak":
        label_map = {
            "gdp_growth": "GDP Growth",
            "inflation": "CPI Inflation",
            "unemployment": "Unemployment",
            "current_account_pct_gdp": "Current Account / GDP",
            "exports_pct_gdp": "Exports / GDP",
            "gdp_per_capita_usd": "GDP per Capita",
        }
        for key, label in label_map.items():
            if key in data:
                items.append((label, _fmt(data[key])))
    elif scraper_id.startswith("ministry") or scraper_id == "ministries":
        # ministry data: list of announcement dicts
        entries = data if isinstance(data, list) else data.get("announcements", [])
        for entry in entries[:5]:
            title = entry.get("title") or entry.get("heading") or ""
            date_ = entry.get("date", "")
            if title:
                items.append((date_[:10] if date_ else "—", title[:80]))
    elif scraper_id == "associations":
        entries = data if isinstance(data, list) else data.get("news", [])
        for entry in entries[:5]:
            title = entry.get("title") or entry.get("heading") or ""
            date_ = entry.get("date", "")
            if title:
                items.append((date_[:10] if date_ else "—", title[:80]))
    else:
        # Generic: show scalar values first, then regulatory_text snippets as fallback
        for k, v in data.items():
            if k in ("regulatory_text", "nlp_entities"):
                continue  # handle below
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                items.append((k.replace("_", " ").title(), _fmt(v)))
            elif isinstance(v, dict):
                items.append((k.replace("_", " ").title(), _fmt(v)))
            if len(items) >= 6:
                break

        # If few numeric values found, fill remaining slots with regulatory text snippets
        reg_text = data.get("regulatory_text", [])
        if isinstance(reg_text, list):
            for snippet in reg_text:
                if len(items) >= 6:
                    break
                if snippet and isinstance(snippet, str):
                    items.append(("Regulatory", snippet[:120]))

    return items[:6]


def _scraper_statuses() -> list[dict]:
    """
    Build status list for all registered gov scrapers and feeds.
    Cache files use date-suffix naming: {scraper_id}_YYYY-MM-DD.json
    Falls back to exact-match {scraper_id}.json for any scraper that writes that way.
    """
    from pathlib import Path
    from datetime import datetime, timezone
    import json

    statuses = []
    cache_dir = Path(".tmp/gov_cache")

    scraper_defs = [
        ("worldbank_pak",  "World Bank Pakistan"),
        ("sbp_monetary",   "SBP Monetary Policy"),
        ("ogra_prices",    "OGRA Petroleum Prices"),
        ("pbs_lsmi",       "PBS Manufacturing Index"),
        ("fbr_revenue",    "FBR Revenue Statistics"),
        ("nepra_power",    "NEPRA Power Sector"),
        ("pta_stats",      "PTA Telecom Statistics"),
        ("ministries",     "Ministry Policy Feeds"),
        ("associations",   "Industry Associations"),
    ]

    for scraper_id, label in scraper_defs:
        status: dict = {
            "id": scraper_id,
            "label": label,
            "last_run": None,
            "age_minutes": None,
            "record_count": None,
            "status": "never_run",
        }

        # Find the most recent matching file: exact name or date-suffixed variants
        candidates: list[Path] = []
        if cache_dir.exists():
            exact = cache_dir / f"{scraper_id}.json"
            if exact.exists():
                candidates.append(exact)
            candidates.extend(sorted(cache_dir.glob(f"{scraper_id}_*.json")))
            if scraper_id == "ministries":
                candidates.extend(sorted(cache_dir.glob("ministry_*.json")))
            elif scraper_id == "associations":
                candidates.extend(sorted(cache_dir.glob("assoc_*.json")))

        if candidates:
            cache_file = max(candidates, key=lambda p: p.stat().st_mtime)
            try:
                mtime = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc)
                age = (datetime.now(timezone.utc) - mtime).total_seconds() / 60
                status["last_run"] = mtime.strftime("%Y-%m-%d %H:%M")
                status["age_minutes"] = int(age)
                status["status"] = "ok" if age < 1440 else "stale"

                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    status["record_count"] = len(data)
                elif isinstance(data, dict):
                    status["record_count"] = len(data)
                # Build a short human-readable preview of extracted values
                status["preview"] = _scraper_data_preview(scraper_id, data)
            except Exception:
                status["status"] = "error"
        else:
            # No cache file — check scraper_runs DB for "partial" (ran, got nothing)
            try:
                from database.connection import get_db
                db = get_db()
                run_row = db.execute(
                    "SELECT status, completed_at, error_msg FROM scraper_runs "
                    "WHERE scraper_id = ? ORDER BY completed_at DESC LIMIT 1",
                    (scraper_id,),
                ).fetchone()
                if run_row:
                    status["status"] = "partial"  # ran but no extractable data
                    status["last_run"] = (run_row["completed_at"] or "")[:16].replace("T", " ")
                    status["error_msg"] = run_row["error_msg"] or ""
            except Exception:
                pass

        statuses.append(status)

    return statuses


async def _run_all_scrapers_background(region_id: str = "pakistan") -> None:
    """Background task: run all gov scrapers and feed fetchers."""
    import asyncio
    loop = asyncio.get_running_loop()

    try:
        from intelligence.gov_scrapers import get_scrapers_for_run
        scrapers = get_scrapers_for_run("general", include_ministries=True)

        async def _run_one(scraper):
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, scraper.fetch),
                    timeout=60,
                )
                log.info("Scraper complete", scraper=scraper.__class__.__name__)
            except Exception as e:
                log.warning("Scraper failed", scraper=scraper.__class__.__name__,
                            error=str(e)[:80])

        await asyncio.gather(*[_run_one(s) for s in scrapers])

        # Also refresh feeds
        try:
            from intelligence.feeds import fetch_feeds_for_region
            from core.feed_cache import set_cached_feeds
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: fetch_feeds_for_region(region_id)),
                timeout=90,
            )
            if result:
                set_cached_feeds(region_id, result)
                log.info("Feeds refreshed", region=region_id)
        except Exception as e:
            log.warning("Feed refresh failed", error=str(e)[:80])

    except Exception as e:
        log.error("Scraper refresh batch failed", error=str(e))
