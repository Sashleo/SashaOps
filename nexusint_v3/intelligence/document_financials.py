"""
intelligence/document_financials.py — Fully local document intelligence.

No AI calls. No external network. All processing stays on the machine.
This module sits between raw uploaded documents and the financial analysis engine,
bridging the gap so that private/unlisted companies get the same quantitative
analysis depth as PSX-listed ones.

Two main capabilities:
  1. Financial extraction — pdfplumber tables → regex → pandas (multi-stage fallback)
  2. Company context — spaCy NER + keyword_engine taxonomy for smarter search targeting

Output of run_document_financial_analysis() is the same flat dict format that
Stage 2 already consumes from run_local_analysis_psx(). Both tracks converge there.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

log = structlog.get_logger()

# ── Pakistan geography signals (cities + provinces) ───────────────────────────
_PAK_GEO = {
    "karachi", "lahore", "islamabad", "rawalpindi", "faisalabad", "multan",
    "peshawar", "quetta", "sialkot", "gujranwala", "hyderabad", "abbottabad",
    "punjab", "sindh", "kpk", "balochistan", "azad kashmir", "gilgit",
    "pakistan", "pak",
}

# ── Business type signal patterns ─────────────────────────────────────────────
_BTYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("startup",         ["startup", "seed round", "series a", "series b", "angel round",
                         "pre-revenue", "mvp", "minimum viable"]),
    ("manufacturing",   ["manufacturing", "production line", "factory", "plant capacity",
                         "raw material", "work in process", "finished goods"]),
    ("trading",         ["import", "export", "trading", "distributor", "wholesale",
                         "commodity", "import/export"]),
    ("services",        ["services", "consulting", "advisory", "outsourcing", "bpo",
                         "professional services", "managed services"]),
    ("ecommerce",       ["e-commerce", "ecommerce", "online marketplace", "platform",
                         "saas", "software as a service"]),
    ("private_company", ["private limited", "pvt. ltd", "pvt ltd", "(pvt)", "sme",
                         "small and medium"]),
]


@dataclass
class CompanyContext:
    """NLP-extracted company profile from document text."""
    industry: str = "general"
    geography: list = field(default_factory=list)
    business_type: str = "private_company"
    keywords: list = field(default_factory=list)
    money_signals: list = field(default_factory=list)


# ── spaCy loader (optional — degrades gracefully if not installed) ─────────────

_nlp = None
_nlp_loaded = False


def _get_nlp():
    """Lazy-load spaCy model. Returns None if unavailable."""
    global _nlp, _nlp_loaded
    if _nlp_loaded:
        return _nlp
    _nlp_loaded = True
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
        log.info("spaCy en_core_web_sm loaded")
    except Exception as e:
        log.info("spaCy unavailable — NLP context extraction will use keyword matching only",
                 reason=str(e)[:80])
        _nlp = None
    return _nlp


# ── Financial extraction helpers ──────────────────────────────────────────────

def _labels_from_psx_parser() -> list:
    """Import _LABELS from psx_report_parser to avoid duplication."""
    try:
        from intelligence.psx_report_parser import _LABELS
        return _LABELS
    except Exception:
        return []


def _first_figure_from_psx() -> callable:
    """Import _first_figure from psx_report_parser."""
    try:
        from intelligence.psx_report_parser import _first_figure
        return _first_figure
    except Exception:
        return lambda line: None


def _parse_table_rows(rows: list[list]) -> dict:
    """
    Extract financial line items from pdfplumber table rows.
    Each row is a list of cells; join them into a pseudo-line and run label matching.
    """
    labels = _labels_from_psx_parser()
    first_figure = _first_figure_from_psx()
    if not labels:
        return {}

    out: dict = {}
    for row in rows:
        if not row:
            continue
        line = " ".join(str(c or "").strip() for c in row if c)
        low = line.lower()
        for key, aliases in labels:
            if key in out:
                continue
            for alias in aliases:
                if alias in low and low.index(alias) < 50:
                    val = first_figure(line)
                    if val is not None:
                        out[key] = val
                    break
    return out


def _extract_tables_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Stage 1: Use pdfplumber.extract_tables() to get structured table data.
    Financial statements are usually tables — this is more reliable than text for numbers.
    """
    try:
        import pdfplumber
    except ImportError:
        return {}

    out: dict = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            n_pages = len(pdf.pages)
            log.info("PDF opened for table extraction", pages=n_pages)
            for i, page in enumerate(pdf.pages[:80]):
                try:
                    tables = page.extract_tables() or []
                    for table in tables:
                        rows = [r for r in table if r] if table else []
                        partial = _parse_table_rows(rows)
                        if partial:
                            log.info("Table rows matched", page=i + 1, new_keys=list(partial.keys()))
                        for k, v in partial.items():
                            if k not in out:
                                out[k] = v
                except Exception as page_err:
                    log.info("Page table extraction error", page=i + 1, error=str(page_err)[:80])
                    continue
    except Exception as e:
        log.info("PDF table extraction failed", error=str(e)[:100])

    log.info("PDF table extraction complete", fields_found=len(out), keys=list(out.keys()))
    return out


