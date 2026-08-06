"""Audit Logger: persists every prompt/response pair for traceability (see spec section 11)."""

from sqlalchemy.orm import Session

from app.db.models.ai_ops import AIResponseLog


def log_ai_call(
    db: Session,
    *,
    engine: str,
    model: str,
    prompt: str,
    response: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    candidate_id: int | None = None,
    prompt_version_id: int | None = None,
    status: str = "success",
    error: str | None = None,
    retry_count: int = 0,
) -> AIResponseLog:
    entry = AIResponseLog(
        engine=engine,
        candidate_id=candidate_id,
        prompt_version_id=prompt_version_id,
        model=model,
        prompt=prompt,
        response=response,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        status=status,
        error=error,
        retry_count=retry_count,
    )
    db.add(entry)
    db.flush()
    return entry
