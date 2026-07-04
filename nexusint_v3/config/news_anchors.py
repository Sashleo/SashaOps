"""
config/news_anchors.py — PSX sector relevance knowledge base.

For each PSX industry (keys match intelligence/keyword_engine.INDUSTRY_TAXONOMY) this
encodes THREE things used by intelligence/news_relevance.py:

  tickers  — representative PSX symbols in the sector (entity-match bonus)
  anchors  — plain-language "concept" phrases embedded once (MiniLM) and compared to
             each headline; captures *semantic* relevance beyond keyword matching
  drivers  — the cross-sector / commodity / macro factors that move the sector's
             earnings, each tagged with `effect`: the sign of the impact when that
             driver *rises*. Example: for cement, coal price has effect=-1 (coal up →
             margins down; coal DOWN → margins UP — the "coal got cheaper helps cement"
             correlation the product owner called out).

`effect` lets the sentiment layer combine (driver baseline sign) × (news up/down) to
state directional impact, e.g. "coal prices fell — positive for cement margins".

Sources: PSX sector cost structures, SBP monetary framework, and standard
Pakistan-equity-research driver maps (KIBOR↔banks, Brent↔E&P, coal↔cement,
cotton/PKR↔textile, gas feed/fuel↔fertilizer, circular debt↔power/E&P).

Everything is plain data — no imports, no I/O.
"""

from __future__ import annotations

# ── Macro drivers that affect the WHOLE market / most sectors ────────────────────
# effect = sign of impact on broad PSX earnings/sentiment when the driver RISES.
MACRO_ANCHORS: list[str] = [
    "State Bank of Pakistan monetary policy and interest rate decision",
    "consumer price index inflation reading for Pakistan",
    "IMF programme review, tranche disbursement and structural benchmarks",
    "Pakistan foreign exchange reserves and rupee dollar exchange rate",
    "Federal Board of Revenue tax collection and federal budget measures",
    "capital gains tax on shares and MSCI index rebalancing of PSX",
    "circular debt in the power and energy chain",
    "KSE-100 index movement and foreign investor flows",
]

MACRO_DRIVERS: list[dict] = [
    {"terms": ["policy rate", "interest rate", "sbp cuts", "sbp hikes", "monetary policy", "discount rate"], "effect": -1, "note": "higher rates raise financing cost for most leveraged sectors (banks are the exception)"},
    {"terms": ["inflation", "cpi", "sensitive price index", "spi"], "effect": -1, "note": "high inflation squeezes real demand and margins"},
    {"terms": ["imf", "extended fund facility", "bailout", "staff level agreement"], "effect": 1, "note": "IMF programme = macro stability, positive for market confidence"},
    {"terms": ["forex reserves", "foreign exchange reserves", "current account"], "effect": 1, "note": "stronger reserves support the rupee and imports"},
    {"terms": ["rupee depreciation", "pkr weakens", "rupee falls", "currency devaluation"], "effect": -1, "note": "broad negative (import cost, debt); exporters benefit separately (see sector effect)"},
    {"terms": ["rupee appreciation", "pkr strengthens", "rupee gains"], "effect": 1, "note": "eases import and debt-servicing cost"},
    {"terms": ["capital gains tax", "cgt", "super tax", "budget"], "effect": -1, "note": "higher equity/corporate taxes weigh on valuations"},
    {"terms": ["msci", "index rebalancing", "foreign buying", "foreign selling"], "effect": 1, "note": "index inflows lift large caps"},
    {"terms": ["circular debt"], "effect": -1, "note": "circular debt build-up hurts the whole energy chain"},
    {"terms": ["fbr", "tax collection", "revenue shortfall"], "effect": 1, "note": "revenue over-performance supports fiscal stability"},
]

