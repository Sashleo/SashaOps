"""
database/clusters.py — Competitor cluster (group) management.

Pakistan-only. Clusters group PSX symbols. The exchange_id parameter is accepted
for call-site compatibility but ignored (always PSX).

Tables: psx_clusters, psx_cluster_members, psx_announcements
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from database.connection import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Cluster CRUD ──────────────────────────────────────────────────────────────

def create_cluster(name: str, exchange_id: Optional[str] = None, description: str = "") -> int:
    """Create a new competitor cluster. Returns the new cluster id."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO psx_clusters (name, description, created_at) VALUES (?,?,?)",
        (name.strip(), description.strip(), _now()),
    )
    db.commit()
    return cur.lastrowid


def delete_cluster(cluster_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM psx_cluster_members WHERE cluster_id = ?", (cluster_id,))
    db.execute("DELETE FROM psx_clusters WHERE id = ?", (cluster_id,))
    db.commit()


def list_clusters(exchange_id: Optional[str] = None) -> list[dict]:
    """List all clusters."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM psx_clusters ORDER BY created_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["exchange_id"] = "KAR"
        out.append(d)
    return out


def get_cluster(cluster_id: int) -> dict | None:
    db  = get_db()
    row = db.execute(
        "SELECT * FROM psx_clusters WHERE id = ?", (cluster_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Member management ─────────────────────────────────────────────────────────

def add_cluster_member(
    cluster_id: int,
    exchange_id: Optional[str],
    ticker: str,
    company_name: str = "",
) -> bool:
    """Add a PSX symbol to a cluster. Returns True if inserted."""
    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO psx_cluster_members "
            "(cluster_id, symbol, company_name, added_at) VALUES (?,?,?,?)",
            (cluster_id, ticker.upper(), company_name.strip(), _now()),
        )
        db.commit()
        return True
    except Exception:
        return False


def remove_cluster_member(cluster_id: int, ticker: str) -> None:
    db = get_db()
    db.execute(
        "DELETE FROM psx_cluster_members WHERE cluster_id = ? AND symbol = ?",
        (cluster_id, ticker.upper()),
    )
    db.commit()


def get_cluster_members(cluster_id: int) -> list[dict]:
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM psx_cluster_members WHERE cluster_id = ? ORDER BY added_at",
        (cluster_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ticker"] = d.get("symbol")
        d["exchange_id"] = "KAR"
        out.append(d)
    return out


def get_clusters_with_members(exchange_id: Optional[str] = None) -> list[dict]:
    """Return each cluster with its member list."""
    clusters = list_clusters(exchange_id)
    for c in clusters:
        c["members"] = get_cluster_members(c["id"])
    return clusters


# ── Announcement persistence ──────────────────────────────────────────────────

def save_announcements(
    exchange_id: Optional[str],
    ticker: str,
    ann_type: str,
    announcements: list[dict],
) -> int:
    """Persist fetched PSX announcements. Returns number of new rows inserted."""
    db      = get_db()
    now     = _now()
    inserted = 0
    for ann in announcements:
        try:
            db.execute(
                "INSERT OR IGNORE INTO psx_announcements "
                "(symbol, ann_type, ann_date, title, document_url, fetched_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    ticker.upper(),
                    ann_type,
                    ann.get("date", ""),
                    ann.get("title", ""),
                    ann.get("pdf_url"),
                    now,
                ),
            )
            if db.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception:
            pass
    db.commit()
    return inserted