def _extract_from_text(text: str) -> dict:
    """Stage 2: Regex label matching on raw text (same logic as psx_report_parser)."""
    try:
        from intelligence.psx_report_parser import _parse_financials
        return _parse_financials(text)
    except Exception:
        return {}


def _extract_from_spreadsheet(doc_bytes: bytes, doc_type: str) -> dict:
    """Stage 3: pandas read for XLSX/CSV — walk rows looking for financial labels."""
    try:
        import pandas as pd
    except ImportError:
        return {}

    labels = _labels_from_psx_parser()
    first_figure = _first_figure_from_psx()
    if not labels:
        return {}

    out: dict = {}
    try:
        if doc_type == "xlsx":
            dfs = pd.read_excel(io.BytesIO(doc_bytes), sheet_name=None, header=None)
            frames = list(dfs.values())
        else:
            frames = [pd.read_csv(io.BytesIO(doc_bytes), header=None)]

        for df in frames:
            for _, row in df.iterrows():
                line = " ".join(str(v) for v in row.values if pd.notna(v))
                low = line.lower()
                for key, aliases in labels:
                    if key in out:
                        continue
                    for alias in aliases:
                        if alias in low and low.index(alias) < 50:
                            val = first_figure(line)
                            if val is not None:
                                out[key] = val
                            break
    except Exception as e:
        log.info("Spreadsheet financial extraction failed", error=str(e)[:100])

    return out


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_financials_from_text(
    text: str,
    doc_bytes: Optional[bytes] = None,
    doc_type: str = "pdf",
) -> dict:
    """
    Extract financial line items from a document using multi-stage local parsing.

    Stage 1 (PDF): pdfplumber.extract_tables() — structured table rows (most reliable)
    Stage 2:       psx_report_parser._parse_financials() — regex on raw text
    Stage 3 (XLSX/CSV): pandas — walk dataframe rows

    Returns dict with keys from _LABELS (revenue, net_income, total_assets, etc.)
    matching what financial_model.analyse_from_document() expects.
    Returns {} if nothing meaningful found.
    """
    out: dict = {}

    log.info("Document financial extraction start", doc_type=doc_type,
             has_bytes=doc_bytes is not None, text_len=len(text or ""))

    # Stage 1: PDF table extraction (best for financial statements)
    if doc_bytes and doc_type == "pdf":
        out = _extract_tables_from_pdf(doc_bytes)
        log.info("Stage 1 (PDF tables) done", fields_found=len(out), keys=list(out.keys()))

    # Stage 2: Text regex (fallback or supplement)
    if len(out) < 3 and text:
        log.info("Stage 2 (text regex) — Stage 1 found too few fields", stage1_count=len(out))
        text_out = _extract_from_text(text)
        log.info("Stage 2 (text regex) done", new_fields=len(text_out), keys=list(text_out.keys()))
        for k, v in text_out.items():
            if k not in out:
                out[k] = v
    elif len(out) >= 3:
        log.info("Stage 2 skipped — Stage 1 sufficient", fields=len(out))

    # Stage 3: Spreadsheet (for XLSX/CSV uploads)
    if len(out) < 3 and doc_bytes and doc_type in ("xlsx", "csv"):
        log.info("Stage 3 (spreadsheet) — previous stages found too few fields", count=len(out))
        sheet_out = _extract_from_spreadsheet(doc_bytes, doc_type)
        log.info("Stage 3 (spreadsheet) done", new_fields=len(sheet_out), keys=list(sheet_out.keys()))
        for k, v in sheet_out.items():
            if k not in out:
                out[k] = v

    log.info("Document financial extraction complete", total_fields=len(out), keys=list(out.keys()))
    return out


