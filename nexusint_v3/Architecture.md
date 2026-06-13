# SashaOps — Architecture Specification

## Directory Structure

```
nexusint_v3/
│
├── CLAUDE.md                    # Master build instructions (read this first)
├── ARCHITECTURE.md              # This file
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env / .env.example
├── app.py                       # FastAPI application entry point
│
├── config/
│   ├── regions.py               # Region definitions (Pakistan primary)
│   ├── modules.py               # All 19+ analysis module definitions
│   └── settings.py              # App constants and env config loader
│
├── security/
│   ├── crypto.py                # AES-256-GCM encryption, Argon2id hashing, HKDF key derivation
│   ├── pqc.py                   # ML-KEM-768 + X25519 hybrid KEM; ML-DSA report signing
│   ├── auth.py                  # Login, TOTP 2FA, session management, lockout
│   └── audit.py                 # Hash-chained audit log (SHA-256 chain)
│
├── database/
│   ├── connection.py            # SQLCipher connection manager (AES-256 encrypted SQLite)
│   ├── schema.py                # Table definitions + migration runner
│   ├── clients.py               # Client CRUD (per-client HKDF key derivation)
│   ├── analyses.py              # Analysis run results storage (per-module, encrypted)
│   ├── documents.py             # Document upload metadata + text extraction + encrypted storage
│   │                            #   get_document_bytes() → raw decrypted bytes for local processing
│   │                            #   get_documents_text_for_client() → extracted text (50k char cap)
│   ├── memory.py                # Memory entries and anonymised pattern storage
│   ├── macro_data.py            # Cached Pakistan macro data (SBP rates, World Bank, sector peers)
│   ├── psx_data.py              # PSX company data cache
│   ├── exchange_data.py         # Exchange price/volume cache
│   ├── watchlist.py             # Client watchlist entries
│   ├── clusters.py              # Company cluster/sector groupings
│   └── client_entities.py      # Named entities associated with clients
│
├── core/
│   ├── ai_client.py             # Gemini API wrapper: call_gemini_raw(), streaming, retry
│   ├── run_manager.py           # Stage gate state machine (see Stage Gate System below)
│   ├── memory_engine.py         # Memory injection + pattern learning
│   ├── privacy_wrapper.py       # Security context for every Gemini prompt
│   ├── sanitiser.py             # TokenSheet class + PII scrubber; restore() = pure dict sub
│   ├── output_validator.py      # Validates Gemini responses against token sheet; retry + fallback
│   ├── narrative_fallbacks.py   # Jinja2 sentence templates per module, filled from token sheet
│   ├── feed_cache.py            # In-memory RSS feed cache (24h TTL)
│   └── exchange_scheduler.py   # Background exchange data refresh scheduler
│
├── ml/                          # Local ML — CPU-only; lazy-loaded; enabled via LOCAL_ML=True
│   ├── relevance.py             # Headline ranking: sentence-transformers/all-MiniLM-L6-v2
│   │                            #   rank(headlines, company_context, top_k=15) → ranked list
│   ├── sentiment.py             # Financial sentiment: yiyanghkust/finbert-tone
│   │                            #   score(texts) → [{label, confidence}] → {{SENTIMENT_*}} tokens
│   ├── anomaly.py               # Price anomaly: sklearn IsolationForest on company_prices
│   │                            #   detect(prices) → anomaly dates → {{ANOMALIES}} token
│   └── trends.py                # Trend forecasting: statsmodels ARIMA on index/forex
│                                #   forecast(series) → {direction, band} → Economy module tokens
│
├── intelligence/
│   │
│   ├── # ── Data collection ──────────────────────────────────────
│   ├── feeds.py                 # RSS feed fetching + headline classification
│   ├── browser.py               # Playwright headless browser (website scraping)
│   ├── search.py                # DuckDuckGo search + result synthesis (no API key needed)
│   ├── stocks.py                # yfinance regional exchange data + global sector peers
│   ├── news_analyser.py         # Feed headline analysis for client-relevant news
│   ├── query_templates.py       # Keyword-driven search query builder (no AI)
│   ├── keyword_engine.py        # Industry taxonomy matching + scraper selector
│   │                            #   detect_industry(text, sector) → industry key
│   │                            #   INDUSTRY_TAXONOMY — term signals per industry
│   │                            #   INDUSTRY_SCRAPERS — industry → scraper list
│   │
│   ├── # ── PSX-specific ─────────────────────────────────────────
│   ├── psx_company_profile.py   # PSX company profile fetcher (sector, website, summary)
│   ├── psx_financials.py        # PSX financial statement data (summary financials)
│   ├── psx_report_parser.py     # PDF annual report parser + OCR fallback
│   │                            #   _LABELS — financial line item aliases (re-used by doc_financials)
│   │                            #   _parse_financials(text) → {revenue, net_income, ...}
│   │                            #   fetch_psx_report_financials(ticker) → financials dict
│   │
│   ├── # ── Financial modelling ──────────────────────────────────
│   ├── financial_model.py       # Quantitative analysis engine (pure Python, no AI)
│   │                            #   analyse_from_document(financials_dict) → FinancialAnalysis
│   │                            #   run_local_analysis_psx(ticker) → flat dict for Stage 2
│   │                            #   run_local_analysis(ticker, region) → yfinance fallback
│   │                            #   Computes: ratios, Altman Z-Score, DuPont, DCF, projections
│   │
│   ├── # ── Document intelligence (NEW) ──────────────────────────
│   ├── document_financials.py   # Fully local doc intelligence — no AI calls
│   │                            #   extract_financials_from_text(text, bytes, type) → dict
│   │                            #     Stage 1: pdfplumber.extract_tables() (structured)
│   │                            #     Stage 2: _parse_financials regex (text fallback)
│   │                            #     Stage 3: pandas read (XLSX/CSV)
│   │                            #   extract_company_context(text) → {industry, geography, ...}
│   │                            #     spaCy en_core_web_sm: GPE/MONEY/ORG entities
│   │                            #     keyword_engine.detect_industry() for taxonomy match
│   │                            #   run_document_financial_analysis(...) → same dict as PSX path
│   │
│   ├── # ── AI analysis modules ──────────────────────────────────
│   ├── extended_analysis.py     # Extended AI module definitions (Porter's, BMC, ESG, etc.)
│   │
│   ├── # ── Government / regulatory scrapers ────────────────────
│   └── gov_scrapers/
│       ├── base.py              # BasePakGovScraper — cache, retry, error handling
│       ├── worldbank_pak.py     # World Bank Pakistan macro indicators
│       ├── sbp_monetary.py      # State Bank of Pakistan monetary policy + rates
│       ├── ogra_prices.py       # OGRA petroleum/gas prices
│       ├── pbs_lsmi.py          # PBS large-scale manufacturing index
│       ├── fbr_revenue.py       # FBR tax revenue statistics
│       ├── nepra_power.py       # NEPRA power sector statistics
│       ├── pta_stats.py         # PTA telecom sector statistics
│       ├── ministries.py        # Key ministry policy feeds (Finance, Energy, IT, etc.)
│       ├── associations.py      # Industry association scrapers (APCMA, APTMA, PAMA, etc.)
│       └── __init__.py          # get_scrapers_for_run(industry) — selects relevant scrapers
│
│   └── exchange_scrapers/
│       ├── psx_market.py        # PSX live market indices + volume
│       └── central_banks/
│           └── sbp_pakistan.py  # SBP forex rates
│
│   └── exchange_crawlers/
│       └── psx.py               # PSX company page crawler (announcements, financials)
│
├── routers/
│   ├── auth.py                  # Login, logout, TOTP 2FA
│   ├── dashboard.py             # Command centre, client management
│   ├── intelligence.py          # Analysis run flow (Stage 1→2→3 + gate endpoints)
│   │                            #   POST /intelligence/start → create run + Stage 1
│   │                            #   POST /intelligence/gate/{id}/approve → advance stage
│   │                            #   GET  /intelligence/run/{id}/status → poll status
│   │                            #   POST /intelligence/refresh-scrapers → on-demand scraper run
│   ├── markets.py               # Market data visualisation + watchlists
│   ├── documents.py             # Document upload, list, delete
│   ├── reports.py               # Report history + download
│   ├── feeds.py                 # News feed management
│   ├── memory.py                # Memory lab (pattern review)
│   ├── settings.py              # App settings
│   └── debug.py                 # Debug utilities (dev only)
│
├── templates/                   # Jinja2 HTML templates (HTMX-powered)
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── intelligence.html        # Main analysis page + Data Sources card
│   ├── markets.html
│   ├── documents.html
│   ├── reports.html
│   ├── feeds.html
│   ├── memory.html
│   └── settings.html
│
├── static/
│   ├── css/main.css             # Dark terminal aesthetic
│   └── js/htmx.min.js
│
└── data/                        # Runtime data (gitignored)
    ├── sashaops.db              # Encrypted SQLite (SQLCipher)
    └── documents/               # Encrypted client documents
        └── {client_id}/
            └── {doc_id}.enc

.tmp/                            # Temporary cache (regenerated as needed, gitignored)
├── gov_cache/                   # Daily gov scraper snapshots
├── psx_reports/                 # Cached PSX annual report PDFs
└── feed_cache/                  # RSS feed cache
```

