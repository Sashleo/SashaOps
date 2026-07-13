"""
config/settings.py — Application settings loaded from environment variables.
All configuration lives here. No hardcoded values anywhere else.
"""

from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache

# Placeholder values shipped in .env.example — must never be accepted at runtime.
_PLACEHOLDER_SECRETS = {
    "", "generate-a-64-char-random-string-here", "set-a-strong-password-here",
    "change-me", "changeme",
}


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME: str = "SashaOps"
    APP_VERSION: str = "1.0.0"
    APP_SECRET_KEY: str
    DEBUG: bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_PATH: str = "/app/data/sashaops.db"
    DB_MASTER_PASSWORD: str
    DOCUMENTS_PATH: str = "/app/data/documents"

    # ── Security ──────────────────────────────────────────────────────────────
    SESSION_EXPIRE_HOURS: int = 2
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15
    # First-run admin creation is gated: either the request originates from
    # localhost, OR this token is set and supplied on the creation request.
    ADMIN_BOOTSTRAP_TOKEN: str = ""

    # ── Local ML Layer ────────────────────────────────────────────────────────
    LOCAL_ML: bool = True                    # Enable local ML models (sentence-transformers, FinBERT, ARIMA)
    ML_TOP_K_HEADLINES: int = 15             # Max headlines passed to news relevance ranking
    SENTIMENT_MODEL: str = "yiyanghkust/finbert-tone"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ── Startup secret validation — refuse to boot on blank/default/short secrets ──
    @field_validator("APP_SECRET_KEY")
    @classmethod
    def _check_app_secret(cls, v: str) -> str:
        if v in _PLACEHOLDER_SECRETS or len(v) < 32:
            raise ValueError(
                "APP_SECRET_KEY must be set to a strong value of at least 32 characters "
                "(not the .env.example placeholder). Generate one with: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @field_validator("DB_MASTER_PASSWORD")
    @classmethod
    def _check_db_password(cls, v: str) -> str:
        if v in _PLACEHOLDER_SECRETS or len(v) < 12:
            raise ValueError(
                "DB_MASTER_PASSWORD must be set to a strong value of at least 12 "
                "characters (not the .env.example placeholder)."
            )
        return v

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"   # tolerate leftover env vars (e.g. removed AI keys)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