# ── Per-sector knowledge base ────────────────────────────────────────────────────
# Keys MUST match keyword_engine.INDUSTRY_TAXONOMY keys.
SECTOR_ANCHORS: dict[str, dict] = {
    "cement": {
        "tickers": ["LUCK", "DGKC", "MLCF", "FCCL", "CHCC", "ACPL", "BWCL", "KOHC", "PIOC", "FECTC"],
        "anchors": [
            "cement dispatches, local sales and export volumes",
            "coal price and imported fuel cost for cement kilns",
            "public sector development programme and construction activity",
            "cement price increase and retention prices per bag",
        ],
        "drivers": [
            {"terms": ["coal price", "south african coal", "richards bay coal", "imported coal", "coal cost"], "effect": -1, "note": "coal ≈ 50% of cement cost; coal DOWN → margins UP"},
            {"terms": ["cement dispatches", "cement sales", "cement offtake", "cement exports"], "effect": 1, "note": "higher volumes = higher earnings"},
            {"terms": ["psdp", "development programme", "construction", "infrastructure spending", "housing scheme"], "effect": 1, "note": "construction demand drives cement"},
            {"terms": ["cement price", "retention price", "bag price"], "effect": 1, "note": "pricing power supports margins"},
            {"terms": ["interest rate", "policy rate"], "effect": -1, "note": "rates suppress construction financing"},
            {"terms": ["gas tariff", "power tariff", "energy cost"], "effect": -1, "note": "energy is a major kiln cost"},
        ],
    },
    "banking": {
        "tickers": ["HBL", "UBL", "MCB", "MEBL", "BAFL", "NBP", "BAHL", "AKBL", "FABL", "BOP"],
        "anchors": [
            "State Bank policy rate, KIBOR and treasury bill yields",
            "bank deposits, advances, spreads and net interest margin",
            "capital adequacy ratio, non-performing loans and provisioning",
            "digital and Islamic banking licences and minimum deposit rate",
        ],
        "drivers": [
            {"terms": ["policy rate", "kibor", "interest rate", "discount rate", "t-bill", "treasury bill", "pib", "bond yield"], "effect": 1, "note": "higher rates widen bank spreads/NIMs — banks are the rate exception"},
            {"terms": ["minimum deposit rate", "mdr"], "effect": -1, "note": "higher MDR raises deposit cost"},
            {"terms": ["non-performing loan", "npl", "infection ratio", "provisioning"], "effect": -1, "note": "credit losses hurt earnings"},
            {"terms": ["deposit growth", "advances", "credit offtake", "car", "capital adequacy"], "effect": 1, "note": "balance-sheet growth"},
            {"terms": ["adr tax", "advance to deposit ratio"], "effect": -1, "note": "ADR-linked taxation pressure"},
        ],
    },
    "oil_gas": {
        "tickers": ["OGDC", "PPL", "MARI", "POL", "PSO", "APL", "SHEL", "PRL", "NRL", "ATRL"],
        "anchors": [
            "international Brent crude oil price and global energy markets",
            "gas and oil discovery, exploration and production volumes",
            "circular debt settlement and gas wellhead price and tariff",
            "petroleum product offtake and OMC margins and inventory gains",
        ],
        "drivers": [
            {"terms": ["brent", "crude oil price", "oil price", "wti", "opec"], "effect": 1, "note": "E&P revenue is oil-price-linked"},
            {"terms": ["gas discovery", "oil discovery", "new well", "flow rate", "exploration success"], "effect": 1, "note": "reserves growth"},
            {"terms": ["circular debt", "receivables settlement", "energy sukuk"], "effect": 1, "note": "settlement releases trapped cash for E&P"},
            {"terms": ["wellhead price", "gas price", "gas tariff hike"], "effect": 1, "note": "higher realised gas price"},
            {"terms": ["rupee depreciation", "pkr weakens"], "effect": 1, "note": "revenue is USD-linked for E&P"},
            {"terms": ["petroleum levy", "fuel demand", "petrol sales", "diesel sales"], "effect": 1, "note": "OMC volumes"},
        ],
    },
    "fertilizer": {
        "tickers": ["FFC", "EFERT", "ENGRO", "FATIMA", "FFBL", "AGL"],
        "anchors": [
            "urea and DAP offtake, inventory and farmer demand",
            "feed and fuel gas price, subsidy and RLNG for fertilizer plants",
            "international urea and DAP prices and crop support prices",
        ],
        "drivers": [
            {"terms": ["urea offtake", "dap offtake", "fertilizer sales", "urea demand"], "effect": 1, "note": "volumes drive earnings"},
            {"terms": ["gas price", "feed gas", "fuel gas", "gas tariff", "rlng"], "effect": -1, "note": "gas is the key feedstock cost"},
            {"terms": ["gas subsidy", "concessionary gas", "subsidised feedstock"], "effect": 1, "note": "subsidy protects margins"},
            {"terms": ["urea price", "dap price", "fertilizer price"], "effect": 1, "note": "pricing power"},
            {"terms": ["support price", "crop price", "wheat price", "farmer income"], "effect": 1, "note": "farm economics drive demand"},
            {"terms": ["gidc", "gas infrastructure development cess"], "effect": -1, "note": "GIDC is a cost/liability overhang"},
        ],
    },
    "textile": {
        "tickers": ["ILP", "NML", "GATM", "KTML", "NCL", "FML", "MTL", "ATRL", "SML"],
        "anchors": [
            "textile and apparel exports and international buyer demand",
            "raw cotton spot rate, cotton arrivals and yarn prices",
            "rupee dollar exchange rate impact on exporters",
            "energy tariff, RCET regionally competitive energy for exporters",
        ],
        "drivers": [
            {"terms": ["rupee depreciation", "pkr weakens", "rupee falls", "devaluation"], "effect": 1, "note": "exporters earn in USD — weak rupee helps"},
            {"terms": ["cotton price", "raw cotton", "cotton arrivals", "yarn price", "lint"], "effect": -1, "note": "cotton is the main input cost"},
            {"terms": ["textile exports", "apparel exports", "export orders", "gsp plus", "buyer demand"], "effect": 1, "note": "demand-driven"},
            {"terms": ["rcet", "regionally competitive energy", "energy tariff", "gas tariff", "power tariff"], "effect": -1, "note": "energy cost for export sector"},
            {"terms": ["export rebate", "drawback", "refund"], "effect": 1, "note": "incentives support cash flow"},
        ],
    },
    "auto": {
        "tickers": ["INDU", "HCAR", "PSMC", "MTL", "GHNI", "AGTL", "SAZEW", "HINO"],
        "anchors": [
            "auto sales, car and motorcycle and tractor volumes",
            "rupee dollar rate impact on imported CKD kits",
            "auto financing regulations and interest rate on car loans",
        ],
        "drivers": [
            {"terms": ["car sales", "auto sales", "vehicle sales", "tractor sales", "motorcycle sales", "bookings"], "effect": 1, "note": "volumes"},
            {"terms": ["rupee depreciation", "pkr weakens", "ckd", "imported kits"], "effect": -1, "note": "imported kits get costlier"},
            {"terms": ["auto financing", "car loan", "interest rate", "policy rate"], "effect": -1, "note": "financing cost suppresses demand"},
            {"terms": ["steel price", "commodity cost"], "effect": -1, "note": "input cost"},
            {"terms": ["duty", "regulatory duty", "additional customs duty"], "effect": -1, "note": "import duties raise cost"},
        ],
    },
    "energy": {  # power generation / IPPs
        "tickers": ["HUBC", "KAPCO", "NPL", "NCPL", "LOTCHEM", "KEL", "PKGP"],
        "anchors": [
            "independent power producers, capacity payments and dividends",
            "circular debt and receivables from the power purchaser",
            "power tariff, dollar-indexed returns and IPP contract renegotiation",
        ],
        "drivers": [
            {"terms": ["circular debt", "receivables", "energy payments", "power purchaser", "cppa"], "effect": -1, "note": "unpaid receivables strangle IPP cash flow"},
            {"terms": ["capacity payment", "dividend", "power tariff"], "effect": 1, "note": "contracted returns"},
            {"terms": ["ipp renegotiation", "contract revision", "tariff cut"], "effect": -1, "note": "renegotiation trims returns"},
            {"terms": ["rupee depreciation"], "effect": 1, "note": "dollar-indexed tariffs benefit from weak rupee"},
        ],
    },
    "pharma": {
        "tickers": ["SEARL", "AGP", "HINOON", "GLAXO", "HALEON", "BFBIO", "ABOT", "FEROZ", "CPHL"],
        "anchors": [
            "DRAP drug pricing, deregulation and price increases",
            "imported active pharmaceutical ingredient cost and rupee rate",
            "pharmaceutical demand, exports and new product registration",
        ],
        "drivers": [
            {"terms": ["drap", "drug price", "price deregulation", "price increase", "hardship case"], "effect": 1, "note": "pricing freedom helps margins"},
            {"terms": ["api", "active pharmaceutical ingredient", "raw material import", "rupee depreciation"], "effect": -1, "note": "imported API cost"},
            {"terms": ["pharma exports", "registration", "who prequalification"], "effect": 1, "note": "demand expansion"},
        ],
    },
    "steel": {
        "tickers": ["ASTL", "ISL", "MUGHAL", "INIL", "ASL", "DSL"],
        "anchors": [
            "international steel scrap and HRC price and local rebar demand",
            "construction and infrastructure activity driving steel demand",
            "anti-dumping duty and energy cost for steel mills",
        ],
        "drivers": [
            {"terms": ["scrap price", "hrc", "billet", "international steel", "rebar price"], "effect": -1, "note": "input cost when rising; but pricing pass-through varies"},
            {"terms": ["construction", "infrastructure", "psdp", "rebar demand"], "effect": 1, "note": "demand"},
            {"terms": ["anti-dumping", "anti dumping duty", "regulatory duty"], "effect": 1, "note": "protects local mills"},
            {"terms": ["energy cost", "power tariff", "gas tariff"], "effect": -1, "note": "energy-intensive"},
        ],
    },
    "sugar": {
        "tickers": ["JDWS", "ALNRS", "SHFA", "HABSM", "MRNS"],
        "anchors": [
            "sugar support price, cane cost and crushing season",
            "sugar export permission and international sugar price",
        ],
        "drivers": [
            {"terms": ["support price", "cane price", "sugarcane"], "effect": -1, "note": "higher cane cost squeezes margins"},
            {"terms": ["sugar export", "export quota", "surplus sugar"], "effect": 1, "note": "exports clear inventory"},
            {"terms": ["sugar price", "ex-mill price"], "effect": 1, "note": "pricing"},
        ],
    },
    "tech": {
        "tickers": ["SYS", "AVN", "TRG", "NETSOL", "OCTOPUS", "PTC"],
        "anchors": [
            "IT and software exports and global technology demand",
            "rupee dollar rate impact on dollar-earning IT exporters",
            "tax exemption on IT exports and special technology zones",
        ],
        "drivers": [
            {"terms": ["it exports", "software exports", "remittance", "dollar revenue", "tech demand"], "effect": 1, "note": "USD-earning exporters"},
            {"terms": ["rupee depreciation", "pkr weakens"], "effect": 1, "note": "weak rupee boosts export earnings"},
            {"terms": ["tax exemption", "it export tax", "special technology zone", "pseb"], "effect": 1, "note": "policy support"},
        ],
    },
    "fmcg": {
        "tickers": ["NESTLE", "EFOODS", "NATF", "UPFL", "COLG", "PAKT", "FCEPL"],
        "anchors": [
            "consumer demand, volumes and pricing for packaged food and staples",
            "commodity input cost (palm oil, milk, wheat) and rupee rate",
        ],
        "drivers": [
            {"terms": ["consumer demand", "volume growth", "offtake", "purchasing power"], "effect": 1, "note": "demand"},
            {"terms": ["palm oil", "milk price", "wheat", "commodity cost", "input cost"], "effect": -1, "note": "raw material cost"},
            {"terms": ["inflation", "price increase"], "effect": -1, "note": "erodes real consumption"},
            {"terms": ["rupee depreciation"], "effect": -1, "note": "imported inputs get costlier"},
        ],
    },
    "chemicals": {
        "tickers": ["EPCL", "LOTCHEM", "ICI", "COLG", "BERGER", "ARPL", "GGL"],
        "anchors": [
            "international PVC, PTA and chemical prices and margins",
            "gas and energy feedstock cost for chemical plants",
        ],
        "drivers": [
            {"terms": ["pvc price", "pta price", "pvc-ethylene", "core delta", "international chemical"], "effect": 1, "note": "product margins"},
            {"terms": ["gas price", "energy cost", "feedstock"], "effect": -1, "note": "input cost"},
            {"terms": ["rupee depreciation"], "effect": -1, "note": "imported feedstock cost"},
        ],
    },
    "telecom": {
        "tickers": ["PTC", "TELE", "WTL"],
        "anchors": [
            "telecom subscribers, ARPU, data usage and spectrum auction",
            "PTA regulation, taxes on telecom and 4G/5G rollout",
        ],
        "drivers": [
            {"terms": ["arpu", "data revenue", "subscribers", "4g", "5g", "broadband"], "effect": 1, "note": "usage growth"},
            {"terms": ["spectrum auction", "license fee"], "effect": -1, "note": "capex/cost"},
            {"terms": ["telecom tax", "advance tax", "sim tax"], "effect": -1, "note": "demand drag"},
        ],
    },
}


def sectors() -> list[str]:
    return list(SECTOR_ANCHORS.keys())


def get_sector_profile(industry: str) -> dict:
    """Return the anchor profile for an industry key, or an empty profile."""
    return SECTOR_ANCHORS.get((industry or "").strip().lower(), {"tickers": [], "anchors": [], "drivers": []})