---

## Data Flow: Listed Company (Track A)

```
User enters "Lucky Cement"
        │
        ▼
Stage 1 — Data Collection
  _guess_ticker("Lucky Cement") → "LUCK"
  ├── psx_company_profile.get_psx_company_profile("LUCK")
  │     → {sector: "Cement", website: "luckycementltd.com", ...}
  ├── keyword_engine.detect_industry("", "Cement") → "cement"
  ├── gov_scrapers: CORE + associations["cement"] (APCMA)
  ├── browser.extract_company_website_content("Lucky Cement", "LUCK", url)
  ├── search.multi_search(cement-specific queries)
  └── feeds: classify headlines against "Lucky Cement"

        │
        ▼
Stage 1 complete → local_financial_result from run_local_analysis_psx("LUCK"):
  psx_report_parser.fetch_psx_report_financials("LUCK")
    → downloads annual report PDF → OCR if needed
    → _parse_financials(text) → {revenue: X, net_income: Y, total_assets: Z, ...}
  financial_model.analyse_from_document(financials_dict)
    → ratios, Altman Z, DuPont, DCF, 3-year projections
  → ratios_text (formatted string of computed numbers)

        │
        ▼
Stage 2 — Module Analysis (Gemini)
  Gemini receives: ratios_text + market data + search snippets + feed headlines
  Gemini does NOT receive: raw PDF, annual report text, specific extracted figures
  → 19 module outputs (financial commentary, competitors, valuation, etc.)
```

