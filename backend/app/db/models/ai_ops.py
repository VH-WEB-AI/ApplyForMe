from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin


class PromptVersion(TimestampMixin, Base):
    """Versioned prompt templates per engine, so prompts can evolve without code changes."""

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    engine: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(50))
    template: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AIResponseLog(TimestampMixin, Base):
    """Audit log: every prompt/response pair, tokens, latency, and errors."""

    __tablename__ = "ai_response_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    engine: Mapped[str] = mapped_column(String(100), index=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_profiles.id"), default=None
    )
    prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id"), default=None
    )
    # Set for resume_intelligence calls made for a stored resume, so token usage
    # can be read per upload. Null for anonymous /resume/ats-check and other engines.
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id"), index=True, default=None
    )
    # Set for every resume_intelligence call (ats-check and analyze): the upload
    # request this API call was made for.
    resume_upload_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_uploads.id"), index=True, default=None
    )

    model: Mapped[str] = mapped_column(String(100))
    prompt: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Summed over every attempt, including ones that failed validation and were retried.
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(50), default="success")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
