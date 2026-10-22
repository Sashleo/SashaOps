"""database/backup.py — Encrypted database backup, restore and prune.

Backup strategy: VACUUM INTO on the live keyed connection.  SQLCipher writes
an encrypted copy of the database atomically — no WAL checkpoint needed, no
plaintext ever touches disk.

Backups live in /app/data/backups by default (same Docker volume as the DB).
Each backup is accompanied by a .sha256 sidecar.  Up to 14 backups are kept;
older ones are pruned automatically after each new backup.

If BACKUP_COPY_DIR is set, each backup and its sidecar are also copied there.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger()

_DEFAULT_BACKUP_DIR = "/app/data/backups"
_KEEP_LAST = 14


# ── Public API ────────────────────────────────────────────────────────────────

def backup_database(
    db_path: Optional[str] = None,  # unused with VACUUM INTO; kept for API compat
    backup_dir: Optional[str] = None,
) -> Path:
    """Create an encrypted backup with VACUUM INTO, write a SHA-256 sidecar.

    Returns the Path of the new backup.  Raises on I/O failure.
    """
    from config.settings import settings
    from database.connection import get_db

    backup_dir = backup_dir or _DEFAULT_BACKUP_DIR

    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = Path(backup_dir) / f"sashaops_{ts}.db"

    db = get_db()
    safe_dest = str(dest).replace("'", "''")
    db.execute(f"VACUUM INTO '{safe_dest}'")

    sha     = hashlib.sha256(dest.read_bytes()).hexdigest()
    sidecar = dest.with_suffix(".db.sha256")
    sidecar.write_text(sha)

    log.info("database backup created", path=str(dest), sha256_prefix=sha[:16])

    copy_dir = settings.BACKUP_COPY_DIR
    if copy_dir:
        _copy_to_secondary(dest, sidecar, copy_dir)

    _prune_backups(backup_dir)
    return dest


def restore_database(
    backup_path: str,
    db_path: Optional[str] = None,
) -> None:
    """Verify the SHA-256 sidecar, snapshot the live DB, then overwrite it.

    Raises ValueError if the .sha256 sidecar is missing, the SHA-256 does not
    match, or PRAGMA integrity_check fails on the restored file.

    Call this from a CLI tool or management script only — the running
    pysqlcipher3 connection is NOT aware of the swap.  Restart the container
    after restoring.
    """
    from config.settings import settings

    db_path  = db_path or settings.DATABASE_PATH
    password = settings.DB_MASTER_PASSWORD

    backup   = Path(backup_path)
    sha_file = backup.with_suffix(".db.sha256")

    if not sha_file.exists():
        raise ValueError(
            f"SHA-256 sidecar not found: {sha_file} — cannot verify backup integrity"
        )

    expected = sha_file.read_text().strip()
    actual   = hashlib.sha256(backup.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(
            f"SHA-256 mismatch — backup may be corrupt.\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}"
        )

    live = Path(db_path)
    if live.exists():
        snap = live.with_suffix(
            f".pre_restore_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
        )
        shutil.copy2(live, snap)
        log.info("pre-restore snapshot saved", path=str(snap))

    shutil.copy2(backup_path, db_path)
    _verify_restored_db(db_path, password)
    log.info("database restored", from_backup=backup_path, db_path=db_path)


def list_backups(backup_dir: Optional[str] = None) -> list[dict]:
    """Return backup metadata sorted newest-first."""
    backup_dir = backup_dir or _DEFAULT_BACKUP_DIR
    p = Path(backup_dir)
    if not p.exists():
        return []
    files = sorted(p.glob("sashaops_*.db"), reverse=True)
    result = []
    for f in files:
        sha_file = f.with_suffix(".db.sha256")
        result.append({
            "path":    str(f),
            "name":    f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "sha256":  sha_file.read_text().strip() if sha_file.exists() else None,
            "mtime":   datetime.utcfromtimestamp(f.stat().st_mtime).isoformat() + "Z",
        })
    return result


def latest_backup(backup_dir: Optional[str] = None) -> Optional[dict]:
    """Return the most recent backup's metadata, or None."""
    backups = list_backups(backup_dir)
    return backups[0] if backups else None


# ── Internal ──────────────────────────────────────────────────────────────────

def _copy_to_secondary(dest: Path, sidecar: Path, copy_dir: str) -> None:
    Path(copy_dir).mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest, Path(copy_dir) / dest.name)
    shutil.copy2(sidecar, Path(copy_dir) / sidecar.name)
    log.info("backup copied to secondary location", copy_dir=copy_dir, name=dest.name)


def _verify_restored_db(db_path: str, password: str, _connect_fn=None) -> None:
    """Open the restored database and run PRAGMA integrity_check."""
    if _connect_fn is None:
        from pysqlcipher3 import dbapi2 as _pysql  # type: ignore[import]
        def _connect_fn(path: str):
            c = _pysql.connect(path)
            c.execute(f"PRAGMA key='{password}'")
            return c
    conn = _connect_fn(db_path)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    if rows != [("ok",)]:
        raise ValueError(f"PRAGMA integrity_check failed after restore: {rows!r}")


def _prune_backups(backup_dir: str, keep: int = _KEEP_LAST) -> None:
    """Delete backup files beyond the keep limit, oldest first."""
    p     = Path(backup_dir)
    files = sorted(p.glob("sashaops_*.db"), reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".db.sha256").unlink(missing_ok=True)
        log.info("pruned old backup", path=str(old))