---

## Data Flow: Unlisted/Private Company (Track B)

```
User enters "ABC Private Ltd" + uploads business_plan.pdf
        │
        ▼
Stage 1 — Data Collection
  _guess_ticker("ABC Private Ltd") → "" (not listed)
  ├── document text extraction (pdfplumber → pytesseract OCR if needed)
  ├── document_financials.extract_company_context(doc_text) via spaCy:
  │     → industry: "textile", geography: ["Lahore", "Punjab"],
  │       keywords: ["fabric", "export", "loom", "cotton"], business_type: "manufacturing"
  ├── gov_scrapers: CORE + associations["textile"] (APTMA) — selected by detected industry
  ├── browser.extract_company_website_content(mentioned_in_doc)
  ├── search.multi_search(doc-extracted keywords — not a PSX sector code)
  ├── feeds: classify headlines against detected industry/company
  └── document_financials.run_document_financial_analysis(doc_bytes, doc_text, "pdf"):
        Stage 1: pdfplumber.extract_tables() → table rows → _parse_financials()
        Stage 2: _parse_financials(raw_text) regex fallback
        → {revenue: X, net_income: Y, ...} if financial tables found
        → analyse_from_document(dict) → ratios, DCF, Altman Z
        → local_financial_result dict (same format as Track A)

        │
        ▼
Stage 1 complete → local_financial_result (from doc extraction, same dict shape as Track A)

        │
        ▼
Stage 2 — Module Analysis (Gemini)
  _local_blocking() checks data_summary.local_financial_result first (Track B priority)
  Gemini receives: ratios_text + keyword-driven search results + gov data + headlines
  Gemini does NOT receive: raw business plan text, original financial figures
  → same 19 module outputs as Track A
```

