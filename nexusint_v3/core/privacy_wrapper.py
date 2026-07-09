"""
core/privacy_wrapper.py — Security context injected into every AI prompt.

This is the most important privacy control in the system.
Every single AI call passes through this module before reaching Gemini.

It does four things:
1. Prepends a security context block (client isolation enforcement)
2. Injects memory context with PII stripped (anonymised patterns only)
3. Appends region localisation modifier
4. Validates output for signs of data leakage (optional, for high-sensitivity calls)
"""

import re
import hashlib
from typing import Optional
import structlog

log = structlog.get_logger()


def _hash_client_id(client_id: str) -> str:
    """Hash the client ID so it appears in prompts without revealing the raw ID."""
    return hashlib.sha256(client_id.encode()).hexdigest()[:12].upper()


def _strip_pii(text: str) -> str:
    """
    Strip common PII patterns from memory entries before injection.
    This ensures the AI never receives raw sensitive data in memory context.

    Strips: email addresses, phone numbers, specific monetary values,
    passport/ID patterns, and any line containing "confidential" markers.
    """
    # Remove email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)

    # Remove phone numbers (various formats)
    text = re.sub(r'\b(\+\d{1,3}[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}[-.\s]?\d{4}\b', '[PHONE]', text)

    # Remove specific monetary values (replace with approximate ranges)
    text = re.sub(r'\$[\d,]+(?:\.\d{2})?', '[AMOUNT]', text)
    text = re.sub(r'₦[\d,]+(?:\.\d{2})?', '[AMOUNT]', text)
    text = re.sub(r'₨[\d,]+(?:\.\d{2})?', '[AMOUNT]', text)
    text = re.sub(r'KSh[\d,]+(?:\.\d{2})?', '[AMOUNT]', text)
    text = re.sub(r'R[\d,]+(?:\.\d{2})?', '[AMOUNT]', text)
    text = re.sub(r'AED[\d,]+(?:\.\d{2})?', '[AMOUNT]', text)

    # Remove ID numbers, passport numbers
    text = re.sub(r'\b[A-Z]{1,2}\d{6,9}\b', '[ID_NUMBER]', text)

    # Remove lines explicitly marked confidential
    lines = text.split('\n')
    lines = [l for l in lines if 'confidential' not in l.lower()]
    text = '\n'.join(lines)

    return text


def build_secure_prompt(
    base_system: str,
    client_id: str = "",
    client_name: str = "",
    memory_context: str = "",
    region_modifier: str = "",
    allowed_sources: Optional[list] = None,
) -> str:
    """
    Build the full secured system prompt for any AI call.

    This function:
    1. Creates the security context header
    2. Strips PII from memory context before injection
    3. Appends region modifier
    4. Combines everything into the final system prompt

    Args:
        base_system: The module's base system prompt
        client_id: Active client ID (will be hashed in prompt)
        client_name: Active client name (for context)
        memory_context: Raw memory entries (will be PII-stripped)
        region_modifier: Region-specific prompt addition
        allowed_sources: List of data sources permitted for this call

    Returns:
        Complete secured system prompt
    """
    parts = [base_system]

    # ── Security context block ────────────────────────────────────────────────
    if client_id or client_name:
        client_ref = f"{client_name} (#{_hash_client_id(client_id)})" if client_id else client_name
        sources_str = ", ".join(allowed_sources) if allowed_sources else "web search, provided documents"

        security_block = f"""

=== SECURITY AND PRIVACY CONTEXT ===
Active client: {client_ref}
Authorised data sources: {sources_str}

MANDATORY RULES - follow these without exception:
1. ISOLATION: You are operating exclusively within the context of {client_name}.
   Do not reference, infer from, or synthesise data from any other client or entity
   not explicitly mentioned in this prompt.

2. MEMORY PRIVACY: Do not store specific financial figures, personal names,
   contract terms, or commercially sensitive specifics in any memory or pattern.
   Only store structural/analytical patterns - observations about how businesses
   in this sector or region tend to behave.

3. DATA LEAKAGE: If you encounter any information that seems out of context
   for {client_name} or this analysis, flag it rather than using it.

4. CONFIDENTIALITY: Treat all information provided as strictly confidential
   to {client_name}. Do not use it as examples in other contexts.
=== END SECURITY CONTEXT ==="""

        parts.append(security_block)

    # ── Memory context (PII-stripped) ─────────────────────────────────────────
    if memory_context and memory_context.strip():
        clean_memory = _strip_pii(memory_context)

        if clean_memory.strip():
            memory_block = f"""

=== PRIOR INTELLIGENCE (from your memory - use to build on, not repeat) ===
{clean_memory}

Use these prior insights to:
- Build on established findings rather than repeating them
- Flag when current findings contradict prior findings
- Identify what is NEW since the prior analysis
- Apply learned patterns to current analysis
=== END PRIOR INTELLIGENCE ==="""
            parts.append(memory_block)

    # ── Region modifier ───────────────────────────────────────────────────────
    if region_modifier and region_modifier.strip():
        parts.append(f"\n=== REGIONAL CONTEXT ===\n{region_modifier}\n=== END REGIONAL CONTEXT ===")

    return "\n".join(parts)


def validate_output(
    output: str,
    client_name: str,
    other_client_names: list[str],
) -> tuple[bool, list[str]]:
    """
    Scan AI output for signs of data leakage between clients.

    Checks if the output references other clients' names.
    Returns: (is_clean, list_of_issues)

    This is an optional post-processing step for high-sensitivity analyses.
    """
    issues = []

    for name in other_client_names:
        if name.lower() in output.lower() and name.lower() != client_name.lower():
            issues.append(f"Output references other client: {name}")
            log.warning("Potential data leakage detected", other_client=name)

    return len(issues) == 0, issues


def anonymise_for_industry_memory(
    insight: str,
    business_name: str,
    client_name: str,
) -> str:
    """
    Anonymise an insight before storing as industry-level (tier 2) memory.
    Removes the specific business and client names, replacing with generic terms.

    This allows the insight to inform future analyses of other clients
    in the same industry without exposing who the original client was.
    """
    # Replace business name with generic placeholder
    anonymised = insight.replace(business_name, "[the company]")
    anonymised = anonymised.replace(client_name, "[the client]")

    # Strip any remaining PII
    anonymised = _strip_pii(anonymised)

    return anonymised
