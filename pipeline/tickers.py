"""pipeline/tickers.py — Ticker universes for pipeline runs."""
from __future__ import annotations

# M2 pilot set: the golden 8 plus 12 more. ENGRO replaced with FFC.
# Golden 8 span bank, cement, fertiliser, oil-and-gas, textile, power, tech and an
# unusual PSX layout (NCL). The owner verifies these figures manually for the golden set.
M2_TICKERS: tuple[str, ...] = (
    # ── Golden 8 ──────────────────────────────────────────────────────────────
    "HBL",    # bank — largest private commercial bank
    "LUCK",   # cement — test case; confirmed digital PDF
    "FFC",    # fertiliser — ENGRO replacement
    "MARI",   # oil & gas — confirmed digital PDF
    "NML",    # textile — Nishat Mills
    "HUBC",   # power — test case; confirmed digital PDF
    "SYS",    # technology — Systems Limited
    "NCL",    # unusual layout — Nishat Chunian; balance_ok=True in prior survey
    # ── 12 more for breadth ───────────────────────────────────────────────────
    "MCB",    # bank
    "UBL",    # bank
    "OGDC",   # oil & gas exploration
    "PPL",    # oil & gas
    "PSO",    # oil marketing
    "NESTLE", # FMCG
    "SEARL",  # pharma
    "GLAXO",  # pharma
    "INDU",   # auto — Indus Motor Company
    "KAPCO",  # power — Kot Addu Power
    "TRG",    # tech / BPO
    "DGKC",   # cement — D.G. Khan Cement
)
