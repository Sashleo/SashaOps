"""
core/memory_engine.py — Three-tier memory system.

Tier 1 — Client-specific: tagged with client_id, never crosses clients.
Tier 2 — Industry-level: anonymised, no company names, shareable within industry.
Tier 3 — Global patterns: fully abstract, cross-industry, informs all analyses.

Memory flow:
1. Analysis completes → extract_and_store() called automatically
2. Gemini extracts 2-3 insights from the result
3. Tier 1 insight stored with client_id (encrypted)
4. Anonymised version stored as Tier 2 if industry tag present
5. On pattern_learning() call → Tier 3 patterns derived from Tier 2

Memory injection flow (every analysis prompt):
1. Load Tier 1 entries for this client + module
2. Load relevant Tier 2 entries for this industry
3. Load Tier 3 global patterns
4. Strip PII via privacy_wrapper
5. Inject into prompt via build_secure_prompt()
"""

import json
from datetime import datetime
from typing import Optional
import structlog

from database.connection import get_db
from security.crypto import encrypt, decrypt
from core.ai_client import extract_memory_insights, run_fast
from core.privacy_wrapper import anonymise_for_industry_memory

log = structlog.get_logger()

MEMORY_LIMIT = 500   # Max entries per client before oldest are pruned


def extract_and_store(
    analysis_text: str,
    module_id: str,
    business_name: str,
    client_id: str,
    client_name: str,
    client_key: bytes,
    industry_tag: Optional[str] = None,
    sanitiser=None,
) -> int:
    """
    Extract insights from an analysis result and store in memory.
    Called automatically after every analysis completes.

    SECURITY: insight extraction sends text to Gemini. The run's sanitiser MUST be
    threaded through so Class-S data (company names, figures) is tokenised first. If
    no sanitiser is available — or LOCAL_ONLY is set (no external calls) — insight
    extraction is skipped entirely rather than leaking restored text.

    Returns: number of insights stored
    """
    from config.settings import settings
    if sanitiser is None or settings.LOCAL_ONLY:
        return 0

    insights = extract_memory_insights(analysis_text, module_id, business_name, sanitiser=sanitiser)

    if not insights:
        return 0

    db  = get_db()
    now = datetime.utcnow().isoformat()
    stored = 0

    for insight in insights:
        if not insight.strip():
            continue

        # ── Tier 1: Client-specific (encrypted with client key) ───────────────
        encrypted_insight = encrypt(insight, client_key)
        db.execute(
            """
            INSERT INTO memory_entries
            (client_id, tier, insight_encrypted, source_module,
             source_business, industry_tag, tags, created_at)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                encrypted_insight,
                module_id,
                business_name,
                industry_tag,
                json.dumps([module_id, industry_tag or ""]),
                now,
            ),
        )

        # ── Tier 2: Industry-level (anonymised, no encryption needed) ─────────
        if industry_tag:
            anon_insight = anonymise_for_industry_memory(
                insight, business_name, client_name
            )
            if anon_insight and len(anon_insight) > 20:
                # Use a neutral key for tier 2 — no client-specific encryption
                # Tier 2 data contains no PII by definition
                db.execute(
                    """
                    INSERT INTO memory_entries
                    (client_id, tier, insight_encrypted, source_module,
                     source_business, industry_tag, tags, created_at)
                    VALUES (NULL, 2, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        anon_insight.encode("utf-8"),  # Plain text — no PII
                        module_id,
                        "[anonymised]",
                        industry_tag,
                        json.dumps([module_id, industry_tag]),
                        now,
                    ),
                )

        stored += 1

    db.commit()

    # Prune old entries if over limit
    _prune_old_entries(client_id)

    log.info("Memory insights stored", client_id=client_id, count=stored, module=module_id)
    return stored


def build_memory_context(
    client_id: str,
    client_key: bytes,
    module_id: str,
    industry_tag: Optional[str] = None,
    limit_per_tier: int = 8,
) -> str:
    """
    Build the memory context string for injection into a prompt.
    Loads from all three tiers and combines them.

    Args:
        client_id: Active client
        client_key: Client's decryption key
        module_id: Current module (for relevance filtering)
        industry_tag: Client's industry (for tier 2 matching)
        limit_per_tier: Max entries per tier to inject

    Returns:
        Formatted memory context string (PII will be stripped by privacy_wrapper)
    """
    db      = get_db()
    context_parts = []

    # ── Tier 1: Client-specific ───────────────────────────────────────────────
    rows = db.execute(
        """
        SELECT insight_encrypted, source_module, source_business, created_at
        FROM memory_entries
        WHERE client_id = ? AND tier = 1
          AND (source_module = ? OR source_module != ?)
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (client_id, module_id, module_id, limit_per_tier),
    ).fetchall()

    if rows:
        context_parts.append("CLIENT INTELLIGENCE:")
        for row in rows:
            insight = decrypt(row["insight_encrypted"], client_key)
            if insight:
                context_parts.append(
                    f"  [{row['source_module']} | {row['source_business']} | {row['created_at'][:10]}] {insight}"
                )

    # ── Tier 2: Industry-level ────────────────────────────────────────────────
    if industry_tag:
        rows = db.execute(
            """
            SELECT insight_encrypted, source_module, created_at
            FROM memory_entries
            WHERE tier = 2 AND industry_tag = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (industry_tag, limit_per_tier),
        ).fetchall()

        if rows:
            context_parts.append(f"\nINDUSTRY PATTERNS ({industry_tag}):")
            for row in rows:
                # Tier 2 is stored as plain text (anonymised)
                insight = row["insight_encrypted"]
                if isinstance(insight, bytes):
                    insight = insight.decode("utf-8", errors="ignore")
                if insight:
                    context_parts.append(f"  [{row['source_module']}] {insight}")

    # ── Tier 3: Global patterns ───────────────────────────────────────────────
    rows = db.execute(
        """
        SELECT pattern_text, category
        FROM learned_patterns
        ORDER BY confidence DESC, updated_at DESC
        LIMIT ?
        """,
        (limit_per_tier,),
    ).fetchall()

    if rows:
        context_parts.append("\nGLOBAL ANALYTICAL PATTERNS:")
        for row in rows:
            context_parts.append(f"  [{row['category']}] {row['pattern_text']}")

    return "\n".join(context_parts)


