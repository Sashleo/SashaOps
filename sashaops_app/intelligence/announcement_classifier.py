"""
intelligence/announcement_classifier.py — Type-classify PSX announcements (M10).

FinBERT on procedural PSX announcement titles ("Transmission of Annual Report",
"Book Closure") returns a meaningless ~100% neutral. Instead we classify each
announcement by TYPE via keyword matching and produce a type breakdown. FinBERT is
reserved for relevance-filtered *news* headlines, not procedural filings.
"""

from __future__ import annotations

ANNOUNCEMENT_SIGNALS = {
    "positive": ["dividend", "bonus share", "buy back", "buyback", "expansion",
                 "new plant", "record profit", "profit up", "acquisition"],
    "negative": ["show cause", "penalty", "default", "loss", "winding up",
                 "delisting", "suspension", "qualified opinion", "going concern",
                 "resignation", "investigation"],
    "procedural": ["transmission of", "board meeting", "agm", "egm", "book closure",
                   "credit of dividend", "certified true copy", "notice of",
                   "postponement", "intimation", "election of directors"],
}


def classify_announcements(titles: list[str]) -> dict:
    """
    Return a type breakdown:
      {"positive":[matched terms], "negative":[matched terms],
       "procedural": n, "counts": {...}, "n": total, "summary": str}
    A title matching a positive/negative signal is counted there; otherwise procedural.
    """
    pos_terms: list[str] = []
    neg_terms: list[str] = []
    procedural = 0
    for t in titles or []:
        low = (t or "").lower()
        hit_pos = next((s for s in ANNOUNCEMENT_SIGNALS["positive"] if s in low), None)
        hit_neg = next((s for s in ANNOUNCEMENT_SIGNALS["negative"] if s in low), None)
        if hit_neg:
            neg_terms.append(hit_neg)
        elif hit_pos:
            pos_terms.append(hit_pos)
        else:
            procedural += 1

    n = len(titles or [])
    parts = []
    if pos_terms:
        parts.append(f"{len(pos_terms)} positive: {', '.join(sorted(set(pos_terms)))}")
    if procedural:
        parts.append(f"{procedural} procedural")
    if neg_terms:
        parts.append(f"{len(neg_terms)} negative: {', '.join(sorted(set(neg_terms)))}")
    summary = " · ".join(parts) if parts else "no announcements"

    return {
        "positive": sorted(set(pos_terms)),
        "negative": sorted(set(neg_terms)),
        "procedural": procedural,
        "counts": {"positive": len(pos_terms), "negative": len(neg_terms), "procedural": procedural},
        "n": n,
        "summary": summary,
    }
