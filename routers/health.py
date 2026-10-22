"""routers/health.py — Owner health dashboard (login-protected).

Shows every item from the production standard (section 11A.5):
  1. Database open
  2. Signing key present
  3. Audit chain verified
  4. Last pipeline run and its result  (N/A until M1)
  5. Stale data sources
  6. Review queue size               (N/A until M8)
  7. Disk use of Bronze              (N/A until M2)
  8. Last backup (bonus, always shown)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import structlog
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from database.clients import list_clients
from database.connection import get_db
from security.auth import require_session

log       = structlog.get_logger()
router    = APIRouter()
templates = Jinja2Templates(directory="templates")


def _sidebar(request: Request) -> dict:
    clients       = list_clients()
    client_id     = getattr(request.state, "client_id", None)
    active_client = next((c for c in clients if c["id"] == client_id), None)
    return {"clients": clients, "active_client": active_client}


@router.get("")
async def health_page(request: Request):
    require_session(request)

    db = get_db()

    # 1. Database
    db_open = False
    try:
        db.execute("SELECT 1")
        db_open = True
    except Exception:
        pass

    # 2. Signing key
    signing_key   = False
    signing_algo  = None
    try:
        row = db.execute(
            "SELECT algorithm FROM pqc_keys WHERE key_type='signing' LIMIT 1"
        ).fetchone()
        signing_key  = bool(row)
        signing_algo = row["algorithm"] if row else None
    except Exception:
        pass

    # 3. Audit chain
    audit = {"valid": False, "total": 0, "checked": 0, "reason": "not checked"}
    try:
        from security.audit import verify_chain_detailed
        audit = verify_chain_detailed(db)
    except Exception as e:
        audit["reason"] = str(e)[:120]

    # 4. Last backup
    backup = None
    try:
        from database.backup import latest_backup
        backup = latest_backup()
    except Exception:
        pass

    # 5. Stale sources
    stale: list[dict] = []
    try:
        from core.data_health import compute_data_health
        stale = [r for r in compute_data_health() if r["status"] in ("red", "amber")]
    except Exception:
        pass

    # 6. Bronze disk usage (M2 populates this directory)
    bronze_info = None
    try:
        bronze_path = "/app/data/bronze"
        if os.path.exists(bronze_path):
            total_bytes = sum(
                f.stat().st_size
                for f in Path(bronze_path).rglob("*")
                if f.is_file()
            )
            disk = shutil.disk_usage(bronze_path)
            bronze_info = {
                "size_mb":   round(total_bytes / (1024 * 1024), 1),
                "free_gb":   round(disk.free / (1024 ** 3), 1),
            }
    except Exception:
        pass

    return templates.TemplateResponse(request, "health.html", {
        "active_page":  "health",
        "db_open":      db_open,
        "signing_key":  signing_key,
        "signing_algo": signing_algo,
        "audit":        audit,
        "backup":       backup,
        "stale":        stale,
        "bronze_info":  bronze_info,
        **_sidebar(request),
    })
