from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin


class ResumeVersion(TimestampMixin, Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)

    file_name: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    sections: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    parsed_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Keyphrases extracted once at upload time (tag_extractor.extract_tags) from
    # every section except header -- Job Match's dominant signal (see WEIGHTS
    # in job_match/analysis.py) compares this against JobPosting.tags.
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)

    scores: Mapped[list["ResumeScore"]] = relationship(back_populates="resume_version")


class ResumeScore(TimestampMixin, Base):
    __tablename__ = "resume_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    resume_version_id: Mapped[int] = mapped_column(ForeignKey("resume_versions.id"), index=True)

    resume_score: Mapped[int] = mapped_column(Integer)
    ats_score: Mapped[int] = mapped_column(Integer)
    section_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    missing_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    weak_sections: Mapped[list[str]] = mapped_column(JSONB, default=list)
    recommendations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    rewrite_suggestions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # API usage of the analysis that produced this row: promptTokens, completionTokens,
    # embeddingTokens, totalTokens, apiCalls, cached (same shape as the response's tokenUsage).
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    resume_version: Mapped["ResumeVersion"] = relationship(back_populates="scores")


class ResumeUpload(TimestampMixin, Base):
    """One row per request to /resume/ats-check or /resume/analyze -- including cache
    hits and failures -- with the API usage that request spent. ats-check stores no
    resume content (see routes/resume.py), so this row is its only record; the resume
    itself is identified by content_hash."""

    __tablename__ = "resume_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(50), index=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_profiles.id"), index=True, default=None
    )
    resume_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume_versions.id"), index=True, default=None
    )
    file_name: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    target_role: Mapped[str] = mapped_column(String(255), default="")
    target_industry: Mapped[str] = mapped_column(String(255), default="")

    # "pending" until the request finishes, then "success", "cached" or "error".
    status: Mapped[str] = mapped_column(String(50), default="pending")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    embedding_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
