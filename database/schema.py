"""
database/schema.py — Pakistan-native schema and migration runner.

SashaOps stores only Pakistani data. There is no region_id / exchange_id coupling —
the single market is Pakistan (PSX). Government and regulatory data each have a
purpose-built table.

Core tables (region-agnostic):
- app_settings, users, clients, documents, reports, audit_log, pqc_keys, sessions

PSX market tables:
- psx_index, psx_companies, psx_prices, psx_financials, psx_announcements, psx_watchlist

Macro / monetary / fiscal:
- pkr_forex, sbp_indicators, pk_macro, pbs_lsmi, pbs_trade

Sector regulators:
- fbr_revenue, nepra_power, ogra_prices, pta_telecom, industry_stats

Benchmarks & ops:
- global_sector_peers, scraper_runs

NOTE: A one-time full reset runs automatically if a legacy multi-region schema is
detected (see _legacy_schema_present). Pre-production — no data is preserved.
"""

import structlog
from database.connection import get_db

log = structlog.get_logger()

SCHEMA_VERSION = 3

# Legacy tables from the old multi-region schema. Their presence triggers a full reset.
_LEGACY_TABLES = (
    "exchange_indices", "forex_rates", "exchange_companies", "company_prices",
    "exchange_bulletins", "company_announcements", "company_clusters",
    "cluster_members", "watchlist",
)

