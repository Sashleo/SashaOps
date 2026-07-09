"""
core/stage3.py — Stage 3 background worker: report generation.

Builds and signs the final encrypted report from the completed module analyses,
then marks the run complete. Split out of routers/intelligence.py — logic unchanged.
"""

import asyncio

import structlog

from config.settings import settings
from core.run_manager import (
    load_run, fail_run, check_cancelled, RunCancelled,
)

log = structlog.get_logger()


async def run_stage3(run_id, client_id, business_name, module_ids, client_key):
    """Stage 3: Generate report."""
    try:
        check_cancelled(run_id)
        from reports.builder import build_report
        from reports.signer import ensure_signing_key
        from security.crypto import derive_master_key
        from database.clients import get_client_salt

        from database.connection import get_db as _get_db
        _master_row = _get_db().execute("SELECT master_salt FROM users LIMIT 1").fetchone()
        master_key  = derive_master_key(settings.DB_MASTER_PASSWORD, bytes(_master_row["master_salt"])) if _master_row and _master_row["master_salt"] else b"\x00" * 32

        ensure_signing_key(master_key)

        record, err = build_report(
            client_id=client_id,
            business_name=business_name,
            module_ids=module_ids,
            client_key=client_key,
            master_key=master_key,
        )

        from core.run_manager import complete_run
        run_state = load_run(run_id)
        if run_state:
            complete_run(run_state)

        log.info("Stage 3 complete", run_id=run_id, report=record.get("id"))

    except (RunCancelled, asyncio.CancelledError):
        log.info("Stage 3 cancelled by user - aborting", run_id=run_id)
        raise
    except Exception as e:
        log.error("Stage 3 crashed", run_id=run_id, error=str(e), exc_info=True)
        fail_run(run_id, f"Stage 3 error: {e}")