def run_pattern_learning() -> str:
    """
    Analyse accumulated Tier 2 memories to derive Tier 3 global patterns.
    Should be run manually from the Memory Lab page.

    Returns: text description of patterns found
    """
    db = get_db()

    # Load last 60 tier-2 entries (anonymised, safe to pass to AI)
    rows = db.execute(
        """
        SELECT insight_encrypted, source_module, industry_tag
        FROM memory_entries
        WHERE tier = 2
        ORDER BY created_at DESC
        LIMIT 60
        """
    ).fetchall()

    if len(rows) < 10:
        return f"Need at least 10 industry-level memory entries. Currently have {len(rows)}. Run more analyses first."

    memory_dump = "\n".join([
        f"[{row['source_module']} | {row['industry_tag']}]: "
        + (row['insight_encrypted'].decode('utf-8', errors='ignore') if isinstance(row['insight_encrypted'], bytes) else str(row['insight_encrypted']))
        for row in rows
    ])

    prompt = f"""
You are an analytical meta-intelligence system reviewing {len(rows)} anonymised intelligence entries.

Review these entries collected across multiple business analyses:

{memory_dump}

Identify 8-10 CROSS-CUTTING PATTERNS - recurring themes, systemic risks, or analytical
heuristics that appear across multiple businesses and industries.

These patterns will be injected into ALL future analyses to make them smarter.

Rules:
- No company names, no specific figures, no PII
- Patterns must be structural observations, not specific facts
- Each pattern must be actionable - something an analyst should consider

Format: One pattern per line.
Start each with a category tag like [MARKET], [RISK], [STRATEGY], [OPERATIONS], [REGULATORY], [CULTURAL]
"""

    result = run_fast(prompt)

    if not result:
        return "Pattern learning failed - no response from AI"

    # Store the patterns
    now  = datetime.utcnow().isoformat()
    patterns = [
        line.strip() for line in result.split("\n")
        if line.strip() and len(line.strip()) > 20
    ]

    # Clear existing patterns and replace with new ones
    db.execute("DELETE FROM learned_patterns")

    for pattern in patterns:
        # Extract category from [TAG] prefix
        category = "GENERAL"
        if pattern.startswith("[") and "]" in pattern:
            category = pattern[1:pattern.index("]")]
            pattern  = pattern[pattern.index("]")+1:].strip()

        db.execute(
            """
            INSERT INTO learned_patterns
            (pattern_text, category, confidence, source_count, created_at, updated_at)
            VALUES (?, ?, 0.7, ?, ?, ?)
            """,
            (pattern, category, len(rows), now, now),
        )

    db.commit()
    log.info("Pattern learning complete", patterns_stored=len(patterns))
    return result


def get_memory_stats(client_id: str) -> dict:
    """Get memory statistics for the Memory Lab page."""
    db = get_db()

    tier1_count = db.execute(
        "SELECT count(*) FROM memory_entries WHERE client_id = ? AND tier = 1",
        (client_id,)
    ).fetchone()[0]

    tier2_count = db.execute(
        "SELECT count(*) FROM memory_entries WHERE tier = 2"
    ).fetchone()[0]

    tier3_count = db.execute(
        "SELECT count(*) FROM learned_patterns"
    ).fetchone()[0]

    return {
        "client_specific": tier1_count,
        "industry_level":  tier2_count,
        "global_patterns": tier3_count,
        "total":           tier1_count + tier2_count + tier3_count,
    }


def _prune_old_entries(client_id: str) -> None:
    """Remove oldest entries if over the memory limit."""
    db = get_db()
    count = db.execute(
        "SELECT count(*) FROM memory_entries WHERE client_id = ? AND tier = 1",
        (client_id,)
    ).fetchone()[0]

    if count > MEMORY_LIMIT:
        excess = count - MEMORY_LIMIT
        db.execute(
            """
            DELETE FROM memory_entries WHERE id IN (
                SELECT id FROM memory_entries
                WHERE client_id = ? AND tier = 1
                ORDER BY created_at ASC
                LIMIT ?
            )
            """,
            (client_id, excess),
        )
        db.commit()
        log.info("Memory pruned", client_id=client_id, removed=excess)