TABLES = """
-- ── App Settings ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- ── Users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    totp_secret     TEXT,
    totp_secret_pending TEXT,
    totp_enabled    INTEGER DEFAULT 0,
    master_salt     BLOB,
    created_at      TEXT NOT NULL,
    last_login      TEXT
);

-- ── Clients (Pakistan-only; no region columns) ───────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    industry        TEXT,
    accent_color    TEXT DEFAULT '#00ff87',
    salt            BLOB NOT NULL,
    notes_encrypted BLOB,
    created_at      TEXT NOT NULL,
    last_accessed   TEXT,
    is_active       INTEGER DEFAULT 1,
    kem_public_key      BLOB,
    kem_private_key_enc BLOB,
    kem_ciphertext      BLOB,
    key_version         INTEGER DEFAULT 1
);

-- ── Documents ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL,
    filename        TEXT NOT NULL,
    doc_type        TEXT,
    file_size       INTEGER,
    content_path    TEXT NOT NULL,
    summary_encrypted BLOB,
    upload_at       TEXT NOT NULL,
    last_analysed   TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

-- ── Reports ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    id              TEXT PRIMARY KEY,
    client_id       TEXT NOT NULL,
    business_name   TEXT NOT NULL,
    report_type     TEXT NOT NULL,
    modules_included TEXT,
    pdf_path        TEXT,
    docx_path       TEXT,
    signature_path  TEXT,
    signing_algo    TEXT,
    created_at      TEXT NOT NULL,
    delivered_at    TEXT,
    delivery_method TEXT,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

-- ── Audit Log ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    user_id     TEXT,
    client_id   TEXT,
    details     TEXT,
    ip_address  TEXT,
    timestamp   TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL UNIQUE
);

-- ── PQC Keys ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pqc_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key_type        TEXT NOT NULL,
    algorithm       TEXT NOT NULL,
    public_key      BLOB NOT NULL,
    private_key_encrypted BLOB NOT NULL,
    fingerprint     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    is_active       INTEGER DEFAULT 1
);

-- ── Sessions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT    PRIMARY KEY,
    payload_enc  BLOB    NOT NULL,
    client_id    TEXT,
    ip_ua_hash   TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    last_seen    TEXT    NOT NULL
);

-- ══ PSX market tables ════════════════════════════════════════════════════════

-- KSE index daily snapshot (from PSX market summary, not yfinance)
CREATE TABLE IF NOT EXISTS psx_index (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    index_code   TEXT NOT NULL,            -- KSE100 | KSE30 | KMI30 | ALLSHR
    trade_date   TEXT NOT NULL,
    close_value  REAL,
    change_pts   REAL,
    change_pct   REAL,
    volume       INTEGER,
    market_cap   REAL,
    fetched_at   TEXT NOT NULL,
    UNIQUE(index_code, trade_date)
);

-- All PSX-listed companies (from dps.psx.com.pk/symbols)
CREATE TABLE IF NOT EXISTS psx_companies (
    symbol       TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    sector_name  TEXT,
    is_debt      INTEGER DEFAULT 0,
    website      TEXT,
    market_cap   REAL,
    last_updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_psx_companies_sector
    ON psx_companies(sector_name);

-- Daily/most-recent company prices (from PSX market summary)
CREATE TABLE IF NOT EXISTS psx_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    current     REAL,
    ldcp        REAL,
    change_pct  REAL,
    volume      INTEGER,
    fetched_at  TEXT NOT NULL,
    UNIQUE(symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_psx_prices_symbol
    ON psx_prices(symbol, trade_date);

-- PSX company financials (Tables 4/5/6 of the company page) — replaces yfinance
CREATE TABLE IF NOT EXISTS psx_financials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    period        TEXT NOT NULL,           -- e.g. FY2024 | Q3-2024
    period_type   TEXT NOT NULL,           -- annual | quarterly
    revenue       REAL,
    gross_profit  REAL,
    ebitda        REAL,
    net_income    REAL,
    total_assets  REAL,
    total_equity  REAL,
    total_debt    REAL,
    cash          REAL,
    current_assets      REAL,
    current_liabilities REAL,
    eps           REAL,
    ebit               REAL,
    retained_earnings  REAL,
    inventory          REAL,
    receivables        REAL,
    payables           REAL,
    interest_expense   REAL,
    depreciation       REAL,
    ocf                REAL,
    capex              REAL,
    raw_json      TEXT,
    fetched_at    TEXT NOT NULL,
    UNIQUE(symbol, period, period_type)
);
CREATE INDEX IF NOT EXISTS idx_psx_financials_symbol
    ON psx_financials(symbol, period_type);
-- Phase 3: build_analysis() reads a ticker's periods newest-first for every
-- section. Including `period` lets that ORDER BY be served from the index rather
-- than a filesort, which is the plan's stated way to hold the 500ms budget
-- without introducing a cache (and the staleness bugs a cache brings).
CREATE INDEX IF NOT EXISTS idx_psx_financials_symbol_period
    ON psx_financials(symbol, period_type, period DESC);

-- PSX announcements (financial results, board meetings, dividends)
CREATE TABLE IF NOT EXISTS psx_announcements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    ann_type     TEXT NOT NULL,            -- financial_results | board_meeting | dividend
    ann_date     TEXT,
    title        TEXT,
    document_url TEXT,
    fetched_at   TEXT NOT NULL,
    UNIQUE(symbol, ann_type, ann_date, title)
);
CREATE INDEX IF NOT EXISTS idx_psx_ann_symbol
    ON psx_announcements(symbol, ann_type);

-- Personal watchlist (PSX symbols only)
CREATE TABLE IF NOT EXISTS psx_watchlist (
    symbol     TEXT PRIMARY KEY,
    notes      TEXT,
    added_at   TEXT NOT NULL
);

-- Sector-median benchmarks (computed nightly from validated company ratios; M9)
CREATE TABLE IF NOT EXISTS psx_sector_medians (
    sector         TEXT NOT NULL,
    metric         TEXT NOT NULL,
    median         REAL,
    company_count  INTEGER NOT NULL DEFAULT 0,
    computed_date  TEXT NOT NULL,
    UNIQUE(sector, metric)
);

--══ Monetary / forex ═════════════════════════════════════════════════════════

-- PKR forex (SBP / ExchangeRate-API)
CREATE TABLE IF NOT EXISTS pkr_forex (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rate_date      TEXT NOT NULL,
    quote_currency TEXT NOT NULL,          -- USD, EUR, GBP, CNY, SAR, AED ...
    rate           REAL NOT NULL,          -- PKR per 1 unit of quote
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    UNIQUE(rate_date, quote_currency)
);
CREATE INDEX IF NOT EXISTS idx_pkr_forex_date
    ON pkr_forex(rate_date);

-- SBP monetary indicators
CREATE TABLE IF NOT EXISTS sbp_indicators (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator     TEXT NOT NULL,           -- policy_rate | cpi | core_cpi | m2_growth | forex_reserves | npl_ratio
    period        TEXT NOT NULL,
    value         REAL NOT NULL,
    unit          TEXT,
    source        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    UNIQUE(indicator, period)
);

-- ══ Macro / fiscal ═══════════════════════════════════════════════════════════

-- Generic Pakistan macro series (PBS, MoF, EAD, IMF, World Bank)
CREATE TABLE IF NOT EXISTS pk_macro (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,               -- pbs | mof | ead | imf | worldbank | fbr | boi
    indicator TEXT NOT NULL,
    period    TEXT NOT NULL,
    value     REAL,
    unit      TEXT,
    detail    TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(source, indicator, period)
);

-- PBS Large Scale Manufacturing Index — per sector
CREATE TABLE IF NOT EXISTS pbs_lsmi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sector      TEXT NOT NULL,
    period      TEXT NOT NULL,
    index_value REAL,
    yoy_change_pct REAL,
    fetched_at  TEXT NOT NULL,
    UNIQUE(sector, period)
);

-- PBS trade — exports/imports by commodity
CREATE TABLE IF NOT EXISTS pbs_trade (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction    TEXT NOT NULL,            -- export | import
    commodity    TEXT NOT NULL,
    period       TEXT NOT NULL,
    value_usd_mn REAL,
    qty          REAL,
    unit         TEXT,
    fetched_at   TEXT NOT NULL,
    UNIQUE(direction, commodity, period)
);

-- ══ Sector regulators ════════════════════════════════════════════════════════

-- FBR tax revenue
CREATE TABLE IF NOT EXISTS fbr_revenue (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tax_type  TEXT NOT NULL,               -- total | customs | sales_tax | income_tax | fed
    period    TEXT NOT NULL,
    collected_pkr_bn REAL,
    target_pkr_bn    REAL,
    fetched_at TEXT NOT NULL,
    UNIQUE(tax_type, period)
);

-- NEPRA electricity (tariff + capacity)
CREATE TABLE IF NOT EXISTS nepra_power (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    metric    TEXT NOT NULL,
    period    TEXT NOT NULL,
    value     REAL,
    unit      TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(metric, period)
);

-- OGRA fuel prices
CREATE TABLE IF NOT EXISTS ogra_prices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product    TEXT NOT NULL,              -- petrol | hsd | lpg | natural_gas
    price_date TEXT NOT NULL,
    price_pkr  REAL NOT NULL,
    unit       TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(product, price_date)
);

-- PTA telecom
CREATE TABLE IF NOT EXISTS pta_telecom (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    metric    TEXT NOT NULL,
    period    TEXT NOT NULL,
    value     REAL,
    unit      TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE(metric, period)
);

-- Industry association production stats (APCMA, APTMA, PAMA, PSMA, PPMA, OICCI)
CREATE TABLE IF NOT EXISTS industry_stats (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    industry  TEXT NOT NULL,
    metric    TEXT NOT NULL,
    period    TEXT NOT NULL,
    value     REAL,
    unit      TEXT,
    source    TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE(industry, metric, period, source)
);

-- ══ Benchmarks & ops ═════════════════════════════════════════════════════════

-- Global sector peer multiples (yfinance) for benchmarking PSX companies
CREATE TABLE IF NOT EXISTS global_sector_peers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    industry    TEXT NOT NULL,
    peer_ticker TEXT NOT NULL,
    peer_name   TEXT,
    pe_ratio    REAL,
    ev_ebitda   REAL,
    net_margin  REAL,
    revenue_growth REAL,
    fetched_at  TEXT NOT NULL,
    UNIQUE(industry, peer_ticker, fetched_at)
);

-- Unified scraper run log (no region_id)
CREATE TABLE IF NOT EXISTS scraper_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scraper_id    TEXT NOT NULL,
    status        TEXT NOT NULL,           -- success | partial | failed
    rows_inserted INTEGER DEFAULT 0,
    rows_skipped  INTEGER DEFAULT 0,
    error_msg     TEXT,
    started_at    TEXT NOT NULL,
    completed_at  TEXT
);

-- Phase 2: company payouts (dividends / bonus / rights) from the PSX company page.
CREATE TABLE IF NOT EXISTS psx_dividends (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    period       TEXT,                    -- e.g. 2025 | Q1 2026
    payout_type  TEXT,                    -- cash | bonus | right
    value        TEXT,                    -- as printed (e.g. "45%" or "4.50")
    announced_at TEXT,
    fetched_at   TEXT NOT NULL,
    UNIQUE(symbol, period, payout_type, value)
);
CREATE INDEX IF NOT EXISTS ix_psx_dividends_symbol ON psx_dividends(symbol);

-- Private Analyser: named saved entries, multiple per client
CREATE TABLE IF NOT EXISTS private_entries (
    id           TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    entry_name   TEXT NOT NULL,
    company_name TEXT,
    data_json    TEXT NOT NULL,
    saved_at     TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);
CREATE INDEX IF NOT EXISTS ix_private_entries_client
    ON private_entries(client_id, saved_at DESC);

-- DCF valuation assumption overrides (user-editable via Settings; NULL = use live/fallback)
CREATE TABLE IF NOT EXISTS dcf_assumptions (
    key            TEXT PRIMARY KEY,
    override_value REAL,
    updated_at     TEXT
);

-- Phase 2: one row per BaseScraper.fetch(). Drives the Data Health card and the
-- regression suite. Richer than scraper_runs (status enum, method, source, artifacts).
CREATE TABLE IF NOT EXISTS scrape_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scraper_id        TEXT NOT NULL,
    status            TEXT NOT NULL,
    rows_inserted     INTEGER DEFAULT 0,
    rows_updated      INTEGER DEFAULT 0,
    rows_failed       INTEGER DEFAULT 0,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    duration_ms       INTEGER,
    error_message     TEXT,
    source_url        TEXT,
    method_used       TEXT,
    artifacts_json    TEXT
);
CREATE INDEX IF NOT EXISTS ix_scrape_log_scraper
    ON scrape_log(scraper_id, started_at DESC);
"""


