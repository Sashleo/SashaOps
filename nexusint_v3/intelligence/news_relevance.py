"""
intelligence/news_relevance.py — Deterministic, local news-relevance scoring for PSX.

No external vector DB. Reuses the in-process MiniLM model (ml/relevance.py) plus a
PSX entity dictionary (built from psx_companies) and the sector knowledge base
(config/news_anchors.py). Produces, per headline:

    score  (0..1+)   — blended relevance to the subject company / its sector / macro
    tags   (list)    — why it matched: "company", "sector:cement", "macro", "driver:coal price"
    impact (int|None)— directional sentiment for a matched driver (+1/-1), when detectable

Scoring blend (deterministic):
    final = 0.45 * semantic(anchor cosine, best of company/sector/macro anchors)
          + 0.35 * ticker/company exact-entity bonus
          + 0.15 * sector/industry keyword + driver-term match
          + 0.05 * source authority (SECP/PSX/SBP/regulator > mainstream > opinion)

Works for listed (ticker known) AND unlisted (industry known) companies alike.
Never raises; degrades to keyword-only scoring if the embedding model is unavailable.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

import structlog

log = structlog.get_logger()

# ── source authority weights (matched against feed label, case-insensitive) ──────
_AUTHORITY = [
    (["secp", "psx", "state bank", "sbp", "data portal", "notice", "circular"], 1.0),
    (["business recorder", "dawn", "profit", "tribune", "the news", "bloomberg", "reuters", "financial times", "wsj", "economist"], 0.6),
]
_DEFAULT_AUTHORITY = 0.3

_UP = re.compile(r"\b(rise|rises|rising|rose|surge|surged|jump|jumped|climb|climbed|up|higher|increase|increased|gain|gains|hike|hiked|soar|soared|rally)\b", re.I)
_DOWN = re.compile(r"\b(fall|falls|fell|drop|dropped|decline|declined|down|lower|decrease|decreased|cut|cuts|slump|slumped|plunge|plunged|ease|eased|cheaper)\b", re.I)


# ── PSX entity dictionary (symbol + normalised name variants) ────────────────────
@lru_cache(maxsize=1)
def _psx_entities() -> dict[str, dict]:
    """
    Build {SYMBOL: {"name": str, "sector": str, "variants": set[str]}} from psx_companies.
    Cached in-process. Empty dict if the DB isn't available.
    """
    out: dict[str, dict] = {}
    try:
        from database.connection import get_db
        rows = get_db().execute(
            "SELECT symbol, company_name, sector_name FROM psx_companies"
        ).fetchall()
    except Exception as e:
        log.info("news_relevance: psx_companies unavailable", error=str(e)[:80])
        return out

    for r in rows:
        sym = (r["symbol"] or "").strip().upper()
        if not sym:
            continue
        name = (r["company_name"] or "").strip()
        variants = {sym.lower()}
        if name:
            low = name.lower()
            variants.add(low)
            # drop common suffixes to catch "Lucky Cement" vs "Lucky Cement Limited"
            stripped = re.sub(r"\b(limited|ltd|company|co|pakistan|corporation|corp|plc)\b\.?", "", low)
            stripped = re.sub(r"\s+", " ", stripped).strip()
            if len(stripped) >= 4:
                variants.add(stripped)
                # first significant word (e.g. "lucky", "engro") if distinctive
                first = stripped.split(" ")[0]
                if len(first) >= 5:
                    variants.add(first)
        out[sym] = {"name": name, "sector": (r["sector_name"] or "").strip(), "variants": variants}
    log.info("news_relevance: entity dictionary built", companies=len(out))
    return out


def clear_entity_cache() -> None:
    _psx_entities.cache_clear()


# ── anchor embedding cache ───────────────────────────────────────────────────────
@lru_cache(maxsize=64)
def _anchor_matrix(anchor_key: str, anchors_tuple: tuple):
    """Embed a tuple of anchor phrases once; cached by key. Returns tensor or None."""
    try:
        from ml.relevance import _load_model
        model = _load_model()
        if model is None or not anchors_tuple:
            return None
        return model.encode(list(anchors_tuple), convert_to_tensor=True, batch_size=32)
    except Exception:
        return None


def _best_cosine(headlines_emb, anchor_emb) -> list:
    """Return, per headline, the max cosine similarity across the anchor set."""
    try:
        from sentence_transformers import util as st_util
        sims = st_util.cos_sim(headlines_emb, anchor_emb)  # [n_head, n_anchor]
        return sims.max(dim=1).values.tolist()
    except Exception:
        return []


def _authority(feed_label: str) -> float:
    lab = (feed_label or "").lower()
    for needles, w in _AUTHORITY:
        if any(n in lab for n in needles):
            return w
    return _DEFAULT_AUTHORITY


def _driver_impact(text_low: str, effect: int) -> Optional[int]:
    """Combine driver baseline sign with up/down direction detected in the headline."""
    up = bool(_UP.search(text_low))
    down = bool(_DOWN.search(text_low))
    if up == down:            # ambiguous / neither
        return None
    direction = 1 if up else -1
    return effect * direction  # +1 = positive for sector, -1 = negative


def score_items(
    items: list,
    business_name: str = "",
    ticker: str = "",
    industry: str = "",
    sector: str = "",
) -> None:
    """
    Score a list of FeedItem objects IN PLACE, setting:
        item.relevance_score (float), item.relevance_tags (list[str]),
        item.impact (int|None)  — directional sentiment for a matched driver.

    business_name/ticker: the subject company (either may be empty).
    industry: keyword_engine industry key (e.g. "cement"); drives sector anchors/drivers.
    """
    if not items:
        return

    from config.news_anchors import get_sector_profile, MACRO_ANCHORS, MACRO_DRIVERS

    profile = get_sector_profile(industry)
    sector_anchors = profile.get("anchors", [])
    sector_drivers = profile.get("drivers", [])
    sector_tickers = {t.upper() for t in profile.get("tickers", [])}

    # entity match set for the subject company
    subj_variants: set[str] = set()
    tk = (ticker or "").strip().upper()
    if tk:
        subj_variants.add(tk.lower())
        ent = _psx_entities().get(tk)
        if ent:
            subj_variants |= ent["variants"]
            if not industry and ent.get("sector"):
                sector = ent["sector"]
    if business_name:
        bn = business_name.lower().strip()
        subj_variants.add(bn)
        stripped = re.sub(r"\b(limited|ltd|company|co|pakistan|private|pvt)\b\.?", "", bn)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if len(stripped) >= 4:
            subj_variants.add(stripped)

    titles = [((it.title or "") + " " + (getattr(it, "summary", "") or "")).strip() for it in items]
    titles_low = [t.lower() for t in titles]

    # ── semantic pass (batch) ─────────────────────────────────────────────────
    company_ctx = f"{business_name} {sector} {industry} Pakistan".strip()
    sem_company: list = []
    sem_sector: list = []
    sem_macro: list = []
    try:
        from ml.relevance import _load_model
        model = _load_model()
        if model is not None:
            head_emb = model.encode(titles, convert_to_tensor=True, batch_size=64)
            comp_emb = _anchor_matrix("company::" + company_ctx, (company_ctx,))
            if comp_emb is not None:
                sem_company = _best_cosine(head_emb, comp_emb)
            if sector_anchors:
                sec_emb = _anchor_matrix("sector::" + industry, tuple(sector_anchors))
                if sec_emb is not None:
                    sem_sector = _best_cosine(head_emb, sec_emb)
            mac_emb = _anchor_matrix("macro", tuple(MACRO_ANCHORS))
            if mac_emb is not None:
                sem_macro = _best_cosine(head_emb, mac_emb)
    except Exception as e:
        log.info("news_relevance: semantic pass skipped", error=str(e)[:80])

    def _sem(lst, i):
        return max(0.0, lst[i]) if (lst and i < len(lst)) else 0.0

    for i, it in enumerate(items):
        low = titles_low[i]
        tags: list[str] = []
        impact: Optional[int] = None

        # entity / ticker match (heavy)
        entity_bonus = 0.0
        if subj_variants and any(v and v in low for v in subj_variants):
            entity_bonus = 1.0
            tags.append("company")
        elif sector_tickers and any(re.search(rf"\b{re.escape(t.lower())}\b", low) for t in sector_tickers):
            entity_bonus = 0.5
            tags.append("sector-peer")

        # keyword + driver match
        kw_score = 0.0
        for d in sector_drivers:
            if any(term in low for term in d["terms"]):
                kw_score = max(kw_score, 0.8)
                term_hit = next(t for t in d["terms"] if t in low)
                tags.append(f"driver:{term_hit}")
                imp = _driver_impact(low, d["effect"])
                if imp is not None and impact is None:
                    impact = imp
                break
        for d in MACRO_DRIVERS:
            if any(term in low for term in d["terms"]):
                kw_score = max(kw_score, 0.6)
                tags.append("macro")
                if impact is None:
                    impact = _driver_impact(low, d["effect"])
                break

        # semantic best-of
        sem = max(_sem(sem_company, i), _sem(sem_sector, i) * 0.9, _sem(sem_macro, i) * 0.7)
        if sem >= 0.35 and not tags:
            tags.append(f"semantic:{industry or 'macro'}")

        authority = _authority(getattr(it, "feed_label", ""))

        final = (0.45 * sem) + (0.35 * entity_bonus) + (0.15 * kw_score) + (0.05 * authority)

        it.relevance_score = round(float(final), 4)
        it.relevance_tags = tags
        it.impact = impact


def is_relevant(item, min_score: float = 0.30) -> bool:
    """A headline is 'relevant' if it clears the score bar OR names the company/sector."""
    score = getattr(item, "relevance_score", 0.0) or 0.0
    tags = getattr(item, "relevance_tags", []) or []
    if score >= min_score:
        return True
    return any(t == "company" or t.startswith("driver:") for t in tags)