---

## AI Privacy Boundary

```
┌──────────────────────── LOCAL MACHINE ─────────────────────────────┐
│                                                                      │
│  Uploaded documents ──→ pdfplumber / pytesseract / pandas           │
│                     ──→ psx_report_parser._parse_financials()       │
│                     ──→ financial_model.analyse_from_document()     │
│                     ──→ ratios_text (computed numbers only)         │
│                                                                      │
│  spaCy NLP          ──→ industry / geography / keywords             │
│                     ──→ search query seeds (not sent to Gemini)     │
│                                                                      │
│  DuckDuckGo         ──→ public web snippets about the company       │
│  Gov scrapers       ──→ public regulatory data                      │
│  RSS feeds          ──→ public news headlines                       │
│                                                                      │
│  privacy_wrapper.py ──→ sanitiser strips client names + figures     │
│                                                                      │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │ Only this crosses to Gemini:
                                   ▼
┌──────────────────────── GEMINI API ────────────────────────────────┐
│  ratios_text (abstract numbers: "current_ratio: 1.8")              │
│  market data (PSX index levels, PKR/USD)                           │
│  DuckDuckGo snippets (public web)                                  │
│  feed headlines (public news)                                      │
│  gov data summaries (public regulatory data)                       │
└────────────────────────────────────────────────────────────────────┘
```

---

## LOCAL_ONLY Mode — Zero-API Pipeline

`settings.LOCAL_ONLY=True` (default) makes the entire pipeline run without any external
API calls. Every module produces structured, data-driven output using only:
- PSX financial data (annual statements, ratios, market prices)
- Government data scrapers (SBP, FBR, World Bank — public endpoints)
- RSS feed headlines (relevance-ranked by sentence-transformers)
- DuckDuckGo search snippets (no API key)
- FinBERT sentiment scoring (local CPU-only model)
- PSX sector peer comparison tables

### Stage 2 execution path

```
For each module in module_list:

  LOCAL_ONLY=True   → render_local_module(mod_id, local_result, data_summary, ...)
                        → core/narrative_fallbacks.py
                        → Returns structured markdown (tables, verdicts, signals)
                        → Banner: "⚙️ Computed locally — data-driven, zero API calls"

  LOCAL_ONLY=False  → Gemini path (token-contract, sanitiser, validator)
  + local_data_sufficient=True → local fast-path (financial/valuation only)
                        → Falls through to Gemini if local render fails
                        → Banner: "✨ AI-enhanced"
```

### Per-module output sources

| Module | Data Source | LOCAL_ONLY output |
|--------|-------------|-------------------|
| financial | PSX statements + ratios engine | Full Altman Z, DuPont, DCF, FinBERT sentiment |
| valuation | DCF + PSX price | DCF scenarios, fair value vs market |
| sentiment | FinBERT on PSX announcements + feeds | Label distribution + top headlines |
| economy | Macro DB + KSE + forex | Rate table + rate sensitivity analysis |
| competitors | PSX sector peer DB | Peer comparison table + relative positioning |
| overview | Financial signals + news brief | Profile card + SWOT from computed signals |
| country | Macro DB + news brief | Regulatory landscape + sector indicators |
| supplychain | Working capital from ratios + docs | WC cycle table, honest gap disclosure |
| scaling | Revenue projections + peers | Growth trajectory + 3-year projections |
| marketing | Website scrape + search | Digital presence data or gap disclosure |
| worldnews | RSS feeds (relevance-ranked) | Curated headline digest |
| Extended modules | Available data snippets | Generic data card + honest gap disclosure |

All modules include a "⚙️ Computed locally" or "✨ AI-enhanced" banner so the
user always knows which path produced the output.

---

## Stage Gate System (`core/run_manager.py`)