def extract_company_context(text: str) -> CompanyContext:
    """
    Extract company profile from document text using local NLP.
    No external calls. spaCy en_core_web_sm for entity recognition;
    keyword_engine for industry taxonomy matching.

    Returns CompanyContext with: industry, geography, business_type, keywords, money_signals.
    """
    ctx = CompanyContext()
    blob = (text or "").lower()

    # Industry detection via keyword_engine taxonomy (most reliable for Pakistan context)
    try:
        from intelligence.keyword_engine import detect_industry, extract_keywords
        ctx.industry = detect_industry(text, "")
        ctx.keywords = extract_keywords(text, "", limit=15)
    except Exception:
        pass

    # Business type via regex signals
    for btype, signals in _BTYPE_PATTERNS:
        if any(s in blob for s in signals):
            ctx.business_type = btype
            break

    # spaCy NER for geography and money signals
    nlp = _get_nlp()
    if nlp and text:
        try:
            doc = nlp(text[:50000])  # cap for performance
            for ent in doc.ents:
                if ent.label_ == "GPE":
                    loc = ent.text.lower().strip()
                    if loc in _PAK_GEO and loc not in ctx.geography:
                        ctx.geography.append(ent.text.strip())
                elif ent.label_ == "MONEY" and len(ctx.money_signals) < 10:
                    ctx.money_signals.append(ent.text.strip())

            # Noun chunks as additional keyword candidates
            if len(ctx.keywords) < 10:
                chunk_counts: dict[str, int] = {}
                for chunk in doc.noun_chunks:
                    if len(chunk.text) > 3 and chunk.root.pos_ in ("NOUN", "PROPN"):
                        word = chunk.text.lower().strip()
                        if word not in _PAK_GEO:
                            chunk_counts[word] = chunk_counts.get(word, 0) + 1
                top_chunks = sorted(chunk_counts, key=chunk_counts.get, reverse=True)[:10]
                for c in top_chunks:
                    if c not in ctx.keywords:
                        ctx.keywords.append(c)
        except Exception as e:
            log.info("spaCy NER failed, using keyword-only context", error=str(e)[:80])
    else:
        # spaCy unavailable — extract geography via simple string matching
        for geo in _PAK_GEO:
            if geo in blob and geo not in ctx.geography:
                ctx.geography.append(geo)

    return ctx


def run_document_financial_analysis(
    doc_bytes: bytes,
    doc_text: str,
    doc_type: str,
    company_name: str,
    region_id: str = "pakistan",
) -> Optional[dict]:
    """
    Orchestrate local financial analysis on an uploaded document.
    Returns the same flat dict Stage 2 consumes (from financial_model._assemble_report_dict),
    or None if the document contains insufficient financial data (<3 line items found).

    No AI calls. No external network. Pure local computation.
    """
    financials = extract_financials_from_text(doc_text, doc_bytes, doc_type)

    if len(financials) < 3:
        log.info(
            "Insufficient financial data in document — skipping local analysis",
            company=company_name,
            fields_found=len(financials),
            doc_type=doc_type,
        )
        return None

    try:
        from intelligence.financial_model import analyse_from_document, _assemble_report_dict
        analysis = analyse_from_document(financials, company_name, region_id=region_id)
        result = _assemble_report_dict(analysis, region_id)
        log.info(
            "Document financial analysis complete",
            company=company_name,
            fields_extracted=len(financials),
            health_score=result.get("health_score"),
            data_source="uploaded_document",
        )
        return result
    except Exception as e:
        log.warning("Document financial analysis failed", company=company_name, error=str(e)[:120])
        return None
