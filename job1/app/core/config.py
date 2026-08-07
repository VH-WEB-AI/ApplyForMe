"""
Centralized application configuration.
Loaded once as a singleton via `get_settings()` (LRU-cached).
"""
from functools import lru_cache
from typing import List, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "ApplyForMe"
    ENV: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # Security
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # OpenAI
    # AI provider. Set to "gemini" to use the Gemini Developer API, or
    # "openai" to use the OpenAI API. Only the selected provider needs a key.
    AI_ENABLED: bool = True
    AI_PROVIDER: Literal["openai", "gemini"] = "openai"

    OPENAI_API_KEY: str = ""
    OPENAI_CHAT_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"

    GEMINI_API_KEY: str = ""
    GEMINI_CHAT_MODEL: str = "gemini-2.5-flash"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIM: int = 3072

    # Cache
    CACHE_TTL_DEFAULT: int = 3600
    CACHE_TTL_EMBEDDINGS: int = 86400

    # Observability
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