def run_migrations() -> None:
    """
    Create all tables if they do not exist. If a legacy multi-region schema is
    detected, perform a one-time full reset first (pre-production — no data kept).
    """
    db = get_db()
    try:
        if _legacy_schema_present(db):
            log.warning("Legacy multi-region schema detected - performing full reset")
            _drop_all_tables(db)
        _drop_deprecated_tables(db)
        db.executescript(TABLES)
        _apply_column_additions(db)
        db.commit()
        log.info("Database schema ready", version=SCHEMA_VERSION)
    except Exception as e:
        log.error("Schema migration failed", error=str(e))
        raise


def _drop_deprecated_tables(db) -> None:
    """One-shot: drop tables from the removed AI/stage/memory layer (Phase 1)."""
    db.execute("PRAGMA foreign_keys=OFF")
    for name in ("analyses", "memory_entries", "learned_patterns",
                 "memory_tier1", "memory_tier2", "memory_tier3", "client_entities",
                 "analysis_runs", "psx_clusters", "psx_cluster_members"):
        db.execute(f"DROP TABLE IF EXISTS {name}")
    db.execute("PRAGMA foreign_keys=ON")


def _apply_column_additions(db) -> None:
    """Add new columns to existing tables that predate them (idempotent)."""
    # Migrate private_entries from old single-row-per-client schema (client_id PK)
    # to multi-entry schema (id PK). Runs only once; the old table has no production
    # data worth preserving (it was just created in this sprint).
    _migrate_private_entries(db)

    _add_column_if_missing(db, "psx_companies", "market_cap", "REAL")
    # Phase 2: everything the PSX company page displays (Equity + Company Profile).
    for _c, _t in (
        ("shares_outstanding", "REAL"), ("free_float", "REAL"), ("free_float_pct", "REAL"),
        ("pe_ttm", "REAL"), ("week52_high", "REAL"), ("week52_low", "REAL"),
        ("ceo", "TEXT"), ("chairman", "TEXT"), ("company_secretary", "TEXT"),
        ("address", "TEXT"), ("registrar", "TEXT"), ("business_description", "TEXT"),
        ("profile_fetched_at", "TEXT"),
    ):
        _add_column_if_missing(db, "psx_companies", _c, _t)
    for _c, _t in (("close", "REAL"), ("total_trades", "INTEGER"), ("sanity_flag", "TEXT")):
        _add_column_if_missing(db, "psx_prices", _c, _t)

    # Phase 2 provenance: every macro observation must be traceable back to the
    # exact page and the raw text it was parsed from, so that when a source
    # silently changes shape the discrepancy is auditable and correctable.
    for _c, _t in (
        ("source_url", "TEXT"),       # exact page/endpoint the value came from
        ("source_snippet", "TEXT"),   # raw as-printed text the number was parsed from
        ("method_used", "TEXT"),      # html_bs4 | json_api | playwright | pdf | ocr
        ("scraper_id", "TEXT"),
        ("sanity_flag", "TEXT"),      # NULL = ok | 'implausible' (never delete, flag)
    ):
        _add_column_if_missing(db, "pk_macro", _c, _t)
    # Balance-sheet / cash-flow columns for richer ratios (Altman, coverage, peer comp).
    for _col in ("ebit", "retained_earnings", "inventory", "receivables", "payables",
                 "interest_expense", "depreciation", "ocf", "capex"):
        _add_column_if_missing(db, "psx_financials", _col, "REAL")

    # Phase 3 Track B: record WHICH document a period's figures came from, so a
    # missing balance sheet can name the actual reason ("the latest filing on PSX
    # is a 37-page interim report") instead of a generic gap - and so a sourcing
    # failure is distinguishable from a genuine absence, per ticker.
    for _c, _t in (("source_document_type", "TEXT"),    # annual_report|annual_results|interim
                   ("source_document_pages", "INTEGER"),
                   ("source_document_url", "TEXT"),
                   ("source_document_date", "TEXT")):
        _add_column_if_missing(db, "psx_financials", _c, _t)


