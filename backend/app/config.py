from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    # Path prefix this app is mounted under behind a reverse proxy (e.g. "/aiapi").
    # Lets FastAPI generate correctly prefixed URLs in its OpenAPI schema/docs.
    root_path: str = ""

    database_url: str = "postgresql+psycopg://applyforme:applyforme@localhost:5432/applyforme"
    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str = ""
    openai_base_url: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    # Only applies to reasoning-tier models (o1/o3/o4/gpt-5 families). Lower effort
    # trades reasoning depth for latency — "low" is a good default for the
    # structured extraction/scoring tasks these engines do.
    openai_reasoning_effort: str = "low"

    # Career Health weighting (admin-configurable; see spec section on suggested weights)
    career_health_weights: dict[str, float] = {
        "resume_quality": 0.20,
        "ats_compatibility": 0.15,
        "profile_completeness": 0.10,
        "skill_relevance": 0.20,
        "application_activity": 0.10,
        "interview_progress": 0.10,
        "market_alignment": 0.10,
        "professional_presence": 0.05,
    }

    response_validation_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