```
create_run() ──→ STAGE1_COLLECTING ──→ complete_stage1()
                                              │
                              GATED: STAGE1_COMPLETE (gate — user reviews)
                              EXPRESS: auto-advance
                                              │
                                       approve_stage1() or auto
                                              │
                                     STAGE2_ANALYSING ──→ complete_stage2()
                                              │
                              GATED: STAGE2_COMPLETE (gate — user reviews)
                              EXPRESS: auto-advance
                                              │
                                       approve_stage2() or auto
                                              │
                                     STAGE3_REPORTING ──→ complete_stage3()
                                              │
                                           COMPLETE
```

Failed modules don't stop the run — they're marked FAILED and the run continues.
Interrupted runs resume from last completed stage via `load_run(run_id)`.

---

## Token-Contract Generation

Every value entering a Gemini prompt is replaced by a `{{TOKEN_N}}` placeholder.
Gemini writes prose using only tokens. Real values are substituted back locally.

```
Python computes                    TokenSheet                     Gemini sees
─────────────────                  ──────────                     ──────────────
current_ratio = 1.8   ──add()──→   {{RATIO_CR}}    ──in prompt──→ {{RATIO_CR}}
company = "Lucky"     ──add()──→   {{COMPANY}}     ──in prompt──→ {{COMPANY}}
                                                  ←──restore()──  {{RATIO_CR}} → 1.8
```

Flow per module:
1. `TokenSheet.add(value)` — registers every entity + computed value
2. Prompt template uses only tokens (no real values)
3. Gemini response contains tokens
4. `OutputValidator.validate(response, sheet)` — checks required tokens present,
   no leaked digits or names; retry once on violation; fallback to Jinja2 template
5. `TokenSheet.restore(response)` — pure dict substitution → final prose

---

## Local ML Layer

CPU-only models loaded once at container start. `settings.LOCAL_ML=True` enables them.
Each module lazy-loads with graceful fallback (no crash if model missing).

```
Stage 1 pipeline                   ML enrichment                  Token sheet
────────────────                   ────────────                   ──────────────
Feed headlines       ──relevance──→  top 15 relevant              (ranked headlines)
                     ──sentiment──→  [{label, conf}, ...]  ──→    {{SENTIMENT_BULL}}
                                                                   {{SENTIMENT_BEAR}}
company_prices       ──anomaly───→  [anomaly_dates]        ──→    {{ANOMALIES}}
index/forex series   ──ARIMA─────→  {direction, band}     ──→    {{TREND_DIR}}
                                                                   {{TREND_BAND}}
```

Models are pre-downloaded at Docker build time — no network calls at runtime.

---

## Key Architectural Decisions

### Why FastAPI + HTMX
All backend logic in Python. HTMX handles reactive UI with partial page swaps.
No JavaScript framework. HTMX auto-poll every 4s updates run status without WebSockets.

### Why SQLCipher
AES-256 encrypted SQLite. No plaintext on disk. Unreadable without master password.

### Per-Client Key Isolation
Master key → HKDF(client_salt) → client key. One compromised client cannot expose others.
Client key lives in session memory only.

### Minimal AI Principle
Gemini writes prose. Python computes everything else. This keeps sensitive data local,
reduces API costs, and makes results deterministic and auditable.

### Token-Contract Principle
No real value (ratio, name, figure) ever appears verbatim in a Gemini prompt.
Tokens ensure that even if a prompt were intercepted, no sensitive data is exposed.
`restore()` is pure Python dict substitution — deterministic, no AI involved.

### spaCy for NLP
`en_core_web_sm` (~15MB) runs offline after first download. Provides entity recognition
(locations, money, organisations) and noun chunk extraction for keyword seeding.
No training required. No external calls.

### Document Text Caps
Extraction: 50,000 chars total, 20,000 per document.
Stage 2 injection: 30,000 chars (Gemini Flash context window: ~1M tokens — no concern).

### Report Signing
Every PDF signed with ML-DSA (post-quantum). Signature stored separately.
Verification is advisory — AES-256-GCM decryption already guarantees file integrity.

### Report Signing
Every PDF signed with ML-DSA (post-quantum). Signature stored separately. Clients can
verify report authenticity and integrity.
