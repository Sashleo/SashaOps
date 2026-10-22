"""database/backup.py — Encrypted database backup, restore and prune.

Backup strategy: WAL checkpoint (flush the WAL back into the main file) then
file copy.  SQLCipher keeps the encryption key outside the file, so the copy
is always encrypted at rest.  No plaintext ever touches disk.

Backups live in /app/data/backups by default (same Docker volume as the DB).
Each backup is accompanied by a .sha256 sidecar.  Up to 14 backups are kept;
older ones are pruned automatically after each new backup.
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
    db_path: Optional[str] = None,
    backup_dir: Optional[str] = None,
) -> Path:
    """Flush the WAL, copy the encrypted DB file, write a SHA-256 sidecar.

    Returns the Path of the new backup.  Raises on I/O failure.
    """
    from config.settings import settings
    from database.connection import get_db

    db_path = db_path or settings.DATABASE_PATH
    backup_dir = backup_dir or _DEFAULT_BACKUP_DIR

    Path(backup_dir).mkdir(parents=True, exist_ok=True)

    db = get_db()
    db.execute("PRAGMA wal_checkpoint(FULL)")

    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = Path(backup_dir) / f"sashaops_{ts}.db"

    shutil.copy2(db_path, dest)

    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    dest.with_suffix(".db.sha256").write_text(sha)

    log.info("database backup created", path=str(dest), sha256_prefix=sha[:16])
    _prune_backups(backup_dir)
    return dest


def restore_database(
    backup_path: str,
    db_path: Optional[str] = None,
) -> None:
    """Verify the SHA-256 sidecar, snapshot the live DB, then overwrite it.

    Call this from a CLI tool or management script only — the running
    pysqlcipher3 connection is NOT aware of the swap.  Restart the container
    after restoring.
    """
    from config.settings import settings

    db_path = db_path or settings.DATABASE_PATH

    backup = Path(backup_path)
    sha_file = backup.with_suffix(".db.sha256")
    if sha_file.exists():
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

def _prune_backups(backup_dir: str, keep: int = _KEEP_LAST) -> None:
    """Delete backup files beyond the keep limit, oldest first."""
    p     = Path(backup_dir)
    files = sorted(p.glob("sashaops_*.db"), reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".db.sha256").unlink(missing_ok=True)
        log.info("pruned old backup", path=str(old))
