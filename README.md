# SashaOps

**Private, local-first business intelligence for the Pakistan (PSX) market.**

SashaOps runs full strategic analyses on companies — listed on the Pakistan Stock
Exchange or private/unlisted — and produces structured, signed intelligence reports.
Every figure, ratio, and relevance score is computed **locally**; sensitive company
data never leaves the machine.

Built with FastAPI + HTMX, SQLCipher (AES-256 encrypted storage), local CPU ML, and
post-quantum cryptography, all containerised with Docker.

---

## Why it exists

Most "AI analyst" tools ship your private financials to a third-party API. SashaOps
inverts that: **the maths, parsing, and modelling are deterministic local Python**, and
the (optional) language model only ever sees pre-computed ratios and public web
summaries. That keeps client data private, results reproducible, and API costs near zero.

---

## Key features

### 🔒 Privacy-first architecture
- Raw company/document text **never** reaches an external API — only computed ratio
  summaries do.
- Two-layer sanitisation: entity anonymisation (`Company A`, `the regulator`) **then**
  value tokenisation (`{{TOKEN_CR}}`), applied before any outbound call and reversed on
  the way back, in strict order.
- AES-256 encrypted SQLite (SQLCipher), per-client key isolation (HKDF), Argon2id
  password hashing, TOTP 2FA, hash-chained audit log, and ML-KEM / ML-DSA
  (post-quantum) key exchange and report signing.

### 🧮 Quantitative financial engine (no external data vendor)
- Full ratio analysis (liquidity, profitability, leverage, efficiency, growth),
  **Altman Z-Score**, **DuPont** ROE decomposition, **DCF** valuation (bear/base/bull),
  3-year projections, working-capital cycle, and sensitivity analysis.
- The DCF risk-free rate is driven by the **live State Bank of Pakistan policy rate**,
  so valuations reflect actual monetary conditions.
- Reads directly from scraped PSX financial statements — no yfinance, no paid feed.

### 📊 Competitor benchmarking
- Auto-selects the **top peers by market share** in a company's PSX sector (or honours
  your market-tab watchlist), then **computes each peer's ratios** and renders a
  side-by-side comparison table with peer medians and relative-positioning verdicts.

### 📰 Deterministic news-relevance engine
- A local scorer blends a **PSX entity dictionary**, **per-sector driver knowledge**,
  and MiniLM semantic similarity to rank headlines by true relevance — no external
  vector database.
- Encodes **cross-sector correlations with direction**: e.g. *coal prices falling* is
  flagged as a **tailwind for cement margins**; *KIBOR rising* as a tailwind for banks;
  *rupee depreciation* as a tailwind for textile/IT exporters but a headwind for
  auto/pharma importers.
- Junk (unrelated global headlines) is filtered out; each qualitative section shows only
  genuinely relevant news, once.

### 🧠 Local ML layer (CPU-only, offline)
- FinBERT financial sentiment, MiniLM headline ranking, IsolationForest price-anomaly
  detection, ARIMA trend forecasting — all run in-container with no runtime network calls.

### 📄 Signed, formatted reports
- Generates PDF **and** DOCX reports with proper headings, tables, and formatting, each
  cryptographically signed (ML-DSA) for authenticity.

---

## The 19 analysis modules

**Core (11):** Overview · Competitors · Sentiment · Marketing · Financials · Country
Intel · Supply Chain · Scaling · Valuation · Economy · World Intel

**Extended (8):** Porter's Five Forces · Business Model Canvas · ESG · Regulatory Risk ·
Management Quality · PESTLE · Scenario Planning · Digital Maturity

Each module is rendered from computed local data. Where data is genuinely missing, the
report states exactly what's absent instead of padding with filler.

---

## How it works — the 3-stage pipeline

```
Stage 1 · Collect   → PSX profile, market data, gov/regulatory scrapers, web search,
                       website scrape, relevance-scored news, local financials
Stage 2 · Analyse   → build + sanitise per-module context, render all 19 modules
Stage 3 · Report    → build + ML-DSA-sign the encrypted PDF/DOCX, mark run complete
```

Runs are managed by a resumable state machine with **Gated** (review at each stage) and
**Express** (auto-advance) modes, and can be cancelled mid-run.

### Two analysis tracks
- **Listed:** PSX ticker → statement/OCR parser → local financial engine.
- **Unlisted/private:** uploaded documents → local extraction (pdfplumber + OCR + spaCy
  NLP) → the *same* financial engine.

---

## Tech stack

| Layer | Tech |
|-------|------|
| Web | FastAPI, HTMX, Jinja2 (no JS framework) |
| Storage | SQLCipher (AES-256 SQLite), encrypted document store |
| ML/NLP | sentence-transformers (MiniLM), FinBERT, scikit-learn, statsmodels, spaCy |
| Security | Argon2id, TOTP, liboqs (ML-KEM-768 / ML-DSA), hash-chained audit |
| Data | PSX scrapers, SBP/FBR/NEPRA/OGRA/PBS/World Bank scrapers, RSS, DuckDuckGo |
| Runtime | Python 3.11, Docker |

---

## Getting started

> Requires Docker.

```bash
# 1. Configure secrets (copy the template and fill in your values)
cp nexusint_v3/.env.example nexusint_v3/.env
#   set GOOGLE_API_KEY (optional — only used if you enable the AI path),
#       APP_SECRET_KEY, and DB_MASTER_PASSWORD

# 2. Build & run
cd nexusint_v3
docker compose up -d --build

# 3. Open the app
#   https://localhost:8000   (health check: /health)
```

Code is volume-mounted, so after a change: `docker compose restart sashaops`
(no rebuild needed).

### Configuration highlights (`config/settings.py`, via `.env`)

| Setting | Default | Meaning |
|---------|---------|---------|
| `LOCAL_ONLY` | `true` | Run the whole pipeline locally with zero external AI calls |
| `LOCAL_ML` | `true` | Enable the local CPU ML models |
| `ENABLE_PROMPT_SANITISATION` | `true` | Strip entities/PII before any outbound AI call |
| `GOOGLE_API_KEY` | — | Only used when `LOCAL_ONLY=false` |

---

## Security note

Never commit `.env`, the database, or the `data/` directory — they are gitignored.
`.env.example` documents the required variables with placeholder values only.

---

_SashaOps — private business intelligence, built for emerging markets._
