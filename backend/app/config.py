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

    # Apollo people enrichment for researched leads. Blank key disables
    # enrichment entirely -- the lead pipeline works without it.
    apollo_api_key: str = ""
    apollo_base_url: str = "https://api.apollo.io"
    apollo_timeout_seconds: float = 15.0
    # How long an Apollo entitlement verdict stays good. The probe has to call a
    # billable, rate-limited people endpoint to learn anything, so it must not run
    # once per page render. Plan changes are rare; 10 minutes is plenty fresh.
    apollo_status_cache_seconds: float = 600.0

    # People Data Labs -- sourcing shortlists of recent graduates. PDL carries
    # education history (which Apollo does not), so it is the sourcing half of
    # the lead pipeline; Apollo/ContactOut remain the contact half.
    pdl_api_key: str = ""
    pdl_base_url: str = "https://api.peopledatalabs.com"
    pdl_timeout_seconds: float = 20.0
    pdl_min_likelihood: int = 6

    response_validation_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
