from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin


class JobPosting(TimestampMixin, Base):
    """An existing job listing that candidates are matched against."""

    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    seniority: Mapped[str | None] = mapped_column(String(50), default=None)
    salary_min: Mapped[int | None] = mapped_column(Integer, default=None)
    salary_max: Mapped[int | None] = mapped_column(Integer, default=None)
    visa_sponsorship: Mapped[bool] = mapped_column(Boolean, default=False)
    required_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    min_experience_years: Mapped[int | None] = mapped_column(Integer, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Keyphrases extracted once at ingest time (tag_extractor.extract_tags) from
    # the job description -- compared against ResumeVersion.tags in Job Match.
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)

    matches: Mapped[list["JobMatch"]] = relationship(back_populates="job_posting")


class JobMatch(TimestampMixin, Base):
    __tablename__ = "job_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)
    job_posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), index=True)
    resume_version_id: Mapped[int] = mapped_column(ForeignKey("resume_versions.id"))

    match_score: Mapped[int] = mapped_column(Integer)
    semantic_score: Mapped[float] = mapped_column(default=0.0)
    tags_score: Mapped[float] = mapped_column(default=0.0)
    experience_score: Mapped[float] = mapped_column(default=0.0)
    location_score: Mapped[float] = mapped_column(default=0.0)
    visa_score: Mapped[float] = mapped_column(default=0.0)
    salary_score: Mapped[float] = mapped_column(default=0.0)
    missing_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    resume_changes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    interview_readiness: Mapped[str] = mapped_column(String(50), default="unknown")
    priority_badge: Mapped[str] = mapped_column(String(50), default="normal")
    explanation: Mapped[str] = mapped_column(Text, default="")

    job_posting: Mapped["JobPosting"] = relationship(back_populates="matches")


class Application(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)
    job_posting_id: Mapped[int | None] = mapped_column(ForeignKey("job_postings.id"), default=None)

    company: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="applied")
    applied_at: Mapped[datetime | None] = mapped_column(default=None)

    interviews: Mapped[list["Interview"]] = relationship(back_populates="application")


class Interview(TimestampMixin, Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidate_profiles.id"), index=True)

    stage: Mapped[str] = mapped_column(String(100))
    scheduled_at: Mapped[datetime | None] = mapped_column(default=None)
    outcome: Mapped[str | None] = mapped_column(String(50), default=None)

    application: Mapped["Application"] = relationship(back_populates="interviews")