def _migrate_private_entries(db) -> None:
    """One-time: if private_entries has client_id as primary key (old schema),
    drop and recreate it with id as PK so multiple entries per client are supported."""
    existing = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='private_entries'"
    ).fetchone()
    if not existing:
        return  # table doesn't exist yet; CREATE TABLE IF NOT EXISTS will create it
    cols = {row[1] for row in db.execute("PRAGMA table_info(private_entries)")}
    if "id" not in cols:
        # Old schema: client_id TEXT PRIMARY KEY. Recreate.
        db.execute("DROP TABLE IF EXISTS private_entries")
        log.info("Migrated private_entries to multi-entry schema")


def _add_column_if_missing(db, table: str, column: str, col_type: str) -> None:
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        log.info("Column added", table=table, column=column)


def _legacy_schema_present(db) -> bool:
    """True if any legacy multi-region table exists, or analysis_runs has region_id."""
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    if names & set(_LEGACY_TABLES):
        return True
    # Detect region_id column on analysis_runs (old schema)
    if "analysis_runs" in names:
        cols = {c[1] for c in db.execute("PRAGMA table_info(analysis_runs)").fetchall()}
        if "region_id" in cols:
            return True
    # Detect primary_region column on clients (old schema)
    if "clients" in names:
        cols = {c[1] for c in db.execute("PRAGMA table_info(clients)").fetchall()}
        if "primary_region" in cols or "regions" in cols:
            return True
    return False


def _drop_all_tables(db) -> None:
    """Drop every user table for a clean rebuild. Pre-production reset only."""
    db.execute("PRAGMA foreign_keys=OFF")
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (name,) in rows:
        db.execute(f"DROP TABLE IF EXISTS {name}")
    db.commit()
    db.execute("PRAGMA foreign_keys=ON")
    log.info("Dropped legacy tables", count=len(rows))
